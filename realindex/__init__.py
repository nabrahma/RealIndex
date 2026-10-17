# SPDX-License-Identifier: GPL-3.0-or-later
"""RealIndex: drop a download into one folder and it shows up in Blender's Asset Browser.

This file only registers and unregisters. core/ is pure Python (indexing, sync, jobs), worker/ runs inside
background Blender processes, and the rest is the add-on's UI side.
"""

import bpy

from . import ops, prefs, props, runtime, ui

_modules = (prefs, props, ops, ui)


def _start():
    if not bpy.app.background or bpy.app.driver_namespace.get("realindex_background_service"):
        runtime.start()
    return None


def register():
    for m in _modules:
        m.register()
    bpy.app.timers.register(_start, first_interval=1.0)


def unregister():
    runtime.stop()
    from . import blender_lib
    try:
        blender_lib.remove_libraries()
    except Exception:
        pass
    for m in reversed(_modules):
        m.unregister()
