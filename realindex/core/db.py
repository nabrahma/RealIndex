# SPDX-License-Identifier: GPL-3.0-or-later
"""SQLite index (one per drop root, in its state folder). The index is the source of truth for what
RealIndex knows; managed .blend files are a rebuildable product of it plus the drop folder.

Tables: files, assets, asset_maps, tags, user_state, jobs, plus 'overrides' (forced detection, keyed by
the detected file so a forced type survives a reconvert) and 'inbox_moves' (the Inbox undo log). WAL mode lets
the UI thread read while the service thread writes. Pure Python."""

import json
import os
import sqlite3
import time

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, qhash TEXT, last_seen REAL, asset_id TEXT);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, key TEXT UNIQUE, type TEXT, name TEXT, catalog TEXT, vendor TEXT, license TEXT,
    author TEXT, source TEXT, sources TEXT, managed TEXT, status TEXT, problem TEXT, warnings TEXT,
    version INTEGER DEFAULT 0, fingerprint TEXT, sig TEXT, spec TEXT, info TEXT, preview TEXT,
    created REAL, updated REAL, missing_since REAL);
CREATE INDEX IF NOT EXISTS assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS assets_sig ON assets(sig);
CREATE TABLE IF NOT EXISTS asset_maps (
    asset_id TEXT, kind TEXT, path TEXT, colour_space TEXT, inverted INTEGER, PRIMARY KEY (asset_id, kind));
CREATE TABLE IF NOT EXISTS tags (asset_id TEXT, tag TEXT, source TEXT, PRIMARY KEY (asset_id, tag));
CREATE TABLE IF NOT EXISTS user_state (
    asset_id TEXT PRIMARY KEY, favourite INTEGER DEFAULT 0, last_used REAL, removed_tags TEXT);
CREATE TABLE IF NOT EXISTS overrides (path TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT, kind TEXT, state TEXT, attempts INTEGER DEFAULT 0,
    started REAL, finished REAL, error TEXT, payload TEXT, created REAL);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE TABLE IF NOT EXISTS inbox_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT, batch TEXT, src TEXT, dst TEXT, time REAL, undone INTEGER DEFAULT 0);
"""

ASSET_COLS = ("id", "key", "type", "name", "catalog", "vendor", "license", "author", "source", "sources",
              "managed", "status", "problem", "warnings", "version", "fingerprint", "sig", "spec", "info",
              "preview", "created", "updated", "missing_since")


def connect(path, readonly=False):
    if readonly:
        uri = "file:%s?mode=ro" % path.replace("\\", "/")
        con = sqlite3.connect(uri, uri=True, timeout=5.0, check_same_thread=False)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        con = sqlite3.connect(path, timeout=10.0, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.executescript(SCHEMA)
        cur = con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if cur is None:
            con.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        con.commit()
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con


class Index:
    """Thin typed access used by the engine (service thread) and, read-only, by the UI and the API."""

    def __init__(self, path, readonly=False):
        self.path = path
        self.con = connect(path, readonly)

    def close(self):
        self.con.close()

    def commit(self):
        self.con.commit()

    # -------------------------------------------------------------------------------------------- meta
    def get_meta(self, key, default=None):
        r = self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_meta(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))

    # ------------------------------------------------------------------------------------------- files
    def file_rows(self):
        return {r["path"]: (r["size"], r["mtime"], r["qhash"]) for r in self.con.execute("SELECT * FROM files")}

    def replace_files(self, listing, hashes, owners):
        """listing {path: (size, mtime)}; hashes {(path,size,mtime): qhash}; owners {path: asset id}."""
        now = time.time()
        self.con.execute("DELETE FROM files")
        self.con.executemany(
            "INSERT INTO files VALUES (?,?,?,?,?,?)",
            [(p, s, m, hashes.get((p, s, m)), now, owners.get(p)) for p, (s, m) in listing.items()])

    # ------------------------------------------------------------------------------------------ assets
    def assets(self, where="", args=()):
        sql = "SELECT * FROM assets" + (" WHERE " + where if where else "") + " ORDER BY key"
        return [dict(r) for r in self.con.execute(sql, args)]

    def asset(self, asset_id):
        r = self.con.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        return dict(r) if r else None

    def asset_by_key(self, key):
        r = self.con.execute("SELECT * FROM assets WHERE key=?", (key,)).fetchone()
        return dict(r) if r else None

    def upsert_asset(self, a):
        a = dict(a)
        a["updated"] = time.time()
        a.setdefault("created", a["updated"])
        cols = [c for c in ASSET_COLS if c in a]
        self.con.execute("INSERT OR REPLACE INTO assets (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                         [a[c] for c in cols])

    def update_asset(self, asset_id, **fields):
        fields["updated"] = time.time()
        sets = ",".join("%s=?" % k for k in fields)
        self.con.execute("UPDATE assets SET %s WHERE id=?" % sets, list(fields.values()) + [asset_id])

    def delete_asset(self, asset_id):
        for t in ("assets", "asset_maps", "tags", "user_state"):
            col = "id" if t == "assets" else "asset_id"
            self.con.execute("DELETE FROM %s WHERE %s=?" % (t, col), (asset_id,))
        self.con.execute("DELETE FROM jobs WHERE asset_id=? AND state IN ('waiting','running')", (asset_id,))

    def set_maps(self, asset_id, maps_rows):
        self.con.execute("DELETE FROM asset_maps WHERE asset_id=?", (asset_id,))
        self.con.executemany("INSERT INTO asset_maps VALUES (?,?,?,?,?)",
                             [(asset_id, k, p, cs, int(inv)) for k, p, cs, inv in maps_rows])

    def maps(self, asset_id):
        return {r["kind"]: dict(r) for r in self.con.execute("SELECT * FROM asset_maps WHERE asset_id=?", (asset_id,))}

    # -------------------------------------------------------------------------------------------- tags
    def set_auto_tags(self, asset_id, tags):
        self.con.execute("DELETE FROM tags WHERE asset_id=? AND source='auto'", (asset_id,))
        user = {r[0] for r in self.con.execute("SELECT tag FROM tags WHERE asset_id=? AND source='user'", (asset_id,))}
        self.con.executemany("INSERT OR IGNORE INTO tags VALUES (?,?,'auto')",
                             [(asset_id, t) for t in tags if t not in user])

    def tags(self, asset_id):
        """Effective tags: auto tags minus the ones the user removed, plus user tags."""
        removed = set(json.loads(self.user_state(asset_id).get("removed_tags") or "[]"))
        rows = self.con.execute("SELECT tag, source FROM tags WHERE asset_id=? ORDER BY source, rowid", (asset_id,))
        return [r["tag"] for r in rows if not (r["source"] == "auto" and r["tag"] in removed)]

    def add_user_tag(self, asset_id, tag):
        self.con.execute("INSERT OR REPLACE INTO tags VALUES (?,?,'user')", (asset_id, tag))
        st = self.user_state(asset_id)
        removed = [t for t in json.loads(st.get("removed_tags") or "[]") if t != tag]
        self._set_state(asset_id, removed_tags=json.dumps(removed))

    def remove_tag(self, asset_id, tag):
        self.con.execute("DELETE FROM tags WHERE asset_id=? AND tag=? AND source='user'", (asset_id, tag))
        st = self.user_state(asset_id)
        removed = sorted(set(json.loads(st.get("removed_tags") or "[]")) | {tag})
        self._set_state(asset_id, removed_tags=json.dumps(removed))

    # -------------------------------------------------------------------------------------- user state
    def user_state(self, asset_id):
        r = self.con.execute("SELECT * FROM user_state WHERE asset_id=?", (asset_id,)).fetchone()
        return dict(r) if r else {"asset_id": asset_id, "favourite": 0, "last_used": None, "removed_tags": None}

    def _set_state(self, asset_id, **fields):
        st = self.user_state(asset_id)
        st.update(fields)
        self.con.execute("INSERT OR REPLACE INTO user_state VALUES (?,?,?,?)",
                         (asset_id, int(st["favourite"] or 0), st["last_used"], st["removed_tags"]))

    def set_favourite(self, asset_id, value):
        self._set_state(asset_id, favourite=int(bool(value)))

    def touch(self, asset_id):
        self._set_state(asset_id, last_used=time.time())

    def overrides(self):
        return {r["path"]: r["value"] for r in self.con.execute("SELECT * FROM overrides")}

    def set_override(self, path, value):
        if value in (None, "", "AUTO"):
            self.con.execute("DELETE FROM overrides WHERE path=?", (path,))
        else:
            self.con.execute("INSERT OR REPLACE INTO overrides VALUES (?,?)", (path, value))

    # -------------------------------------------------------------------------------------------- jobs
    def add_job(self, asset_id, kind, payload=None):
        cur = self.con.execute("INSERT INTO jobs (asset_id, kind, state, attempts, payload, created) "
                               "VALUES (?,?,'waiting',0,?,?)", (asset_id, kind, json.dumps(payload or {}), time.time()))
        return cur.lastrowid

    def jobs(self, state=None):
        if state:
            return [dict(r) for r in self.con.execute("SELECT * FROM jobs WHERE state=? ORDER BY id", (state,))]
        return [dict(r) for r in self.con.execute("SELECT * FROM jobs ORDER BY id")]

    def job(self, job_id):
        r = self.con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    def update_job(self, job_id, **fields):
        sets = ",".join("%s=?" % k for k in fields)
        self.con.execute("UPDATE jobs SET %s WHERE id=?" % sets, list(fields.values()) + [job_id])

    def cancel_jobs(self, asset_id):
        self.con.execute("UPDATE jobs SET state='cancelled' WHERE asset_id=? AND state='waiting'", (asset_id,))

    # ------------------------------------------------------------------------------------- inbox moves
    def log_move(self, batch, src, dst):
        self.con.execute("INSERT INTO inbox_moves (batch, src, dst, time) VALUES (?,?,?,?)",
                         (batch, src, dst, time.time()))

    def last_move_batch(self):
        r = self.con.execute("SELECT batch FROM inbox_moves WHERE undone=0 ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None, []
        rows = [dict(x) for x in self.con.execute("SELECT * FROM inbox_moves WHERE batch=? AND undone=0", (r[0],))]
        return r[0], rows

    def mark_batch_undone(self, batch):
        self.con.execute("UPDATE inbox_moves SET undone=1 WHERE batch=?", (batch,))
