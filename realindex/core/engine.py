# SPDX-License-Identifier: GPL-3.0-or-later
"""One drop root's sync engine: watch, index, reconcile, queue, collect results.

Runs on the service thread (core/service.py); tests drive it synchronously with tick(). It never touches
the drop root except Inbox auto-filing; everything it writes lives in the root's state folder.

Reconciliation, per candidate from detection:
  same key as a known asset   -> unchanged: nothing; content changed: reconvert; name/catalog changed: update
  new key, same content signature as a vanished asset -> renamed or moved: update (no reconvert)
  new key, nothing matches    -> new asset: convert
  known asset no longer found -> moved to trash/ and hidden (status 'missing'); removed once confirmed
"""

import hashlib
import json
import os
import time
import uuid

from . import db, detect, fsutil, indexer, library, names, scan

CONVERTIBLE = ("material", "hdri", "model", "blend")
WEEK = 7 * 24 * 3600.0


class Settings:
    """Preferences the engine and workers use (a plain copy, so the service thread never reads bpy)."""
    DEFAULTS = dict(poll_interval=2.0, workers=2, origin_bottom=True, remove_lights=True, mapping="UV",
                    displacement="TRUE", keep_lods=False, preview_size=256, preview_engine="AUTO", inbox_autofile=True,
                    delete_mode="ASK", ao_multiply=False, blend_mode="COLLECTIONS", timeout=120.0)

    def __init__(self, **kw):
        for k, v in self.DEFAULTS.items():
            setattr(self, k, kw.get(k, v))

    def worker_prefs(self):
        return {k: getattr(self, k) for k in ("origin_bottom", "remove_lights", "mapping", "displacement", "keep_lods",
                                              "preview_size", "preview_engine", "ao_multiply", "blend_mode")}


class RootConfig:
    def __init__(self, path, state_dir="", pack=False, enabled=True, label="RealIndex"):
        self.path = os.path.abspath(path)
        self.state = os.path.abspath(state_dir) if state_dir else fsutil.default_state_dir(self.path)
        self.pack, self.enabled, self.label = pack, enabled, label


def _fingerprint(c, vf):
    items = []
    for p in sorted(set(c.files) | set(c.uses)):
        v = vf.get(p)
        items.append([p, v.size if v else 0, v.mtime if v else 0])
    body = [c.kind, items, sorted((k, v["path"], v.get("dx", False)) for k, v in c.maps.items()), c.lods,
            c.problem or ""]
    return hashlib.sha1(json.dumps(body).encode("utf-8")).hexdigest()


def _signature(c, vf):
    hs = sorted(vf[p].qhash or "" for p in set(c.files) | set(c.uses) if p in vf)
    return hashlib.sha1(("%s|%s" % (c.kind, ",".join(hs))).encode("utf-8")).hexdigest()


class Engine:
    def __init__(self, cfg, settings=None, clock=time.time):
        self.cfg = cfg
        self.settings = settings or Settings()
        self.clock = clock
        self.root, self.state = cfg.path, cfg.state
        self.paused = False
        self.online = os.path.isdir(self.root)
        fsutil.ensure_state_dir(self.state, self.root)
        self.db = db.Index(os.path.join(self.state, "index.db"))
        rows = self.db.file_rows()
        self.hashes = {(p, s, m): q for p, (s, m, q) in rows.items() if q}
        self.settler = scan.Settler({p: (s, m) for p, (s, m, _q) in rows.items()})
        self.probe_cache = {}
        self.listing = {}
        self.events = []
        self.force = False
        self.last_poll_ms = 0.0
        self.last_index_ms = 0.0
        self.pending_files = 0
        self.disk = {}
        self.batch_total = 0
        self.batch_done = 0
        self.publish_state = ""
        self.db.con.execute("UPDATE jobs SET state='waiting' WHERE state='running'")  # Blender quit mid-job
        self.db.commit()

    # =================================================================================== polling
    def tick(self):
        """One poll: walk, settle, reindex settled changes, purge expired removals."""
        if not os.path.isdir(self.root):
            if self.online:
                self.online = False
                self.events.append("status")
            return
        if not self.online:
            self.online = True
            self.force = True
            self.events.append("status")
        if self.paused:
            return
        t = time.perf_counter()
        cur = scan.walk(self.root)
        self.last_poll_ms = (time.perf_counter() - t) * 1000.0
        self.listing = cur
        eff, changed = self.settler.settle(cur, self.root)
        if changed or self.force:
            self.force = False
            t = time.perf_counter()
            self.reindex(eff)
            self.last_index_ms = (time.perf_counter() - t) * 1000.0
        self.pending_files = len(self.settler.pending(cur))
        self._purge_expired()

    def sync_now(self):
        """Settle whatever is on disk now (two back-to-back polls)."""
        self.tick()
        self.tick()

    def reindex(self, listing):
        overrides = self.db.overrides()
        probe = detect.Probe(self.probe_cache)
        cands, exp = indexer.run_detection(self.root, self.state, listing, self.hashes, overrides, probe)
        if self.settings.inbox_autofile:
            moved = self._autofile(cands, listing)
            if moved:
                listing = moved
                cands, exp = indexer.run_detection(self.root, self.state, listing, self.hashes, overrides, probe)
        keep = set((p, s, m) for p, (s, m) in listing.items())
        self.hashes = {k: v for k, v in self.hashes.items() if k in keep}
        self._apply(cands, exp, listing)
        self.settler.commit(listing)

    # ============================================================================== reconciliation
    def _apply(self, cands, exp, listing):
        vf = {v.vpath: v for v in exp.vfiles}
        existing = {a["key"]: a for a in self.db.assets()}
        conv = [c for c in cands if c.kind in CONVERTIBLE]
        paths = library.assign_paths([(c.key, c.catalog, c.name) for c in conv])
        seen, fresh = set(), []
        for c in cands:
            fp, sig = _fingerprint(c, vf), _signature(c, vf)
            a = existing.get(c.key)
            if a is None:
                fresh.append((c, fp, sig))
                continue
            seen.add(a["id"])
            self._update(a, c, fp, sig, paths.get(c.key, ""), vf)
        vanished = [a for a in existing.values() if a["id"] not in seen]
        by_sig = {}
        for a in vanished:
            if a["status"] not in ("problem", "ignored") and a["sig"]:
                by_sig.setdefault(a["sig"], []).append(a)
        for c, fp, sig in fresh:
            match = by_sig.get(sig)
            if match and c.kind in CONVERTIBLE:
                a = match.pop(0)
                vanished.remove(a)
                self._renamed(a, c, fp, sig, paths.get(c.key, ""), vf)
            else:
                self._create(c, fp, sig, paths.get(c.key, ""), vf)
        for a in vanished:
            self._vanish(a)
        owners = {}
        keyed = {a["key"]: a["id"] for a in self.db.assets()}
        for c in cands:
            aid = keyed.get(c.key)
            for p in c.files:
                v = vf.get(p)
                src = v.src if v else p
                owners.setdefault(src, aid)
        self.db.replace_files(listing, self.hashes, owners)
        self._write_cats()
        self.db.commit()
        self.events.append("changed")

    def _spec(self, c, vf):
        d = c.to_dict()
        d["real"] = {p: vf[p].real for p in set(c.files) | set(c.uses) if p in vf}
        d["qreal"] = {vf[p].qhash: vf[p].real for p in set(c.files) | set(c.uses) if p in vf and vf[p].qhash}
        return json.dumps(d)

    def _row(self, c, fp, sig, rel, vf):
        meta = c.meta
        return {"key": c.key, "type": c.kind if c.kind in CONVERTIBLE else ("ignored" if c.kind == "ignored"
                                                                             else "unknown"),
                "name": c.name, "catalog": c.catalog, "vendor": c.vendor, "license": meta.get("license"),
                "author": meta.get("author"), "source": meta.get("source", c.key),
                "sources": json.dumps(sorted(set(c.files) | set(c.uses))), "fingerprint": fp, "sig": sig,
                "spec": self._spec(c, vf), "problem": c.problem}

    def _create(self, c, fp, sig, rel, vf):
        row = self._row(c, fp, sig, rel, vf)
        row["id"] = str(uuid.uuid4())
        row["version"] = 1
        row["managed"] = rel if c.kind in CONVERTIBLE else ""
        row["status"] = "pending" if c.kind in CONVERTIBLE else ("ignored" if c.kind == "ignored" else "problem")
        self.db.upsert_asset(row)
        self._store_maps_tags(row["id"], c, vf)
        if c.kind in CONVERTIBLE:
            self._queue(row["id"], "convert")

    def _update(self, a, c, fp, sig, rel, vf):
        row = self._row(c, fp, sig, rel, vf)
        aid = a["id"]
        if c.kind not in CONVERTIBLE:
            self._drop_managed(a)
            self.db.cancel_jobs(aid)
            row.update(status="ignored" if c.kind == "ignored" else "problem", managed="")
            self.db.update_asset(aid, **row)
            return
        type_changed = a["type"] != row["type"]
        if a["status"] in ("problem", "ignored") and a["type"] not in CONVERTIBLE:
            type_changed = True
        # files that come back after a delete carry new modified times; same content restores from trash
        returned = a["status"] == "missing" and sig == a["sig"]
        if (fp != a["fingerprint"] and not returned) or type_changed:
            if a["managed"] and a["managed"] != rel:
                self._drop_managed(a)
            row.update(status="pending", managed=rel, version=(a["version"] or 0) + 1, problem=None,
                       missing_since=None)
            self.db.update_asset(aid, **row)
            self._store_maps_tags(aid, c, vf)
            self._queue(aid, "convert")
            return
        if a["status"] == "missing":
            if self._restore_from_trash(a):
                row.update(status="ok", missing_since=None)
            else:
                row.update(status="pending", version=(a["version"] or 0) + 1, missing_since=None)
                self.db.update_asset(aid, **row)
                self._queue(aid, "convert")
                return
        moved = rel != a["managed"] or c.name != a["name"] or c.catalog != a["catalog"]
        self.db.update_asset(aid, **row)
        if moved and a["status"] == "ok":
            self._store_maps_tags(aid, c, vf)
            self._queue(aid, "update", {"from": a["managed"], "to": rel})
            self.db.update_asset(aid, managed=a["managed"], status="ok")
        elif moved:
            self.db.update_asset(aid, managed=rel)

    def _renamed(self, a, c, fp, sig, rel, vf):
        """A known asset showed up under a new path (rename or move): keep its id, user state and preview."""
        aid = a["id"]
        old_spec = json.loads(a["spec"] or "{}")
        old_q = old_spec.get("qreal", {})
        new_q = {vf[p].qhash: vf[p].real for p in set(c.files) | set(c.uses) if p in vf and vf[p].qhash}
        remap = {old_q[q]: new_q[q] for q in old_q if q in new_q and old_q[q] != new_q[q]}
        self._move_overrides(a["key"], c.key)
        row = self._row(c, fp, sig, rel, vf)
        if a["status"] == "missing":
            self._restore_from_trash(a)
            a = self.db.asset(aid) or a
        self.db.update_asset(aid, **row)
        self._store_maps_tags(aid, c, vf)
        if a["status"] in ("ok", "missing") and a["managed"]:
            self.db.update_asset(aid, status="ok", missing_since=None, managed=a["managed"])
            self._queue(aid, "update", {"from": a["managed"], "to": rel, "remap": remap})
        else:
            self.db.cancel_jobs(aid)
            self.db.update_asset(aid, status="pending", managed=rel, version=(a["version"] or 0) + 1)
            self._queue(aid, "convert")

    def _vanish(self, a):
        if a["status"] in ("problem", "ignored") or a["type"] not in CONVERTIBLE:
            self.db.delete_asset(a["id"])
            return
        if a["status"] == "missing":
            return
        self.db.cancel_jobs(a["id"])
        if a["managed"]:
            src = os.path.join(library.library_dir(self.state), *a["managed"].split("/"))
            if os.path.exists(src):
                dst = os.path.join(self.state, "trash", a["id"] + ".blend")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
        self.db.update_asset(a["id"], status="missing", missing_since=self.clock())

    def _restore_from_trash(self, a):
        t = os.path.join(self.state, "trash", a["id"] + ".blend")
        if not os.path.exists(t) or not a["managed"]:
            return False
        dst = os.path.join(library.library_dir(self.state), *a["managed"].split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(t, dst)
        self.events.append("library")
        return True

    def _drop_managed(self, a):
        if a.get("managed"):
            p = os.path.join(library.library_dir(self.state), *a["managed"].split("/"))
            if os.path.exists(p):
                fsutil.remove_file(p)
                self.events.append("library")

    def _move_overrides(self, old_key, new_key):
        ov = self.db.overrides()
        if old_key in ov:
            self.db.set_override(new_key, ov[old_key])
            self.db.set_override(old_key, None)

    def _store_maps_tags(self, aid, c, vf):
        from . import maps as M
        if c.kind == "material":
            rows = []
            for kind, m in c.maps.items():
                v = vf.get(m["path"])
                rows.append((kind, v.real if v else m["path"], M.colorspace(kind), kind == M.GLOSS))
            self.db.set_maps(aid, rows)
        self.db.set_auto_tags(aid, self.auto_tags(c))

    @staticmethod
    def auto_tags(c):
        """Words from file and folder names, vendor, resolution, type and map list."""
        stems = [os.path.splitext(posixpath_basename(p))[0] for p in [c.key]]
        words = names.tag_words(c.name, *stems)
        vendor = (c.vendor or "").lower().replace(" ", "")
        for part in c.catalog.split("/"):  # folder names as written: 'ambientCG' stays one word
            for w in names.tokens(part.lower()):
                if w not in words and w != vendor and w not in names.NOISE and len(w) > 1 and not w.isdigit()                         and w not in ("materials", "models", "hdris", "blends", "unsorted"):
                    words.append(w)
        tags = words[:12]
        if c.vendor:
            tags.append(c.vendor.lower().replace(" ", ""))
        if c.meta.get("res"):
            tags.append(c.meta["res"].lower())
        tags.append({"material": "material", "hdri": "hdri", "model": "model", "blend": "model"}.get(c.kind, c.kind))
        if c.kind == "material":
            tags += sorted(c.maps)
        out = []
        for t in tags:
            if t and t not in out:
                out.append(t)
        return out

    def _write_cats(self):
        cats = [a["catalog"] for a in self.db.assets("status IN ('ok','pending')") if a["catalog"]]
        if library.write_cats(self.state, cats):
            self.events.append("library")

    # ======================================================================================= jobs
    def _queue(self, aid, kind, payload=None):
        self.db.cancel_jobs(aid)
        self.db.add_job(aid, kind, payload)
        self.batch_total += 1

    def can_run_jobs(self):
        return self.online and not self.paused

    def next_job(self):
        row = self.db.con.execute("SELECT * FROM jobs WHERE state='waiting' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        row = dict(row)
        a = self.db.asset(row["asset_id"]) if row["asset_id"] else None
        if row["asset_id"] and a is None:
            self.db.update_job(row["id"], state="cancelled")
            self.db.commit()
            return self.next_job()
        self.db.update_job(row["id"], state="running", attempts=(row["attempts"] or 0) + 1, started=self.clock())
        self.db.commit()
        try:
            return self.build_job(row, a)
        except Exception as e:  # a broken spec must not stop the queue
            self.on_job_done({"job": row["id"], "asset_id": row["asset_id"], "kind": row["kind"]},
                             {"ok": False, "error": "Could not prepare job: %s" % e})
            return self.next_job()

    def build_job(self, row, a):
        payload = json.loads(row["payload"] or "{}")
        jobs_dir = os.path.join(self.state, "jobs")
        base = {"job": row["id"], "kind": row["kind"], "asset_id": row["asset_id"],
                "result": os.path.join(jobs_dir, "job_%s.result.json" % row["id"])}
        if row["kind"] in ("publish", "listing"):
            base.update(payload)
            return base
        spec = json.loads(a["spec"] or "{}")
        real = spec.get("real", {})
        lib = library.library_dir(self.state)
        tags = self.db.tags(a["id"])
        size_mb = sum(os.path.getsize(fsutil.long(p)) for p in real.values() if os.path.exists(fsutil.long(p)))
        size_mb /= 1024.0 * 1024.0
        desc = "Source: %s" % a["source"]
        if a["vendor"]:
            desc += "\nVendor: %s" % a["vendor"]
        base.update({
            "version": a["version"], "asset_type": a["type"], "name": a["name"], "catalog": a["catalog"],
            "catalog_id": names.catalog_id(a["catalog"]) if a["catalog"] else "",
            "out_blend": os.path.join(jobs_dir, "job_%s.blend" % row["id"]),
            "out_preview": os.path.join(self.state, "previews", a["id"] + ".png"),
            "tags": tags,
            "meta": {"author": a["author"] or "", "license": a["license"] or "", "description": desc,
                     "copyright": ""},
            "prefs": self.settings.worker_prefs(), "pack": bool(self.cfg.pack),
            "timeout": float(self.settings.timeout) + 0.5 * size_mb,
        })
        if row["kind"] == "update":
            base["blend"] = os.path.join(lib, *payload["from"].split("/"))
            base["remap"] = payload.get("remap", {})
            base["timeout"] = 60.0 + 0.1 * size_mb
            return base
        key = spec.get("key", a["key"])
        base.update({
            "key_real": real.get(key, ""),
            "images": sorted(real[p] for p in real if os.path.splitext(p)[1].lower() in
                             (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".hdr", ".tga", ".bmp", ".webp")),
            "maps": {k: {"path": real.get(m["path"], m["path"]), "dx": m.get("dx", False)}
                     for k, m in spec.get("maps", {}).items()},
            "size_m": spec.get("meta", {}).get("size_m"),
            "vendor_preview": real.get(spec.get("meta", {}).get("preview", ""), None),
            "lods": [real[p] for p in spec.get("lods", []) if p in real],
            "materials": spec.get("meta", {}).get("materials", []),
            "visible_blend": a["type"] == "blend" and not _from_zip(spec, key),
        })
        return base

    def on_job_done(self, job, result):
        jid = job["job"]
        for p in (os.path.join(self.state, "jobs", "job_%s.json" % jid), job.get("result")):
            if p and os.path.exists(p):
                fsutil.remove_file(p)
        row = self.db.job(jid)
        kind = job.get("kind")
        if kind in ("publish", "listing"):
            self._publish_done(job, result)
            return
        a = self.db.asset(job.get("asset_id")) if job.get("asset_id") else None
        self.batch_done += 1
        stale = a is None or (row and row["state"] == "cancelled") or (
            kind == "convert" and a["version"] != job.get("version"))
        tmp = job.get("out_blend")
        if stale:
            if tmp and os.path.exists(tmp):
                fsutil.remove_file(tmp)
            self.db.update_job(jid, state="stale", finished=self.clock())
            self.db.commit()
            return
        if result.get("ok"):
            self._accept(a, job, result)
            self.db.update_job(jid, state="done", finished=self.clock(), error=None)
        elif result.get("crash") and row and (row["attempts"] or 0) < 2:
            self.db.update_job(jid, state="waiting", error=result.get("error"))
        else:
            if tmp and os.path.exists(tmp):
                fsutil.remove_file(tmp)
            self.db.update_asset(a["id"], status="problem", problem=result.get("error") or "Conversion failed",
                                 warnings=json.dumps(result.get("warnings", [])))
            self.db.update_job(jid, state="failed", finished=self.clock(), error=result.get("error"))
            self.events.append("status")
        self.db.commit()

    def _accept(self, a, job, result):
        lib = library.library_dir(self.state)
        info = result.get("info", {})
        fields = {"status": "ok", "problem": None, "warnings": json.dumps(result.get("warnings", [])),
                  "info": json.dumps(info)}
        if result.get("no_file"):
            self._drop_managed(a)
            fields["managed"] = ""
        else:
            rel = (job.get("kind") == "update" and json.loads(self.db.job(job["job"])["payload"] or "{}").get("to")) \
                or a["managed"]
            dst = os.path.join(lib, *rel.split("/"))
            if not os.path.exists(job["out_blend"]):
                self.db.update_asset(a["id"], status="problem", problem="The worker reported success but wrote no file")
                return
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(job["out_blend"], dst)
            if job.get("kind") == "update":
                old = job.get("blend")
                if old and os.path.exists(old) and fsutil.norm(old) != fsutil.norm(dst):
                    fsutil.remove_file(old)
            fields["managed"] = rel
        if result.get("preview"):
            fields["preview"] = result["preview"]
        for t in result.get("extra_tags", []):
            self.db.con.execute("INSERT OR IGNORE INTO tags VALUES (?,?,'auto')", (a["id"], t))
        self.db.update_asset(a["id"], **fields)
        self._write_cats()
        self.events.append("library")

    # ==================================================================================== commands
    def retry(self, aid):
        a = self.db.asset(aid)
        if a is None:
            return
        if a["type"] in CONVERTIBLE:
            self.reconvert(aid)
        else:
            src = a["source"]
            self.hashes = {k: v for k, v in self.hashes.items() if k[0] != src}
            self.force = True

    def reconvert(self, aid):
        a = self.db.asset(aid)
        if a is None or a["type"] not in CONVERTIBLE:
            return
        self.db.update_asset(aid, status="pending", version=(a["version"] or 0) + 1, problem=None)
        self._queue(aid, "convert")
        self.db.commit()

    def set_override(self, aid, value):
        """Override detection: 'material' (Treat as Texture Set), 'model', 'hdri', 'ignore' or None (automatic)."""
        a = self.db.asset(aid)
        if a is None:
            return
        spec = json.loads(a["spec"] or "{}")
        files = spec.get("files", [a["key"]])
        if value == "material":
            for p in files:
                self.db.set_override(p, "material")
        else:
            self.db.set_override(a["key"], value)
        self.db.commit()
        self.force = True

    def clear_override(self, path):
        self.db.set_override(path, None)
        self.db.commit()
        self.force = True

    def add_tag(self, aid, tag):
        tag = tag.strip().lower()
        if not tag:
            return
        self.db.add_user_tag(aid, tag)
        self._retag(aid)

    def remove_tag(self, aid, tag):
        self.db.remove_tag(aid, tag)
        self._retag(aid)

    def _retag(self, aid):
        a = self.db.asset(aid)
        if a and a["status"] == "ok" and a["managed"]:
            self._queue(aid, "update", {"from": a["managed"], "to": a["managed"]})
        self.db.commit()

    def missing(self):
        return self.db.assets("status='missing'")

    def confirm_removals(self, ids=None):
        for a in self.missing():
            if ids is None or a["id"] in ids:
                self._purge(a)
        self.db.commit()
        self._write_cats()
        self.db.commit()

    def _purge(self, a):
        for p in (os.path.join(self.state, "trash", a["id"] + ".blend"),
                  os.path.join(self.state, "previews", a["id"] + ".png")):
            if os.path.exists(p):
                fsutil.remove_file(p)
        self.db.delete_asset(a["id"])

    def _purge_expired(self):
        if self.settings.delete_mode != "AUTO":
            return
        now = self.clock()
        old = [a for a in self.missing() if a["missing_since"] and now - a["missing_since"] > WEEK]
        if old:
            for a in old:
                self._purge(a)
            self.db.commit()

    def rebuild(self):
        """Delete the managed library and reconvert everything; user edits stay in the index."""
        for sub in ("library", "previews", "trash", "jobs"):
            p = os.path.join(self.state, sub)
            if os.path.isdir(p):
                fsutil.remove_tree(p)
            os.makedirs(p, exist_ok=True)
        self.db.con.execute("UPDATE jobs SET state='cancelled' WHERE state IN ('waiting','running')")
        for a in self.db.assets():
            if a["status"] == "missing":
                self.db.delete_asset(a["id"])
            elif a["type"] in CONVERTIBLE:
                self.db.update_asset(a["id"], status="pending", version=(a["version"] or 0) + 1, problem=None)
                self._queue(a["id"], "convert")
        self._write_cats()
        self.db.commit()
        self.events.append("library")

    def clear_cache(self):
        """Remove extracted zips whose zip is gone."""
        live = {self.hashes[k] for k in self.hashes if k[0].lower().endswith(".zip")}
        keep = indexer.nested_hashes(self.state, live)
        removed = 0
        for d in indexer.unused_cache_dirs(self.state, keep):
            fsutil.remove_tree(d)
            removed += 1
        return removed

    def disk_usage(self):
        self.disk = {"drop": sum(s for s, _m in self.listing.values()),
                     "cache": fsutil.dir_size(os.path.join(self.state, "cache")),
                     "library": fsutil.dir_size(os.path.join(self.state, "library")),
                     "previews": fsutil.dir_size(os.path.join(self.state, "previews"))}
        return self.disk

    # ================================================================================ inbox filing
    def _autofile(self, cands, listing):
        """The one exception to the read-only rule: move finished Inbox items into <Type>/<Vendor or Unsorted>/, logged with undo."""
        entries = {}
        for c in cands:
            parts = c.key.split("/")
            if len(parts) >= 2 and parts[0].lower() == "inbox":
                entries.setdefault(parts[0] + "/" + parts[1], []).append(c)
        if not entries:
            return None
        unsettled = [p for p, v in self.listing.items() if listing.get(p) != v]  # still arriving
        moves = []
        for entry, cs in sorted(entries.items()):
            if any(c.kind not in CONVERTIBLE for c in cs):
                continue
            cats = {c.catalog for c in cs}
            if len(cats) != 1:
                continue
            if any(p == entry or p.startswith(entry + "/") for p in unsettled):
                continue
            leaf = entry.split("/", 1)[1]
            dst = cats.pop() + "/" + leaf
            n = 2
            while os.path.exists(os.path.join(self.root, *dst.split("/"))) or any(d == dst for _s, d in moves):
                stem, ext = os.path.splitext(leaf) if "." in leaf else (leaf, "")
                dst = "%s/%s (%d)%s" % (dst.rsplit("/", 1)[0], stem, n, ext)
                n += 1
            moves.append((entry, dst))
        if not moves:
            return None
        batch = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        new_listing = dict(listing)
        log = os.path.join(self.state, "logs", "inbox_moves.log")
        done = []
        for src, dst in moves:
            a, b = os.path.join(self.root, *src.split("/")), os.path.join(self.root, *dst.split("/"))
            try:
                os.makedirs(os.path.dirname(b), exist_ok=True)
                os.rename(a, b)
            except OSError:
                continue
            done.append((src, dst))
            self.db.log_move(batch, src, dst)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("%s\t%s\t%s -> %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), batch, src, dst))
        if not done:
            return None
        self.db.commit()

        def ren(d):
            out = {}
            for p, v in d.items():
                for src, dst in done:
                    if p == src or p.startswith(src + "/"):
                        p = dst + p[len(src):]
                        break
                out[p] = v
            return out
        new_listing = ren(new_listing)
        self.listing = ren(self.listing)
        if self.settler.prev is not None:
            self.settler.prev = ren(self.settler.prev)
        self.hashes = {(ren({k[0]: 0}).popitem()[0], k[1], k[2]): v for k, v in self.hashes.items()}
        self.events.append("moved")
        return new_listing

    def undo_inbox(self):
        batch, rows = self.db.last_move_batch()
        if not batch:
            return 0
        n = 0
        for r in reversed(rows):
            a, b = os.path.join(self.root, *r["dst"].split("/")), os.path.join(self.root, *r["src"].split("/"))
            if os.path.exists(a) and not os.path.exists(b):
                os.makedirs(os.path.dirname(b), exist_ok=True)
                os.rename(a, b)
                n += 1
        self.db.mark_batch_undone(batch)
        self.db.commit()
        with open(os.path.join(self.state, "logs", "inbox_moves.log"), "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\tundone (%d moves back)\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), batch, n))
        return n

    # ===================================================================================== publish
    def publish(self, blender, dest=None):
        """Packed copies of every asset in dest (default <state>/publish), then Blender's listing."""
        dest = dest or os.path.join(self.state, "publish")
        lib = library.library_dir(self.state)
        blends = [os.path.join(lib, *a["managed"].split("/")) for a in self.db.assets("status='ok'") if a["managed"]]
        self.db.add_job(None, "publish", {"library": lib, "dest": dest, "blends": blends, "timeout": 60 + 10 * len(blends),
                                          "blender": blender})
        self.db.commit()
        self.publish_state = "packing %d assets" % len(blends)

    def _publish_done(self, job, result):
        jid = job["job"]
        if job["kind"] == "publish" and result.get("ok"):
            self.db.update_job(jid, state="done", finished=self.clock())
            argv = [job["blender"], "--factory-startup", "-c", "asset_listing", "generate", job["dest"]]
            self.db.add_job(None, "listing", {"argv": argv, "dest": job["dest"], "timeout": 300})
            self.publish_state = "writing listing"
        elif job["kind"] == "listing":
            ok = os.path.exists(os.path.join(job["dest"], "_v1", "asset-index.json"))
            self.db.update_job(jid, state="done" if ok else "failed", finished=self.clock(),
                               error=None if ok else result.get("error"))
            self.publish_state = ("published to %s" % job["dest"]) if ok else ("listing failed: %s" %
                                                                               (result.get("log", "")[-300:]))
        else:
            self.db.update_job(jid, state="failed", finished=self.clock(), error=result.get("error"))
            self.publish_state = "publish failed: %s" % result.get("error")
        self.db.commit()
        self.events.append("status")

    # ====================================================================================== status
    def snapshot(self, running=()):
        q = self.db.con.execute
        counts = {r[0]: r[1] for r in q("SELECT status, COUNT(*) FROM assets GROUP BY status")}
        waiting = q("SELECT COUNT(*) FROM jobs WHERE state='waiting'").fetchone()[0]
        problems = [dict(id=a["id"], name=a["name"], source=a["source"], problem=a["problem"], type=a["type"])
                    for a in self.db.assets("status='problem'")]
        queue = []
        for r in running:
            queue.append({"name": r.job.get("name") or r.job.get("kind"), "state": "converting",
                          "seconds": int(time.time() - r.started)})
        for j in q("SELECT jobs.kind, assets.name FROM jobs LEFT JOIN assets ON assets.id = jobs.asset_id "
                   "WHERE jobs.state='waiting' ORDER BY jobs.id LIMIT 8"):
            queue.append({"name": j[1] or j[0], "state": "waiting", "seconds": 0})
        active = len(running) + waiting
        if active == 0:
            self.batch_total = self.batch_done = 0
        return {"root": self.root, "state": self.state, "label": self.cfg.label, "online": self.online,
                "paused": self.paused, "assets": counts.get("ok", 0), "pending": counts.get("pending", 0),
                "problems": problems, "missing": [dict(id=a["id"], name=a["name"]) for a in self.missing()],
                "converting": len(running), "waiting": waiting, "queue": queue,
                "batch_total": max(self.batch_total, active), "batch_done": self.batch_done,
                "settling": self.pending_files, "poll_ms": round(self.last_poll_ms, 1), "disk": dict(self.disk),
                "publish": self.publish_state}

    def close(self):
        self.db.close()


def _from_zip(spec, key):
    src = spec.get("meta", {}).get("source", key)
    return src != key


def posixpath_basename(p):
    return p.rsplit("/", 1)[-1]

