# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences: drop roots and the settings the engine and workers use."""

import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, PropertyGroup, UIList

DEFAULT_WORKERS = max(1, min(4, (os.cpu_count() or 4) // 2))


def _changed(self, context):
    from . import runtime
    runtime.reconfigure_soon()


def default_root():
    return os.path.join(os.path.expanduser("~"), "Documents", "RealIndex")


class RI_Root(PropertyGroup):
    path: StringProperty(name="Drop Folder", subtype="DIR_PATH", update=_changed,
                         description="Folder you drop downloads into. RealIndex only reads it")
    library_dir: StringProperty(
        name="Library Folder", subtype="DIR_PATH", update=_changed,
        description="Where RealIndex keeps this folder's index, extracted zips, previews and asset files. Empty: "
                    "a folder in your user data, outside the drop folder. Pick a local disk to keep the library "
                    "browsable while an external drop folder is unplugged")
    pack: BoolProperty(name="Pack Textures", default=False, update=_changed,
                       description="Pack textures into each asset file (doubles disk use, works with the drop "
                                   "folder offline)")
    enabled: BoolProperty(name="Enabled", default=True, update=_changed)


class RI_UL_roots(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        row.label(text=item.path or "(choose a folder)", icon="FILE_FOLDER")
        if item.pack:
            row.label(text="", icon="PACKAGE")


class RealIndexPreferences(AddonPreferences):
    bl_idname = __package__

    roots: CollectionProperty(type=RI_Root)
    roots_index: IntProperty()
    poll_interval: FloatProperty(name="Poll Interval", default=2.0, min=0.5, max=60.0, subtype="TIME_ABSOLUTE",
                                 update=_changed, description="How often the drop folders are checked")
    workers: IntProperty(name="Parallel Workers", default=DEFAULT_WORKERS, min=1, max=16, update=_changed,
                         description="Background Blender processes converting at the same time")
    origin_bottom: BoolProperty(name="Origin to Bottom Centre", default=True, update=_changed)
    remove_lights: BoolProperty(name="Remove Imported Lights and Cameras", default=True, update=_changed)
    mapping: EnumProperty(name="Texture-Set Mapping", update=_changed,
                          items=(("UV", "UV", "UV coordinates (Box toggle on the material)"),
                                 ("BOX", "Box", "Object coordinates with box projection")), default="UV")
    ao_multiply: BoolProperty(name="Multiply AO into Color", default=False, update=_changed)
    keep_lods: BoolProperty(name="Keep LODs", default=False, update=_changed,
                            description="Keep LOD1+ as hidden objects in the model's collection")
    preview_size: EnumProperty(name="Preview Size", update=_changed, default="256",
                               items=(("128", "128 px", ""), ("256", "256 px", ""), ("512", "512 px", "")))
    preview_engine: EnumProperty(name="Preview Engine", update=_changed, default="AUTO",
                                 items=(("AUTO", "EEVEE if Available", "EEVEE, else Workbench"),
                                        ("EEVEE", "EEVEE", ""), ("WORKBENCH", "Workbench", "")))
    inbox_autofile: BoolProperty(name="Inbox Auto-Filing", default=True, update=_changed,
                                 description="Move finished items from Inbox into type folders (logged, undoable)")
    delete_mode: EnumProperty(name="Delete Assets When Source Deleted", update=_changed, default="ASK",
                              items=(("ASK", "Ask", "One confirmation per batch"),
                                     ("AUTO", "After 7 Days", "Remove automatically after 7 days")))
    blend_mode: EnumProperty(name="Unmarked .blend Files", update=_changed, default="COLLECTIONS",
                             items=(("COLLECTIONS", "Top-Level Collections", ""), ("OBJECTS", "Objects", ""),
                                    ("MATERIALS", "Materials", ""), ("NODE_GROUPS", "Node Groups", "")))
    timeout: FloatProperty(name="Job Timeout", default=120.0, min=10.0, max=3600.0, subtype="TIME_ABSOLUTE",
                           update=_changed)
    place_mode: EnumProperty(name="Place Models As", default="INSTANCE",
                             items=(("INSTANCE", "Collection Instance", "An empty instancing the model"),
                                    ("REAL", "Real Objects", "The model's objects, editable")))
    place_align: BoolProperty(name="Align to Surface", default=True)
    place_random: BoolProperty(name="Random Rotation", default=False,
                               description="Random rotation around the surface normal")
    material_slots: EnumProperty(name="Material Slots", default="ALL",
                                 items=(("ALL", "All Slots", ""), ("ACTIVE", "Active Slot", "")))
    show_advanced: BoolProperty(name="Advanced", default=False)

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Drop Folders", icon="FILE_FOLDER")
        row = box.row()
        row.template_list("RI_UL_roots", "", self, "roots", self, "roots_index", rows=2)
        col = row.column(align=True)
        col.operator("realindex.add_root", text="", icon="ADD")
        col.operator("realindex.remove_root", text="", icon="REMOVE")
        if 0 <= self.roots_index < len(self.roots):
            r = self.roots[self.roots_index]
            box.prop(r, "path")
            box.prop(r, "library_dir")
            box.prop(r, "pack")
        col = layout.column()
        col.use_property_split = True
        col.prop(self, "poll_interval")
        col.prop(self, "workers")
        col.prop(self, "origin_bottom")
        col.prop(self, "remove_lights")
        col.prop(self, "mapping")
        col.prop(self, "keep_lods")
        col.prop(self, "preview_size")
        col.prop(self, "preview_engine")
        col.prop(self, "inbox_autofile")
        col.prop(self, "delete_mode")
        col.prop(self, "place_mode")
        col.prop(self, "place_align")
        col.prop(self, "place_random")
        col.prop(self, "material_slots")
        col.prop(self, "show_advanced")
        if self.show_advanced:
            col.prop(self, "ao_multiply")
            col.prop(self, "blend_mode")
            col.prop(self, "timeout")
        box = layout.box()
        box.label(text="Quick Place Shortcut (unassigned by default)", icon="KEYINGSET")
        from . import ops
        kc = context.window_manager.keyconfigs.user
        import rna_keymap_ui
        for km_name, idname in ops.KEYMAP_ITEMS:
            km = kc.keymaps.get(km_name)
            if km is None:
                continue
            for kmi in km.keymap_items:
                if kmi.idname == idname:
                    rna_keymap_ui.draw_kmi([], kc, km, kmi, box, 0)
                    break


_classes = (RI_Root, RI_UL_roots, RealIndexPreferences)


def get():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon is not None else None


def settings():
    from .core.engine import Settings
    p = get()
    if p is None:
        return Settings()
    return Settings(poll_interval=p.poll_interval, workers=p.workers, origin_bottom=p.origin_bottom,
                    remove_lights=p.remove_lights, mapping=p.mapping, keep_lods=p.keep_lods,
                    preview_size=int(p.preview_size), preview_engine=p.preview_engine,
                    inbox_autofile=p.inbox_autofile, delete_mode=p.delete_mode, ao_multiply=p.ao_multiply,
                    blend_mode=p.blend_mode, timeout=p.timeout)


def root_configs():
    """RootConfig for each configured drop root, with its Blender library label."""
    from .core.engine import RootConfig
    p = get()
    if p is None:
        return []
    out = []
    for r in p.roots:
        if not r.path:
            continue
        path = bpy.path.abspath(r.path)
        state = bpy.path.abspath(r.library_dir) if r.library_dir else ""
        out.append(RootConfig(path, state, r.pack, r.enabled))
    used = 0
    for c in out:
        if not c.enabled:
            continue
        c.label = "RealIndex" if used == 0 else "RealIndex (%s)" % (os.path.basename(os.path.normpath(c.path)) or c.path)
        used += 1
    return out


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
