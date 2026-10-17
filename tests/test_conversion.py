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
    assert len(ok) == 21
    from perf_log import record
    record("Fixture library: 21 assets converted, 4 workers", dt, "s", None, "merged fixtures, cold workers")


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
    assert found == 20


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
    assert not bsdf.inputs["Specular IOR Level"].links  # no specular map: Blender's default 4%


def test_true_displacement(conv, clean):
    """Height drives real displacement in Cycles: the normal map keeps the fine detail (no extra bump), the
    height texture is smooth (cubic) and, with no vendor value, the scale follows the texture's real size."""
    m = _mat(conv, "Bricks076C")
    nodes = m.node_tree.nodes
    disp = nodes["RI Displacement"]
    assert _feeds(nodes["RI Output"].inputs["Displacement"]) == [disp]
    assert _feeds(disp.inputs["Height"])[0].name == "RI height" and nodes["RI height"].interpolation == "Cubic"
    assert disp.space == "OBJECT" and disp.inputs["Midlevel"].default_value == pytest.approx(0.5)
    assert disp.inputs["Scale"].default_value == pytest.approx(0.03)  # ambientCG's own .blend says 0: unknown
    assert m.displacement_method == "DISPLACEMENT"
    assert _feeds(nodes["RI BSDF"].inputs["Normal"])[0].name == "RI Normal Map" and "RI Bump" not in nodes
    # no normal map: displacement plus bump for detail finer than the dicing
    rock = _mat(conv, "rock_wall_08")
    assert rock.displacement_method == "DISPLACEMENT"  # has a DX normal
    tiles = _mat(conv, "grès_tiles")
    assert tiles.displacement_method == "BUMP" and "RI Displacement" not in tiles.node_tree.nodes  # no height map


def test_megascans_extras(conv, clean):
    """Specular in F0 form becomes Specular IOR Level (x 12.5, sRGB), translucency makes a thin wall with
    subsurface, fuzz drives sheen, cavity joins the set unwired, and a 1:2 atlas keeps square pixels."""
    m = _mat(conv, "vjkqfg")
    nt = m.node_tree
    bsdf = nt.nodes["RI BSDF"]
    lvl = _feeds(bsdf.inputs["Specular IOR Level"])[0]
    assert lvl.name == "RI F0 to IOR Level" and lvl.inputs[1].default_value == pytest.approx(12.5)
    assert nt.nodes["RI specular"].image.colorspace_settings.name == "sRGB"
    assert bsdf.inputs["Thin Wall"].default_value is True
    assert _feeds(bsdf.inputs["Subsurface Weight"])[0].name == "RI translucency"
    assert _feeds(bsdf.inputs["Sheen Weight"])[0].name == "RI sheen"
    assert _feeds(bsdf.inputs["Alpha"])[0].name == "RI opacity"
    assert _feeds(bsdf.inputs["Roughness"])[0].name == "RI rough"  # roughness wins over gloss
    assert "RI cavity" not in nt.nodes and "RI ao" in nt.nodes and not nt.nodes["RI ao"].outputs[0].links
    assert tuple(nt.nodes["RI Mapping"].inputs["Scale"].default_value) == pytest.approx((1.0, 0.5, 1.0))


def _write_png(path, w, h, v):
    img = bpy.data.images.new("tmp", w, h)
    img.pixels.foreach_set([v, v, v, 1.0] * (w * h))
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def test_vendor_displacement_and_bump_mode(clean):
    """A Poly Haven style .blend beside the maps gives the vendor's own scale and midlevel; nothing it loads
    stays behind. Bump-only mode keeps the old gentle bump. Spec maps in level form (0.5) wire straight in."""
    from conftest import fresh_dir
    from realindex.worker import build_material as bm
    d = fresh_dir("vendor_disp")
    tex = os.path.join(d, "textures")
    os.makedirs(tex)
    maps = {}
    for kind, stem, v in (("color", "brown_planks_09_diff_1k", 0.4), ("height", "brown_planks_09_disp_1k", 0.5),
                          ("normal", "brown_planks_09_nor_gl_1k", 0.5), ("specular", "brown_planks_09_spec_1k", 0.5)):
        p = os.path.join(tex, stem + ".png")
        _write_png(p, 8, 8, v)
        maps[kind] = {"path": p, "dx": False}
    vendor = bpy.data.materials.new("brown_planks_09")
    nt = vendor.node_tree
    img = nt.nodes.new("ShaderNodeTexImage")
    img.image = bpy.data.images.load(maps["height"]["path"])
    dn = nt.nodes.new("ShaderNodeDisplacement")
    dn.inputs["Scale"].default_value, dn.inputs["Midlevel"].default_value = 0.018, 0.57
    nt.links.new(img.outputs["Color"], dn.inputs["Height"])
    vendor.use_fake_user = True
    bpy.data.libraries.write(os.path.join(d, "brown_planks_09_1k.blend"), {vendor}, path_remap="ABSOLUTE")
    bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)

    mat, wired = bm.build("planks", maps, 1.0)
    disp = mat.node_tree.nodes["RI Displacement"]
    assert disp.inputs["Scale"].default_value == pytest.approx(0.018)
    assert disp.inputs["Midlevel"].default_value == pytest.approx(0.57)
    assert mat["ri_vendor_displacement"] == pytest.approx(0.018)
    assert [m.name for m in bpy.data.materials] == ["planks"] and not list(bpy.data.libraries)
    assert len(bpy.data.images) == 4
    spec = _feeds(mat.node_tree.nodes["RI BSDF"].inputs["Specular IOR Level"])[0]
    assert spec.name == "RI specular"  # level form: straight in

    flat, _w = bm.build("planks_bump", maps, 1.0, displacement="BUMP")
    assert flat.displacement_method == "BUMP" and "RI Displacement" not in flat.node_tree.nodes
    assert _feeds(flat.node_tree.nodes["RI BSDF"].inputs["Normal"])[0].name == "RI Bump"


def test_setup_displacement(clean):
    from realindex.worker import build_material as bm
    me = bpy.data.meshes.new("plane")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    ob = bpy.data.objects.new("plane", me)
    bpy.context.scene.collection.objects.link(ob)
    assert bm.setup_displacement([ob]) == 1
    mod = ob.modifiers[-1]
    assert mod.type == "SUBSURF" and mod.use_adaptive_subdivision and mod.subdivision_type == "SIMPLE"
    assert bm.setup_displacement([ob]) == 0 and len(ob.modifiers) == 1  # idempotent


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
    assert len(eng.db.assets("status='ok'")) == 21
    for a in eng.db.assets("status='ok'"):
        if a["managed"]:
            assert os.path.exists(cu.managed_path(state, a))
