# SPDX-License-Identifier: GPL-3.0-or-later
"""Placing assets from Quick Place. Appends from the managed .blend files (no importing in
the UI process): models at the cursor or on the surface under the mouse, materials onto the selection,
HDRIs as the scene world with the previous world kept for one-click restore."""

import math
import random

import bpy
from mathutils import Matrix, Vector

PREV_WORLD = "ri_previous_world"


def _existing(attr, aid):
    for idb in getattr(bpy.data, attr):
        if idb.library is None and idb.get("ri_asset_id") == aid and not idb.get("ri_placed"):
            return idb
    return None


def _append(path, attr, name):
    with bpy.data.libraries.load(path, link=False, assets_only=True) as (src, dst):
        avail = list(getattr(src, attr))
        if not avail:
            raise RuntimeError("No %s in %s" % (attr, path))
        setattr(dst, attr, [name if name in avail else avail[0]])
    idb = getattr(dst, attr)[0]
    return idb


def datablock(rec, member=None, reuse=True):
    """(attr, ID) for an asset record: reuses an earlier append of the same asset when allowed."""
    t = rec["type"]
    if t == "material":
        attr = "materials"
    elif t == "hdri":
        attr = "worlds"
    elif t == "blend":
        attr = "collections"
    else:
        attr = "objects" if rec.get("asset_kind") == "OBJECT" else "collections"
    if not rec.get("managed"):
        raise RuntimeError("%s has no asset file yet" % rec["name"])
    if reuse and member is None:
        idb = _existing(attr, rec["id"])
        if idb is not None:
            return attr, idb
    idb = _append(rec["managed"], attr, member or rec["name"])
    idb["ri_asset_id"] = rec["id"]
    return attr, idb


# --------------------------------------------------------------------------------------------- surface

def surface_hit(context, region, rv3d, coord):
    """(location, normal) under a region coordinate, or None."""
    from bpy_extras import view3d_utils
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    dg = context.evaluated_depsgraph_get()
    hit, loc, normal, _i, _ob, _m = context.scene.ray_cast(dg, origin, direction)
    return (loc, normal) if hit else None


def placement_matrix(loc, normal=None, align=True, random_rot=False, rng=None):
    rot = Matrix.Identity(4)
    if normal is not None and align:
        rot = Vector((0, 0, 1)).rotation_difference(Vector(normal).normalized()).to_matrix().to_4x4()
    if random_rot:
        rng = rng or random.Random()
        rot = rot @ Matrix.Rotation(rng.uniform(0, 2 * math.pi), 4, "Z")
    return Matrix.Translation(Vector(loc)) @ rot


# ------------------------------------------------------------------------------------------- placing

def place_model(context, rec, matrix, mode="INSTANCE", member=None):
    """Add a model at matrix. Returns the new top-level objects."""
    target = context.collection or context.scene.collection
    attr, idb = datablock(rec, member, reuse=(mode == "INSTANCE"))
    if attr == "objects":
        # the appended object stays an unlinked template; each placement is a copy sharing its mesh
        ob = idb.copy()
        ob["ri_asset_id"] = rec["id"]
        ob["ri_placed"] = True
        target.objects.link(ob)
        ob.matrix_world = matrix @ idb.matrix_world
        return [ob]
    col = idb
    if mode == "INSTANCE":
        empty = bpy.data.objects.new(col.name, None)
        empty.instance_type = "COLLECTION"
        empty.instance_collection = col
        empty.empty_display_size = 0.5
        empty["ri_asset_id"] = rec["id"]
        target.objects.link(empty)
        empty.matrix_world = matrix
        return [empty]
    target.children.link(col)
    roots = [o for o in col.objects if o.parent is None]
    for o in roots:
        o.matrix_world = matrix @ o.matrix_world
    return roots


def apply_material(context, rec, objects, slots="ALL"):
    _attr, mat = datablock(rec)
    n = 0
    for ob in objects:
        if ob.type not in ("MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME"):
            continue
        data_slots = ob.material_slots
        if not len(data_slots):
            ob.data.materials.append(mat)
        elif slots == "ACTIVE":
            ob.material_slots[ob.active_material_index].material = mat
        else:
            for s in data_slots:
                s.material = mat
        n += 1
    return mat, n


def set_world(context, rec):
    _attr, world = datablock(rec)
    sc = context.scene
    if sc.world is not None and sc.world != world:
        sc[PREV_WORLD] = sc.world.name
    sc.world = world
    return world


def restore_world(context):
    sc = context.scene
    name = sc.get(PREV_WORLD)
    w = bpy.data.worlds.get(name) if name else None
    if w is None:
        return False
    sc.world = w
    del sc[PREV_WORLD]
    return True


def replace_selected(context, rec, mode="INSTANCE"):
    """Swap each selected object for the model, keeping its transform."""
    old = [o for o in context.selected_objects]
    new = []
    for ob in old:
        new += place_model(context, rec, ob.matrix_world.copy(), mode)
    for ob in old:
        bpy.data.objects.remove(ob, do_unlink=True)
    for ob in new:
        ob.select_set(True)
    return new
