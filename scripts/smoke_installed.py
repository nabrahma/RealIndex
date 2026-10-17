"""Smoke test of the packaged extension (run inside a Blender whose BLENDER_USER_RESOURCES is a throwaway
folder where dist/realindex-1.0.0.zip was installed): enable it, convert one fixture with the installed
worker, check the asset file.

    python scripts/smoke_installed.py        (does the install and the run)
"""
import os
import subprocess
import sys

if "bpy" not in sys.modules:  # outer process
    HERE = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(HERE)
    sys.path.insert(0, HERE)
    from _common import blender_bin
    cfg = os.path.join(ROOT, "tests", "_out", "smoke_config")
    env = dict(os.environ, BLENDER_USER_RESOURCES=cfg)
    subprocess.run([blender_bin(), "--factory-startup", "--command", "extension", "install-file", "-r", "user_default",
                    "--enable", os.path.join(ROOT, "dist", "realindex-1.0.0.zip")], env=env, check=True)
    r = subprocess.run([blender_bin(), "--background", "--python", os.path.abspath(__file__)], env=env,
                       capture_output=True, text=True)
    print("\n".join(l for l in r.stdout.splitlines() if l.startswith("SMOKE")))
    sys.exit(0 if "SMOKE ok" in r.stdout else 1)

import shutil  # noqa: E402
import time  # noqa: E402

import addon_utils  # noqa: E402
import bpy  # noqa: E402

MOD = "bl_ext.user_default.realindex"
addon_utils.enable(MOD, default_set=True)
pkg = sys.modules[MOD]
print("SMOKE installed at", os.path.dirname(pkg.__file__))
core = sys.modules[MOD + ".core.engine"]
jobs = sys.modules[MOD + ".core.jobs"]
print("SMOKE worker", jobs.WORKER, os.path.exists(jobs.WORKER))
base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "_out", "smoke_run")
shutil.rmtree(base, ignore_errors=True)
root, state = os.path.join(base, "root"), os.path.join(base, "state")
src = os.path.join(os.path.dirname(base), "downloads", "acg_zip")
shutil.copytree(src, root)
eng = core.Engine(core.RootConfig(root, state), core.Settings(inbox_autofile=False))
pool = jobs.Pool(bpy.app.binary_path, 1)
eng.sync_now()
t0 = time.time()
while time.time() - t0 < 120:
    pool.step([eng])
    if not pool.running and not eng.db.jobs("waiting"):
        break
    time.sleep(0.2)
a = eng.db.assets()[0]
ok = a["status"] == "ok" and os.path.exists(os.path.join(state, "library", *a["managed"].split("/")))
print("SMOKE", "ok" if ok else "FAILED", a["name"], a["status"], a["problem"])
