# SPDX-License-Identifier: GPL-3.0-or-later
"""Quick Place: a popup with a search field and a thumbnail grid. Typing matches names and
tags (ranked by match quality, then recent use); click a thumbnail to place it."""

import bpy
import bpy.utils.previews

from .. import place, runtime
from ..core import search

TYPES = {"ALL": None, "MODELS": {"model", "blend"}, "MATERIALS": {"material"}, "HDRIS": {"hdri"}}
_cache = {"records": None, "items": [], "pcoll": None}
COLUMNS, LIMIT = 6, 24


def _items():
    recs = runtime.records()
    if recs is not _cache["records"]:
        items = []
        for r in recs:
            if r["type"] == "blend":
                for m in r["members"] or []:
                    d = dict(r)
                    d["name"], d["member"] = m, m
                    items.append(search.Item(d))
                continue
            items.append(search.Item(r))
        _cache["records"], _cache["items"] = recs, items
    return _cache["items"]


def _icon(rec):
    if not rec.get("preview"):
        return {"material": "MATERIAL", "hdri": "WORLD"}.get(rec["type"], "MESH_DATA"), 0
    if _cache["pcoll"] is None:
        _cache["pcoll"] = bpy.utils.previews.new()
    pc = _cache["pcoll"]
    key = rec["preview"]
    if key not in pc:
        pc.load(key, key, "IMAGE")
    return None, pc[key].icon_id


def results(props):
    return search.search(_items(), props.search, TYPES[props.kind], props.catalog, props.vendor, props.recent,
                         props.favourites, LIMIT)


class RI_OT_quick_place(bpy.types.Operator):
    """Search your RealIndex library and place an asset (set a shortcut in the RealIndex preferences)"""
    bl_idname = "realindex.quick_place"
    bl_label = "Quick Place"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        self.hit = None
        area, region = context.area, context.region
        if area is not None and area.type == "VIEW_3D" and region is not None and region.type == "WINDOW":
            try:
                self.hit = place.surface_hit(context, region, area.spaces.active.region_3d,
                                             (event.mouse_region_x, event.mouse_region_y))
            except Exception:
                self.hit = None
        runtime.records(force=True)
        return context.window_manager.invoke_popup(self, width=COLUMNS * 120)

    def draw(self, context):
        props = context.window_manager.realindex
        layout = self.layout
        row = layout.row(align=True)
        row.activate_init = True
        row.prop(props, "search", text="", icon="VIEWZOOM")
        row = layout.row(align=True)
        row.prop(props, "kind", expand=True)
        row.separator()
        row.prop(props, "recent", toggle=True, icon="TIME")
        row.prop(props, "favourites", toggle=True, icon="SOLO_ON")
        row = layout.row(align=True)
        row.prop(props, "catalog", text="")
        row.prop(props, "vendor", text="")
        row.prop(props, "replace", toggle=True, icon="FILE_REFRESH")
        res = results(props)
        if not res:
            layout.label(text="No assets match" if _items() else "No assets yet: drop files into your RealIndex folder",
                         icon="INFO")
            return
        grid = layout.grid_flow(row_major=True, columns=COLUMNS, even_columns=True, even_rows=True, align=False)
        for it in res:
            rec = it.data
            col = grid.column(align=True)
            icon, icon_id = _icon(rec)
            if icon_id:
                col.template_icon(icon_value=icon_id, scale=5.0)
            else:
                col.label(text="", icon=icon)
            row = col.row(align=True)
            op = row.operator("realindex.place", text=rec["name"][:18])
            op.asset_id, op.root = rec["id"], rec["root"]
            op.member = rec.get("member") or ""
            op.replace = props.replace
            if self.hit is not None:
                op.use_hit = True
                op.hit_location, op.hit_normal = self.hit
            star = row.operator("realindex.favourite", text="", icon="SOLO_ON" if rec["favourite"] else "SOLO_OFF",
                                emboss=False)
            star.asset_id, star.root = rec["id"], rec["root"]

    def execute(self, context):
        return {"FINISHED"}


def register():
    bpy.utils.register_class(RI_OT_quick_place)


def unregister():
    bpy.utils.unregister_class(RI_OT_quick_place)
    if _cache["pcoll"] is not None:
        bpy.utils.previews.remove(_cache["pcoll"])
        _cache["pcoll"] = None
