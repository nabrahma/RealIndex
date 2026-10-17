# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators. Everything that changes the index is queued to the service thread (runtime.command);
placing assets and the Box toggle run here in the UI process (appending only, never importing)."""

import os
import subprocess
import sys

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import place, prefs, runtime

KEYMAP_ITEMS = (("3D View", "realindex.quick_place"),)
_keymaps = []


def _rec(asset_id, root=""):
    for r in runtime.records(force=True):
        if r["id"] == asset_id and (not root or os.path.normcase(r["root"]) == os.path.normcase(root)):
            return r
    return None


class _RootAsset:
    root: StringProperty(name="Root", default="", options={"HIDDEN", "SKIP_SAVE"})
    asset_id: StringProperty(name="Asset", default="", options={"HIDDEN", "SKIP_SAVE"})


# ------------------------------------------------------------------------------------------- setup

class RI_OT_setup(bpy.types.Operator):
    """Create the RealIndex drop folder with suggested subfolders and start watching it"""
    bl_idname = "realindex.setup"
    bl_label = "Set Up RealIndex"
    bl_options = {"REGISTER", "INTERNAL"}

    path: StringProperty(name="Drop Folder", subtype="DIR_PATH")

    def invoke(self, context, event):
        if not self.path:
            self.path = prefs.default_root()
        return context.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, context):
        p = prefs.get()
        path = bpy.path.abspath(self.path or prefs.default_root())
        os.makedirs(path, exist_ok=True)
        for sub in ("Models", "Materials", "HDRIs", "Inbox"):
            os.makedirs(os.path.join(path, sub), exist_ok=True)
        if p is not None and not any(os.path.normcase(bpy.path.abspath(r.path)) == os.path.normcase(path)
                                     for r in p.roots):
            r = p.roots.add()
            r.path = path
            p.roots_index = len(p.roots) - 1
        runtime.reconfigure_soon()
        self.report({"INFO"}, "RealIndex is watching %s" % path)
        return {"FINISHED"}


class RI_OT_add_root(bpy.types.Operator):
    """Add another drop folder (for example an external drive for big packs)"""
    bl_idname = "realindex.add_root"
    bl_label = "Add Drop Folder"
    bl_options = {"INTERNAL"}

    directory: StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        p = prefs.get()
        if p is None or not self.directory:
            return {"CANCELLED"}
        r = p.roots.add()
        r.path = self.directory
        p.roots_index = len(p.roots) - 1
        return {"FINISHED"}


class RI_OT_remove_root(bpy.types.Operator):
    """Stop watching the selected drop folder (its files and library folder are left on disk)"""
    bl_idname = "realindex.remove_root"
    bl_label = "Remove Drop Folder"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        p = prefs.get()
        if p is None or not (0 <= p.roots_index < len(p.roots)):
            return {"CANCELLED"}
        p.roots.remove(p.roots_index)
        p.roots_index = max(0, p.roots_index - 1)
        runtime.reconfigure_soon()
        return {"FINISHED"}


# ------------------------------------------------------------------------------------------- sync

class RI_OT_pause(bpy.types.Operator):
    """Pause or resume watching and converting"""
    bl_idname = "realindex.pause"
    bl_label = "Pause"
    bl_options = {"INTERNAL"}

    pause: BoolProperty(default=True)

    def execute(self, context):
        runtime.command(None, "pause", self.pause)
        return {"FINISHED"}


class RI_OT_sync_now(bpy.types.Operator):
    """Check the drop folders now"""
    bl_idname = "realindex.sync_now"
    bl_label = "Sync Now"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        if not runtime.is_running():
            runtime.start()
        runtime.command(None, "sync_all")
        return {"FINISHED"}


# --------------------------------------------------------------------------------------- problems

class RI_OT_retry(_RootAsset, bpy.types.Operator):
    """Try this asset again"""
    bl_idname = "realindex.retry"
    bl_label = "Retry"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        runtime.command(self.root, "retry", self.asset_id)
        return {"FINISHED"}


class RI_OT_reconvert(_RootAsset, bpy.types.Operator):
    """Convert this asset again from its source files"""
    bl_idname = "realindex.reconvert"
    bl_label = "Reconvert"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        runtime.command(self.root, "reconvert", self.asset_id)
        return {"FINISHED"}


class RI_OT_override(_RootAsset, bpy.types.Operator):
    """Override what RealIndex detected"""
    bl_idname = "realindex.override"
    bl_label = "Detect As"
    bl_options = {"INTERNAL"}

    value: EnumProperty(name="Detect As", items=(
        ("AUTO", "Automatic", "What RealIndex detected"),
        ("material", "Texture Set", "Treat as Texture Set"),
        ("model", "Model", "Treat as Model"),
        ("hdri", "HDRI", "Treat as HDRI"),
        ("ignore", "Ignore", "Ignore these files")))

    def execute(self, context):
        runtime.command(self.root, "set_override", self.asset_id, None if self.value == "AUTO" else self.value)
        return {"FINISHED"}


class RI_OT_show_source(_RootAsset, bpy.types.Operator):
    """Show the source file in the file manager"""
    bl_idname = "realindex.show_source"
    bl_label = "Show Source File"
    bl_options = {"INTERNAL"}

    path: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        path = self.path
        if not path:
            r = _rec(self.asset_id, self.root)
            path = r["source"] if r else ""
        if not path or not os.path.exists(path):
            self.report({"WARNING"}, "Source not found: %s" % path)
            return {"CANCELLED"}
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
        return {"FINISHED"}


class RI_OT_removals(bpy.types.Operator):
    """Remove the assets whose source files were deleted (one confirmation per batch)"""
    bl_idname = "realindex.confirm_removals"
    bl_label = "Remove Deleted Assets"
    bl_options = {"INTERNAL"}

    root: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        runtime.command(self.root, "confirm_removals")
        return {"FINISHED"}


class RI_OT_undo_inbox(bpy.types.Operator):
    """Move the last batch of auto-filed Inbox items back into Inbox"""
    bl_idname = "realindex.undo_inbox"
    bl_label = "Undo Inbox Filing"
    bl_options = {"INTERNAL"}

    root: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        runtime.command(self.root, "undo_inbox")
        return {"FINISHED"}


# ------------------------------------------------------------------------------------- tags, stars

class RI_OT_tag(_RootAsset, bpy.types.Operator):
    """Add or remove a tag (kept through reconverts and rebuilds)"""
    bl_idname = "realindex.tag"
    bl_label = "Tag"
    bl_options = {"INTERNAL"}

    tag: StringProperty()
    remove: BoolProperty(default=False)

    def execute(self, context):
        tag = self.tag or context.window_manager.realindex.tag_input
        if not tag.strip():
            return {"CANCELLED"}
        runtime.command(self.root, "remove_tag" if self.remove else "add_tag", self.asset_id, tag)
        context.window_manager.realindex.tag_input = ""
        return {"FINISHED"}


class RI_OT_favourite(_RootAsset, bpy.types.Operator):
    """Star or unstar this asset (Quick Place Favourites)"""
    bl_idname = "realindex.favourite"
    bl_label = "Favourite"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        r = _rec(self.asset_id, self.root)
        if r is None:
            return {"CANCELLED"}
        from .core import db
        idx = db.Index(os.path.join(r["state"], "index.db"))
        try:
            idx.set_favourite(r["id"], not r["favourite"])
            idx.commit()
        finally:
            idx.close()
        runtime.records(force=True)
        return {"FINISHED"}


# ------------------------------------------------------------------------------------- maintenance

class RI_OT_rebuild(bpy.types.Operator):
    """Delete the managed library and convert everything again from the drop folder (user tags, favourites
    and overrides are kept)"""
    bl_idname = "realindex.rebuild"
    bl_label = "Rebuild Library"
    bl_options = {"INTERNAL"}

    root: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        for r in prefs.root_configs():
            if r.enabled and (not self.root or os.path.normcase(r.path) == os.path.normcase(self.root)):
                runtime.command(r.path, "rebuild")
        return {"FINISHED"}


class RI_OT_clear_cache(bpy.types.Operator):
    """Remove extracted zips that are no longer needed"""
    bl_idname = "realindex.clear_cache"
    bl_label = "Clear Cache"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        for r in prefs.root_configs():
            runtime.command(r.path, "clear_cache")
            runtime.command(r.path, "disk_usage")
        return {"FINISHED"}


class RI_OT_disk_usage(bpy.types.Operator):
    """Measure disk use now"""
    bl_idname = "realindex.disk_usage"
    bl_label = "Measure Disk Use"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        for r in prefs.root_configs():
            runtime.command(r.path, "disk_usage")
        return {"FINISHED"}


class RI_OT_publish(bpy.types.Operator):
    """Write a packed copy of every asset and Blender's listing, ready to host as a remote library"""
    bl_idname = "realindex.publish"
    bl_label = "Publish as Remote Library"
    bl_options = {"INTERNAL"}

    directory: StringProperty(subtype="DIR_PATH")
    root: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        roots = prefs.root_configs()
        r = next((x for x in roots if x.enabled and (not self.root or x.path == self.root)), None)
        if r is None:
            return {"CANCELLED"}
        self.root = r.path
        self.directory = os.path.join(r.state, "publish") + os.sep
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.root or not self.directory:
            return {"CANCELLED"}
        runtime.command(self.root, "publish", bpy.path.abspath(self.directory))
        self.report({"INFO"}, "Publishing to %s" % self.directory)
        return {"FINISHED"}


# ------------------------------------------------------------------------------------------ material

class RI_OT_material_mapping(bpy.types.Operator):
    """Switch a RealIndex material between UV and Box mapping"""
    bl_idname = "realindex.material_mapping"
    bl_label = "Box Mapping"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(items=(("UV", "UV", ""), ("BOX", "Box", "")))

    @classmethod
    def poll(cls, context):
        ob = context.object
        mat = ob.active_material if ob else None
        return mat is not None and mat.node_tree is not None and mat.node_tree.nodes.get("RI Coord") is not None

    def execute(self, context):
        from .worker import build_material
        build_material.set_mapping(context.object.active_material, self.mode)
        return {"FINISHED"}


class RI_OT_setup_displacement(bpy.types.Operator):
    """Add an adaptive Subdivision modifier to the selected objects so Cycles renders the material's true
    displacement"""
    bl_idname = "realindex.setup_displacement"
    bl_label = "Set Up Displacement"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from .worker import build_material
        ob = context.object
        return ob is not None and build_material.has_displacement(ob.active_material)

    def execute(self, context):
        from .worker import build_material
        objs = context.selected_objects or [context.object]
        n = build_material.setup_displacement(objs)
        note = "" if context.scene.render.engine == "CYCLES" else " (displacement renders in Cycles)"
        self.report({"INFO"}, "Adaptive subdivision on %d object%s%s" % (n, "" if n == 1 else "s", note))
        return {"FINISHED"}


# ------------------------------------------------------------------------------------------- placing

class RI_OT_place(_RootAsset, bpy.types.Operator):
    """Place this asset: a model at the surface under the mouse or the 3D cursor, a material on the
    selected objects, an HDRI as the world"""
    bl_idname = "realindex.place"
    bl_label = "Place Asset"
    bl_options = {"REGISTER", "UNDO"}

    replace: BoolProperty(name="Replace Selected", default=False,
                          description="Swap the selected objects for this model, keeping their transforms")
    member: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    use_hit: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    hit_location: bpy.props.FloatVectorProperty(options={"HIDDEN", "SKIP_SAVE"})
    hit_normal: bpy.props.FloatVectorProperty(default=(0, 0, 1), options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        rec = _rec(self.asset_id, self.root)
        if rec is None:
            self.report({"WARNING"}, "Asset not found (still converting?)")
            return {"CANCELLED"}
        p = prefs.get()
        mode = p.place_mode if p else "INSTANCE"
        try:
            if rec["type"] == "material":
                targets = context.selected_objects or ([context.object] if context.object else [])
                m, n = place.apply_material(context, rec, targets, p.material_slots if p else "ALL")
                if not n:
                    self.report({"WARNING"}, "Select objects to apply %s to" % rec["name"])
                elif p is None or p.auto_subdivision:
                    from .worker import build_material
                    if build_material.has_displacement(m):
                        build_material.setup_displacement(targets)
            elif rec["type"] == "hdri":
                place.set_world(context, rec)
            elif self.replace and context.selected_objects:
                place.replace_selected(context, rec, mode)
            else:
                if self.use_hit:
                    m = place.placement_matrix(self.hit_location, self.hit_normal, p.place_align if p else True,
                                               p.place_random if p else False)
                else:
                    m = place.placement_matrix(context.scene.cursor.location, None, False,
                                               p.place_random if p else False)
                for ob in context.selected_objects:
                    ob.select_set(False)
                new = place.place_model(context, rec, m, mode, self.member or None)
                for ob in new:
                    ob.select_set(True)
                if new:
                    context.view_layer.objects.active = new[0]
        except Exception as e:
            self.report({"ERROR"}, "Could not place %s: %s" % (rec["name"], e))
            return {"CANCELLED"}
        _touch(rec)
        return {"FINISHED"}


def _touch(rec):
    from .core import db
    try:
        idx = db.Index(os.path.join(rec["state"], "index.db"))
        idx.touch(rec["id"])
        idx.commit()
        idx.close()
    except Exception:
        pass


class RI_OT_restore_world(bpy.types.Operator):
    """Put back the world that was active before the last HDRI was set"""
    bl_idname = "realindex.restore_world"
    bl_label = "Restore Previous World"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.scene.get(place.PREV_WORLD) is not None

    def execute(self, context):
        return {"FINISHED"} if place.restore_world(context) else {"CANCELLED"}


_classes = (RI_OT_setup, RI_OT_add_root, RI_OT_remove_root, RI_OT_pause, RI_OT_sync_now, RI_OT_retry,
            RI_OT_reconvert, RI_OT_override, RI_OT_show_source, RI_OT_removals, RI_OT_undo_inbox, RI_OT_tag,
            RI_OT_favourite, RI_OT_rebuild, RI_OT_clear_cache, RI_OT_disk_usage, RI_OT_publish,
            RI_OT_material_mapping, RI_OT_setup_displacement, RI_OT_place, RI_OT_restore_world)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new("realindex.quick_place", type="NONE", value="PRESS")  # unassigned by default
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _keymaps.clear()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
