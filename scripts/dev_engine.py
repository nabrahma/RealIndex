"""Run the RealIndex engine on a folder until the queue is empty (no Blender UI; real worker processes).

    python scripts/dev_engine.py                    # fresh copy of the merged fixtures, fresh state
    python scripts/dev_engine.py ROOT [STATE]       # any root (state defaults to tests/_out/dev_state)
"""
import json
import os
import shutil
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "scripts")]
if "realindex" not in sys.modules:
    pkg = types.ModuleType("realindex")
    pkg.__path__ = [os.path.join(ROOT, "realindex")]
    sys.modules["realindex"] = pkg

from _common import blender_bin  # noqa: E402
from realindex.core import engine as E, fsutil, jobs  # noqa: E402


def run(root, state, workers=4, settings=None, quiet=False, timeout=600):
    eng = E.Engine(E.RootConfig(root, state), settings or E.Settings(workers=workers))
    pool = jobs.Pool(blender_bin(), workers)
    t0 = time.time()
    eng.sync_now()
    while time.time() - t0 < timeout:
        pool.step([eng])
        snap = eng.snapshot(pool.running)
        if not pool.running and snap["waiting"] == 0:
            break
        time.sleep(0.2)
    dt = time.time() - t0
    if not quiet:
        for a in eng.db.assets():
            w = json.loads(a["warnings"] or "[]")
            print("%-8s %-8s %-38s %-30r %s %s" % (a["status"], a["type"], a["catalog"], a["name"],
                                                   a["problem"] or "", ("WARN " + "; ".join(w)) if w else ""))
        print("done in %.1f s" % dt)
    return eng, pool, dt


def main():
    out = os.path.join(ROOT, "tests", "_out")
    if len(sys.argv) > 1:
        root = os.path.abspath(sys.argv[1])
        state = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(out, "dev_state")
    else:
        from fixtures import layout
        layout.ensure_downloads()
        root, state = os.path.join(out, "dev_root"), os.path.join(out, "dev_state")
        for p in (root, state):
            if os.path.isdir(p):
                shutil.rmtree(fsutil.long(p, force=True))
        layout.merge(root)
    run(root, state)


if __name__ == "__main__":
    main()
