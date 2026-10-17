# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender's side of the managed library: registering the libraries in Preferences and refreshing open
Asset Browsers after a batch.

Per drop root: 'RealIndex' (the managed library) and 'RealIndex Files' (the drop folder itself, where
Blender lists already-marked .blend files natively). Both import as Append: Blender 5.2.1 lists
Append (Reuse Data) but refuses it from Python for libraries, assets and browsers alike, so reuse is done by
Quick Place itself."""

import os

import bpy

from .core import fsutil

PREFIX = "RealIndex"


def wanted(roots):
    out = {}
    for r in roots:
        if not r.enabled:
            continue
        files = r.label.replace("RealIndex", "RealIndex Files", 1)
        out[r.label] = (os.path.join(r.state, "library"), "APPEND")
        out[files] = (r.path, "APPEND")
    return out


def ensure_libraries(roots):
    """Create, update or remove RealIndex's asset library entries to match the configured roots."""
    libs = bpy.context.preferences.filepaths.asset_libraries
    want = wanted(roots)
    for r in roots:
        if r.enabled:
            fsutil.ensure_state_dir(r.state, r.path)
    for lib in list(libs):
        if lib.name.startswith(PREFIX) and lib.name not in want:
            libs.remove(lib)
    for name, (path, method) in want.items():
        lib = libs.get(name) if hasattr(libs, "get") else next((l for l in libs if l.name == name), None)
        if lib is None:
            lib = libs.new(name=name, directory=path)
        elif fsutil.norm(bpy.path.abspath(lib.path)) != fsutil.norm(path):
            lib.path = path
        if lib.import_method != method:
            lib.import_method = method
    return want


def remove_libraries():
    libs = bpy.context.preferences.filepaths.asset_libraries
    for lib in list(libs):
        if lib.name.startswith(PREFIX):
            libs.remove(lib)


def asset_browsers():
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == "FILE_BROWSER" and area.ui_type == "ASSETS":
                yield win, area


def refresh_browsers():
    """Reread the libraries in every open Asset Browser (no restart needed)."""
    n = 0
    for win, area in asset_browsers():
        try:
            with bpy.context.temp_override(window=win, screen=win.screen, area=area):
                bpy.ops.asset.library_refresh()
            n += 1
        except Exception:
            pass
    return n


def redraw(kinds=("FILE_BROWSER", "VIEW_3D")):
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type in kinds:
                area.tag_redraw()
