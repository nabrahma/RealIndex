# SPDX-License-Identifier: GPL-3.0-or-later
"""Background worker entry point, run by core/jobs.py as:

    blender --background --factory-startup --disable-autoexec --python worker.py -- job.json

One job per process: 'convert' (a candidate asset into one managed .blend plus a preview), 'update' (rename,
move or retag an existing managed .blend without reconverting), 'publish' (packed copies for a remote
library). Writes <job>.result.json and exits. The drop folder is only read; writes go to the state folder.
"""

import json
import os
import sys
import time
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
if "realindex" not in sys.modules:  # import the package's modules without running its add-on __init__
    _pkg = types.ModuleType("realindex")
    _pkg.__path__ = [PKG]
    sys.modules["realindex"] = _pkg

import bpy  # noqa: E402

from realindex.core import fsutil  # noqa: E402
from realindex.worker import (build_material, build_world, import_models, metadata, normalise,  # noqa: E402
                              preview)


def _write(ids, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part.blend"
    bpy.data.libraries.write(tmp, set(ids), fake_user=True, path_remap="ABSOLUTE", compress=True)
    os.replace(tmp, path)


def _pack_all(warnings):
    for img in bpy.data.images:
        if img.packed_file is None and img.source == "FILE" and img.filepath:
            try:
                img.pack()
            except RuntimeError as e:
                warnings.append("Could not pack %s: %s" % (img.name, e))


def _preview(idb, job, fn, *args):
    """Run a preview function; a failed preview is a warning, never a failed conversion."""
    try:
        size, flat, *rest = fn(*args)
        preview.set_preview(idb, size, flat)
        return rest[0] if rest else "image"
    except Exception as e:
        job.setdefault("_warnings", []).append("Preview failed: %s" % e)
        return None


def convert(job):
    t = job["asset_type"]
    warnings = job.setdefault("_warnings", [])
    prefs = job.get("prefs", {})
    size = int(prefs.get("preview_size", 256))
    engine = prefs.get("preview_engine", "AUTO")
    info, extra = {}, []
    if t == "material":
        mat, wired = build_material.build(job["name"], job["maps"], job.get("size_m"), prefs.get("mapping", "UV"),
                                          prefs.get("ao_multiply", False))
        info["maps"] = wired
        vp = job.get("vendor_preview")
        if vp and os.path.exists(vp):
            used = _preview(mat, job, preview.from_image, vp, size, job["out_preview"])
        else:
            used = _preview(mat, job, preview.render_material, mat, size, engine, job["out_preview"])
        info["preview"] = used
        metadata.apply(mat, job, t)
        ids = [mat]
    elif t == "hdri":
        world = build_world.build(job["name"], job["key_real"])
        info["preview"] = _preview(world, job, preview.from_hdri, job["key_real"], size, job["out_preview"])
        metadata.apply(world, job, t)
        ids = [world]
    elif t == "model":
        ids, info, extra = convert_model(job, warnings, size, engine)
    elif t == "blend":
        return convert_blend(job, warnings, size, engine)
    else:
        raise RuntimeError("Unknown asset type %s" % t)
    if job.get("pack"):
        _pack_all(warnings)
    missing = import_models.missing_images()
    if missing:
        warnings.append("Missing textures: %s" % ", ".join(missing[:8]))
    _write(ids, job["out_blend"])
    return {"ok": True, "warnings": warnings, "info": info, "extra_tags": extra,
            "preview": job["out_preview"] if os.path.exists(job["out_preview"]) else None}


def convert_model(job, warnings, size, engine):
    prefs = job.get("prefs", {})
    objs = import_models.import_file(fsutil.long(job["key_real"]))
    objs = normalise.remove_helpers(objs, prefs)
    objs = normalise.lod_objects(objs, prefs.get("keep_lods", False))
    if not [o for o in objs if o.type in normalise.GEOMETRY]:
        raise RuntimeError("The file contains no geometry")
    relinked = import_models.fix_materials(objs, job.get("images", []), job["name"], warnings)
    lod_objs = []
    if prefs.get("keep_lods") and job.get("lods"):
        for i, p in enumerate(job["lods"]):
            try:
                more = import_models.import_file(p)
            except Exception as e:
                warnings.append("LOD %s not imported: %s" % (os.path.basename(p), e))
                continue
            more = normalise.remove_helpers(more, prefs)
            for o in more:
                o.hide_viewport = o.hide_render = True
            lod_objs += more
    objs, d, info = normalise.normalise(objs + lod_objs, job["name"], prefs, warnings)
    info["relinked"] = relinked
    info["objects"] = len(objs)
    if job.get("lods"):
        info["lods"] = [os.path.basename(p) for p in job["lods"]]
    geo = [o for o in objs if o.type in normalise.GEOMETRY]
    col = bpy.data.collections.new(job["name"])
    for o in objs:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        col.objects.link(o)
    single = len(objs) == 1 and len(geo) == 1
    target = geo[0] if single else col
    if single:
        target.name = job["name"]
    info["asset_kind"] = "OBJECT" if single else "COLLECTION"
    info["preview"] = _preview(target, job, preview.render_collection, col, size, engine, job["out_preview"])
    extra = [normalise.size_class(d)]
    metadata.apply(target, job, "model", list(job.get("tags", [])) + extra)
    if single:
        bpy.data.collections.remove(col)
    return [target], info, extra


def convert_blend(job, warnings, size, engine):
    """Marked .blend files in the drop root are left to the RealIndex Files library; unmarked
    ones (and marked ones extracted from a zip) are written into a managed asset file."""
    src = job["key_real"]
    with bpy.data.libraries.load(src, assets_only=True) as (data_from, _data_to):
        marked = {attr: list(getattr(data_from, attr)) for attr in dir(data_from)
                  if not attr.startswith("_") and isinstance(getattr(data_from, attr), list) and getattr(data_from, attr)}
    count = sum(len(v) for v in marked.values())
    if count and job.get("visible_blend"):
        return {"ok": True, "no_file": True, "warnings": warnings,
                "info": {"marked": count, "names": sorted(n for v in marked.values() for n in v)[:50]}}
    bpy.ops.wm.open_mainfile(filepath=src, load_ui=False, use_scripts=False)
    info = {}
    if count:
        ids = metadata.asset_ids()
        for idb in ids:
            metadata.apply(idb, job, "blend", list(idb.asset_data.tags.keys()) + list(job.get("tags", [])))
        info["marked"] = len(ids)
    else:
        mode = job.get("prefs", {}).get("blend_mode", "COLLECTIONS")
        ids = _unmarked_targets(mode, job["name"])
        if not ids:
            raise RuntimeError("Nothing to turn into an asset (no %s)" % mode.lower())
        first = True
        for idb in ids:
            metadata.apply(idb, job, "blend")
            if isinstance(idb, bpy.types.Collection):
                out = job["out_preview"] if first else os.path.join(os.path.dirname(job["out_preview"]),
                                                                      "%s_%s.png" % (job["asset_id"], idb.name))
                _preview(idb, job, preview.render_collection, idb, size, engine, out)
            elif isinstance(idb, bpy.types.Material):
                _preview(idb, job, preview.render_material, idb, size, engine,
                         job["out_preview"] if first else job["out_preview"] + ".tmp.png")
            first = False
        info["converted"] = [i.name for i in ids][:50]
    if job.get("pack"):
        _pack_all(warnings)
    _write(ids, job["out_blend"])
    return {"ok": True, "warnings": warnings, "info": info,
            "preview": job["out_preview"] if os.path.exists(job["out_preview"]) else None}


def _unmarked_targets(mode, name):
    if mode == "OBJECTS":
        return [o for o in bpy.data.objects if o.parent is None and o.type in normalise.GEOMETRY]
    if mode == "MATERIALS":
        return [m for m in bpy.data.materials if m.users and not m.is_grease_pencil]
    if mode == "NODE_GROUPS":
        return [g for g in bpy.data.node_groups if not g.name.startswith(".")]
    tops = []
    for sc in bpy.data.scenes:
        for c in sc.collection.children:
            if c not in tops:
                tops.append(c)
    if not tops:
        sc = bpy.data.scenes[0] if bpy.data.scenes else None
        roots = [o for o in (sc.collection.objects if sc else []) if o.parent is None and o.type in normalise.GEOMETRY]
        if roots:
            col = bpy.data.collections.new(name)
            for o in roots:
                col.objects.link(o)
            tops = [col]
    return tops


def update(job):
    """Rename / move / retag: reopen the managed file, remap texture paths, rewrite metadata, save anew."""
    bpy.ops.wm.open_mainfile(filepath=job["blend"], load_ui=False, use_scripts=False)
    remap = {os.path.normcase(os.path.abspath(k)): v for k, v in job.get("remap", {}).items()}
    n = 0
    for img in bpy.data.images:
        if img.packed_file is None and img.filepath:
            p = os.path.normcase(os.path.abspath(bpy.path.abspath(img.filepath)))
            if p in remap:
                img.filepath = remap[p]
                n += 1
    ids = metadata.asset_ids()
    if not ids:
        raise RuntimeError("Managed file has no assets")
    for idb in ids:
        extra = [t.name for t in idb.asset_data.tags if t.name in ("small", "medium", "large")]
        metadata.apply(idb, job, job["asset_type"], list(job.get("tags", [])) + extra)
    if len(ids) == 1 and job["asset_type"] != "blend":
        ids[0].name = job["name"]
    _write(ids, job["out_blend"])
    return {"ok": True, "warnings": [], "info": {"remapped": n}}


def publish(job):
    """A self-contained (packed) copy of every managed asset file under dest, plus the catalogs."""
    import shutil
    lib, dest = job["library"], job["dest"]
    os.makedirs(dest, exist_ok=True)
    warnings = []
    done = 0
    for blend in job["blends"]:
        if not os.path.exists(blend):
            continue
        bpy.ops.wm.open_mainfile(filepath=blend, load_ui=False, use_scripts=False)
        _pack_all(warnings)
        ids = metadata.asset_ids()
        rel = os.path.relpath(blend, lib)
        _write(ids, os.path.join(dest, rel))
        done += 1
    cats = os.path.join(lib, "blender_assets.cats.txt")
    if os.path.exists(cats):
        shutil.copyfile(cats, os.path.join(dest, "blender_assets.cats.txt"))
    return {"ok": True, "warnings": warnings, "info": {"published": done}}


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    job_path = argv[0]
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    t0 = time.time()
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        kind = job.get("kind")
        if kind == "convert":
            result = convert(job)
        elif kind == "update":
            result = update(job)
        elif kind == "publish":
            result = publish(job)
        else:
            raise RuntimeError("Unknown job kind %r" % kind)
    except Exception as e:
        result = {"ok": False, "error": "%s" % (str(e) or type(e).__name__), "trace": traceback.format_exc(),
                  "warnings": job.get("_warnings", [])}
        print(result["trace"])
    result["worker_seconds"] = round(time.time() - t0, 2)
    tmp = job["result"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    os.replace(tmp, job["result"])
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
