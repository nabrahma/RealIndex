# SPDX-License-Identifier: GPL-3.0-or-later
"""Previews: the vendor's own image when there is one, a tone-mapped downscale for HDRIs,
otherwise a render: materials on a sphere, models on a neutral grey ground with a soft studio light.
EEVEE when it works in this background process, else Workbench; a preview never blocks a conversion."""

import math
import os

import bpy
import numpy as np
from mathutils import Vector

from ..core import fsutil


def set_preview(idb, size, flat):
    pv = idb.preview_ensure()
    pv.image_size = size
    pv.image_pixels_float = flat


def _save_png(path, w, h, flat):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = bpy.data.images.new("ri_preview_out", w, h, alpha=True)
    img.pixels.foreach_set(flat)
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def _square(px, size):
    """Fit an (h, w, 4) array into a size x size transparent square (letterbox), returns float32 array."""
    h, w = px.shape[:2]
    out = np.zeros((size, size, 4), np.float32)
    y0, x0 = (size - h) // 2, (size - w) // 2
    out[y0:y0 + h, x0:x0 + w] = px
    return out


def _pixels(img):
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(h, w, 4)


def _scaled_copy(path, w, h):
    img = bpy.data.images.load(fsutil.long(path), check_existing=False)
    img.scale(max(1, w), max(1, h))
    px = _pixels(img)
    bpy.data.images.remove(img)
    return px


def from_image(path, size, out_png):
    """The vendor's preview image, resized to fit size."""
    img = bpy.data.images.load(fsutil.long(path), check_existing=False)
    w, h = img.size
    bpy.data.images.remove(img)
    if not w or not h:
        raise RuntimeError("Unreadable preview image")
    s = size / float(max(w, h))
    px = _scaled_copy(path, int(round(w * s)), int(round(h * s)))
    sq = _square(px, size)
    flat = sq.ravel()
    _save_png(out_png, size, size, flat)
    return (size, size), flat


def from_hdri(path, size, out_png):
    """HDRI previews are a tone-mapped downscale of the image itself (no render)."""
    w, h = size, size // 2
    px = _scaled_copy(path, w, h)
    rgb = np.clip(px[..., :3], 0, None)
    lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    key = float(np.exp(np.mean(np.log(lum + 1e-4))))  # log-average luminance
    rgb = rgb * (0.25 / max(key, 1e-4))
    rgb = rgb / (1.0 + rgb)
    rgb = np.power(np.clip(rgb, 0, 1), 1 / 2.2)
    out = np.concatenate([rgb, np.ones(rgb.shape[:2] + (1,), np.float32)], axis=2)
    # stored preview pixels are linear-ish display values; keep them as is (Blender shows previews raw)
    sq = _square(out.astype(np.float32), size)
    flat = sq.ravel()
    _save_png(out_png, size, size, flat)
    return (size, size), flat


# --------------------------------------------------------------------------------------------- renders

def _studio_scene(name="RI_Preview"):
    sc = bpy.data.scenes.new(name)
    world = bpy.data.worlds.new("RI_PreviewWorld")
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.21, 0.21, 0.22, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6
    sc.world = world
    cam = bpy.data.objects.new("RI_Cam", bpy.data.cameras.new("RI_Cam"))
    cam.data.lens = 50
    sc.collection.objects.link(cam)
    sc.camera = cam
    helpers = [cam]
    for nm, loc, energy, size in (("Key", (-3.0, -3.5, 4.0), 600.0, 3.0), ("Fill", (4.0, -2.0, 1.5), 180.0, 4.0),
                                  ("Rim", (0.5, 4.0, 3.0), 300.0, 2.0)):
        ld = bpy.data.lights.new("RI_" + nm, "AREA")
        ld.energy, ld.size = energy, size
        lo = bpy.data.objects.new("RI_" + nm, ld)
        lo.location = loc
        _look_at(lo, Vector((0, 0, 0.5)))
        sc.collection.objects.link(lo)
        helpers.append(lo)
    return sc, cam, helpers


def _look_at(ob, target):
    d = Vector(target) - ob.location
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def _ground(sc, z, extent):
    me = bpy.data.meshes.new("RI_Ground")
    s = max(extent * 20.0, 10.0)
    me.from_pydata([(-s, -s, z), (s, -s, z), (s, s, z), (-s, s, z)], [], [(0, 1, 2, 3)])
    ob = bpy.data.objects.new("RI_Ground", me)
    m = bpy.data.materials.new("RI_Grey")
    bsdf = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0.38, 0.38, 0.38, 1)
    bsdf.inputs["Roughness"].default_value = 0.8
    me.materials.append(m)
    sc.collection.objects.link(ob)
    return ob


def _render(sc, size, engine_pref, out_png):
    """Render sc to out_png; EEVEE first unless Workbench is asked for. Returns (size, flat pixels, engine)."""
    sc.render.resolution_x = sc.render.resolution_y = size
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.render.filepath = out_png
    engines = ["BLENDER_WORKBENCH"] if engine_pref == "WORKBENCH" else ["BLENDER_EEVEE", "BLENDER_WORKBENCH"]
    last = None
    for eng in engines:
        try:
            sc.render.engine = eng
            if eng == "BLENDER_EEVEE":
                sc.eevee.taa_render_samples = 24
                try:
                    sc.eevee.use_shadows = True
                except AttributeError:
                    pass
            else:
                sh = sc.display.shading
                sh.light = "STUDIO"
                sh.color_type = "TEXTURE"
                sh.show_shadows = True
            if os.path.exists(out_png):
                os.remove(out_png)
            with bpy.context.temp_override(scene=sc):
                bpy.ops.render.render(write_still=True, scene=sc.name)
            img = bpy.data.images.load(out_png, check_existing=False)
            px = _pixels(img)
            bpy.data.images.remove(img)
            if float(px[..., :3].max()) <= 0.0:
                raise RuntimeError("blank render")
            return (size, size), px.ravel(), eng
        except Exception as e:  # engine unavailable here (no GPU context): fall back
            last = e
            continue
    raise RuntimeError("Preview render failed: %s" % last)


def _cleanup(sc, helpers):
    for ob in helpers:
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)
            elif isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
    w = sc.world
    bpy.data.scenes.remove(sc)
    if w is not None and w.users == 0:
        bpy.data.worlds.remove(w)
    for m in [m for m in bpy.data.materials if m.name.startswith("RI_Grey") and m.users == 0]:
        bpy.data.materials.remove(m)


def render_material(mat, size, engine_pref, out_png):
    sc, cam, helpers = _studio_scene()
    me = bpy.data.meshes.new("RI_Sphere")
    import bmesh
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")  # calc_uvs only fills an existing UV layer
    bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=32, radius=1.0, calc_uvs=True)
    bm.to_mesh(me)
    bm.free()
    uv = me.uv_layers.active  # real-world UVs: the 2 m ball shows the material at its real tile size
    if uv is not None:
        for d in uv.data:
            d.uv = (d.uv[0] * 2 * math.pi, d.uv[1] * math.pi)
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(mat)
    ob = bpy.data.objects.new("RI_Sphere", me)
    ob.location = (0, 0, 1.0)
    sc.collection.objects.link(ob)
    helpers.append(ob)
    helpers.append(_ground(sc, 0.0, 1.0))
    cam.location = (0, -3.6, 1.55)
    _look_at(cam, Vector((0, 0, 0.95)))
    try:
        return _render(sc, size, engine_pref, out_png)
    finally:
        _cleanup(sc, helpers)


def bbox_world(objs):
    lo, hi = Vector((1e18, 1e18, 1e18)), Vector((-1e18, -1e18, -1e18))
    found = False
    for o in objs:
        if o.type not in ("MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME"):
            continue
        for c in o.bound_box:
            v = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, v))
            hi = Vector(map(max, hi, v))
            found = True
    return (lo, hi) if found else (Vector((0, 0, 0)), Vector((0, 0, 0)))


def render_collection(col, size, engine_pref, out_png):
    """A model framed from the front-left, three-quarter view, on a grey ground."""
    sc, cam, helpers = _studio_scene()
    sc.collection.children.link(col)
    bpy.context.view_layer.update()
    objs = [o for o in col.all_objects if not o.hide_render]
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    lo, hi = bbox_world(objs)
    centre = (lo + hi) / 2.0
    radius = max((hi - lo).length / 2.0, 1e-3)
    helpers.append(_ground(sc, lo.z, radius))
    fov = 2 * math.atan(18.0 / cam.data.lens)
    dist = radius / math.sin(fov / 2.0) * 1.08
    az, el = math.radians(-35.0), math.radians(22.0)
    cam.location = centre + Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))) * dist
    cam.data.clip_start = max(dist / 1000.0, 0.001)
    cam.data.clip_end = dist * 10 + radius * 10
    _look_at(cam, centre)
    for lo_ in helpers[1:4]:  # scale the studio to the model
        lo_.location = centre + (lo_.location - Vector((0, 0, 0.5))) * max(radius, 0.3)
        lo_.data.size *= max(radius, 0.3)
        lo_.data.energy *= max(radius, 0.3) ** 2
        _look_at(lo_, centre)
    try:
        return _render(sc, size, engine_pref, out_png)
    finally:
        sc.collection.children.unlink(col)
        _cleanup(sc, helpers)
