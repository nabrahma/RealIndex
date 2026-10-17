# SPDX-License-Identifier: GPL-3.0-or-later
"""Texture sets become one Principled material.

Colour and emission maps are sRGB, everything else Non-Color; gloss is inverted to roughness; DirectX
normals get their green channel flipped; packed ARM/ORM maps are split (R occlusion, G roughness,
B metallic). Height becomes true displacement (the Displacement node into the material output, with the
vendor's own scale when its .blend sits next to the maps) or, in bump-only mode, a gentle bump. Specular,
transmission, subsurface, translucency (thin wall) and sheen maps feed their Principled inputs. Non-square
maps keep square pixels. Coordinates: UV or Object with Box projection, switchable later with set_mapping()
(the Box toggle RealIndex shows on the material). Also used by the UI process for that toggle and by model
conversion to texture untextured materials."""

import os

import bpy

from ..core import fsutil
from ..core import maps as M

PREFIX = "RI "
# Displacement height when the vendor gives none, as a fraction of the texture's real-world size.
# Poly Haven's own materials range from about 2% (planks, bricks) to 10% (aerial rocks).
DISP_FRACTION = 0.03


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


def wire(mat, maps, size_m=None, mapping="UV", ao_multiply=False, displacement="BUMP", vendor=None):
    """(Re)build the RealIndex texture network of mat from maps {kind: {"path", "dx"}}.

    displacement: 'TRUE' wires height into the material output as real displacement (Cycles renders it on
    adaptively subdivided meshes), 'BUMP' keeps it a bump. vendor: values from the vendor's own .blend
    (vendor_hints). Returns the list of map kinds wired. Nodes are named 'RI ...' so later toggles find them.
    """
    vendor = vendor or {}
    nt = mat.node_tree
    nt.nodes.clear()
    out = _node(nt, "ShaderNodeOutputMaterial", PREFIX + "Output", (900, 300))
    bsdf = _node(nt, "ShaderNodeBsdfPrincipled", PREFIX + "BSDF", (550, 300))
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    coord = _node(nt, "ShaderNodeTexCoord", PREFIX + "Coord", (-1100, 300))
    mp = _node(nt, "ShaderNodeMapping", PREFIX + "Mapping", (-900, 300))
    size = float(size_m or 1.0)
    mat["ri_size_m"] = size
    tex = {}
    y = 700
    for kind in (M.COLOR, M.AO, M.ARM, M.ROUGH, M.GLOSS, M.METAL, M.SPECULAR, M.OPACITY, M.EMISSION,
                 M.TRANSMISSION, M.SSS, M.TRANSLUCENCY, M.SHEEN, M.NORMAL, M.HEIGHT):
        m = maps.get(kind)
        if not m:
            continue
        t = _node(nt, "ShaderNodeTexImage", PREFIX + kind, (-550, y))
        t.image = load_image(m["path"], kind)
        nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
        tex[kind] = t
        y -= 300
    # square pixels on non-square maps: a 1:2 image covers twice the length along V
    mp.inputs["Scale"].default_value = (1.0 / size, 1.0 / (size * _aspect(tex)), 1.0 / size)
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
    if M.SPECULAR in tex:
        _wire_specular(nt, tex[M.SPECULAR], bsdf)
    if M.OPACITY in tex:
        nt.links.new(tex[M.OPACITY].outputs["Color"], bsdf.inputs["Alpha"])
    if M.EMISSION in tex:
        nt.links.new(tex[M.EMISSION].outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 1.0
    if M.TRANSMISSION in tex:
        nt.links.new(tex[M.TRANSMISSION].outputs["Color"], bsdf.inputs["Transmission Weight"])
    if M.TRANSLUCENCY in tex:
        # leaves and paper: thin-walled, where subsurface becomes light passing through the sheet
        bsdf.inputs["Thin Wall"].default_value = True
        nt.links.new(tex[M.TRANSLUCENCY].outputs["Color"], bsdf.inputs["Subsurface Weight"])
    elif M.SSS in tex:
        nt.links.new(tex[M.SSS].outputs["Color"], bsdf.inputs["Subsurface Weight"])
    if M.SHEEN in tex:
        nt.links.new(tex[M.SHEEN].outputs["Color"], bsdf.inputs["Sheen Weight"])
    normal_out = None
    if M.NORMAL in tex:
        src = tex[M.NORMAL].outputs["Color"]
        if maps[M.NORMAL].get("dx"):
            src = flip_green(nt, src, (-250, tex[M.NORMAL].location.y))
        nm = _node(nt, "ShaderNodeNormalMap", PREFIX + "Normal Map", (150, tex[M.NORMAL].location.y))
        nt.links.new(src, nm.inputs["Color"])
        normal_out = nm.outputs["Normal"]
    mat.displacement_method = "BUMP"
    if M.HEIGHT in tex and displacement == "TRUE":
        h = tex[M.HEIGHT]
        h.interpolation = "Cubic"  # smooth slopes between texels instead of terraces
        disp = _node(nt, "ShaderNodeDisplacement", PREFIX + "Displacement", (550, -150))
        disp.space = "OBJECT"
        disp.inputs["Midlevel"].default_value = vendor.get("midlevel", 0.5)
        disp.inputs["Scale"].default_value = vendor.get("scale") or DISP_FRACTION * size
        nt.links.new(h.outputs["Color"], disp.inputs["Height"])
        nt.links.new(disp.outputs["Displacement"], out.inputs["Displacement"])
        # with a normal map the fine detail is already there (since Blender 5.0 it follows the displaced
        # surface); without one, bump adds the detail finer than the dicing rate
        mat.displacement_method = "DISPLACEMENT" if normal_out is not None else "BOTH"
    elif M.HEIGHT in tex:
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


def _aspect(tex):
    """Height over width of the set's maps (1.0 when square or unreadable)."""
    for kind in (M.COLOR, M.ROUGH, M.NORMAL, M.HEIGHT):
        t = tex.get(kind)
        if t is not None and t.image is not None:
            w, h = t.image.size
            if w and h:
                return h / float(w)
    return 1.0


def _wire_specular(nt, t, bsdf):
    """Specular maps come in two conventions. Spec/gloss vendors (Megascans and others) store reflectance at
    normal incidence (F0) as an sRGB colour, about 0.22 for non-metals; that becomes Specular IOR Level as
    F0 / 0.08 (0.5 is Blender's 4%). Game-engine packs store a level where 0.5 is the default, which maps
    straight onto Specular IOR Level. The map's own average tells them apart."""
    mean = _mean_value(t.image)
    if mean is not None and mean < 0.35:
        try:
            t.image.colorspace_settings.name = "sRGB"
        except TypeError:
            pass
        bw = _node(nt, "ShaderNodeRGBToBW", PREFIX + "Specular F0", (-250, t.location.y))
        mul = _node(nt, "ShaderNodeMath", PREFIX + "F0 to IOR Level", (-100, t.location.y))
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = 1.0 / 0.08
        nt.links.new(t.outputs["Color"], bw.inputs["Color"])
        nt.links.new(bw.outputs["Val"], mul.inputs[0])
        nt.links.new(mul.outputs["Value"], bsdf.inputs["Specular IOR Level"])
    else:
        nt.links.new(t.outputs["Color"], bsdf.inputs["Specular IOR Level"])


def _mean_value(img):
    """Average of an image's stored values (a 16 x 16 copy keeps this cheap on 8K maps)."""
    if img is None:
        return None
    small = None
    try:
        small = img.copy()
        small.scale(16, 16)
        px = list(small.pixels)
        rgb = [v for i, v in enumerate(px) if i % 4 != 3]
        return sum(rgb) / len(rgb) if rgb else None
    except Exception:
        return None
    finally:
        if small is not None:
            bpy.data.images.remove(small)


def vendor_hints(maps):
    """Displacement scale and midlevel from a vendor .blend beside the maps.

    Poly Haven ships a hand-tuned Displacement node in every material .blend (0.018 m for planks, 5 m for
    aerial rocks); ambientCG ships one at scale 0, which counts as no value. The vendor's material is the one
    using an image with the same file name as one of our maps; everything loaded is removed again.
    """
    if not maps.get(M.HEIGHT):
        return {}
    wanted = {os.path.basename(m["path"]).lower() for m in maps.values() if m and m.get("path")}
    dirs = []
    for m in maps.values():
        d = os.path.dirname(m["path"])
        for x in (d, os.path.dirname(d)):
            if x and x not in dirs:
                dirs.append(x)
    blends = []
    for d in dirs:
        try:
            listing = sorted(os.listdir(fsutil.long(d)))
        except OSError:
            continue
        blends += [os.path.join(d, n) for n in listing if n.lower().endswith(".blend")]
    for path in blends[:4]:
        try:
            if os.path.getsize(fsutil.long(path)) > 64 * 1024 * 1024:
                continue
            hints = _hints_from(path, wanted)
        except Exception:
            continue
        if hints:
            return hints
    return {}


def _hints_from(path, wanted):
    kinds = ("materials", "images", "node_groups", "textures")
    before = {k: set(getattr(bpy.data, k)) for k in kinds}
    libs = set(bpy.data.libraries)
    try:
        with bpy.data.libraries.load(fsutil.long(path), link=False) as (src, dst):
            dst.materials = list(src.materials)
        for mat in dst.materials:
            if mat is None or mat.node_tree is None:
                continue
            files = {os.path.basename(bpy.path.abspath(n.image.filepath)).lower()
                     for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image is not None}
            if not files & wanted:
                continue
            for n in mat.node_tree.nodes:
                if n.type == "DISPLACEMENT" and n.inputs["Scale"].default_value > 0:
                    return {"scale": float(n.inputs["Scale"].default_value),
                            "midlevel": float(n.inputs["Midlevel"].default_value)}
        return {}
    finally:
        for k in kinds:
            coll = getattr(bpy.data, k)
            for idb in [x for x in coll if x not in before[k]]:
                coll.remove(idb)
        for lib in [x for x in bpy.data.libraries if x not in libs]:
            bpy.data.libraries.remove(lib)


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


def has_displacement(mat):
    """True when mat is a RealIndex material with true displacement wired."""
    nt = mat.node_tree if mat is not None else None
    return nt is not None and nt.nodes.get(PREFIX + "Displacement") is not None


def setup_displacement(objects):
    """Give each mesh an adaptive Subdivision modifier at the end of its stack (Cycles dices it per pixel at
    render time). An existing Subdivision modifier is switched to adaptive instead. Returns how many changed."""
    changed = 0
    for ob in objects:
        if ob.type != "MESH":
            continue
        mod = next((m for m in ob.modifiers if m.type == "SUBSURF"), None)
        if mod is None:
            mod = ob.modifiers.new("Displacement Subdivision", "SUBSURF")
            mod.subdivision_type = "SIMPLE"  # keeps the shape; only the displacement adds detail
            mod.levels = 0  # light viewport; the render is diced adaptively
        if not mod.use_adaptive_subdivision:
            mod.use_adaptive_subdivision = True
            changed += 1
    return changed


def build(name, maps, size_m=None, mapping="UV", ao_multiply=False, displacement="TRUE"):
    vendor = vendor_hints(maps) if displacement == "TRUE" else {}
    mat = bpy.data.materials.new(name)
    wired = wire(mat, maps, size_m, mapping, ao_multiply, displacement, vendor)
    if vendor:
        mat["ri_vendor_displacement"] = vendor["scale"]
    return mat, wired
