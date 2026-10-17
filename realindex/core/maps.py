# SPDX-License-Identifier: GPL-3.0-or-later
"""PBR map naming: which map an image is, its colour space, packed ARM/ORM channels, gloss and
DirectX normals. Pure Python; the worker and the indexer share these rules."""

from . import names

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".hdr", ".tga", ".bmp", ".webp"}

# Map kinds. 'arm' and 'orm' are packed AO (R), Roughness (G), Metallic (B).
COLOR, AO, ROUGH, GLOSS, METAL, NORMAL, HEIGHT, OPACITY, EMISSION, ARM, SPECULAR = (
    "color", "ao", "rough", "gloss", "metal", "normal", "height", "opacity", "emission", "arm", "specular")
KINDS = (COLOR, AO, ROUGH, GLOSS, METAL, NORMAL, HEIGHT, OPACITY, EMISSION, ARM, SPECULAR)

# Single words (lower case) that name a map. Abbreviations only count as the last word of a name.
WORDS = {
    "color": COLOR, "colour": COLOR, "col": COLOR, "albedo": COLOR, "diffuse": COLOR, "diff": COLOR, "dif": COLOR,
    "basecolor": COLOR, "basecolour": COLOR, "base_color": COLOR, "alb": COLOR,
    "ao": AO, "ambientocclusion": AO, "occlusion": AO, "occ": AO,
    "rough": ROUGH, "roughness": ROUGH, "rgh": ROUGH,
    "gloss": GLOSS, "glossiness": GLOSS, "glossy": GLOSS,
    "metal": METAL, "metallic": METAL, "metalness": METAL, "metalic": METAL, "mtl": METAL, "met": METAL,
    "normal": NORMAL, "normalgl": NORMAL, "normaldx": NORMAL, "nor": NORMAL, "nrm": NORMAL, "norm": NORMAL,
    "normals": NORMAL,
    "height": HEIGHT, "disp": HEIGHT, "displacement": HEIGHT, "displace": HEIGHT, "bump": HEIGHT, "hgt": HEIGHT,
    "opacity": OPACITY, "alpha": OPACITY, "transparency": OPACITY, "mask": OPACITY, "cutout": OPACITY,
    "emission": EMISSION, "emissive": EMISSION, "emit": EMISSION, "glow": EMISSION,
    "arm": ARM, "orm": ARM, "occlusionroughnessmetallic": ARM,
    "specular": SPECULAR, "spec": SPECULAR,
}
# Last-word-only abbreviations (marketplace habits: T_Chair_D.png, T_Chair_N.png, ...).
SHORT = {"d": COLOR, "bc": COLOR, "n": NORMAL, "r": ROUGH, "m": METAL, "h": HEIGHT,
         "e": EMISSION, "o": OPACITY, "g": GLOSS, "s": SPECULAR, "mr": None}
# Two-word map names, checked before single words.
PAIRS = {("base", "color"): COLOR, ("base", "colour"): COLOR, ("ambient", "occlusion"): AO,
         ("normal", "gl"): NORMAL, ("normal", "dx"): NORMAL, ("nor", "gl"): NORMAL, ("nor", "dx"): NORMAL,
         ("nrm", "gl"): NORMAL, ("nrm", "dx"): NORMAL, ("normal", "opengl"): NORMAL,
         ("normal", "directx"): NORMAL}
FORMAT_TOKENS = {"jpg", "jpeg", "png", "exr", "tif", "tiff", "tga", "hdr", "webp", "bmp"}

SRGB_KINDS = {COLOR, EMISSION}
# Preferred file formats when one map exists more than once (height and normal like high bit depth).
_EXT_RANK = {".png": 0, ".tif": 1, ".tiff": 1, ".jpg": 2, ".jpeg": 2, ".exr": 3, ".tga": 4, ".bmp": 5, ".webp": 6,
             ".hdr": 7}
_EXT_RANK_HI = {".exr": 0, ".tif": 1, ".tiff": 1, ".png": 2, ".jpg": 3, ".jpeg": 3, ".tga": 4, ".bmp": 5,
                ".webp": 6, ".hdr": 7}


class Parsed:
    __slots__ = ("kind", "prefix", "res", "dx", "gl", "stem")

    def __init__(self, kind, prefix, res, dx=False, gl=False, stem=""):
        self.kind, self.prefix, self.res, self.dx, self.gl, self.stem = kind, prefix, res, dx, gl, stem

    def __repr__(self):
        return "Parsed(%r, %r, %r, dx=%r)" % (self.kind, self.prefix, self.res, self.dx)


def parse(stem):
    """Parse an image file stem into map kind, set prefix, resolution and normal handedness.

    'Bricks076C_2K-JPG_NormalDX' -> normal, 'Bricks076C', '2K', dx
    'brick_wall_001_nor_gl_2k'   -> normal, 'brick_wall_001', '2K', gl
    'T_Chair_Wood_BaseColor'     -> color, 'T_Chair_Wood', None
    A stem without a map word gives kind None and the whole stem (minus resolution) as prefix.
    """
    toks = names.tokens(stem)
    res = None
    body = []
    for t in toks:
        lab = names.res_label(t)
        if lab and res is None:
            res = lab
            continue
        if t.lower() in FORMAT_TOKENS:
            continue
        body.append(t)
    kind, dx, gl, cut = None, False, False, None
    low = [t.lower() for t in body]
    # Scan from the right: the map word is one of the last three tokens.
    for i in range(len(low) - 1, max(-1, len(low) - 4), -1):
        if i >= 1 and (low[i - 1], low[i]) in PAIRS:
            kind, cut = PAIRS[(low[i - 1], low[i])], i - 1
            dx, gl = low[i] in ("dx", "directx"), low[i] in ("gl", "opengl")
            break
        w = low[i]
        if w in WORDS and (i > 0 or len(low) == 1):
            kind, cut = WORDS[w], i
            dx, gl = w.endswith("dx"), w.endswith("gl")
            break
        if w in ("gl", "dx", "opengl", "directx") and i >= 1 and low[i - 1] in WORDS and WORDS[low[i - 1]] == NORMAL:
            continue  # handled by PAIRS on the next step
        if i == len(low) - 1 and i > 0 and w in SHORT and SHORT[w]:
            kind, cut = SHORT[w], i
            break
        # Camel-cased compound map word glued to the last token: 'ChairWoodBaseColor'
        if i == len(low) - 1 and i > 0:
            words = [x.lower() for x in names.split_words(body[i])]
            for j in range(1, len(words)):  # longest tail first: BaseColor, not Color
                tail = "".join(words[j:])
                if tail in WORDS and len(tail) > 3:
                    kind = WORDS[tail]
                    dx, gl = tail.endswith("dx"), tail.endswith("gl")
                    head = names.split_words(body[i])[:j]
                    prefix = "_".join(body[:i] + ["".join(head)])
                    return Parsed(kind, prefix, res, dx, gl, stem)
    if kind is None:
        return Parsed(None, "_".join(body) if body else stem, res, stem=stem)
    prefix = "_".join(body[:cut])
    return Parsed(kind, prefix, res, dx, gl, stem)


def colorspace(kind):
    """Colour and emission are sRGB, everything else Non-Color."""
    return "sRGB" if kind in SRGB_KINDS else "Non-Color"


def ext_rank(kind, ext):
    table = _EXT_RANK_HI if kind in (HEIGHT, NORMAL) else _EXT_RANK
    return table.get(ext.lower(), 9)


def pick(maps_found):
    """Choose one file per map kind from [(kind, path, Parsed)].

    OpenGL normals win over DirectX ones; a separate roughness wins over gloss; per kind the
    preferred format wins. Returns {kind: {"path", "dx"}} plus 'normal_dx' when only a DX normal exists.
    """
    import os
    best = {}
    for kind, path, p in maps_found:
        ext = os.path.splitext(path)[1]
        score = (1 if (kind == NORMAL and p.dx) else 0, ext_rank(kind, ext), path.lower())
        cur = best.get(kind)
        if cur is None or score < cur[0]:
            best[kind] = (score, path, p)
    out = {}
    for kind, (_s, path, p) in best.items():
        out[kind] = {"path": path, "dx": bool(kind == NORMAL and p.dx)}
    if ROUGH in out and GLOSS in out:
        del out[GLOSS]
    return out


def has_color(kinds):
    return COLOR in kinds
