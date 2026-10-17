"""Measure the performance budgets on real downloads with the production service (thread, 2 s polls).

    python tests/corpus/fetch.py        # once: real CC0 downloads
    python scripts/perf.py [--skip-batch]

"Visible" means the asset's .blend is in the managed library (the UI timer refreshes open Asset Browsers
within 0.5 s of that). Results go to tests/_out/perf.md.
"""
import argparse
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
from perf_log import record  # noqa: E402
from realindex.core import engine as E, fsutil, service  # noqa: E402

OUT = os.path.join(ROOT, "tests", "_out")
CORPUS = os.path.join(OUT, "corpus")
DOWN = os.path.join(OUT, "downloads")


def fresh(name):
    p = os.path.join(OUT, "perf", name)
    if os.path.isdir(p):
        shutil.rmtree(fsutil.long(p, force=True))
    os.makedirs(p)
    return p


def copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copyfile(src, dst)


def start(root, state, workers=4):
    os.makedirs(root, exist_ok=True)
    svc = service.Service(blender_bin(), E.Settings(workers=workers, inbox_autofile=False, poll_interval=2.0))
    svc.start([E.RootConfig(root, state)])
    time.sleep(2.5)  # first poll done (nothing yet)
    return svc


def wait_visible(svc, state, n, timeout=600):
    """Seconds until n assets are ok with their file on disk (or no work is left)."""
    import sqlite3
    t0 = time.time()
    dbp = os.path.join(state, "index.db")
    while time.time() - t0 < timeout:
        time.sleep(0.2)
        if not os.path.exists(dbp):
            continue
        con = sqlite3.connect(dbp, timeout=5)
        try:
            rows = con.execute("SELECT managed, status, type FROM assets").fetchall()
        except sqlite3.OperationalError:
            continue
        finally:
            con.close()
        ok = [r for r in rows if r[1] == "ok" and (not r[0] or os.path.exists(os.path.join(state, "library",
                                                                                            *r[0].split("/"))))]
        if len(ok) >= n:
            return time.time() - t0, rows
        snap = svc.snapshot()
        if rows and all(r[1] in ("ok", "problem", "ignored") for r in rows) and snap and \
                all(s["converting"] == 0 and s["waiting"] == 0 for s in snap.values()):
            return time.time() - t0, rows
    return float("inf"), []


def drop_one(label, src, rel, budget, note):
    base = fresh(label.split(":")[0])
    root, state, stage = os.path.join(base, "root"), os.path.join(base, "state"), os.path.join(base, "stage")
    copy(src, os.path.join(stage, os.path.basename(rel)))
    svc = start(root, state)
    try:
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        t = time.time()
        os.rename(os.path.join(stage, os.path.basename(rel)), os.path.join(root, rel))  # the "drop"
        dt, rows = wait_visible(svc, state, 1)
        dt = time.time() - t if dt != float("inf") else dt
    finally:
        svc.stop()
    print("%-60s %6.1f s  %s" % (label, dt, rows))
    record("Drop to visible: " + label, dt, "s", budget, note)
    return dt


def batch50():
    base = fresh("batch50")
    root, state, stage = os.path.join(base, "root"), os.path.join(base, "state"), os.path.join(base, "stage")
    items = []
    acg = sorted(os.listdir(os.path.join(CORPUS, "ambientcg")))
    ph = sorted(os.listdir(os.path.join(CORPUS, "polyhaven")))
    hd = sorted(os.listdir(os.path.join(CORPUS, "hdris")))
    for k in range(3):
        for z in acg:
            items.append((os.path.join(CORPUS, "ambientcg", z), "Materials/Set%d/%s" % (k, z)))
        for f in ph:
            items.append((os.path.join(CORPUS, "polyhaven", f), "Materials/PH%d/%s" % (k, f)))
        for h in hd:
            items.append((os.path.join(CORPUS, "hdris", h), "HDRIs/Set%d/%s" % (k, h)))
    models = [("market_fbx", "Models/MedievalPack"), ("obj_mtl", "Models/Furniture/Table"),
              ("usdz", "Models/Lights/Lamp.usdz"), ("glb", "Models/Props/Vase.glb"), ("abc", "Models/Anim/Spinner.abc"),
              ("stl_ply", "Models/Parts"), ("sketchfab_zip", "Models/Furniture/wooden_chair.zip"),
              ("big_gltf", "Models/Statues/stone_statue.zip")]
    k = 0
    while len(items) < 50:
        fid, rel = models[k % len(models)]
        items.append((os.path.join(DOWN, fid, *rel.split("/")), "Models/Batch%d/%s" % (k, rel.split("/", 1)[1])))
        k += 1
    items = items[:50]
    for src, rel in items:
        copy(src, os.path.join(stage, *rel.split("/")))
    expected = 0
    for src, rel in items:  # assets per download (packs hold two models, Parts holds two)
        expected += 2 if rel.endswith(("MedievalPack", "Parts")) else 1
    svc = start(root, state, 4)
    try:
        t = time.time()
        for top in os.listdir(stage):  # drop all 50 at once (retry while antivirus holds fresh copies)
            for attempt in range(60):
                try:
                    os.rename(os.path.join(stage, top), os.path.join(root, top))
                    break
                except PermissionError:
                    time.sleep(0.5)
        dt, rows = wait_visible(svc, state, expected, timeout=900)
        dt = time.time() - t if dt != float("inf") else dt
    finally:
        svc.stop()
    ok = sum(1 for r in rows if r[1] == "ok")
    probs = [r for r in rows if r[1] == "problem"]
    print("50 downloads: %d assets ok, %d problems, %.1f s" % (ok, len(probs), dt))
    record("50 mixed downloads, 4 workers (%d assets)" % ok, dt, "s", 300, "%d problems" % len(probs))
    lib = fsutil.dir_size(os.path.join(state, "library"))
    drop = fsutil.dir_size(root)
    record("Managed library size vs drop folder (50 real downloads)", 100.0 * lib / drop, "%", 5,
           "%s library, %s drop" % (fsutil.human(lib), fsutil.human(drop)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-batch", action="store_true")
    ap.add_argument("--batch-only", action="store_true")
    a = ap.parse_args()
    if not os.path.isdir(CORPUS):
        raise SystemExit("run tests/corpus/fetch.py first")
    from fixtures import layout
    layout.ensure_downloads()
    if not os.path.isdir(os.path.join(DOWN, "big_gltf")):
        import subprocess
        subprocess.run([blender_bin(), "-b", "--factory-startup", "--python",
                        os.path.join(ROOT, "tests", "fixtures", "make_downloads.py"), "--", DOWN, "--only", "big_gltf"],
                       check=True, stdout=subprocess.DEVNULL)
    if not a.batch_only: drop_one("ambientCG 2K zip (vendor preview)", os.path.join(CORPUS, "ambientcg", "Bricks076C_2K-JPG.zip"),
             "Materials/Bricks076C_2K-JPG.zip", 10, "Bricks076C, 30 MB")
    if not a.batch_only: drop_one("Poly Haven 2K texture set (rendered preview)", os.path.join(CORPUS, "polyhaven", "rock_wall_08_2k"),
             "Materials/rock_wall_08_2k", 20, "6 maps incl. ARM and 16-bit disp")
    if not a.batch_only: drop_one("Sketchfab-style glTF, 50k triangles", os.path.join(DOWN, "big_gltf", "Models", "Statues", "stone_statue.zip"),
             "Models/stone_statue.zip", 30, "generated: 50,040 tris, two 2K PNG textures, 15 MB zip")
    if not a.batch_only: drop_one("HDRI 4K", os.path.join(CORPUS, "hdris", "kloofendal_48d_partly_cloudy_puresky_4k.hdr"),
             "HDRIs/kloofendal_48d_partly_cloudy_puresky_4k.hdr", 10, "Poly Haven 4K .hdr, 20 MB")
    if not a.skip_batch:
        batch50()
    print(open(os.path.join(OUT, "perf.md"), encoding="utf-8").read())


if __name__ == "__main__":
    main()
