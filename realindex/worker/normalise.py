# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalise imported models: unit mistakes (100x and 0.01x), rotation and scale applied, origin at the bottom
centre, helper lights, cameras and childless empties removed; LOD objects inside a file."""

import re

import bpy
from mathutils import Matrix, Vector

from .preview import bbox_world

BIG_WORDS = ("building", "house", "terrain", "landscape", "city", "street", "ship", "castle", "bridge", "tower",
             "mountain", "island", "stadium", "hangar", "warehouse", "factory", "large", "huge", "giant")
_LOD_OBJ = re.compile(r"[_\-. ]lod[_\-]?([1-9]\d*)$", re.I)
GEOMETRY = ("MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME")


def remove_helpers(objs, prefs):
    """Drop imported lights and cameras, and empties without children (preference)."""
    if not prefs.get("remove_lights", True):
        return objs
    keep = []
    for o in objs:
        if o.type in ("LIGHT", "CAMERA", "LIGHT_PROBE", "SPEAKER") and not o.children:
            bpy.data.objects.remove(o, do_unlink=True)
        elif o.type == "EMPTY" and not o.children and o.instance_type == "NONE":
            bpy.data.objects.remove(o, do_unlink=True)
        else:
            keep.append(o)
    return keep


def lod_objects(objs, keep_lods):
    """Objects named *_LOD1.. inside a file: removed, or kept hidden when the preference asks."""
    out = []
    for o in objs:
        m = _LOD_OBJ.search(o.name)
        if m and int(m.group(1)) > 0:
            if keep_lods:
                o.hide_viewport = o.hide_render = True
                o.hide_set(True) if o.name in bpy.context.view_layer.objects else None
                out.append(o)
            else:
                bpy.data.objects.remove(o, do_unlink=True)
            continue
        out.append(o)
    return out


def dims(objs):
    lo, hi = bbox_world([o for o in objs if not o.hide_render])
    return lo, hi, hi - lo


def normalise(objs, name, prefs, warnings):
    """Returns (objs, dimensions Vector, info dict)."""
    view_layer = bpy.context.view_layer
    view_layer.update()
    info = {}
    rigged = any(o.type == "ARMATURE" for o in objs)
    animated = any(o.animation_data and o.animation_data.action for o in objs)
    has_cache = any(m.type == "MESH_SEQUENCE_CACHE" for o in objs for m in getattr(o, "modifiers", [])) or any(
        c.type == "TRANSFORM_CACHE" for o in objs for c in o.constraints)
    driven = rigged or animated or has_cache
    if driven:
        # Animation, rigs and Alembic caches own their transforms: move and scale a new root instead.
        root = bpy.data.objects.new(name + "_Root", None)
        root.empty_display_size = 0.25
        for c in list(objs[0].users_collection) or [bpy.context.scene.collection]:
            c.objects.link(root)
        for o in [o for o in objs if o.parent is None]:
            o.parent = root
            o.matrix_parent_inverse = Matrix.Identity(4)
        objs = [root] + list(objs)
        view_layer.update()
    lo, hi, d = dims(objs)
    maxd = max(d.x, d.y, d.z)
    fix = 1.0
    low = name.lower()
    if maxd > 50.0 and not any(w in low for w in BIG_WORDS):
        fix = 0.01
    elif 0.0 < maxd < 0.01:
        fix = 100.0
    roots = [o for o in objs if o.parent is None]
    if fix != 1.0:
        for r in roots:
            r.matrix_world = Matrix.Scale(fix, 4) @ r.matrix_world
        info["unit_fix"] = fix
        warnings.append("Scaled by %g (looked like a unit mistake: %.3g m)" % (fix, maxd))
        view_layer.update()
    if not driven:
        _apply_transforms(objs)
    else:
        info["kept_transforms"] = True
    view_layer.update()
    if prefs.get("origin_bottom", True):
        lo, hi, d = dims(objs)
        offset = Vector((-(lo.x + hi.x) / 2.0, -(lo.y + hi.y) / 2.0, -lo.z))
        for r in [o for o in objs if o.parent is None]:
            r.matrix_world = Matrix.Translation(offset) @ r.matrix_world
        view_layer.update()
        single = [o for o in objs if o.type in GEOMETRY]
        if len(objs) == 1 and len(single) == 1 and single[0].type == "MESH" and not rigged and not animated \
                and not has_cache and single[0].data.users == 1:
            ob = single[0]
            loc = ob.matrix_world.translation.copy()
            ob.data.transform(Matrix.Translation(ob.matrix_world.inverted().to_3x3() @ loc))
            ob.matrix_world.translation = (0, 0, 0)
            view_layer.update()
    lo, hi, d = dims(objs)
    info["dims"] = [round(d.x, 4), round(d.y, 4), round(d.z, 4)]
    info["bottom"] = [round((lo.x + hi.x) / 2, 4), round((lo.y + hi.y) / 2, 4), round(lo.z, 4)]
    return objs, d, info


def _apply_transforms(objs):
    """Apply rotation and scale on geometry with single-user data (instanced data keeps its transform)."""
    targets = [o for o in objs if o.type in ("MESH", "CURVE", "SURFACE", "FONT", "EMPTY") and
               (o.data is None or o.data.users == 1)]
    if not targets:
        return
    vl = bpy.context.view_layer
    for o in vl.objects:
        o.select_set(False)
    sel = [o for o in targets if o.name in vl.objects]
    for o in sel:
        o.select_set(True)
    if not sel:
        return
    vl.objects.active = sel[0]
    try:
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    except RuntimeError:
        pass
    for o in sel:
        o.select_set(False)


def size_class(d):
    """Size class tag for models: Small under 0.5 m, Large over 3 m, else Medium."""
    m = max(d.x, d.y, d.z)
    return "small" if m < 0.5 else ("large" if m > 3.0 else "medium")
