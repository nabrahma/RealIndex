# SPDX-License-Identifier: GPL-3.0-or-later
"""Texture sets become one Principled material.

Colour and emission maps are sRGB, everything else Non-Color; gloss is inverted to roughness; DirectX
normals get their green channel flipped; packed ARM/ORM maps are split (R occlusion, G roughness,
B metallic); height goes in as bump. Coordinates: UV or Object with Box projection, switchable later with
set_mapping() (the Box toggle RealIndex shows on the material). Also used by the UI process for that toggle
and by model conversion to texture untextured materials."""

import os

import bpy

from ..core import fsutil
from ..core import maps as M

PREFIX = "RI "


def _node(nt, idname, name, loc):
    n = nt.nodes.new(idname)
    n.name = n.label = name
    n.location = loc
    return n


def load_image(path, kind):
    path = fsutil.long(path)  # Blender reads paths past 260 characters only with the long-path prefix
    img = None
    for im in bpy.data.images:
        if im.filepath and os.path.normcase(bpy.path.abspath(im.filepath)) == os.path.normcase(path):
            img = im
            break
    if img is None:
        img = bpy.data.images.load(path, check_existing=True)
    try:
        img.colorspace_settings.name = M.colorspace(kind)
    except TypeError:
        pass
    if kind == M.OPACITY or kind == M.COLOR:
        img.alpha_mode = "STRAIGHT"
    return img


def _mix_rgba(nt, name, loc, blend):
    n = _node(nt, "ShaderNodeMix", name, loc)
    n.data_type = "RGBA"
    n.blend_type = blend
    n.inputs["Factor_Float"].default_value = 1.0
    return n


def wire(mat, maps, size_m=None, mapping="UV", ao_multiply=False):
    """(Re)build the RealIndex texture network of mat from maps {kind: {"path", "dx"}}.

    Returns the list of map kinds wired. Nodes are named 'RI ...' so later toggles can find them.
    """
    nt = mat.node_tree
    nt.nodes.clear()
    out = _node(nt, "ShaderNodeOutputMaterial", PREFIX + "Output", (900, 300))
    bsdf = _node(nt, "ShaderNodeBsdfPrincipled", PREFIX + "BSDF", (550, 300))
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    coord = _node(nt, "ShaderNodeTexCoord", PREFIX + "Coord", (-1100, 300))
    mp = _node(nt, "ShaderNodeMapping", PREFIX + "Mapping", (-900, 300))
    size = float(size_m or 1.0)
    mp.inputs["Scale"].default_value = (1.0 / size, 1.0 / size, 1.0 / size)
    mat["ri_size_m"] = size
    tex = {}
    y = 700
    for kind in (M.COLOR, M.AO, M.ARM, M.ROUGH, M.GLOSS, M.METAL, M.OPACITY, M.EMISSION, M.NORMAL, M.HEIGHT):
        m = maps.get(kind)
        if not m:
            continue
        t = _node(nt, "ShaderNodeTexImage", PREFIX + kind, (-550, y))
        t.image = load_image(m["path"], kind)
        nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
        tex[kind] = t
        y -= 300
    color_out = tex[M.COLOR].outputs["Color"] if M.COLOR in tex else None
    ao_src = tex[M.AO].outputs["Color"] if M.AO in tex else None
    sep = None
    if M.ARM in tex:
        sep = _node(nt, "ShaderNodeSeparateColor", PREFIX + "ARM Split", (-250, tex[M.ARM].location.y))
        nt.links.new(tex[M.ARM].outputs["Color"], sep.inputs["Color"])
        if ao_src is None:
            ao_src = sep.outputs["Red"]
    if color_out is not None:
        if ao_multiply and ao_src is not None:
            mix = _mix_rgba(nt, PREFIX + "AO Multiply", (150, 650), "MULTIPLY")
            nt.links.new(color_out, mix.inputs["A_Color"])
            nt.links.new(ao_src, mix.inputs["B_Color"])
            color_out = mix.outputs["Result_Color"]
        nt.links.new(color_out, bsdf.inputs["Base Color"])
    if M.ROUGH in tex:
        nt.links.new(tex[M.ROUGH].outputs["Color"], bsdf.inputs["Roughness"])
    elif M.GLOSS in tex:
        inv = _node(nt, "ShaderNodeInvert", PREFIX + "Gloss to Roughness", (-150, tex[M.GLOSS].location.y))
        nt.links.new(tex[M.GLOSS].outputs["Color"], inv.inputs["Color"])
        nt.links.new(inv.outputs["Color"], bsdf.inputs["Roughness"])
    elif sep is not None:
        nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
    if M.METAL in tex:
        nt.links.new(tex[M.METAL].outputs["Color"], bsdf.inputs["Metallic"])
    elif sep is not None:
        nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
    if M.OPACITY in tex:
        nt.links.new(tex[M.OPACITY].outputs["Color"], bsdf.inputs["Alpha"])
    if M.EMISSION in tex:
        nt.links.new(tex[M.EMISSION].outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 1.0
    normal_out = None
    if M.NORMAL in tex:
        src = tex[M.NORMAL].outputs["Color"]
        if maps[M.NORMAL].get("dx"):
            src = flip_green(nt, src, (-250, tex[M.NORMAL].location.y))
        nm = _node(nt, "ShaderNodeNormalMap", PREFIX + "Normal Map", (150, tex[M.NORMAL].location.y))
        nt.links.new(src, nm.inputs["Color"])
        normal_out = nm.outputs["Normal"]
    if M.HEIGHT in tex:
        bump = _node(nt, "ShaderNodeBump", PREFIX + "Bump", (300, tex[M.HEIGHT].location.y))
        # gentle by default: real 16-bit displacement maps at full strength scramble the shading normals
        bump.inputs["Strength"].default_value = 0.25
        bump.inputs["Distance"].default_value = 0.01 * size
        nt.links.new(tex[M.HEIGHT].outputs["Color"], bump.inputs["Height"])
        if normal_out is not None:
            nt.links.new(normal_out, bump.inputs["Normal"])
        normal_out = bump.outputs["Normal"]
    if normal_out is not None:
        nt.links.new(normal_out, bsdf.inputs["Normal"])
    set_mapping(mat, mapping)
    return sorted(tex)


def flip_green(nt, src_socket, loc):
    """DirectX to OpenGL normal: G -> 1 - G."""
    sep = _node(nt, "ShaderNodeSeparateColor", PREFIX + "DX Split", loc)
    inv = _node(nt, "ShaderNodeMath", PREFIX + "DX Flip Green", (loc[0] + 150, loc[1] - 60))
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    comb = _node(nt, "ShaderNodeCombineColor", PREFIX + "DX Combine", (loc[0] + 300, loc[1]))
    nt.links.new(src_socket, sep.inputs["Color"])
    nt.links.new(sep.outputs["Red"], comb.inputs["Red"])
    nt.links.new(sep.outputs["Green"], inv.inputs[1])
    nt.links.new(inv.outputs["Value"], comb.inputs["Green"])
    nt.links.new(sep.outputs["Blue"], comb.inputs["Blue"])
    return comb.outputs["Color"]


def set_mapping(mat, mode):
    """Box toggle: 'UV' (UV coordinates, flat projection) or 'BOX' (Object coordinates, box projection)."""
    nt = mat.node_tree
    coord, mp = nt.nodes.get(PREFIX + "Coord"), nt.nodes.get(PREFIX + "Mapping")
    if coord is None or mp is None:
        return False
    for l in list(mp.inputs["Vector"].links):
        nt.links.remove(l)
    nt.links.new(coord.outputs["Object" if mode == "BOX" else "UV"], mp.inputs["Vector"])
    for n in nt.nodes:
        if n.bl_idname == "ShaderNodeTexImage" and n.name.startswith(PREFIX):
            n.projection = "BOX" if mode == "BOX" else "FLAT"
            n.projection_blend = 0.2
    mat["ri_mapping"] = mode
    return True


def build(name, maps, size_m=None, mapping="UV", ao_multiply=False):
    mat = bpy.data.materials.new(name)
    wired = wire(mat, maps, size_m, mapping, ao_multiply)
    return mat, wired
