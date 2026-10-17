"""Generate the "downloads folder" fixtures from code.

    $BLENDER_BIN --background --factory-startup --python tests/fixtures/make_downloads.py -- OUT [--only id,id]

Each fixture is a small drop root of its own: OUT/<fixture id>/<path where it lives in a drop root>. Merge
them all (fixtures.layout.merge) for the full mixed root. Models are exported from Blender primitives with
known sizes, so conversion results can be checked exactly (see EXPECT at the bottom).
"""

import math
import os
import shutil
import sys
import zipfile

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from layout import EXPECT, LONG_DIR  # noqa: E402,F401


# ------------------------------------------------------------------------------------------------ images

def _pattern(w, h, rgb, kind="bricks", seed=0):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    if kind == "bricks":
        row = (y // max(4, h // 8)).astype(int)
        off = (row % 2) * (w // 8)
        mortar = ((y % max(4, h // 8)) < 1) | (((x + off) % max(8, w // 4)) < 1)
        v = np.where(mortar, 0.35, 1.0)
    elif kind == "noise":
        v = 0.6 + 0.4 * rng.random((h, w))
    else:
        v = np.ones((h, w), np.float32)
    px = np.zeros((h, w, 4), np.float32)
    for i in range(3):
        px[..., i] = np.clip(v * rgb[i], 0, 1)
    px[..., 3] = 1.0
    return px


def _lp(p):
    return "\\\\?\\" + os.path.abspath(p) if os.name == "nt" and len(os.path.abspath(p)) > 200 else p


def save_image(path, w, h, rgb, kind="bricks", fmt=None, seed=0, px=None):
    final = path
    if os.name == "nt" and len(os.path.abspath(path)) > 200:  # Blender's writer wants short paths
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), os.path.basename(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ext = os.path.splitext(path)[1].lower()
    fmt = fmt or {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".exr": "OPEN_EXR", ".tga": "TARGA",
                  ".tif": "TIFF", ".hdr": "HDR"}[ext]
    img = bpy.data.images.new("fx", w, h, alpha=True, float_buffer=fmt in ("OPEN_EXR", "HDR"))
    if px is None:
        px = _pattern(w, h, rgb, kind, seed)
    img.pixels.foreach_set(px.ravel())
    img.filepath_raw = path
    img.file_format = fmt
    img.save()
    bpy.data.images.remove(img)
    if final != path:
        os.makedirs(_lp(os.path.dirname(final)), exist_ok=True)
        shutil.copyfile(path, _lp(final))
    return final


def hdri_pixels(w, h):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    sky = np.clip(1.0 - y / h, 0, 1)  # bright at the top row (Blender images are bottom-up)
    px = np.zeros((h, w, 4), np.float32)
    px[..., 0] = 0.3 + 0.6 * (1 - sky)
    px[..., 1] = 0.5 + 0.4 * (1 - sky)
    px[..., 2] = 0.8 + 0.4 * (1 - sky)
    sun = ((x - w * 0.3) ** 2 + (y - h * 0.8) ** 2) < (h * 0.04) ** 2
    px[sun, :3] = 40.0
    px[y < h * 0.45, :3] *= 0.25  # ground
    px[..., 3] = 1
    return px


# ------------------------------------------------------------------------------------------------ scenes

def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def cube(name, size, loc=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return ob


def cylinder(name, r, depth, loc=(0, 0, 0), verts=24):
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=depth, location=loc, vertices=verts)
    ob = bpy.context.active_object
    ob.name = name
    return ob


def material(name, images=None):
    """A Principled material; images: {'color'|'normal'|'rough': path} wired as usual."""
    m = bpy.data.materials.new(name)
    nt = m.node_tree
    bsdf = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    for kind, path in (images or {}).items():
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = bpy.data.images.load(path)
        if kind == "color":
            nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
        elif kind == "rough":
            t.image.colorspace_settings.name = "Non-Color"
            nt.links.new(t.outputs["Color"], bsdf.inputs["Roughness"])
        elif kind == "normal":
            t.image.colorspace_settings.name = "Non-Color"
            nm = nt.nodes.new("ShaderNodeNormalMap")
            nt.links.new(t.outputs["Color"], nm.inputs["Color"])
            nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    return m


def select_only(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def zip_dir(src, dst, arc_prefix=""):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _d, files in os.walk(src):
            for f in sorted(files):
                full = os.path.join(dirpath, f)
                zf.write(full, arc_prefix + os.path.relpath(full, src).replace("\\", "/"))


# --------------------------------------------------------------------------------------------- fixtures

def fx_acg_zip(out, tmp):
    stage = os.path.join(tmp, "acg")
    p = "Bricks076C_2K-JPG"
    save_image(os.path.join(stage, p + "_Color.jpg"), 64, 64, (0.75, 0.25, 0.15))
    save_image(os.path.join(stage, p + "_NormalGL.jpg"), 64, 64, (0.5, 0.5, 1.0), "flat")
    save_image(os.path.join(stage, p + "_NormalDX.jpg"), 64, 64, (0.5, 0.5, 1.0), "flat")
    save_image(os.path.join(stage, p + "_Roughness.jpg"), 64, 64, (0.8, 0.8, 0.8), "noise")
    save_image(os.path.join(stage, p + "_Displacement.jpg"), 64, 64, (0.5, 0.5, 0.5), "bricks")
    save_image(os.path.join(stage, p + "_AmbientOcclusion.jpg"), 64, 64, (0.9, 0.9, 0.9), "bricks")
    save_image(os.path.join(stage, "Bricks076C.png"), 256, 256, (0.9, 0.4, 0.2), "bricks")
    with open(os.path.join(stage, p + ".usdc"), "w") as fh:
        fh.write("#usda 1.0\n# material sidecar\n")
    with open(os.path.join(stage, p + ".mtlx"), "w") as fh:
        fh.write("<?xml version=\"1.0\"?><materialx/>\n")
    with open(os.path.join(stage, p + ".tres"), "w") as fh:
        fh.write("[gd_resource type=\"StandardMaterial3D\"]\n")
    # ambientCG zips (2026) also carry a .blend with the material: a sidecar, not a model
    fresh()
    bpy.data.materials.new(p).use_fake_user = True
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(stage, p + ".blend"), compress=False)
    zip_dir(stage, os.path.join(out, "Inbox", p + ".zip"))


def fx_ph_folder(out, tmp):
    d = os.path.join(out, "Materials", "Rock", "rock_wall_08_2k", "textures")
    save_image(os.path.join(d, "rock_wall_08_diff_2k.jpg"), 64, 64, (0.45, 0.42, 0.38), "noise", seed=1)
    # ARM: AO 1.0 (R), roughness 0.5 (G), metal 0.0 (B)
    save_image(os.path.join(d, "rock_wall_08_arm_2k.jpg"), 64, 64, (1.0, 0.5, 0.0), "flat")
    save_image(os.path.join(d, "rock_wall_08_nor_dx_2k.png"), 64, 64, (0.5, 0.5, 1.0), "flat")
    save_image(os.path.join(d, "rock_wall_08_disp_2k.png"), 64, 64, (0.5, 0.5, 0.5), "noise", seed=2)


def fx_ph_hdri(out, tmp):
    save_image(os.path.join(out, "HDRIs", "meadow_2k.exr"), 512, 256, None, px=hdri_pixels(512, 256))


def fx_multi_res(out, tmp):
    d = os.path.join(out, "Materials", "Planks")
    for res, n in (("1k", 32), ("2k", 64)):
        save_image(os.path.join(d, "planks_diff_%s.jpg" % res), n, n, (0.6, 0.45, 0.3), "noise")
        save_image(os.path.join(d, "planks_nor_gl_%s.png" % res), n, n, (0.5, 0.5, 1.0), "flat")
        save_image(os.path.join(d, "planks_rough_%s.jpg" % res), n, n, (0.7, 0.7, 0.7), "noise")


def fx_gloss_set(out, tmp):
    d = os.path.join(out, "Materials", "OldMetal")
    save_image(os.path.join(d, "OldMetal_Diffuse.png"), 64, 64, (0.4, 0.4, 0.42), "noise")
    save_image(os.path.join(d, "OldMetal_Glossiness.png"), 64, 64, (0.8, 0.8, 0.8), "flat")  # rough 0.2
    save_image(os.path.join(d, "OldMetal_Normal.png"), 64, 64, (0.5, 0.5, 1.0), "flat")
    save_image(os.path.join(d, "OldMetal_Metallic.png"), 64, 64, (1.0, 1.0, 1.0), "flat")


def fx_megascans_set(out, tmp):
    """Megascans-style surface: spec/gloss extras (Specular as sRGB F0, Cavity, Fuzz, Translucency) beside the
    usual maps, non-square (1:2) like many foliage atlases."""
    d = os.path.join(out, "Materials", "Plants", "Fern_vjkqfg")
    p = "vjkqfg_2K_"
    save_image(os.path.join(d, p + "Albedo.jpg"), 32, 64, (0.2, 0.45, 0.15), "noise")
    save_image(os.path.join(d, p + "AO.jpg"), 32, 64, (0.9, 0.9, 0.9), "flat")
    save_image(os.path.join(d, p + "Cavity.jpg"), 32, 64, (0.8, 0.8, 0.8), "flat")
    save_image(os.path.join(d, p + "Displacement.jpg"), 32, 64, (0.5, 0.5, 0.5), "noise")
    save_image(os.path.join(d, p + "Gloss.jpg"), 32, 64, (0.4, 0.4, 0.4), "flat")
    save_image(os.path.join(d, p + "Roughness.jpg"), 32, 64, (0.6, 0.6, 0.6), "flat")
    save_image(os.path.join(d, p + "Normal.jpg"), 32, 64, (0.5, 0.5, 1.0), "flat")
    save_image(os.path.join(d, p + "Specular.jpg"), 32, 64, (0.22, 0.22, 0.22), "flat")  # F0 4% as sRGB
    save_image(os.path.join(d, p + "Translucency.jpg"), 32, 64, (0.5, 0.6, 0.2), "flat")
    save_image(os.path.join(d, p + "Fuzz.jpg"), 32, 64, (0.3, 0.3, 0.3), "flat")
    save_image(os.path.join(d, p + "Opacity.jpg"), 32, 64, (1.0, 1.0, 1.0), "flat")


def fx_unicode(out, tmp):
    d = os.path.join(out, "Materials", "Ünïcode Földer", "Grès Tiles 2m")
    save_image(os.path.join(d, "grès_tiles_Color.png"), 64, 64, (0.85, 0.82, 0.75), "bricks")
    save_image(os.path.join(d, "grès_tiles_Normal.png"), 64, 64, (0.5, 0.5, 1.0), "flat")
    save_image(os.path.join(d, "grès_tiles_Roughness.png"), 64, 64, (0.6, 0.6, 0.6), "noise")


def fx_long_path(out, tmp):
    d = os.path.join(out, *LONG_DIR.split("/"))
    save_image(os.path.join(d, "Concrete_Long_Path_Material_Color.png"), 32, 32, (0.6, 0.6, 0.6), "noise")
    save_image(os.path.join(d, "Concrete_Long_Path_Material_Roughness.png"), 32, 32, (0.7, 0.7, 0.7), "noise")


def fx_sketchfab_zip(out, tmp):
    fresh()
    stage = os.path.join(tmp, "sketchfab")
    tex = os.path.join(tmp, "sketchfab_src")
    col = save_image(os.path.join(tex, "Wood_baseColor.png"), 64, 64, (0.55, 0.35, 0.2), "noise")
    nrm = save_image(os.path.join(tex, "Wood_normal.png"), 64, 64, (0.5, 0.5, 1.0), "flat")
    mat = material("Wood", {"color": col, "normal": nrm})
    parts = [cube("Seat", (0.45, 0.45, 0.05), (0, 0, 0.45))]
    for i, (x, y) in enumerate(((-0.2, -0.2), (0.2, -0.2), (-0.2, 0.2), (0.2, 0.2))):
        parts.append(cube("Leg%d" % i, (0.04, 0.04, 0.45), (x, y, 0.225)))
    parts.append(cube("Back", (0.45, 0.04, 0.45), (0, 0.2, 0.7)))
    select_only(parts)
    bpy.ops.object.join()
    chair = bpy.context.active_object
    chair.name = "Chair"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project()
    bpy.ops.object.mode_set(mode="OBJECT")
    chair.data.materials.append(mat)
    os.makedirs(stage, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=os.path.join(stage, "scene.gltf"), export_format="GLTF_SEPARATE",
                              export_texture_dir="textures", use_selection=False)
    with open(os.path.join(stage, "license.txt"), "w", encoding="utf-8") as fh:
        fh.write("Model Information:\n* title:\tWooden Chair\n* source:\thttps://sketchfab.com/3d-models/"
                 "wooden-chair-0000\n* author:\tJane Maker (https://sketchfab.com/janemaker)\n\n"
                 "Model License:\n* license type:\tCC-BY-4.0 (http://creativecommons.org/licenses/by/4.0/)\n"
                 "* requirements:\tAuthor must be credited. Commercial use is allowed.\n")
    zip_dir(stage, os.path.join(out, "Models", "Furniture", "wooden_chair.zip"))


def fx_market_fbx(out, tmp):
    pack = os.path.join(out, "Models", "MedievalPack")
    src = os.path.join(tmp, "market_src")
    # textures the artist used (names the FBX remembers); never shipped under these names
    barrel_src = {"color": save_image(os.path.join(src, "barrel_albedo.png"), 32, 32, (0.5, 0.3, 0.15)),
                  "normal": save_image(os.path.join(src, "barrel_normal.png"), 32, 32, (0.5, 0.5, 1), "flat"),
                  "rough": save_image(os.path.join(src, "barrel_rough.png"), 32, 32, (0.6, 0.6, 0.6), "noise")}
    crate_src = {"color": save_image(os.path.join(src, "crate_col.png"), 32, 32, (0.6, 0.45, 0.25)),
                 "normal": save_image(os.path.join(src, "crate_nrm.png"), 32, 32, (0.5, 0.5, 1), "flat")}
    os.makedirs(os.path.join(pack, "FBX"), exist_ok=True)
    for lod, verts in ((0, 32), (1, 12)):
        fresh()
        ob = cylinder("SM_Barrel_LOD%d" % lod, 0.3, 0.9, (0, 0, 0.45), verts)
        ob.data.materials.append(material("M_Barrel", barrel_src))
        select_only([ob])
        bpy.ops.export_scene.fbx(filepath=os.path.join(pack, "FBX", "SM_Barrel_LOD%d.fbx" % lod),
                                 use_selection=True, path_mode="ABSOLUTE")
        fresh()
        ob = cube("SM_Crate_LOD%d" % lod, (0.6, 0.6, 0.6), (0, 0, 0.3))
        if lod:
            ob.modifiers.new("d", "DECIMATE")
        ob.data.materials.append(material("M_Crate", crate_src))
        select_only([ob])
        bpy.ops.export_scene.fbx(filepath=os.path.join(pack, "FBX", "SM_Crate_LOD%d.fbx" % lod),
                                 use_selection=True, path_mode="ABSOLUTE")
    t = os.path.join(pack, "Textures")
    save_image(os.path.join(t, "T_BarrelWood_D.png"), 32, 32, (0.5, 0.3, 0.15))
    save_image(os.path.join(t, "T_BarrelWood_N.png"), 32, 32, (0.5, 0.5, 1), "flat")
    save_image(os.path.join(t, "T_BarrelWood_R.png"), 32, 32, (0.6, 0.6, 0.6), "noise")
    save_image(os.path.join(t, "T_CrateOak_BaseColor.png"), 32, 32, (0.6, 0.45, 0.25))
    save_image(os.path.join(t, "T_CrateOak_Normal.png"), 32, 32, (0.5, 0.5, 1), "flat")
    shutil.rmtree(src, ignore_errors=True)  # the FBX now points at files that do not exist
    with open(os.path.join(pack, "readme.txt"), "w") as fh:
        fh.write("Medieval props pack. Unreal-style names.\n")


def fx_obj_mtl(out, tmp):
    fresh()
    d = os.path.join(out, "Models", "Furniture", "Table")
    tex = save_image(os.path.join(d, "table_diffuse.png"), 32, 32, (0.5, 0.35, 0.2), "noise")
    top = cube("Top", (1.2, 0.7, 0.05), (0, 0, 0.72))
    legs = [cube("L%d" % i, (0.05, 0.05, 0.7), (x, y, 0.35)) for i, (x, y) in
            enumerate(((-0.55, -0.3), (0.55, -0.3), (-0.55, 0.3), (0.55, 0.3)))]
    select_only([top] + legs)
    bpy.ops.object.join()
    ob = bpy.context.active_object
    ob.name = "Table"
    ob.data.materials.append(material("TableWood", {"color": tex}))
    bpy.ops.wm.obj_export(filepath=os.path.join(d, "table.obj"), export_selected_objects=True,
                          path_mode="RELATIVE")


def fx_usdz(out, tmp):
    fresh()
    tex = save_image(os.path.join(tmp, "lamp_src", "lampshade_color.png"), 32, 32, (0.9, 0.85, 0.6))
    base = cylinder("Base", 0.12, 0.05, (0, 0, 0.025))
    pole = cylinder("Pole", 0.015, 0.5, (0, 0, 0.3))
    shade = cylinder("Shade", 0.18, 0.2, (0, 0, 0.6))
    shade.data.materials.append(material("Shade", {"color": tex}))
    os.makedirs(os.path.join(out, "Models", "Lights"), exist_ok=True)
    select_only([base, pole, shade])
    bpy.ops.wm.usd_export(filepath=os.path.join(out, "Models", "Lights", "Lamp.usdz"), selected_objects_only=True)


def fx_stl_ply(out, tmp):
    fresh()
    d = os.path.join(out, "Models", "Parts")
    os.makedirs(d, exist_ok=True)
    ob = cube("Bracket", (0.8, 0.1, 0.3), (0, 0, 0.15))
    select_only([ob])
    # exported 100x too large (a millimetre file read as metres): 80 m, normalised back to 0.8 m
    bpy.ops.wm.stl_export(filepath=os.path.join(d, "Bracket.stl"), export_selected_objects=True, global_scale=100)
    fresh()
    ob = cylinder("Gear", 0.25, 0.05, (0, 0, 0.025), 16)
    select_only([ob])
    # exported 100x too small: 5 mm, normalised back to 0.5 m
    bpy.ops.wm.ply_export(filepath=os.path.join(d, "Gear.ply"), export_selected_objects=True, global_scale=0.01)


def fx_abc(out, tmp):
    fresh()
    ob = cube("Spinner", (0.3, 0.3, 0.3), (0, 0, 0.15))
    ob.rotation_euler = (0, 0, 0)
    ob.keyframe_insert("rotation_euler", frame=1)
    ob.rotation_euler = (0, 0, math.pi)
    ob.keyframe_insert("rotation_euler", frame=24)
    d = os.path.join(out, "Models", "Anim")
    os.makedirs(d, exist_ok=True)
    bpy.ops.wm.alembic_export(filepath=os.path.join(d, "Spinner.abc"), start=1, end=24, selected=False)


def fx_glb(out, tmp):
    fresh()
    tex = save_image(os.path.join(tmp, "vase_src", "vase_color.png"), 32, 32, (0.2, 0.4, 0.7), "noise")
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.15, location=(0, 0, 0.15))
    ob = bpy.context.active_object
    ob.name = "Vase"
    ob.scale = (1, 1, 1.6)
    ob.data.materials.append(material("Glaze", {"color": tex}))
    d = os.path.join(out, "Models", "Props")
    os.makedirs(d, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=os.path.join(d, "Vase.glb"), export_format="GLB")


def fx_blend_marked(out, tmp):
    fresh()
    gold = bpy.data.materials.new("Gold")
    bsdf = next(n for n in gold.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (1.0, 0.77, 0.34, 1)
    bsdf.inputs["Metallic"].default_value = 1.0
    gold.asset_mark()
    ob = cube("Statue", (0.3, 0.3, 1.0), (0, 0, 0.5))
    ob.data.materials.append(gold)
    ob.asset_mark()
    d = os.path.join(out, "Blends")
    os.makedirs(d, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(d, "AssetPack.blend"), compress=False)


def fx_blend_unmarked(out, tmp):
    fresh()
    sc = bpy.context.scene
    for name, maker in (("Crate", lambda: cube("CrateBox", (0.5, 0.5, 0.5), (2, 0, 0.25))),
                        ("Barrel", lambda: cylinder("BarrelBody", 0.25, 0.8, (-2, 0, 0.4)))):
        col = bpy.data.collections.new(name)
        sc.collection.children.link(col)
        ob = maker()
        for c in list(ob.users_collection):
            c.objects.unlink(ob)
        col.objects.link(ob)
        if name == "Crate":
            lid = bpy.data.collections.new("Lid")
            col.children.link(lid)
            ob2 = cube("CrateLid", (0.5, 0.5, 0.05), (2, 0, 0.52))
            for c in list(ob2.users_collection):
                c.objects.unlink(ob2)
            lid.objects.link(ob2)
    d = os.path.join(out, "Blends")
    os.makedirs(d, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(d, "Props.blend"), compress=False)


def fx_corrupt_zip(out, tmp):
    p = os.path.join(out, "Inbox", "rocks_pack.zip")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    good = os.path.join(tmp, "good.zip")
    with zipfile.ZipFile(good, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("rock_Color.png", os.urandom(4000))
    data = open(good, "rb").read()
    with open(p, "wb") as fh:
        fh.write(data[: len(data) // 2])  # truncated: central directory missing


def fx_max_file(out, tmp):
    p = os.path.join(out, "Inbox", "old_lamp.max")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(b"\xd0\xcf\x11\xe0" + os.urandom(2000))


def fx_loose(out, tmp):
    os.makedirs(os.path.join(out, "Inbox"), exist_ok=True)
    with open(os.path.join(out, "readme.txt"), "w") as fh:
        fh.write("My downloads\n")
    save_image(os.path.join(out, "Inbox", "decal_grunge.png"), 32, 32, (0.2, 0.2, 0.2), "noise")


def fx_nested_zip(out, tmp):
    inner_stage = os.path.join(tmp, "nested_inner")
    p = "Tiles012_1K-JPG"
    save_image(os.path.join(inner_stage, p + "_Color.jpg"), 32, 32, (0.9, 0.9, 0.85), "bricks")
    save_image(os.path.join(inner_stage, p + "_NormalGL.jpg"), 32, 32, (0.5, 0.5, 1.0), "flat")
    save_image(os.path.join(inner_stage, p + "_Roughness.jpg"), 32, 32, (0.4, 0.4, 0.4), "noise")
    outer = os.path.join(tmp, "nested_outer")
    zip_dir(inner_stage, os.path.join(outer, p + ".zip"))
    with open(os.path.join(outer, "license.txt"), "w") as fh:
        fh.write("CC0 1.0 Universal (Creative Commons Zero)\n")
    zip_dir(outer, os.path.join(out, "Inbox", "tiles_bundle.zip"))


def fx_big_gltf(out, tmp):
    """Performance only: a Sketchfab-style zip with a ~50k triangle glTF and 2K textures."""
    fresh()
    stage = os.path.join(tmp, "big")
    tex = os.path.join(tmp, "big_src")
    col = save_image(os.path.join(tex, "Statue_baseColor.png"), 2048, 2048, (0.7, 0.6, 0.5), "noise")
    nrm = save_image(os.path.join(tex, "Statue_normal.png"), 2048, 2048, (0.5, 0.5, 1.0), "noise", seed=3)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=180, ring_count=140, radius=0.5, location=(0, 0, 0.5))
    ob = bpy.context.active_object
    ob.name = "Statue"
    ob.modifiers.new("disp", "DISPLACE").strength = 0.05
    bpy.ops.object.modifier_apply(modifier="disp")
    ob.data.materials.append(material("Stone", {"color": col, "normal": nrm}))
    os.makedirs(stage, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=os.path.join(stage, "scene.gltf"), export_format="GLTF_SEPARATE",
                              export_texture_dir="textures")
    with open(os.path.join(stage, "license.txt"), "w", encoding="utf-8") as fh:
        fh.write("Model Information:\n* title:\tStone Statue\n* author:\tTest Maker\n\nModel License:\n"
                 "* license type:\tCC-BY-4.0\n")
    zip_dir(stage, os.path.join(out, "Models", "Statues", "stone_statue.zip"))


PERF_ONLY = {"big_gltf": fx_big_gltf}

FIXTURES = {
    "acg_zip": fx_acg_zip, "ph_folder": fx_ph_folder, "ph_hdri": fx_ph_hdri, "multi_res": fx_multi_res,
    "gloss_set": fx_gloss_set, "megascans_set": fx_megascans_set, "unicode": fx_unicode, "long_path": fx_long_path,
    "sketchfab_zip": fx_sketchfab_zip, "market_fbx": fx_market_fbx, "obj_mtl": fx_obj_mtl, "usdz": fx_usdz,
    "stl_ply": fx_stl_ply, "abc": fx_abc, "glb": fx_glb, "blend_marked": fx_blend_marked,
    "blend_unmarked": fx_blend_unmarked, "corrupt_zip": fx_corrupt_zip, "max_file": fx_max_file,
    "loose": fx_loose, "nested_zip": fx_nested_zip,
}


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    out = os.path.abspath(argv[0])
    only = None
    if "--only" in argv:
        only = set(argv[argv.index("--only") + 1].split(","))
    tmp = os.path.join(out, "_tmp")
    table = dict(FIXTURES, **PERF_ONLY) if only else FIXTURES
    for fid, fn in table.items():
        if only and fid not in only:
            continue
        dst = os.path.join(out, fid)
        if os.path.isdir(dst):
            shutil.rmtree("\\\\?\\" + dst if os.name == "nt" else dst)
        os.makedirs(dst)
        os.makedirs(tmp, exist_ok=True)
        fn(dst, tmp)
        shutil.rmtree(tmp, ignore_errors=True)
        print("[fixtures] built", fid)
    with open(os.path.join(out, "FIXTURES_OK"), "w") as fh:
        fh.write("\n".join(sorted(FIXTURES)))


main()
