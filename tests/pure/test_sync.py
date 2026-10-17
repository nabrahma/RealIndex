"""Sync events and idempotency, on the real engine with an in-process fake worker
pool: add, change, rename, move, delete (trash, restore, confirm), half-written files, zip replaced,
offline root, Inbox auto-filing with undo, user edits surviving reconversion, and safety checksums."""

import hashlib
import json
import os
import shutil
import time
import zipfile

import pytest

from realindex.core import engine as E
from realindex.core import fsutil


class FakePool:
    """Runs queued jobs synchronously: writes a stand-in .blend and reports success."""

    def __init__(self):
        self.ran = []

    def run(self, eng):
        n = 0
        while True:
            job = eng.next_job()
            if job is None:
                return n
            self.ran.append(job)
            if job.get("out_blend"):
                with open(job["out_blend"], "wb") as fh:
                    fh.write(b"BLENDER-fake " + job["name"].encode())
            eng.on_job_done(job, {"ok": True, "info": {}, "warnings": []})
            n += 1


def write(root, rel, data=None):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data if data is not None else os.urandom(64))
    return p


def texture_set(root, folder, name, extra=b""):
    for m in ("Color", "NormalGL", "Roughness"):
        write(root, "%s/%s_%s.png" % (folder, name, m), os.urandom(64) + extra)


def settle(eng, pool, polls=2):
    for _ in range(polls):
        eng.tick()
    pool.run(eng)


def make(root_state, **kw):
    root, state = root_state
    s = E.Settings(inbox_autofile=kw.pop("inbox", False), **kw)
    return E.Engine(E.RootConfig(root, state), s), FakePool(), root, state


def assets(eng):
    return {a["name"]: a for a in eng.db.assets()}


def managed(state, a):
    return os.path.join(state, "library", *a["managed"].split("/"))


def test_new_files_wait_two_polls(root_state):
    eng, pool, root, state = make(root_state)
    eng.tick()
    texture_set(root, "Materials/Wood", "Oak")
    eng.tick()
    assert not eng.db.assets()                  # seen once: not yet
    eng.tick()
    pool.run(eng)
    a = assets(eng)["Oak"]
    assert a["status"] == "ok" and os.path.exists(managed(state, a))
    assert a["catalog"] == "Materials/Wood"
    cats = open(os.path.join(state, "library", "blender_assets.cats.txt"), encoding="utf-8").read()
    assert ":Materials/Wood:Materials-Wood" in cats


def test_half_written_file_is_not_grabbed(root_state):
    eng, pool, root, state = make(root_state)
    eng.tick()
    p = write(root, "Models/big.fbx", b"x")
    for i in range(5):  # still growing on every poll
        with open(p, "ab") as fh:
            fh.write(b"y" * 100)
        eng.tick()
        assert not eng.db.assets()
    eng.tick()
    eng.tick()
    assert [a["name"] for a in eng.db.assets()] == ["big"]


def test_idempotent_second_scan_queues_nothing(root_state):
    eng, pool, root, state = make(root_state)
    texture_set(root, "Materials", "Oak")
    write(root, "Models/Chairs/chair.fbx")
    settle(eng, pool, 3)
    n = len(pool.ran)
    assert n == 2
    for _ in range(4):
        eng.tick()
    assert pool.run(eng) == 0
    # a restart (new engine on the same state) reconciles without new jobs
    eng.close()
    eng2 = E.Engine(E.RootConfig(root, state), eng.settings)
    for _ in range(3):
        eng2.tick()
    assert pool.run(eng2) == 0


def test_change_reconverts_and_keeps_user_edits(root_state):
    eng, pool, root, state = make(root_state)
    texture_set(root, "Materials", "Oak")
    settle(eng, pool, 3)
    a = assets(eng)["Oak"]
    eng.add_tag(a["id"], "favourite-wood")
    eng.db.set_favourite(a["id"], True)
    eng.db.commit()
    pool.run(eng)
    time.sleep(0.01)
    write(root, "Materials/Oak_Roughness.png", os.urandom(80))  # replace one texture
    settle(eng, pool)
    b = assets(eng)["Oak"]
    assert b["id"] == a["id"] and b["version"] == a["version"] + 1
    assert pool.ran[-1]["kind"] == "convert"
    assert "favourite-wood" in eng.db.tags(a["id"]) and eng.db.user_state(a["id"])["favourite"] == 1


def test_rename_and_move_do_not_reconvert(root_state):
    eng, pool, root, state = make(root_state)
    write(root, "Models/Chairs/chair.fbx", b"chair-bytes")
    settle(eng, pool, 3)
    a = assets(eng)["chair"]
    old_path = managed(state, a)
    os.rename(os.path.join(root, "Models", "Chairs", "chair.fbx"), os.path.join(root, "Models", "Chairs", "armchair.fbx"))
    settle(eng, pool)
    b = assets(eng)["armchair"]
    assert b["id"] == a["id"]
    assert pool.ran[-1]["kind"] == "update" and pool.ran[-1]["remap"]
    assert os.path.exists(managed(state, b)) and not os.path.exists(old_path)
    os.makedirs(os.path.join(root, "Models", "Seating"))
    os.rename(os.path.join(root, "Models", "Chairs", "armchair.fbx"), os.path.join(root, "Models", "Seating", "armchair.fbx"))
    settle(eng, pool)
    c = assets(eng)["armchair"]
    assert c["id"] == a["id"] and c["catalog"] == "Models/Seating"
    assert [j["kind"] for j in pool.ran] == ["convert", "update", "update"]


def test_delete_trash_restore_and_confirm(root_state):
    eng, pool, root, state = make(root_state)
    texture_set(root, "Materials", "Oak")
    texture_set(root, "Materials", "Pine")
    settle(eng, pool, 3)
    oak = assets(eng)["Oak"]
    keep = {}
    for m in ("Color", "NormalGL", "Roughness"):
        p = os.path.join(root, "Materials", "Oak_%s.png" % m)
        keep[p] = open(p, "rb").read()
        os.remove(p)
    settle(eng, pool)
    a = assets(eng)["Oak"]
    assert a["status"] == "missing" and not os.path.exists(managed(state, a))
    assert os.path.exists(os.path.join(state, "trash", a["id"] + ".blend"))
    assert [m["name"] for m in eng.missing()] == ["Oak"]
    for p, data in keep.items():  # the files come back before the user confirmed
        with open(p, "wb") as fh:
            fh.write(data)
    n = len(pool.ran)
    settle(eng, pool)
    a = assets(eng)["Oak"]
    assert a["status"] == "ok" and os.path.exists(managed(state, a)) and len(pool.ran) == n  # no reconvert
    for p in keep:
        os.remove(p)
    settle(eng, pool)
    eng.confirm_removals()
    assert "Oak" not in assets(eng) and "Pine" in assets(eng)
    assert not os.path.exists(os.path.join(state, "trash", oak["id"] + ".blend"))


def test_auto_removal_after_seven_days(root_state):
    now = [1000.0]
    root, state = root_state
    eng = E.Engine(E.RootConfig(root, state), E.Settings(delete_mode="AUTO", inbox_autofile=False), clock=lambda: now[0])
    pool = FakePool()
    texture_set(root, "Materials", "Oak")
    settle(eng, pool, 3)
    shutil.rmtree(os.path.join(root, "Materials"))
    settle(eng, pool)
    assert assets(eng)["Oak"]["status"] == "missing"
    now[0] += 6 * 86400
    eng.tick()
    assert "Oak" in assets(eng)
    now[0] += 2 * 86400
    eng.tick()
    assert "Oak" not in assets(eng)


def test_zip_replaced_is_a_change(root_state):
    eng, pool, root, state = make(root_state)
    zp = os.path.join(root, "Materials", "Tiles001_1K-JPG.zip")
    os.makedirs(os.path.dirname(zp))
    with zipfile.ZipFile(zp, "w") as zf:
        for m in ("Color", "NormalGL"):
            zf.writestr("Tiles001_1K-JPG_%s.jpg" % m, os.urandom(50))
    settle(eng, pool, 3)
    a = assets(eng)["Tiles001"]
    assert a["vendor"] == "ambientCG" and a["catalog"] == "Materials"
    time.sleep(0.02)
    with zipfile.ZipFile(zp, "w") as zf:
        for m in ("Color", "NormalGL", "Roughness"):
            zf.writestr("Tiles001_1K-JPG_%s.jpg" % m, os.urandom(50))
    settle(eng, pool)
    b = assets(eng)["Tiles001"]
    assert b["version"] == a["version"] + 1 and pool.ran[-1]["kind"] == "convert"
    assert "rough" in pool.ran[-1]["maps"]


def test_offline_root_keeps_library_and_pauses(root_state):
    eng, pool, root, state = make(root_state)
    texture_set(root, "Materials", "Oak")
    settle(eng, pool, 3)
    a = assets(eng)["Oak"]
    away = root + "_unplugged"
    os.rename(root, away)
    try:
        for _ in range(3):
            eng.tick()
        assert not eng.online and not eng.can_run_jobs()
        assert assets(eng)["Oak"]["status"] == "ok" and os.path.exists(managed(state, a))
        assert eng.snapshot()["online"] is False
    finally:
        os.rename(away, root)
    settle(eng, pool)
    assert eng.online and assets(eng)["Oak"]["status"] == "ok" and pool.run(eng) == 0


def test_inbox_autofile_and_undo(root_state):
    eng, pool, root, state = make(root_state, inbox=True)
    zp = os.path.join(root, "Inbox", "Bricks001_2K-JPG.zip")
    os.makedirs(os.path.dirname(zp))
    with zipfile.ZipFile(zp, "w") as zf:
        for m in ("Color", "NormalGL", "Roughness"):
            zf.writestr("Bricks001_2K-JPG_%s.jpg" % m, os.urandom(50))
    write(root, "Inbox/old_lamp.max")
    settle(eng, pool, 3)
    moved = os.path.join(root, "Materials", "ambientCG", "Bricks001_2K-JPG.zip")
    assert os.path.exists(moved) and not os.path.exists(zp)
    assert os.path.exists(os.path.join(root, "Inbox", "old_lamp.max"))  # problems stay put
    a = assets(eng)["Bricks001"]
    assert a["catalog"] == "Materials/ambientCG" and a["key"].startswith("Materials/ambientCG/")
    assert [j["kind"] for j in pool.ran] == ["convert"]  # filed before converting: no relocation
    log = open(os.path.join(state, "logs", "inbox_moves.log"), encoding="utf-8").read()
    assert "Inbox/Bricks001_2K-JPG.zip -> Materials/ambientCG/Bricks001_2K-JPG.zip" in log
    eng.settings.inbox_autofile = False
    assert eng.undo_inbox() == 1
    assert os.path.exists(zp) and not os.path.exists(moved)
    settle(eng, pool)
    b = assets(eng)["Bricks001"]
    assert b["id"] == a["id"] and pool.ran[-1]["kind"] == "update"  # moved back: tracked by content


def _checksums(root):
    out = {}
    for dp, _d, fs in os.walk(fsutil.long(root, force=True)):
        for f in fs:
            p = os.path.join(dp, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, fsutil.long(root, force=True))] = hashlib.sha1(fh.read()).hexdigest()
    return out


def test_safety_drop_root_untouched(root_state, downloads):
    """Checksums of every file in the drop folder are identical before and after a full run."""
    from fixtures import layout
    root, state = root_state
    layout.merge(root)
    before = _checksums(root)
    eng = E.Engine(E.RootConfig(root, state), E.Settings(inbox_autofile=False))
    pool = FakePool()
    settle(eng, pool, 3)
    eng.rebuild()
    pool.run(eng)
    eng.clear_cache()
    assert _checksums(root) == before
    assert len([a for a in eng.db.assets() if a["status"] == "ok"]) == 20


def test_safety_inbox_moves_only_with_preference(root_state, downloads):
    from fixtures import layout
    root, state = root_state
    layout.merge(root)
    before = _checksums(root)
    eng = E.Engine(E.RootConfig(root, state), E.Settings(inbox_autofile=True))
    pool = FakePool()
    settle(eng, pool, 3)
    after = _checksums(root)
    assert sorted(before.values()) == sorted(after.values())  # same bytes, some moved
    moved = {k for k in before if k not in after}
    assert moved and all(k.startswith("Inbox") for k in moved)
    log = open(os.path.join(state, "logs", "inbox_moves.log"), encoding="utf-8").read()
    for k in {k.split(os.sep)[1] for k in moved}:
        assert k in log


def test_guarded_delete_refuses_outside_state(root_state):
    root, state = root_state
    fsutil.ensure_state_dir(state, root)
    p = write(root, "Materials/x.png")
    with pytest.raises(PermissionError):
        fsutil.remove_file(p)
    with pytest.raises(PermissionError):
        fsutil.remove_tree(root)
    with pytest.raises(PermissionError):
        fsutil.remove_tree(state)  # the state root itself
    assert os.path.exists(p)


def test_overrides_and_problems(root_state):
    eng, pool, root, state = make(root_state)
    write(root, "Materials/decal_grunge.png")
    write(root, "Materials/Lonely_Normal.png")
    write(root, "Models/old.max")
    settle(eng, pool, 3)
    probs = {a["name"]: a for a in eng.db.assets("status='problem'")}
    assert set(probs) == {"decal_grunge", "Lonely", "old"}
    eng.set_override(probs["Lonely"]["id"], "material")  # Treat as Texture Set
    eng.tick()
    pool.run(eng)
    a = assets(eng)
    assert a["Lonely"]["type"] == "material" and a["Lonely"]["status"] == "ok"
    eng.set_override(a["old"]["id"], "ignore")
    eng.tick()
    assert assets(eng)["old"]["status"] == "ignored"
    snap = eng.snapshot()
    assert {p["name"] for p in snap["problems"]} == {"decal_grunge"}


def test_job_specs_are_complete(root_state, downloads):
    from fixtures import layout
    root, state = root_state
    layout.merge(root)
    eng = E.Engine(E.RootConfig(root, state), E.Settings(inbox_autofile=False))
    for _ in range(3):
        eng.tick()
    seen = set()
    while True:
        job = eng.next_job()
        if job is None:
            break
        seen.add(job["asset_type"])
        assert job["catalog_id"] and job["out_blend"] and job["out_preview"]
        assert os.path.exists(fsutil.long(job["key_real"])), job["key_real"]
        if job["asset_type"] == "material":
            assert job["maps"]["color"]["path"] and all(os.path.exists(fsutil.long(m["path"])) for m in job["maps"].values())
        if job["name"] == "Bricks076C":
            assert job["vendor_preview"].endswith("Bricks076C.png") and job["meta"]["license"] == "CC0"
        json.dumps(job)
        eng.on_job_done(job, {"ok": True, "no_file": True})
    assert seen == {"material", "hdri", "model", "blend"}
