# SPDX-License-Identifier: GPL-3.0-or-later
"""HDRIs become a world asset: Environment Texture through a Mapping node with a Rotation value,
into a Background with Strength. Dragging the world asset into the viewport sets the scene world."""

import bpy

from ..core import fsutil

PREFIX = "RI "


def build(name, path, rotation=0.0, strength=1.0):
    world = bpy.data.worlds.new(name)
    nt = world.node_tree
    nt.nodes.clear()
    coord = nt.nodes.new("ShaderNodeTexCoord")
    coord.name = coord.label = PREFIX + "Coord"
    coord.location = (-900, 300)
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.name = mp.label = PREFIX + "Rotation"
    mp.location = (-700, 300)
    mp.inputs["Rotation"].default_value = (0.0, 0.0, rotation)
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.name = env.label = PREFIX + "Environment"
    env.location = (-450, 300)
    env.image = bpy.data.images.load(fsutil.long(path), check_existing=True)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.name = bg.label = PREFIX + "Background"
    bg.location = (-150, 300)
    bg.inputs["Strength"].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (100, 300)
    nt.links.new(coord.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return world
