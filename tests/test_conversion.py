"""Conversion, native visibility and catalog stability (Blender suite, real background workers).

Every converted asset is marked, in the right catalog, has a preview, correct colour spaces, inverted gloss,
split ARM and correct normal handedness; models have their origin at the bottom centre and a sane size;
listing the managed library through bpy.data.libraries.load(..., assets_only=True) finds every asset; and
Rebuild Library gives identical catalog ids and asset paths."""

import json
import os

import bpy
import pytest

import convert_util as cu
from fixtures.layout import EXPECT
from realindex.core import library, names


@pytest.fixture(scope="module")
def conv():
    return cu.converted()


@pytest.fixture
def clean():
    bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
    yield


def assets(eng):
    return {a["name"]: a for a in eng.db.assets()}


def test_everything_converts(conv):
    eng, pool, root, state, dt = conv
    a = assets(eng)
    ok = sorted(n for n, x in a.items() if x["status"] == "ok")
    probs = sorted(n for n, x in a.items() if x["status"] == "problem")
    assert probs == ["decal_grunge", "old_lamp", "rocks_pack"], [(n, a[n]["problem"]) for n in probs]
    assert len(ok) == 20
    from perf_log import record
    record("Fixture library: 20 assets converted, 4 workers", dt, "s", None, "merged fixtures, cold workers")


def test_native_visibility(conv, clean):
    eng, pool, root, state, dt = conv
    found = 0
    for a in eng.db.assets("status='ok'"):
        if not a["managed"]:
            assert a["type"] == "blend" and a["name"] == "AssetPack"  # listed natively in RealIndex Files
            with bpy.data.libraries.load(os.path.join(root, *a["source"].split("/")), assets_only=True) as (s, _d):
                assert list(s.materials) == ["Gold"] and list(s.objects) == ["Statue"]
            continue
        p = cu.managed_path(state, a)
        with bpy.data.libraries.load(p, assets_only=True) as (src, _dst):
            n = sum(len(getattr(src, attr)) for attr in ("materials", "worlds", "collections", "objects"))
        assert n >= 1, a["name"]
        found += 1
    assert found == 19


def test_catalogs_and_metadata(conv, clean):
    eng, pool, root, state, dt = conv
    cats = library.read_cats(state)
    for a in eng.db.assets("status='ok'"):
        if not a["managed"]:
            continue
        attr = {"material": "materials", "hdri": "worlds"}.get(a["type"])
        info = json.loads(a["info"] or "{}")
        if attr is None:
            attr = "objects" if info.get("asset_kind") == "OBJECT" else "collections"
        idb = cu.append(cu.managed_path(state, a), attr, a["name"])
        assert idb is not None and idb.asset_data is not None, a["name"]
        cid = idb.asset_data.catalog_id
        assert cid == names.catalog_id(a["catalog"]) and cats[cid] == a["catalog"], a["name"]
        assert idb.preview is not None and tuple(idb.preview.image_size) != (0, 0), a["name"]
        assert os.path.exists(os.path.join(state, "previews", a["id"] + ".png"))
        tags = set(idb.asset_data.tags.keys())
        assert {"material": "material", "hdri": "hdri"}.get(a["type"], "model") in tags
        if a["type"] == "model":
            assert idb.asset_data.use_preferred_import_method and idb.asset_data.preferred_import_method == "APPEND"
            assert tags & {"small", "medium", "large"}
        if a["name"] == "Wooden Chair":
            assert idb.asset_data.license == "CC-BY-4.0" and idb.asset_data.author == "Jane Maker"
        if a["vendor"] in ("ambientCG", "Poly Haven"):
            assert idb.asset_data.license == "CC0"
        assert a["source"] in idb.asset_data.description


def _mat(conv, name):
    eng, pool, root, state, dt = conv
    a = assets(eng)[name]
    return cu.append(cu.managed_path(state, a), "materials", name)


def _feeds(socket):
    return [l.from_node for l in socket.links]


def test_colour_spaces_and_maps(conv, clean):
    m = _mat(conv, "Bricks076C")
    nodes = {n.name: n for n in m.node_tree.nodes}
    assert nodes["RI color"].image.colorspace_settings.name == "sRGB"
    for k in ("normal", "rough", "height", "ao"):
        assert nodes["RI " + k].image.colorspace_settings.name == "Non-Color", k
    assert "NormalGL" in nodes["RI normal"].image.filepath  # OpenGL preferred over DirectX
    assert "RI DX Flip Green" not in nodes
    bsdf = nodes["RI BSDF"]
    assert _feeds(bsdf.inputs["Base Color"])[0].name == "RI color"
    assert _feeds(bsdf.inputs["Normal"])[0].name == "RI Bump"


def test_gloss_inverted(conv, clean):
    m = _mat(conv, "OldMetal")
    bsdf = m.node_tree.nodes["RI BSDF"]
    inv = _feeds(bsdf.inputs["Roughness"])[0]
    assert inv.bl_idname == "ShaderNodeInvert" and _feeds(inv.inputs["Color"])[0].name == "RI gloss"
    assert _feeds(bsdf.inputs["Metallic"])[0].name == "RI metal"


def test_arm_split_and_dx_normal(conv, clean):
    m = _mat(conv, "rock_wall_08")
    nt = m.node_tree
    bsdf = nt.nodes["RI BSDF"]
    r = bsdf.inputs["Roughness"].links[0]
    mm = bsdf.inputs["Metallic"].links[0]
    assert r.from_node.name == "RI ARM Split" and r.from_socket.name == "Green"
    assert mm.from_node.name == "RI ARM Split" and mm.from_socket.name == "Blue"
    assert nt.nodes["RI ARM Split"].inputs["Color"].links[0].from_node.name == "RI arm"
    nm = nt.nodes["RI Normal Map"]
    comb = nm.inputs["Color"].links[0].from_node
    assert comb.name == "RI DX Combine"
    flip = comb.inputs["Green"].links[0].from_node
    assert flip.name == "RI DX Flip Green" and flip.operation == "SUBTRACT" and flip.inputs[0].default_value == 1.0


def test_real_world_scale(conv, clean):
    m = _mat(conv, "grès_tiles")
    assert tuple(m.node_tree.nodes["RI Mapping"].inputs["Scale"].default_value) == pytest.approx((0.5, 0.5, 0.5))
    assert m["ri_size_m"] == 2.0
    from realindex.worker import build_material
    build_material.set_mapping(m, "BOX")
    assert m.node_tree.nodes["RI color"].projection == "BOX"
    assert m.node_tree.nodes["RI Mapping"].inputs["Vector"].links[0].from_socket.name == "Object"


def test_hdri_world(conv, clean):
    eng, pool, root, state, dt = conv
    a = assets(eng)["meadow"]
    w = cu.append(cu.managed_path(state, a), "worlds", "meadow")
    nt = w.node_tree
    env = nt.nodes["RI Environment"]
    assert env.image is not None and env.image.filepath.endswith("meadow_2k.exr")
    assert nt.nodes["RI Rotation"].bl_idname == "ShaderNodeMapping"
    assert nt.nodes["RI Background"].inputs["Strength"].default_value == 1.0
    out = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputWorld")
    assert out.inputs["Surface"].links[0].from_node.name == "RI Background"


def test_models_normalised(conv, clean):
    eng, pool, root, state, dt = conv
    by_key = {a["key"]: a for a in eng.db.assets()}
    for a in eng.db.assets("status='ok' AND type='model'"):
        info = json.loads(a["info"])
        bx, by, bz = info["bottom"]
        assert abs(bx) < 1e-3 and abs(by) < 1e-3 and abs(bz) < 1e-3, (a["name"], info)  # origin at bottom centre
        d = info["dims"]
        assert 0.05 < max(d) < 5.0, (a["name"], d)  # sane size
    for key, exp in EXPECT.items():
        info = json.loads(by_key[key]["info"])
        assert info["dims"] == pytest.approx(exp["dims"], abs=2e-3), key


def test_marketplace_textures_all_matched(conv, clean):
    eng, pool, root, state, dt = conv
    for name, want in (("SM_Barrel", {"T_BarrelWood_D.png", "T_BarrelWood_N.png", "T_BarrelWood_R.png"}),
                       ("SM_Crate", {"T_CrateOak_BaseColor.png", "T_CrateOak_Normal.png"})):
        a = assets(eng)[name]
        assert json.loads(a["warnings"]) == [], name
        bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
        ob = cu.append(cu.managed_path(state, a), "objects", name)
        imgs = {os.path.basename(i.filepath) for i in bpy.data.images if i.filepath}
        assert imgs == want, (name, imgs)
        assert all(os.path.exists(bpy.path.abspath(i.filepath)) for i in bpy.data.images if i.filepath)
        assert ob.data.materials[0].node_tree.nodes  # the FBX material kept, now textured
        assert json.loads(a["info"])["lods"] == [name + "_LOD1.fbx"]


def test_sketchfab_and_obj_textures(conv, clean):
    eng, pool, root, state, dt = conv
    for name in ("Wooden Chair", "table", "Lamp", "Vase"):
        a = assets(eng)[name]
        bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
        info = json.loads(a["info"])
        attr = "objects" if info.get("asset_kind") == "OBJECT" else "collections"
        cu.append(cu.managed_path(state, a), attr, name)
        imgs = [i for i in bpy.data.images if i.source == "FILE"]
        assert imgs, name
        for i in imgs:
            assert i.packed_file is not None or os.path.exists(bpy.path.abspath(i.filepath)), (name, i.filepath)


def test_unmarked_blend_collections(conv, clean):
    eng, pool, root, state, dt = conv
    a = assets(eng)["Props"]
    with bpy.data.libraries.load(cu.managed_path(state, a), assets_only=True) as (src, _dst):
        assert sorted(src.collections) == ["Barrel", "Crate"]
    assert json.loads(a["info"])["converted"] == ["Crate", "Barrel"] or sorted(json.loads(a["info"])["converted"]) == [
        "Barrel", "Crate"]


def test_library_small(conv):
    eng, pool, root, state, dt = conv
    from realindex.core import fsutil
    lib = fsutil.dir_size(os.path.join(state, "library"))
    drop = sum(s for s, _m in eng.listing.values())
    from perf_log import record
    # informational: fixture textures are 32-256 px, so geometry dominates; the 5% budget is measured on
    # real downloads by scripts/perf.py
    record("Managed library size vs drop folder (fixtures, informational)", 100.0 * lib / drop, "%", None,
           "tiny fixture textures; budget measured on real downloads")


def test_rebuild_is_stable(conv):
    """Catalog stability: identical catalog ids and asset paths after Rebuild Library."""
    eng, pool, root, state, dt = conv
    cats_before = open(os.path.join(state, "library", library.CATS_FILE), encoding="utf-8").read()
    paths_before = {a["key"]: a["managed"] for a in eng.db.assets()}
    ids_before = {a["key"]: a["id"] for a in eng.db.assets()}
    eng.rebuild()
    assert not os.path.exists(os.path.join(state, "library", "Materials"))
    cu.run(eng, pool)
    assert open(os.path.join(state, "library", library.CATS_FILE), encoding="utf-8").read() == cats_before
    assert {a["key"]: a["managed"] for a in eng.db.assets()} == paths_before
    assert {a["key"]: a["id"] for a in eng.db.assets()} == ids_before
    assert len(eng.db.assets("status='ok'")) == 20
    for a in eng.db.assets("status='ok'"):
        if a["managed"]:
            assert os.path.exists(cu.managed_path(state, a))
