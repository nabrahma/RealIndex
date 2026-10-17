# SPDX-License-Identifier: GPL-3.0-or-later
"""Session properties (Quick Place filters, the tag field). Nothing RealIndex needs is stored in .blend files
except 'ri_asset_id' on appended data and the previous world name on the scene."""

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup

_enum_cache = {"catalog": [], "vendor": []}


def _redraw(self, context):
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            area.tag_redraw()


def _catalog_items(self, context):
    from . import runtime
    cats = sorted({r["catalog"] for r in runtime.records() if r["catalog"]})
    items = [("", "All Catalogs", "")] + [(c, c, "") for c in cats]
    _enum_cache["catalog"] = items
    return items


def _vendor_items(self, context):
    from . import runtime
    vs = sorted({r["vendor"] for r in runtime.records() if r["vendor"]})
    items = [("", "All Vendors", "")] + [(v, v, "") for v in vs]
    _enum_cache["vendor"] = items
    return items


class RI_WMProps(PropertyGroup):
    search: StringProperty(name="Search", options={"TEXTEDIT_UPDATE"}, update=_redraw,
                           description="Words matching names, tags, catalogs or vendors")
    kind: EnumProperty(name="Type", items=(("ALL", "All", ""), ("MODELS", "Models", "", "MESH_DATA", 1),
                                           ("MATERIALS", "Materials", "", "MATERIAL", 2),
                                           ("HDRIS", "HDRIs", "", "WORLD", 3)), default="ALL", update=_redraw)
    catalog: EnumProperty(name="Catalog", items=_catalog_items, update=_redraw)
    vendor: EnumProperty(name="Vendor", items=_vendor_items, update=_redraw)
    recent: BoolProperty(name="Recent", default=False, update=_redraw)
    favourites: BoolProperty(name="Favourites", default=False, update=_redraw)
    replace: BoolProperty(name="Replace Selected", default=False,
                          description="Swap the selected objects for the chosen model, keeping transforms")
    tag_input: StringProperty(name="Tag", default="")


def register():
    bpy.utils.register_class(RI_WMProps)
    bpy.types.WindowManager.realindex = PointerProperty(type=RI_WMProps)


def unregister():
    del bpy.types.WindowManager.realindex
    bpy.utils.unregister_class(RI_WMProps)
