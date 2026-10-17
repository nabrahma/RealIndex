# SPDX-License-Identifier: GPL-3.0-or-later
"""Name handling shared by the indexer, the worker and the UI: tokens, tags, safe file names and
deterministic catalog ids. Pure Python (no bpy)."""

import hashlib
import re
import uuid

_SEP = re.compile(r"[\s_\-.()\[\]{}+,;]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_RES = re.compile(r"^(\d{1,2})k$", re.I)
_PX = re.compile(r"^(256|512|1024|2048|4096|8192|16384)(px)?$", re.I)
_SIZE2 = re.compile(r"(?:^|[_\-\s])(\d+(?:[.,]\d+)?)(?:x\d+(?:[.,]\d+)?)?(m|cm|mm)(?=$|[_\-\s])", re.I)

CATALOG_NS = uuid.UUID("6b1f3a52-5d0e-4c55-9b5e-52ea1d3c0f7e")

# Words that say nothing about an asset: never tags, and skipped when picking a display name.
NOISE = {
    "the", "a", "an", "of", "and", "with", "for", "by", "in", "on", "to", "fbx", "obj", "gltf", "glb", "usd",
    "usda", "usdc", "usdz", "abc", "stl", "ply", "blend", "jpg", "jpeg", "png", "exr", "hdr", "tif", "tiff", "tga",
    "zip", "textures", "texture", "tex", "maps", "map", "images", "image", "source", "sources", "model", "models",
    "mesh", "meshes", "scene", "export", "exported", "untitled", "final", "file", "files", "inbox", "unsorted",
    "lod", "lod0", "lod1", "lod2", "lod3", "lod4", "lod5", "pbr", "low", "high", "poly", "lowpoly", "highpoly",
    "free", "sm", "t", "m", "copy",
}
GENERIC_STEMS = {"scene", "model", "mesh", "untitled", "export", "object", "default", "main", "asset", "out",
                 "output", "file", "source"}


def split_words(text):
    """Words of a file or folder name: split on separators, then on camel case and digit runs."""
    words = []
    for part in _SEP.split(text):
        if not part:
            continue
        words.extend(_CAMEL.findall(part) or [part])
    return words


def tokens(text):
    """Separator-split tokens (no camel splitting), as written: 'Bricks076C_2K-JPG' -> [Bricks076C, 2K, JPG]."""
    return [t for t in _SEP.split(text) if t]


def is_res_token(tok):
    return bool(_RES.match(tok) or _PX.match(tok))


def res_label(tok):
    """'2k' / '2K' / '2048' -> '2K'; None when not a resolution token."""
    m = _RES.match(tok)
    if m:
        return m.group(1) + "K"
    m = _PX.match(tok)
    if m:
        return {"256": "0.25K", "512": "0.5K"}.get(m.group(1), str(int(m.group(1)) // 1024) + "K")
    return None


def real_size_m(text):
    """Real-world size in metres from a name ('wall_2m', 'tiles_150cm', 'rock-1.5x1.5m'); None if absent."""
    m = _SIZE2.search(text)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return v * {"m": 1.0, "cm": 0.01, "mm": 0.001}[m.group(2).lower()]


def tag_words(*texts):
    """Tags from names: lower-case words, no numbers, noise or one-letter words, in first-seen order."""
    out = []
    for text in texts:
        for w in split_words(text or ""):
            lw = w.lower()
            if len(lw) < 2 or lw.isdigit() or lw in NOISE or is_res_token(lw) or lw in out:
                continue
            out.append(lw)
    return out


def display_name(stem):
    """A readable asset name from a file stem: keeps the stem but trims resolution and format tokens."""
    toks = [t for t in tokens(stem) if not is_res_token(t) and t.lower() not in ("jpg", "png", "exr", "hdr")]
    return "_".join(toks) if toks else stem


_BAD_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name, limit=80):
    """A file name every OS accepts (Windows reserved characters and trailing dots removed)."""
    s = _BAD_FS.sub("_", name).strip().rstrip(".")
    if s.upper() in {"CON", "PRN", "AUX", "NUL"} or re.match(r"^(COM|LPT)\d$", s.upper()):
        s = "_" + s
    return (s or "asset")[:limit]


def catalog_id(path):
    """Catalog UUID derived from its path, identical on every rebuild."""
    return str(uuid.uuid5(CATALOG_NS, path.strip("/")))


def catalog_simple_name(path):
    return path.strip("/").replace("/", "-")


def short_hash(text, n=6):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def similarity(a, b):
    """Token overlap between two names (0..1), case and separator insensitive; 'ChairWood' ~ 'chair_wood'."""
    wa = {w.lower() for w in split_words(a) if w.lower() not in NOISE and not w.isdigit()}
    wb = {w.lower() for w in split_words(b) if w.lower() not in NOISE and not w.isdigit()}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / float(min(len(wa), len(wb)))
