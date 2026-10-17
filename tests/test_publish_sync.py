"""Publish, live rename/move/retag through real 'update' workers, packed roots and the read-only API."""

import json
import os
import shutil

import bpy
import pytest

import convert_util as cu
from realindex.core import engine as E
from realindex.core import jobs


@pytest.fixture
def clean():
    bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
    yield


def test_publish_remote_library(clean):
    eng, pool, root, state, _dt = cu.converted("converted_publish", ["acg_zip", "ph_folder", "ph_hdri", "obj_mtl",
                                                                     "sketchfab_zip", "blend_unmarked"])
    dest = os.path.join(state, "publish")
    eng.publish(bpy.app.binary_path, dest)
    cu.run(eng, pool)
    assert "published to" in eng.publish_state, eng.publish_state
    index = os.path.join(dest, "_v1", "asset-index.json")
    assert os.path.exists(index) and os.path.exists(os.path.join(dest, "blender_assets.cats.txt"))
    data = json.load(open(index, encoding="utf-8"))
    assert data["asset_count"] >= 6 and data["catalogs"]
    blends = []
    for dp, _d, fs in os.walk(dest):
        blends += [os.path.join(dp, f) for f in fs if f.endswith(".blend")]
    assert len(blends) == len([a for a in eng.db.assets("status='ok'") if a["managed"]])
    for b in blends:  # self-contained: no external file paths
        bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
        with bpy.data.libraries.load(b, link=False) as (src, dst):
            dst.images = list(src.images)
        for img in dst.images:
            if img is not None and img.source == "FILE":
                assert img.packed_file is not None, (b, img.filepath)


def test_rename_move_and_retag_with_real_workers(root_state, downloads, clean):
    from fixtures import layout
    root, state = root_state
    layout.copy_fixture("obj_mtl", root)
    layout.copy_fixture("ph_folder", root)
    eng = E.Engine(E.RootConfig(root, state), E.Settings(inbox_autofile=False))
    pool = jobs.Pool(bpy.app.binary_path, 2)
    cu.run(eng, pool)
    table = {a["name"]: a for a in eng.db.assets()}["table"]
    assert table["status"] == "ok"
    os.rename(os.path.join(root, "Models", "Furniture", "Table"), os.path.join(root, "Models", "Furniture", "Desk"))
    shutil.move(os.path.join(root, "Materials", "Rock"), os.path.join(root, "Materials", "Stone"))
    cu.run(eng, pool)
    kinds = [j["kind"] for j in eng.db.jobs() if j["state"] == "done"]
    assert kinds.count("convert") == 2 and kinds.count("update") == 2  # nothing reconverted
    a = {a["name"]: a for a in eng.db.assets()}
    assert a["table"]["id"] == table["id"] and a["table"]["status"] == "ok"
    assert a["rock_wall_08"]["catalog"] == "Materials/Stone"
    bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
    cu.append(cu.managed_path(state, a["table"]), "objects", "table")
    paths = [i.filepath for i in bpy.data.images if i.filepath]
    assert paths and all(os.sep + "Desk" + os.sep in p and os.path.exists(p) for p in paths), paths
    bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
    m = cu.append(cu.managed_path(state, a["rock_wall_08"]), "materials", "rock_wall_08")
    assert m.asset_data.catalog_id == __import__("realindex.core.names", fromlist=["x"]).catalog_id("Materials/Stone")
    assert all(os.sep + "Stone" + os.sep in i.filepath for i in bpy.data.images if i.filepath)
    eng.add_tag(a["rock_wall_08"]["id"], "cliff")
    eng.remove_tag(a["rock_wall_08"]["id"], "rock")
    cu.run(eng, pool)
    bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
    m = cu.append(cu.managed_path(state, a["rock_wall_08"]), "materials", "rock_wall_08")
    tags = set(m.asset_data.tags.keys())
    assert "cliff" in tags and "rock" not in tags


def test_packed_root(root_state, downloads, clean):
    from fixtures import layout
    root, state = root_state
    layout.copy_fixture("unicode", root)
    eng = E.Engine(E.RootConfig(root, state, pack=True), E.Settings(inbox_autofile=False))
    pool = jobs.Pool(bpy.app.binary_path, 1)
    cu.run(eng, pool)
    a = eng.db.assets("status='ok'")[0]
    cu.append(cu.managed_path(state, a), "materials", a["name"])
    imgs = [i for i in bpy.data.images if i.source == "FILE"]
    assert imgs and all(i.packed_file is not None for i in imgs)


def test_api(clean):
    eng, pool, root, state, _dt = cu.converted()
    from realindex.core import api
    states = [(root, state)]
    sets = api.load(states, ["material"])
    assert len(sets) == len(eng.db.assets("status='ok' AND type='material'"))
    acg = next(s for s in sets if s["name"] == "Bricks076C")
    assert acg["maps"]["color"].endswith("Bricks076C_2K-JPG_Color.jpg") and os.path.exists(acg["maps"]["color"])
    assert acg["folder"] == os.path.dirname(acg["maps"]["color"]) and acg["size_m"] == 1.0
    assert acg["preview"] and os.path.exists(acg["preview"]) and "ambientcg" in acg["tags"]
    assert acg["license"] == "CC0" and acg["kind"] == "texture_set"
    tiles = next(s for s in sets if s["name"] == "grès_tiles")
    assert tiles["size_m"] == 2.0
    assert {m["name"] for m in api.load(states, ["hdri"])} == {"meadow"}
    models = api.load(states, ["model", "blend"])
    assert {"Wooden Chair", "SM_Barrel", "Props", "AssetPack"} <= {m["name"] for m in models}
    hits = api.find(states, "brick")
    assert hits and hits[0]["name"] == "Bricks076C"
    assert api.find(states, "chair")[0]["name"] == "Wooden Chair"
    # the public module answers from the configured roots
    import realindex.prefs as P
    p = P.get()
    p.roots.clear()
    r = p.roots.add()
    r.path, r.library_dir = root, state
    try:
        import realindex.api as ri
        assert len(ri.texture_sets()) == len(sets) and ri.hdris()[0]["name"] == "meadow"
        assert ri.find("barrel")[0]["name"] == "SM_Barrel"
    finally:
        p.roots.clear()
