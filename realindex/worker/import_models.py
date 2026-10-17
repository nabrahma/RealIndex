# SPDX-License-Identifier: GPL-3.0-or-later
"""Model import with Blender's own importers and material repair.

Importer per format, verified on Blender 5.2.1:
  .fbx  import_scene.fbx       .gltf/.glb  import_scene.gltf     .obj  wm.obj_import
  .usd/.usda/.usdc/.usdz  wm.usd_import (textures packed)        .abc  wm.alembic_import
  .stl  wm.stl_import          .ply  wm.ply_import
"""

import os

import bpy

from ..core import fsutil
from ..core import maps as M
from ..core import names
from . import build_material


def _call(ext, path):
    if ext == ".fbx":
        return bpy.ops.import_scene.fbx(filepath=path, use_image_search=False)
    if ext == ".gltf":  # separate texture files: reference them, the importer packs by default
        return bpy.ops.import_scene.gltf(filepath=path, import_pack_images=False)
    if ext == ".glb":  # textures live inside the .glb: they can only be packed
        return bpy.ops.import_scene.gltf(filepath=path)
    if ext == ".obj":
        return bpy.ops.wm.obj_import(filepath=path)
    if ext in (".usd", ".usda", ".usdc", ".usdz"):
        return bpy.ops.wm.usd_import(filepath=path, import_textures_mode="IMPORT_PACK", import_cameras=False,
                                     import_lights=False)
    if ext == ".abc":
        return bpy.ops.wm.alembic_import(filepath=path, as_background_job=False)
    if ext == ".stl":
        return bpy.ops.wm.stl_import(filepath=path)
    if ext == ".ply":
        return bpy.ops.wm.ply_import(filepath=path)
    raise RuntimeError("No importer for %s" % ext)


def import_file(path):
    """Import one model file into the current scene; returns the new objects."""
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    res = _call(ext, path)
    if "FINISHED" not in res:
        raise RuntimeError("Importer refused the file (%s)" % ", ".join(res))
    new = [o for o in bpy.data.objects if o not in before]
    if not new:
        raise RuntimeError("The file contains no objects")
    return new


def _materials(objs):
    out = []
    for o in objs:
        for s in getattr(o, "material_slots", []):
            if s.material and s.material not in out:
                out.append(s.material)
    return out


def _image_nodes(mat):
    if mat.node_tree is None:
        return []
    return [n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage"]


def _missing(img):
    if img.packed_file is not None or img.source not in ("FILE", "SEQUENCE", "TILED"):
        return False
    p = bpy.path.abspath(img.filepath)
    return not p or not os.path.exists(p)


def _kind_of_name(stem):
    return M.parse(stem).kind


def _target_kind(node):
    """The map kind a texture node serves, from the socket it feeds."""
    for link in node.outputs["Color"].links:
        s, n = link.to_socket.name, link.to_node
        if n.bl_idname == "ShaderNodeNormalMap":
            return M.NORMAL
        if n.bl_idname == "ShaderNodeBump":
            return M.HEIGHT
        if s == "Base Color":
            return M.COLOR
        if s == "Roughness":
            return M.ROUGH
        if s == "Metallic":
            return M.METAL
        if s == "Alpha":
            return M.OPACITY
        if s.startswith("Emission"):
            return M.EMISSION
    for link in node.outputs["Alpha"].links:
        if link.to_socket.name == "Alpha":
            return M.OPACITY
    return None


def fix_materials(objs, claimed, model_name, warnings):
    """Relink missing textures from the claimed images, fix colour spaces, invert gloss, flip DX
    normals, split ARM/ORM, and texture untextured materials from a matching claimed texture set."""
    claimed = [p for p in claimed if os.path.splitext(p)[1].lower() in M.IMAGE_EXTS]
    by_base = {os.path.basename(p).lower(): p for p in claimed}
    parsed = {p: M.parse(os.path.splitext(os.path.basename(p))[0]) for p in claimed}
    mats = _materials(objs)
    used = set()
    relinked = 0
    for mat in mats:
        for node in _image_nodes(mat):
            img = node.image
            if img is None or not _missing(img):
                if img is not None and img.filepath:
                    used.add(os.path.normcase(bpy.path.abspath(img.filepath)))
                continue
            base = os.path.basename(bpy.path.abspath(img.filepath) or img.name).lower()
            new = by_base.get(base)
            if new is None:
                stem = os.path.splitext(base)[0]
                kind = _kind_of_name(stem) or _target_kind(node)
                best, best_s = None, 0.0
                for p in claimed:
                    pk = parsed[p].kind
                    if kind and pk and pk != kind and not (kind == M.ROUGH and pk == M.GLOSS):
                        continue
                    s = (names.similarity(parsed[p].prefix, mat.name) + names.similarity(parsed[p].prefix, model_name)
                         + names.similarity(parsed[p].prefix, M.parse(stem).prefix))
                    if kind and pk == kind:
                        s += 0.5
                    if s > best_s:
                        best, best_s = p, s
                new = best
            if new is None:
                warnings.append("Material %s: texture %s not found" % (mat.name, base))
                continue
            img.filepath = fsutil.long(new)
            img.reload()
            relinked += 1
            used.add(os.path.normcase(new))
        _fix_nodes(mat)
    # untextured materials take a claimed texture set
    sets = {}
    for p in claimed:
        pp = parsed[p]
        if pp.kind:
            sets.setdefault(pp.prefix.lower(), []).append(p)
    for mat in mats:
        if mat.node_tree is None or _image_nodes(mat):
            continue
        if not sets:
            continue
        ranked = sorted(sets.items(), key=lambda kv: (-max(names.similarity(kv[0], mat.name),
                                                            names.similarity(kv[0], model_name)), kv[0]))
        pre, files = ranked[0]
        score = max(names.similarity(pre, mat.name), names.similarity(pre, model_name))
        if score <= 0 and len(sets) > 1:
            warnings.append("Material %s: no textures matched" % mat.name)
            continue
        found = [(parsed[p].kind, p, parsed[p]) for p in files]
        picked = M.pick(found)
        if M.COLOR not in picked:
            warnings.append("Material %s: matched set %s has no color map" % (mat.name, pre))
            continue
        build_material.wire(mat, picked, None, "UV")
        for v in picked.values():
            used.add(os.path.normcase(v["path"]))
    return relinked


def _fix_nodes(mat):
    """Colour spaces from map names, gloss to roughness, DX normals, ARM split, for an imported material."""
    nt = mat.node_tree
    for node in list(_image_nodes(mat)):
        img = node.image
        if img is None:
            continue
        stem = os.path.splitext(os.path.basename(bpy.path.abspath(img.filepath) or img.name))[0]
        p = M.parse(stem)
        kind = p.kind or _target_kind(node)
        if kind:
            try:
                img.colorspace_settings.name = M.colorspace(kind if kind != M.ARM else M.ROUGH)
            except TypeError:
                pass
        out = node.outputs["Color"]
        if kind == M.GLOSS:
            for link in list(out.links):
                if link.to_socket.name == "Roughness":
                    inv = nt.nodes.new("ShaderNodeInvert")
                    inv.name = inv.label = "RI Gloss to Roughness"
                    inv.location = node.location.x + 250, node.location.y
                    to = link.to_socket
                    nt.links.remove(link)
                    nt.links.new(out, inv.inputs["Color"])
                    nt.links.new(inv.outputs["Color"], to)
        elif kind == M.NORMAL and p.dx:
            for link in list(out.links):
                if link.to_node.bl_idname == "ShaderNodeNormalMap":
                    to = link.to_socket
                    nt.links.remove(link)
                    nt.links.new(build_material.flip_green(nt, out, (node.location.x + 250, node.location.y)), to)
        elif kind == M.ARM:
            links = list(out.links)
            if not links:
                continue
            sep = nt.nodes.new("ShaderNodeSeparateColor")
            sep.name = sep.label = "RI ARM Split"
            sep.location = node.location.x + 250, node.location.y
            nt.links.new(out, sep.inputs["Color"])
            for link in links:
                to = link.to_socket
                nt.links.remove(link)
                ch = {"Roughness": "Green", "Metallic": "Blue"}.get(to.name, "Red")
                nt.links.new(sep.outputs[ch], to)


def missing_images():
    return sorted(img.name for img in bpy.data.images if _missing(img))
