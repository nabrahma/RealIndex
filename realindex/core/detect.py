# SPDX-License-Identifier: GPL-3.0-or-later
"""Detection: group a pile of files into candidate assets the way a person would.

detect(files, probe, overrides) is a pure function of the virtual file list: zips are already expanded by the
caller (each member's vpath runs through the zip's own path, 'Materials/Bricks.zip/Bricks_Color.jpg'), all
paths are relative to the drop root, and the result is deterministic. Every file is owned by at most one
candidate ('files'); images several models share are listed under 'uses' of the others.

Rules, in order: (2) model files claim their textures, (3) LODs and duplicate formats become one asset,
(4) remaining images form texture sets when a colour map is present, (5) remaining large equirect images are
HDRIs, (6) everything left is a known extra (ignored silently) or a problem with a reason.
"""

import os
import posixpath
import re

from . import maps, names

MODEL_EXTS = (".blend", ".glb", ".gltf", ".fbx", ".usdz", ".usdc", ".usd", ".usda", ".obj", ".abc", ".ply", ".stl")
MODEL_RANK = {e: i for i, e in enumerate(MODEL_EXTS)}
UNSUPPORTED_MODELS = {".max", ".c4d", ".ma", ".mb", ".3ds", ".lwo", ".lxo", ".skp", ".dae", ".3dm", ".ztl",
                      ".zpr", ".x3d", ".wrl", ".dxf", ".iges", ".igs", ".step", ".stp", ".hip", ".mdl", ".spp"}
OTHER_ARCHIVES = {".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
EXTRA_EXTS = {".txt", ".md", ".pdf", ".html", ".htm", ".url", ".nfo", ".mtl", ".bin", ".mtlx", ".tres", ".json",
              ".xml", ".ini", ".log", ".blend1", ".blend2", ".csv", ".rtf", ".doc", ".docx", ".webloc", ".lnk",
              ".sbsar", ".sbs", ".spsm", ".uasset", ".unitypackage", ".mat", ".meta", ".db", ".ds_store", ".gif",
              ".mp4", ".mov", ".webm", ".ttf", ".otf"}
EXTRA_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "license", "licence", "readme", "copying"}
EXTRA_WORDS = ("license", "licence", "readme", "credit", "attribution", "preview", "thumbnail", "thumb", "render",
               "screenshot", "cover", "promo", "wireframe", "sphere", "cube_preview")
SIDECAR_MODEL_EXTS = {".usda", ".usdc", ".usd", ".mtlx", ".blend"}  # ambientCG ships all of these
# Folder names that carry no catalog meaning ("FBX" inside a pack, "textures" next to a model).
NOISE_DIRS = {"textures", "texture", "tex", "maps", "images", "image", "materials_textures", "fbx", "obj",
              "gltf", "glb", "usd", "usdz", "abc", "stl", "ply", "blend", "blender", "source", "sources", "src",
              "mesh", "meshes", "lods", "lod", "export", "exports", "files", "geometry", "geo", "3d", "model",
              "3d models", "3d_models"}
TEXTURE_DIRS = {"textures", "texture", "tex", "maps", "images", "image", "materials", "material", "mats"}
TYPE_DIRS = {"model": "Models", "blend": "Models", "material": "Materials", "hdri": "HDRIs"}
TOP_TYPE_DIRS = {"models", "materials", "hdris", "hdri", "textures", "inbox", "blend files"}

_LOD = re.compile(r"^(.*?)[_\-. ]?lod[_\-]?(\d+)$", re.I)
_RESDIR = re.compile(r"^\d+k([_\-](jpg|png|exr|hdr))?$", re.I)
_ACG = re.compile(r"^[A-Za-z]+\d{3}[A-Z]?_\d+K-(JPG|PNG|HDR|EXR)", re.I)
_ACG_ZIP = re.compile(r"^[A-Za-z]+\d{3}[A-Z]?_\d+K-(JPG|PNG|HDR|EXR)\.zip$", re.I)
_PH_MAP = re.compile(r"^[a-z0-9_]+_(diff|col|arm|rough|nor_gl|nor_dx|disp|ao|metal|spec|bump|mask|alpha|"
                     r"translucent|emission|height|displacement)_\d+k$")
_PH_HDRI = re.compile(r"^[a-z0-9_]+_\d+k$")
_MARKET = (("fab", "Fab"), ("turbosquid", "TurboSquid"), ("cgtrader", "CGTrader"))


class VFile:
    """One file as detection sees it. vpath: root-relative virtual path; real: path on disk."""
    __slots__ = ("vpath", "real", "size", "mtime", "src", "qhash")

    def __init__(self, vpath, real="", size=0, mtime=0, src=None, qhash=None):
        self.vpath, self.real, self.size, self.mtime = vpath, real, size, mtime
        self.src = src if src is not None else vpath
        self.qhash = qhash

    @property
    def dir(self):
        return posixpath.dirname(self.vpath)

    @property
    def name(self):
        return posixpath.basename(self.vpath)

    @property
    def stem(self):
        return os.path.splitext(self.name)[0]

    @property
    def ext(self):
        return os.path.splitext(self.name)[1].lower()

    def __repr__(self):
        return "VFile(%r)" % self.vpath


class Candidate:
    __slots__ = ("key", "kind", "name", "vendor", "catalog", "files", "uses", "maps", "lods", "meta", "problem")

    def __init__(self, key, kind, name="", files=None):
        self.key, self.kind, self.name = key, kind, name
        self.vendor, self.catalog, self.problem = None, "", None
        self.files, self.uses, self.lods = list(files or []), [], []
        self.maps, self.meta = {}, {}

    def to_dict(self):
        d = {"key": self.key, "kind": self.kind, "name": self.name, "vendor": self.vendor, "catalog": self.catalog,
             "files": sorted(self.files), "uses": sorted(self.uses), "lods": list(self.lods),
             "maps": {k: dict(v) for k, v in sorted(self.maps.items())}, "meta": dict(sorted(self.meta.items()))}
        if self.problem:
            d["problem"] = self.problem
        return d

    def __repr__(self):
        return "Candidate(%s %r %r)" % (self.kind, self.name, self.key)


class Probe:
    """Reads facts from disk (overridable in tests). Results are cached per (path, size, mtime)."""

    def __init__(self, cache=None):
        self.cache = cache if cache is not None else {}

    def _get(self, kind, vf, fn):
        k = (kind, vf.real, vf.size, vf.mtime)
        if k not in self.cache:
            self.cache[k] = fn(vf.real)
        return self.cache[k]

    def image_size(self, vf):
        from . import fileinfo
        return self._get("size", vf, fileinfo.image_size)

    def model_refs(self, vf):
        from . import fileinfo
        return self._get("refs", vf, fileinfo.model_refs)

    def license(self, vf):
        from . import fileinfo
        return self._get("lic", vf, fileinfo.read_license)


def _is_image(vf):
    return vf.ext in maps.IMAGE_EXTS


def _is_extra(vf):
    low = vf.name.lower()
    if vf.ext in EXTRA_EXTS or low in EXTRA_NAMES or low.startswith("._"):
        return True
    stem = vf.stem.lower()
    return any(stem.startswith(w) for w in ("license", "licence", "readme", "credits", "attribution", "copying"))


def _is_preview_name(stem):
    low = stem.lower()
    return any(w in low for w in ("preview", "thumbnail", "thumb", "_render", "screenshot", "cover", "promo"))


def _norm_prefix(p):
    return re.sub(r"[\s_\-.]+", "_", p.lower()).strip("_")


def _parts(d):
    return [p for p in d.split("/") if p]


def _strip_noise(parts):
    """Drop trailing format / texture / resolution folder names ('Pack/FBX' -> 'Pack')."""
    out = list(parts)
    while len(out) > 1 and (_unzip_name(out[-1]).lower() in NOISE_DIRS or _RESDIR.match(_unzip_name(out[-1]))):
        out.pop()
    return out


def _unzip_name(part):
    return part[:-4] if part.lower().endswith(".zip") else part


def detect(files, probe=None, overrides=None):
    """Group files into candidates. Returns a list of Candidate sorted by key."""
    probe = probe or Probe()
    overrides = overrides or {}
    files = sorted(files, key=lambda f: f.vpath)
    owner = {}       # vpath -> candidate key (each file owned once)
    cands = []
    by_dir = {}
    for f in files:
        by_dir.setdefault(f.dir, []).append(f)
    all_dirs = set(by_dir)
    for d in list(all_dirs):  # parents too, so sibling lookups see folders that only hold folders
        while d:
            d = posixpath.dirname(d)
            all_dirs.add(d)

    def claim(cand, vf):
        if vf.vpath not in owner:
            owner[vf.vpath] = cand.key
            cand.files.append(vf.vpath)
            return True
        return False

    parsed = {f.vpath: maps.parse(f.stem) for f in files if _is_image(f)}

    # ---- forced types (user overrides) and ignored files
    for f in files:
        ov = overrides.get(f.vpath)
        if ov == "ignore":
            c = Candidate(f.vpath, "ignored", f.stem)
            claim(c, f)
            cands.append(c)
        elif ov == "hdri" and _is_image(f):
            c = Candidate(f.vpath, "hdri", names.display_name(f.stem))
            claim(c, f)
            cands.append(c)

    # ---- material sidecars (ambientCG ships Bricks076C_2K-JPG.usdc / .mtlx beside the maps)
    sidecars = set()
    for f in files:
        if f.ext in SIDECAR_MODEL_EXTS and f.vpath not in owner and overrides.get(f.vpath) != "model":
            stem = f.stem.lower()
            for g in by_dir.get(f.dir, []):
                p = parsed.get(g.vpath)
                if p and p.kind and g.stem.lower().startswith(stem + "_"):
                    sidecars.add(f.vpath)
                    break
        if overrides.get(f.vpath) == "material" and f.ext in MODEL_EXTS:
            sidecars.add(f.vpath)

    # ---- rule 2/3: models, grouped by LOD and by format
    groups = {}
    for f in files:
        if f.ext not in MODEL_EXTS or f.vpath in owner or f.vpath in sidecars:
            continue
        m = _LOD.match(f.stem)
        base, lod = (m.group(1), int(m.group(2))) if m and m.group(1) else (f.stem, 0 if not m else int(m.group(2)))
        pack = "/".join(_strip_noise(_parts(f.dir))) if f.ext != ".blend" else f.dir
        key = (pack, base.lower(), f.ext == ".blend")
        groups.setdefault(key, []).append((lod, MODEL_RANK.get(f.ext, 99), f))
    model_cands = []
    for gkey in sorted(groups):
        members = sorted(groups[gkey], key=lambda t: (t[0], t[1], t[2].vpath))
        lod0 = members[0][0]
        best_rank = min(r for lod, r, _f in members if lod == lod0)
        rep = next(f for lod, r, f in members if lod == lod0 and r == best_rank)
        kind = "blend" if rep.ext == ".blend" else "model"
        c = Candidate(rep.vpath, kind, rep.stem)
        claim(c, rep)
        base = gkey[1]
        c.meta["format"] = rep.ext[1:]
        for lod, r, f in members:
            if f is rep:
                continue
            if f.ext == rep.ext and lod != lod0:
                c.lods.append(f.vpath)
            else:
                c.meta.setdefault("alternates", []).append(f.vpath)
            claim(c, f)
        # Claim companions: .mtl of an OBJ, .bin buffers of a glTF
        for g in by_dir.get(rep.dir, []):
            if (rep.ext == ".obj" and g.ext == ".mtl") or (rep.ext == ".gltf" and g.ext == ".bin"):
                if rep.ext == ".gltf" or g.stem.lower() == rep.stem.lower() or len(
                        [x for x in by_dir[rep.dir] if x.ext == ".obj"]) == 1:
                    claim(c, g)
        c.meta["_base"] = base
        model_cands.append(c)
        cands.append(c)

    _claim_model_images(model_cands, files, by_dir, all_dirs, parsed, owner, probe, claim)
    for c in model_cands:
        c.meta.pop("_base", None)

    # ---- rule 4: texture sets from remaining images
    for d in sorted(by_dir):
        imgs = [f for f in by_dir[d] if _is_image(f) and f.vpath not in owner]
        if not imgs:
            continue
        sets = {}
        loose = []
        for f in imgs:
            p = parsed[f.vpath]
            if p.kind is None:
                loose.append(f)
                continue
            sets.setdefault((_norm_prefix(p.prefix), p.res), []).append(f)
        # a set written without resolution joins the single resolution group of its prefix
        for (pre, res) in sorted([k for k in sets if k[1] is None]):
            others = [k for k in sets if k[0] == pre and k[1] is not None]
            if len(others) == 1:
                sets[others[0]].extend(sets.pop((pre, res)))
        prefixes = {}
        for (pre, res) in sets:
            prefixes.setdefault(pre, []).append(res)
        for (pre, res) in sorted(sets, key=lambda k: (k[0], k[1] or "")):
            members = sorted(sets[(pre, res)], key=lambda f: f.vpath)
            found = [(parsed[f.vpath].kind, f.vpath, parsed[f.vpath]) for f in members]
            kinds = {k for k, _p, _x in found}
            picked = maps.pick(found)
            color = picked.get(maps.COLOR)
            key = color["path"] if color else members[0].vpath
            forced = any(overrides.get(f.vpath) == "material" for f in members)
            if not color and not forced:
                continue  # handled as leftovers below
            c = Candidate(key, "material")
            for f in members:
                claim(c, f)
            c.maps = picked
            first = parsed[members[0].vpath]
            raw = first.prefix or ""
            label = names.display_name(raw) if raw else ""
            if not label:
                label = _package_name(d) or "Material"
            if len(prefixes[pre]) > 1 and res:
                label = "%s %s" % (label, res)
            c.name = label
            if res:
                c.meta["res"] = res
            c.meta["maps"] = sorted(kinds)
            # ambientCG previews: an image named exactly like the set prefix, no map word
            for f in list(loose):
                st = parsed[f.vpath]
                if _norm_prefix(st.prefix) == pre or (_is_preview_name(f.stem) and pre and pre in _norm_prefix(f.stem)):
                    claim(c, f)
                    c.meta["preview"] = f.vpath
                    loose.remove(f)
                    break
            cands.append(c)

    # ---- rule 5: HDRIs
    for f in files:
        if f.vpath in owner or f.ext not in (".hdr", ".exr"):
            continue
        size = probe.image_size(f)
        if not size or size[1] == 0:
            continue
        aspect = size[0] / float(size[1])
        if 1.8 <= aspect <= 2.2 and size[0] >= 256:
            p = parsed[f.vpath]
            c = Candidate(f.vpath, "hdri", names.display_name(p.prefix or f.stem))
            claim(c, f)
            c.meta["size"] = list(size)
            if p.res:
                c.meta["res"] = p.res
            same = [g for g in by_dir[f.dir] if g.ext == f.ext and g is not f and
                    _norm_prefix(parsed[g.vpath].prefix) == _norm_prefix(p.prefix)]
            if same and p.res:
                c.name = "%s %s" % (c.name, p.res)
            base = _norm_prefix(p.prefix or f.stem)
            for g in by_dir.get(f.dir, []):
                if g.vpath in owner or not _is_image(g) or g.ext in (".hdr", ".exr"):
                    continue
                gs = _norm_prefix(parsed[g.vpath].prefix)
                if gs == base or (gs.startswith(base) and _is_preview_name(g.stem)):
                    claim(c, g)
                    c.meta["preview"] = g.vpath
                    break
            cands.append(c)

    # ---- rule 6: leftovers
    lone_sets = {}
    for f in files:
        if f.vpath in owner:
            continue
        if f.vpath in sidecars or _is_extra(f):
            owner[f.vpath] = "#extra"
            continue
        if f.ext in UNSUPPORTED_MODELS:
            c = Candidate(f.vpath, "problem", f.stem)
            c.problem = "Unsupported format %s: export to FBX or glTF first" % f.ext
            claim(c, f)
            cands.append(c)
        elif f.ext in OTHER_ARCHIVES:
            c = Candidate(f.vpath, "problem", f.stem)
            c.problem = "Unsupported archive %s: extract it or re-pack as .zip" % f.ext
            claim(c, f)
            cands.append(c)
        elif _is_image(f):
            p = parsed[f.vpath]
            if p.kind is None and _is_preview_name(f.stem):
                owner[f.vpath] = "#extra"
                continue
            if p.kind is None:
                c = Candidate(f.vpath, "problem", f.stem)
                c.problem = "Image is not part of a texture set (no map name such as _Color or _Normal)"
                claim(c, f)
                cands.append(c)
            else:
                lone_sets.setdefault((f.dir, _norm_prefix(p.prefix), p.res), []).append(f)
        else:
            c = Candidate(f.vpath, "problem", f.stem)
            c.problem = "Unsupported file type %s" % (f.ext or "(no extension)")
            claim(c, f)
            cands.append(c)
    for k in sorted(lone_sets, key=lambda k: (k[0], k[1], k[2] or "")):
        members = lone_sets[k]
        c = Candidate(members[0].vpath, "problem", names.display_name(parsed[members[0].vpath].prefix or members[0].stem))
        c.problem = "Texture set without a color map (%s)" % ", ".join(sorted({parsed[m.vpath].kind for m in members}))
        for m in members:
            claim(c, m)
        cands.append(c)

    # ---- names, vendors, licences, catalogs
    for c in cands:
        _finish(c, files, by_dir, parsed, probe)
    _assign_catalogs(cands)
    cands.sort(key=lambda c: c.key)
    return cands


def _claim_model_images(model_cands, files, by_dir, all_dirs, parsed, owner, probe, claim):
    """Rule 2: a model claims images in its folder and conventional texture folders: referenced by name first,
    then by name similarity; unmatched ones in a package folder are shared by its models."""
    scope_of = {}
    for c in model_cands:
        mdir = posixpath.dirname(c.key)
        dirs = {mdir}
        parent = posixpath.dirname(mdir)
        bases = [mdir]
        if mdir and not _public_dir(parent):
            bases.append(parent)  # Pack/FBX/chair.fbx looks in Pack/Textures too
        for base in bases:
            for d in all_dirs:
                if d == base or not d.startswith(base + "/" if base else ""):
                    continue
                rel = d[len(base) + 1:] if base else d
                top = rel.split("/")[0].lower()
                if top in TEXTURE_DIRS or _RESDIR.match(top):
                    dirs.add(d)
        scope_of[c.key] = sorted(dirs)

    fmap = {f.vpath: f for f in files}
    score = {}  # vpath -> [(score, key, dir)]
    for c in model_cands:
        rep = fmap[c.key]
        refs = probe.model_refs(rep) if rep.ext != ".blend" else {"images": [], "materials": []}
        ref_names = set(refs.get("images", []))
        mats = refs.get("materials", [])
        c.meta["materials"] = list(mats)[:32] if mats else []
        for d in scope_of[c.key]:
            for f in by_dir.get(d, []):
                if not _is_image(f) or f.vpath in owner:
                    continue
                if f.name.lower() in ref_names:
                    s = 3.0
                else:
                    pre = parsed[f.vpath].prefix
                    s = max([names.similarity(pre, c.meta["_base"])] + [names.similarity(pre, m) for m in mats])
                    s = s if s >= 0.5 else 0.0
                score.setdefault(f.vpath, []).append((s, c.key, d))
    by_key = {c.key: c for c in model_cands}
    for vpath in sorted(score):
        entries = score[vpath]
        best = max(e[0] for e in entries)
        if best > 0:
            winners = [e for e in entries if e[0] == best]
            win = sorted(winners, key=lambda e: e[1])[0]
            f = fmap[vpath]
            claim(by_key[win[1]], f)
            for e in winners[1:]:
                by_key[e[1]].uses.append(vpath)
            continue
        # no specific match: shared by every model that sees it, unless it sits loose in a public folder
        f = fmap[vpath]
        takers = []
        for s, key, d in sorted(entries, key=lambda e: e[1]):
            mdir = posixpath.dirname(key)
            if d == mdir and _public_dir(mdir):
                continue
            takers.append(key)
        if not takers:
            continue
        claim(by_key[takers[0]], f)
        for key in takers[1:]:
            by_key[key].uses.append(vpath)


def _public_dir(d):
    """The root, Inbox and top-level type folders: loose images there are not a model's by default."""
    parts = _parts(d)
    return not parts or (len(parts) == 1 and parts[0].lower() in TOP_TYPE_DIRS)


def _package_name(d):
    parts = _strip_noise(_parts(d))
    while parts and (_unzip_name(parts[-1]).lower() in NOISE_DIRS or _RESDIR.match(_unzip_name(parts[-1]))):
        parts.pop()
    if not parts:
        return ""
    last = _unzip_name(parts[-1])
    if last.lower() in TOP_TYPE_DIRS:
        return ""
    return last


def _finish(c, files, by_dir, parsed, probe):
    if c.kind in ("problem", "ignored"):
        return
    fmap = {f.vpath: f for f in files}
    key_file = fmap[c.key]
    zip_src = key_file.src if key_file.src != key_file.vpath else ""
    path_words = c.key.lower()
    # vendor
    vendor = None
    if c.kind == "material":
        stems = [fmap[p].stem for p in c.files if p in fmap]
        if any(_ACG.match(s) for s in stems) or (zip_src and _ACG_ZIP.match(posixpath.basename(zip_src))):
            vendor = "ambientCG"
        elif any(_PH_MAP.match(s) for s in stems):
            vendor = "Poly Haven"
    elif c.kind == "hdri":
        if _ACG.match(key_file.stem):
            vendor = "ambientCG"
        elif _PH_HDRI.match(key_file.stem):
            vendor = "Poly Haven"
    # licence files near the asset: same folder, or up to the package / zip root
    lic = {}
    d = key_file.dir
    for _ in range(3):
        for g in by_dir.get(d, []):
            st = g.stem.lower()
            if g.ext in (".txt", ".md", "") and (st.startswith("license") or st.startswith("licence")):
                lic = probe.license(g) or {}
                if lic:
                    c.meta["license_file"] = g.vpath
                break
        if lic or not d or d.lower().endswith(".zip") or _public_dir(d):
            break
        d = posixpath.dirname(d)
    if lic.get("vendor") == "Sketchfab" or (c.kind == "model" and key_file.stem.lower() == "scene" and
                                            key_file.ext in (".gltf", ".glb") and lic):
        vendor = "Sketchfab"
    if vendor is None:
        for word, label in _MARKET:
            if re.search(r"(^|[/_\-\s.])%s([/_\-\s.]|$)" % word, path_words):
                vendor = label
                break
    c.vendor = vendor
    if vendor in ("ambientCG", "Poly Haven"):
        c.meta["license"] = "CC0"
        c.meta["author"] = vendor
    for k in ("license", "author", "title", "source"):
        if lic.get(k):
            c.meta["license" if k == "license" else k] = lic[k]
    # names: generic stems ('scene.gltf') take the title or the package folder
    if c.kind in ("model", "blend"):
        base = _LOD.match(key_file.stem)
        stem = base.group(1) if base and base.group(1) else key_file.stem
        if stem.lower() in names.GENERIC_STEMS:
            stem = lic.get("title") or _package_name(key_file.dir) or stem
        c.name = stem.strip() or key_file.stem
    if c.kind == "material":
        size = None
        for text in [fmap[p].stem for p in c.files if p in fmap] + _parts(key_file.dir)[::-1]:
            size = names.real_size_m(text)
            if size:
                break
        if size:
            c.meta["size_m"] = size
    c.meta["source"] = c.key if not zip_src else zip_src
    c.name = c.name.replace(":", "-").replace("\n", " ").strip() or "Asset"


def anchor_dir_parts(c):
    """The folder an asset belongs to for catalog purposes, zip names unwrapped, noise folders dropped."""
    d = posixpath.dirname(c.key)
    parts = [_unzip_name(p) for p in _parts(d)]
    return _strip_noise(parts) if parts else []


def _is_package(c, parts, n_in_folder, zip_only=False):
    """Is the asset's own folder a download package (not a user category)? A zip holding one asset, a folder
    named like the asset, or a folder around a generic 'scene.gltf' style file."""
    if n_in_folder != 1 or not parts:
        return False
    if len(parts) == 1 and parts[0].lower() in TOP_TYPE_DIRS:
        return False
    raw = _parts(posixpath.dirname(c.key))
    folder = parts[-1]
    if any(p.lower().endswith(".zip") and _unzip_name(p) == folder for p in raw):
        return True
    if zip_only:
        return False
    stem = os.path.splitext(posixpath.basename(c.key))[0]
    if stem.lower() in names.GENERIC_STEMS:
        return True
    return names.similarity(folder, c.name) >= 0.5 or names.similarity(folder, stem) >= 0.5


def _assign_catalogs(cands):
    """Catalog = folder path under the root. A download package folder (zip or folder named after
    its single asset) does not become a catalog of its own; root and Inbox items go to <Type>/<Vendor or
    Unsorted>."""
    real = [c for c in cands if c.kind not in ("problem", "ignored")]
    dirs = {c.key: anchor_dir_parts(c) for c in real}
    for c in cands:
        if c.kind in ("problem", "ignored"):
            c.catalog = ""
            continue
        parts = list(dirs[c.key])
        first = True
        while parts:  # unwrap the package folder; further levels only when they are zips (zip in a zip)
            n = sum(1 for p in dirs.values() if p[:len(parts)] == parts)
            if not _is_package(c, parts, n, zip_only=not first):
                break
            first = False
            parts = parts[:-1]
            while parts and (parts[-1].lower() in NOISE_DIRS or _RESDIR.match(parts[-1])):
                parts.pop()
        if not parts or parts[0].lower() == "inbox":
            parts = [TYPE_DIRS[c.kind], c.vendor or "Unsorted"]
        c.catalog = "/".join(p.replace(":", "-").strip() for p in parts)
