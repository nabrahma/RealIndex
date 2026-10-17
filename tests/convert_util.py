"""Run an engine with the real worker pool to completion (Blender suites share one converted fixture root)."""

import os
import time

import bpy

from conftest import fresh_dir
from fixtures import layout
from realindex.core import engine as E
from realindex.core import jobs

_cache = {}


def run(eng, pool, timeout=900):
    t0 = time.time()
    eng.sync_now()
    while time.time() - t0 < timeout:
        eng.tick()
        pool.step([eng])
        if not pool.running and not eng.db.jobs("waiting"):
            break
        time.sleep(0.1)
    return time.time() - t0


def converted(name="converted", ids=None, settings=None):
    """(engine, pool, root, state, seconds) for the merged fixtures converted with real workers (cached)."""
    key = (name, tuple(ids or ()))
    if key in _cache:
        return _cache[key]
    layout.ensure_downloads()
    base = fresh_dir(name)
    root, state = os.path.join(base, "root"), os.path.join(base, "state")
    layout.merge(root, ids)
    s = settings or E.Settings(inbox_autofile=False, workers=4)
    eng = E.Engine(E.RootConfig(root, state), s)
    pool = jobs.Pool(bpy.app.binary_path, s.workers)
    dt = run(eng, pool)
    _cache[key] = (eng, pool, root, state, dt)
    return _cache[key]


def managed_path(state, a):
    return os.path.join(state, "library", *a["managed"].split("/"))


def append(path, attr, name=None):
    with bpy.data.libraries.load(path, link=False, assets_only=True) as (src, dst):
        names = list(getattr(src, attr))
        setattr(dst, attr, [name] if name in names else names[:1])
    return getattr(dst, attr)[0] if getattr(dst, attr) else None
