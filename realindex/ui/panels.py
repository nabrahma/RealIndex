# SPDX-License-Identifier: GPL-3.0-or-later
"""The RealIndex panel (3D Viewport sidebar and Asset Browser sidebar) and the Asset Browser header status
line. Status, problems and settings only: the Asset Browser is the browsing UI."""

import os
import textwrap

import bpy

from .. import prefs, runtime
from ..core import fsutil


def _wrap(layout, text, width=48, icon="NONE"):
    lines = textwrap.wrap(text, width) or [""]
    col = layout.column(align=True)
    col.scale_y = 0.8
    for i, line in enumerate(lines):
        col.label(text=line, icon=icon if i == 0 else "BLANK1" if icon != "NONE" else "NONE")


def _selected_record(context):
    asset = getattr(context, "asset", None)
    if asset is not None:
        path = getattr(asset, "full_library_path", "") or ""
        rec = runtime.record_for_blend(path)
        if rec is not None:
            return rec
    ob = context.object
    aid = None
    if ob is not None:
        aid = ob.get("ri_asset_id")
        if aid is None and ob.instance_collection is not None:
            aid = ob.instance_collection.get("ri_asset_id")
        if aid is None and ob.active_material is not None:
            aid = ob.active_material.get("ri_asset_id")
    if aid is None and context.scene and context.scene.world is not None:
        aid = context.scene.world.get("ri_asset_id")
    if aid:
        return next((r for r in runtime.records() if r["id"] == aid), None)
    return None


def draw_setup(layout):
    box = layout.box()
    box.label(text="Set up RealIndex", icon="INFO")
    _wrap(box, "Pick one folder to drop downloads into. RealIndex creates Models, Materials, HDRIs and Inbox "
               "in it and turns everything you drop there into Asset Browser assets.")
    box.operator("realindex.setup", icon="NEWFOLDER").path = prefs.default_root()


def draw_main(layout, context):
    snaps = runtime.snapshot()
    roots = prefs.root_configs()
    if not roots:
        draw_setup(layout)
        return
    paused = snaps and all(s["paused"] for s in snaps.values())
    row = layout.row(align=True)
    if paused:
        row.operator("realindex.pause", text="Resume", icon="PLAY").pause = False
    else:
        row.operator("realindex.pause", text="Pause", icon="PAUSE").pause = True
    row.operator("realindex.sync_now", text="Sync Now", icon="FILE_REFRESH")
    row.operator("realindex.quick_place", text="", icon="VIEWZOOM")

    total = sum(s["assets"] for s in snaps.values())
    conv = sum(s["converting"] for s in snaps.values())
    box = layout.box()
    box.label(text="{} root{} · {:,} assets · {} converting".format(len(snaps), "" if len(snaps) == 1 else "s",
                                                                 total, conv), icon="ASSET_MANAGER")
    for r in roots:
        s = snaps.get(r.path)
        row = box.row()
        row.label(text=r.path if len(r.path) < 34 else "..." + r.path[-31:], icon="FILE_FOLDER")
        if not r.enabled:
            row.label(text="disabled")
        elif s is None:
            row.label(text="starting")
        else:
            state = "offline" if not s["online"] else ("paused" if s["paused"] else "OK")
            row.label(text="{:,} assets   {}".format(s["assets"], state))
            if s["settling"]:
                box.label(text="   %d file(s) still arriving" % s["settling"], icon="TIME")
            if not s["online"]:
                _wrap(box, "Drop folder offline: the library stays browsable; textures from it show as "
                           "missing until it returns; conversions paused.", icon="ERROR")
            if s.get("publish"):
                _wrap(box, "Publish: " + s["publish"], icon="WORLD")
    for err in runtime.errors():
        _wrap(box, "Error: " + err.strip().splitlines()[-1], icon="ERROR")

    queue = [q for s in snaps.values() for q in s["queue"]]
    if queue:
        box = layout.box()
        box.label(text="Queue", icon="SORTTIME")
        for q in queue[:8]:
            row = box.row()
            row.label(text=q["name"][:30])
            row.label(text=q["state"] + ("   %d s" % q["seconds"] if q["state"] == "converting" else ""))

    problems = [(root, p) for root, s in snaps.items() for p in s["problems"]]
    if problems:
        box = layout.box()
        box.label(text="Problems (%d)" % len(problems), icon="ERROR")
        for root, p in problems[:20]:
            col = box.column(align=True)
            row = col.row(align=True)
            row.label(text=p["name"][:28], icon="FILE")
            op = row.operator("realindex.retry", text="", icon="FILE_REFRESH")
            op.root, op.asset_id = root, p["id"]
            op = row.operator("realindex.override", text="", icon="HIDE_ON")
            op.root, op.asset_id, op.value = root, p["id"], "ignore"
            op = row.operator("realindex.show_source", text="", icon="FILEBROWSER")
            op.root, op.path = root, os.path.join(root, *(p["source"] or "").split("/"))
            _wrap(col, p["problem"] or "", 46)
        if len(problems) > 20:
            box.label(text="... and %d more" % (len(problems) - 20))

    for root, s in snaps.items():
        if s["missing"]:
            box = layout.box()
            _wrap(box, "%d asset(s) lost their source files and are hidden: %s" % (
                len(s["missing"]), ", ".join(m["name"] for m in s["missing"][:5])), icon="TRASH")
            box.operator("realindex.confirm_removals", text="Remove Them", icon="TRASH").root = root

    rec = _selected_record(context)
    if rec is not None:
        draw_asset(layout, context, rec)

    ob = context.object
    mat = ob.active_material if ob is not None else None
    if mat is not None and mat.node_tree is not None and mat.node_tree.nodes.get("RI Coord") is not None:
        box = layout.box()
        box.label(text="Material: %s" % mat.name, icon="MATERIAL")
        row = box.row(align=True)
        cur = mat.get("ri_mapping", "UV")
        for mode, label in (("UV", "UV"), ("BOX", "Box")):
            row.operator("realindex.material_mapping", text=label, depress=(cur == mode)).mode = mode
    if context.scene is not None and context.scene.get("ri_previous_world"):
        layout.operator("realindex.restore_world", icon="LOOP_BACK")

    disk = {}
    for s in snaps.values():
        for k, v in s.get("disk", {}).items():
            disk[k] = disk.get(k, 0) + v
    box = layout.box()
    row = box.row(align=True)
    if disk:
        row.label(text="Disk: drop %s · cache %s · library %s" % (fsutil.human(disk.get("drop", 0)),
                                                                   fsutil.human(disk.get("cache", 0)),
                                                                   fsutil.human(disk.get("library", 0) +
                                                                                disk.get("previews", 0))))
    else:
        row.label(text="Disk: not measured yet")
    row.operator("realindex.disk_usage", text="", icon="FILE_REFRESH")
    row = box.row(align=True)
    row.operator("realindex.clear_cache", icon="TRASH")
    row.operator("realindex.undo_inbox", icon="LOOP_BACK").root = roots[0].path
    row = box.row(align=True)
    row.operator("realindex.rebuild", icon="FILE_REFRESH")
    row.operator("realindex.publish", text="Publish as Remote Library...", icon="WORLD")


def draw_asset(layout, context, rec):
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Selected asset: %s" % rec["name"], icon="ASSET_MANAGER")
    op = row.operator("realindex.favourite", text="", icon="SOLO_ON" if rec["favourite"] else "SOLO_OFF",
                      emboss=False)
    op.root, op.asset_id = rec["root"], rec["id"]
    kind = {"material": "Texture Set", "hdri": "HDRI", "model": "Model", "blend": "Blend File"}.get(rec["type"])
    _wrap(box, "Type %s · Source %s · Licence %s · %d tags" % (kind, rec["vendor"] or "Unknown",
                                                              rec["license"] or "unknown", len(rec["tags"])))
    if rec.get("maps"):
        _wrap(box, "Maps: " + ", ".join(sorted(rec["maps"])))
    for w in rec.get("warnings", [])[:4]:
        _wrap(box, w, icon="ERROR")
    row = box.row(align=True)
    row.label(text="Detected as")
    op = row.operator_menu_enum("realindex.override", "value", text=kind)
    row = box.row(align=True)
    op = row.operator("realindex.reconvert", icon="FILE_REFRESH")
    op.root, op.asset_id = rec["root"], rec["id"]
    op = row.operator("realindex.show_source", icon="FILEBROWSER")
    op.root, op.asset_id = rec["root"], rec["id"]
    flow = box.column_flow(columns=3, align=True)
    for t in rec["tags"][:24]:
        op = flow.operator("realindex.tag", text=t, icon="X")
        op.root, op.asset_id, op.tag, op.remove = rec["root"], rec["id"], t, True
    row = box.row(align=True)
    row.prop(context.window_manager.realindex, "tag_input", text="", icon="BOOKMARKS")
    op = row.operator("realindex.tag", text="Add Tag")
    op.root, op.asset_id = rec["root"], rec["id"]


class RI_PT_view3d(bpy.types.Panel):
    bl_label = "RealIndex"
    bl_idname = "RI_PT_view3d"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RealIndex"

    def draw(self, context):
        draw_main(self.layout, context)


class RI_PT_asset_browser(bpy.types.Panel):
    bl_label = "RealIndex"
    bl_idname = "RI_PT_asset_browser"
    bl_space_type = "FILE_BROWSER"
    bl_region_type = "TOOL_PROPS"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        sp = context.space_data
        return sp is not None and getattr(sp, "browse_mode", "") == "ASSETS"

    def draw(self, context):
        draw_main(self.layout, context)


def draw_header(self, context):
    sp = context.space_data
    if getattr(sp, "browse_mode", "") != "ASSETS":
        return
    text = runtime.status_line()
    snaps = runtime.snapshot()
    busy = any(s["converting"] or s["waiting"] for s in snaps.values())
    self.layout.label(text=text, icon="SORTTIME" if busy else "ASSET_MANAGER")


_classes = (RI_PT_view3d, RI_PT_asset_browser)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.FILEBROWSER_HT_header.append(draw_header)


def unregister():
    bpy.types.FILEBROWSER_HT_header.remove(draw_header)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)

