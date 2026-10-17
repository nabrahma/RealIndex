# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only access to every root's index, used by realindex.api, Quick Place and other add-ons.
Opens each index read-only; never blocks the service thread (SQLite WAL). Pure Python."""

import json
import os

from . import db, fsutil, search

PUBLIC_TYPES = {"material": "texture_set", "hdri": "hdri", "model": "model", "blend": "model"}


def _open(state):
    p = os.path.join(state, "index.db")
    if not os.path.exists(p):
        return None
    try:
        return db.Index(p, readonly=True)
    except Exception:
        return None


def load(states, types=None):
    """[(root, state)] -> list of asset dicts with status ok. types: internal types ('material', 'hdri', ...)."""
    out = []
    for root, state in states:
        idx = _open(state)
        if idx is None:
            continue
        try:
            where = "status='ok'"
            args = ()
            if types:
                where += " AND type IN (%s)" % ",".join("?" * len(types))
                args = tuple(types)
            tags = {}
            for r in idx.con.execute("SELECT asset_id, tag, source FROM tags ORDER BY source DESC, rowid"):
                tags.setdefault(r[0], []).append((r[1], r[2]))
            states_ = {r["asset_id"]: dict(r) for r in idx.con.execute("SELECT * FROM user_state")}
            maps_ = {}
            for r in idx.con.execute("SELECT * FROM asset_maps"):
                maps_.setdefault(r["asset_id"], {})[r["kind"]] = r["path"]
            for a in idx.assets(where, args):
                out.append(_record(root, state, a, tags.get(a["id"], []), states_.get(a["id"], {}),
                                   maps_.get(a["id"], {})))
        finally:
            idx.close()
    return out


def _record(root, state, a, tag_rows, st, maps):
    spec = json.loads(a["spec"] or "{}")
    info = json.loads(a["info"] or "{}")
    removed = set(json.loads(st.get("removed_tags") or "[]"))
    tags = [t for t, src in tag_rows if not (src == "auto" and t in removed)]
    seen = []
    for t in tags:
        if t not in seen:
            seen.append(t)
    preview = os.path.join(state, "previews", a["id"] + ".png")
    managed = os.path.join(state, "library", *a["managed"].split("/")) if a["managed"] else None
    rec = {"id": a["id"], "name": a["name"], "type": a["type"], "kind": PUBLIC_TYPES.get(a["type"], a["type"]),
           "catalog": a["catalog"], "vendor": a["vendor"], "license": a["license"], "author": a["author"],
           "tags": seen, "preview": preview if os.path.exists(preview) else None,
           "source": os.path.join(root, *(a["source"] or "").split("/")),
           "sources": sorted(fsutil.long(p) for p in spec.get("real", {}).values()), "managed": managed,
           "root": root, "state": state,
           "favourite": bool(st.get("favourite")), "last_used": st.get("last_used"),
           "asset_kind": info.get("asset_kind"), "members": info.get("converted") or [],
           "warnings": json.loads(a["warnings"] or "[]")}
    if a["type"] == "material":
        maps = {k: fsutil.long(v) for k, v in maps.items()}  # usable as given, even past 260 characters
        rec["maps"] = dict(maps)
        color = maps.get("color")
        rec["folder"] = os.path.dirname(color) if color else None
        rec["size_m"] = spec.get("meta", {}).get("size_m") or 1.0
    return rec


def find(states, query, types=None, limit=48):
    items = [search.Item(r) for r in load(states, types)]
    return [i.data for i in search.search(items, query, limit=limit)]
