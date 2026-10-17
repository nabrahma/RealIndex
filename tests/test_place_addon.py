"""Quick Place (search ranking, placement on a surface, material on selection, world set and restore,
Replace Selected, favourites and recents) and add-on integration (library registration, status line, the UI
timer budget while the service scans)."""

import math
import os
import time

import bpy
import pytest
from mathutils import Vector

import convert_util as cu
from realindex import blender_lib, prefs, runtime
from realindex.core import engine as E


@pytest.fixture
def lib():
    eng, pool, root, state, _dt = cu.converted()
    p = prefs.get()
    p.roots.clear()
    r = p.roots.add()
    r.path, r.library_dir = root, state
    runtime.records(force=True)
    bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
    yield {a["name"]: a for a in eng.db.assets()}, root, state
    p.roots.clear()


def place(rec_id, root, **kw):
    return bpy.ops.realindex.place(asset_id=rec_id, root=root, **kw)


def cube(name, loc=(0, 0, 0), rot=(0, 0, 0), scale=1.0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = (scale, scale, scale)
    return ob


def test_search_ranking(lib):
    from realindex.ui import quick_place as qp
    props = bpy.context.window_manager.realindex
    props.search, props.kind = "brick", "ALL"
    res = qp.results(props)
    assert res[0].data["name"] == "Bricks076C"
    props.search, props.kind = "", "HDRIS"
    assert [r.data["name"] for r in qp.results(props)] == ["meadow"]
    props.kind = "MODELS"
    names = {r.data["name"] for r in qp.results(props)}
    assert {"Wooden Chair", "SM_Barrel", "Crate", "Barrel"} <= names  # blend members are listed one by one
    props.search, props.kind = "", "ALL"


def test_model_on_surface_and_cursor(lib):
    a, root, state = lib
    p = prefs.get()
    p.place_mode, p.place_align, p.place_random = "INSTANCE", True, False
    floor = cube("Floor", (0, 0, 1.0), (math.radians(30), 0, 0), 4.0)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    hit, loc, normal, *_ = bpy.context.scene.ray_cast(dg, Vector((0, 0, 20)), Vector((0, 0, -1)))
    assert hit
    assert place(a["Wooden Chair"]["id"], root, use_hit=True, hit_location=loc, hit_normal=normal) == {"FINISHED"}
    ob = bpy.context.active_object
    assert ob.type == "MESH" and ob.data.name  # single-object asset: a copy of the object
    assert (ob.matrix_world.translation - loc).length < 1e-4
    z = ob.matrix_world.to_3x3() @ Vector((0, 0, 1))
    assert z.angle(normal) < 1e-3  # aligned to the surface normal
    bpy.context.scene.cursor.location = (2, 3, 0)
    place(a["Wooden Chair"]["id"], root)
    ob2 = bpy.context.active_object
    assert tuple(ob2.matrix_world.translation) == pytest.approx((2, 3, 0))
    assert ob2.data == ob.data  # appended once, mesh shared
    place(a["Props"]["id"], root, member="Crate")
    inst = bpy.context.active_object
    assert inst.instance_type == "COLLECTION" and inst.instance_collection.name == "Crate"
    p.place_mode = "REAL"
    place(a["table"]["id"], root)
    t = bpy.context.active_object
    assert t.type == "MESH" and tuple(t.matrix_world.translation) == pytest.approx((2, 3, 0))
    p.place_mode = "INSTANCE"
    del floor


def test_material_on_selection(lib):
    a, root, state = lib
    c1, c2 = cube("A", (0, 0, 0)), cube("B", (3, 0, 0))
    c2.data.materials.append(bpy.data.materials.new("Old1"))
    c2.data.materials.append(bpy.data.materials.new("Old2"))
    for o in (c1, c2):
        o.select_set(True)
    prefs.get().material_slots = "ALL"
    place(a["Bricks076C"]["id"], root)
    m = bpy.data.materials["Bricks076C"]
    assert c1.material_slots[0].material == m and all(s.material == m for s in c2.material_slots)
    n_mats, n_imgs = len(bpy.data.materials), len(bpy.data.images)
    place(a["Bricks076C"]["id"], root)
    assert len(bpy.data.materials) == n_mats and len(bpy.data.images) == n_imgs  # reused, not duplicated
    prefs.get().material_slots = "ACTIVE"
    c2.active_material_index = 1
    c1.select_set(False)
    place(a["OldMetal"]["id"], root)
    assert c2.material_slots[0].material == m and c2.material_slots[1].material.name == "OldMetal"
    prefs.get().material_slots = "ALL"


def test_world_set_and_restore(lib):
    a, root, state = lib
    sc = bpy.context.scene
    before = bpy.data.worlds.new("Before")
    sc.world = before
    place(a["meadow"]["id"], root)
    assert sc.world.name == "meadow" and sc["ri_previous_world"] == "Before"
    assert bpy.ops.realindex.restore_world() == {"FINISHED"}
    assert sc.world == before and "ri_previous_world" not in sc


def test_replace_selected(lib):
    a, root, state = lib
    c1 = cube("P1", (1, 2, 0), (0, 0, 0.5), 1.0)
    c2 = cube("P2", (-3, 1, 0.5), (0.2, 0, 1.0), 2.0)
    mats = [c1.matrix_world.copy(), c2.matrix_world.copy()]
    for o in (c1, c2):
        o.select_set(True)
    place(a["SM_Barrel"]["id"], root, replace=True)
    assert "P1" not in bpy.data.objects and "P2" not in bpy.data.objects
    new = [o for o in bpy.context.scene.objects if o.get("ri_asset_id") == a["SM_Barrel"]["id"]]
    assert all(o.data == new[0].data for o in new)
    assert len(new) == 2
    got = sorted([tuple(round(v, 4) for v in o.matrix_world.translation) for o in new])
    assert got == sorted([tuple(round(v, 4) for v in m.translation) for m in mats])


def test_favourites_and_recent(lib):
    a, root, state = lib
    rid = a["Tiles012"]["id"]
    bpy.ops.realindex.favourite(asset_id=rid, root=root)
    rec = next(r for r in runtime.records(force=True) if r["id"] == rid)
    assert rec["favourite"]
    c = cube("X")
    c.select_set(True)
    place(rid, root)
    rec = next(r for r in runtime.records(force=True) if r["id"] == rid)
    assert rec["last_used"]
    from realindex.ui import quick_place as qp
    props = bpy.context.window_manager.realindex
    props.search, props.recent, props.favourites = "", True, True
    assert [r.data["name"] for r in qp.results(props)][0] == "Tiles012"
    props.recent = props.favourites = False
    bpy.ops.realindex.favourite(asset_id=rid, root=root)


def test_library_registration(root_state):
    root, state = root_state
    other = os.path.join(os.path.dirname(root), "Packs")
    os.makedirs(other, exist_ok=True)
    roots = [E.RootConfig(root, state, label="RealIndex"),
             E.RootConfig(other, state + "_2", label="RealIndex (Packs)")]
    blender_lib.ensure_libraries(roots)
    libs = {l.name: l for l in bpy.context.preferences.filepaths.asset_libraries}
    assert os.path.normcase(libs["RealIndex"].path) == os.path.normcase(os.path.join(state, "library"))
    assert libs["RealIndex"].import_method == "APPEND"
    assert os.path.normcase(libs["RealIndex Files"].path) == os.path.normcase(root)
    assert "RealIndex (Packs)" in libs and "RealIndex Files (Packs)" in libs
    blender_lib.ensure_libraries(roots[:1])
    names = [l.name for l in bpy.context.preferences.filepaths.asset_libraries]
    assert "RealIndex (Packs)" not in names and "RealIndex" in names
    blender_lib.remove_libraries()
    assert not [n for n in (l.name for l in bpy.context.preferences.filepaths.asset_libraries) if n.startswith("RealIndex")]


def test_status_line(monkeypatch):
    snap = {"r": {"batch_total": 3, "batch_done": 0, "converting": 1, "waiting": 2, "problems": [], "assets": 10,
                  "online": True, "paused": False}}
    monkeypatch.setattr(runtime, "snapshot", lambda: snap)
    assert runtime.status_line() == "RealIndex: 3 new, converting 1 of 3"
    snap["r"].update(converting=0, waiting=0, batch_total=0, assets=1284, problems=[{}, {}])
    assert runtime.status_line() == "RealIndex: 1,284 assets, 2 problems"


def test_ui_timer_budget_while_scanning(root_state):
    """UI freeze caused by RealIndex: never above 50 ms per timer tick, measured while the service thread polls
    a 10,000-file root; and startup reconciliation of that root (nothing changed) under 3 s."""
    root, state = root_state
    for d in range(200):
        dd = os.path.join(root, "Materials", "set_%03d" % d)
        os.makedirs(dd)
        for i in range(50):
            open(os.path.join(dd, "set_%03d_%02d_x.dat" % (d, i)), "wb").close()  # 10,000 files, no assets
    from realindex.core import engine as EE
    eng = EE.Engine(EE.RootConfig(root, state), EE.Settings())
    eng.sync_now()
    eng.close()
    t = time.perf_counter()
    eng = EE.Engine(EE.RootConfig(root, state), EE.Settings())
    eng.tick()
    eng.tick()
    recon = time.perf_counter() - t
    assert not eng.db.jobs("waiting")
    eng.close()
    p = prefs.get()
    p.roots.clear()
    r = p.roots.add()
    r.path, r.library_dir = root, state
    p.poll_interval = 0.5
    runtime.start()
    try:
        worst, ticks = 0.0, 0
        t0 = time.time()
        while time.time() - t0 < 8.0:
            a = time.perf_counter()
            runtime._tick()
            worst = max(worst, time.perf_counter() - a)
            ticks += 1
            time.sleep(0.05)
        snap = runtime.snapshot()
        assert snap and list(snap.values())[0]["poll_ms"] > 0
    finally:
        runtime.stop()
        p.roots.clear()
        p.poll_interval = 2.0
    from perf_log import record
    record("UI timer tick while polling 10,000 files (worst of %d)" % ticks, worst * 1000, "ms", 50)
    record("Startup reconciliation, 10,000 files, nothing changed", recon, "s", 3)
    assert worst < 0.05 and recon < 3.0
