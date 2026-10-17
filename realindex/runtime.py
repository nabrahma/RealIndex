# SPDX-License-Identifier: GPL-3.0-or-later
"""Glue between Blender's main thread and the background service.

A bpy timer (every 0.5 s) only drains events and snapshots from the service thread: refreshes open Asset
Browsers after finished conversions (throttled) and redraws the panels. All scanning, indexing and job
handling happens on the service thread or in worker processes (well under 50 ms per tick)."""

import os
import time

import bpy

from . import blender_lib, prefs
from .core import api as core_api
from .core import service as S

_service = None
_state = {"lib_dirty": False, "last_refresh": 0.0, "last_snap": None, "records": None, "records_time": 0.0,
          "reconf": False, "tick_ms": 0.0, "max_tick_ms": 0.0}


def service():
    return _service


def is_running():
    return _service is not None


def start():
    global _service
    if _service is not None:
        return
    roots = prefs.root_configs()
    try:
        blender_lib.ensure_libraries(roots)
    except Exception as e:
        print("[RealIndex] library registration failed:", e)
    _service = S.Service(bpy.app.binary_path, prefs.settings())
    _service.start(roots)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)


def stop():
    global _service
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if _service is not None:
        _service.stop()
        _service = None


def reconfigure_soon():
    if not _state["reconf"]:
        _state["reconf"] = True
        bpy.app.timers.register(_reconfigure, first_interval=0.4)


def _reconfigure():
    _state["reconf"] = False
    roots = prefs.root_configs()
    try:
        blender_lib.ensure_libraries(roots)
    except Exception as e:
        print("[RealIndex] library registration failed:", e)
    if _service is None:
        start()
    else:
        _service.reconfigure(roots, prefs.settings())
    _state["records"] = None
    return None


def states():
    """[(root, state folder)] for every enabled root (works with or without the service running)."""
    return [(r.path, r.state) for r in prefs.root_configs() if r.enabled]


def command(root, name, *args, **kw):
    if _service is not None:
        _service.command(root, name, *args, **kw)


def snapshot():
    return _service.snapshot() if _service is not None else {}


def errors():
    return list(_service.errors[-3:]) if _service is not None else []


def records(force=False):
    """All converted assets (cached, refreshed when the library changes) for Quick Place and the panel."""
    now = time.time()
    if force or _state["records"] is None or now - _state["records_time"] > 30.0:
        _state["records"] = core_api.load(states())
        _state["records_time"] = now
    return _state["records"]


def record_for_blend(path):
    """The asset record whose managed .blend is path (Asset Browser selection -> index)."""
    if not path:
        return None
    key = os.path.normcase(os.path.abspath(path))
    for r in records():
        if r["managed"] and os.path.normcase(r["managed"]) == key:
            return r
        if r["type"] == "blend" and not r["managed"] and os.path.normcase(r["source"]) == key:
            return r
    return None


def status_line():
    """Header text: 'RealIndex: 3 new, converting 1 of 3' / 'RealIndex: 1,284 assets'."""
    snaps = snapshot()
    if not snaps:
        return "RealIndex: no drop folder set" if not states() else "RealIndex: starting"
    total = sum(s["batch_total"] for s in snaps.values())
    done = sum(s["batch_done"] for s in snaps.values())
    conv = sum(s["converting"] for s in snaps.values())
    wait = sum(s["waiting"] for s in snaps.values())
    probs = sum(len(s["problems"]) for s in snaps.values())
    assets = sum(s["assets"] for s in snaps.values())
    offline = [s for s in snaps.values() if not s["online"]]
    if all(s["paused"] for s in snaps.values()):
        return "RealIndex: paused"
    parts = []
    if conv or wait:
        total = max(total, done + conv + wait)
        parts.append("%d new, converting %d of %d" % (total, min(done + 1, total), total))
    else:
        parts.append("{:,} assets".format(assets))
    if probs:
        parts.append("%d problem%s" % (probs, "" if probs == 1 else "s"))
    if offline:
        parts.append("%d offline" % len(offline))
    return "RealIndex: " + ", ".join(parts)


def _tick():
    t0 = time.perf_counter()
    try:
        if _service is None:
            return None
        events = _service.drain_events()
        if any(e in ("library", "changed", "moved") for e in events):
            _state["lib_dirty"] = True
            _state["records"] = None
        snaps = _service.snapshot()
        busy = any(s["converting"] or s["waiting"] for s in snaps.values())
        now = time.time()
        if _state["lib_dirty"] and (not busy or now - _state["last_refresh"] > 5.0):
            _state["lib_dirty"] = False
            _state["last_refresh"] = now
            blender_lib.refresh_browsers()
        key = repr([(s["assets"], s["converting"], s["waiting"], len(s["problems"]), len(s["missing"]), s["online"],
                     s["paused"], s["settling"], s["batch_done"], s["publish"]) for s in snaps.values()])
        if key != _state["last_snap"]:
            _state["last_snap"] = key
            blender_lib.redraw()
    except Exception as e:  # never let the timer die
        print("[RealIndex] timer:", e)
    dt = (time.perf_counter() - t0) * 1000.0
    _state["tick_ms"] = dt
    _state["max_tick_ms"] = max(_state["max_tick_ms"], dt)
    return 0.5
