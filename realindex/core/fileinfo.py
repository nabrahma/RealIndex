# SPDX-License-Identifier: GPL-3.0-or-later
"""Cheap facts about files, read without Blender: a quick content hash (rename tracking), image sizes from
headers (HDRI detection), the texture files a model refers to, and vendor licence files. Pure Python."""

import hashlib
import json
import os
import re
import struct

from . import fsutil

_CHUNK = 64 * 1024


def quick_hash(path, size=None):
    """sha1 of the size plus the first and last 64 KB: stable across renames and moves, cheap on big files."""
    if size is None:
        size = os.path.getsize(fsutil.long(path))
    h = hashlib.sha1(str(size).encode())
    with open(fsutil.long(path), "rb") as fh:
        h.update(fh.read(_CHUNK))
        if size > 2 * _CHUNK:
            fh.seek(size - _CHUNK)
            h.update(fh.read(_CHUNK))
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------- image sizes

def image_size(path):
    """(width, height) read from the file header, or None. PNG, JPEG, Radiance HDR and OpenEXR."""
    try:
        with open(fsutil.long(path), "rb") as fh:
            head = fh.read(32)
            ext = os.path.splitext(path)[1].lower()
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            if head.startswith(b"\xff\xd8"):
                fh.seek(2)
                return _jpeg_size(fh)
            if head.startswith(b"#?") or ext == ".hdr":
                fh.seek(0)
                return _hdr_size(fh)
            if head.startswith(b"\x76\x2f\x31\x01"):
                fh.seek(8)
                return _exr_size(fh)
    except (OSError, struct.error, ValueError):
        return None
    return None


def _jpeg_size(fh):
    while True:
        b = fh.read(1)
        while b and b != b"\xff":
            b = fh.read(1)
        while b == b"\xff":
            b = fh.read(1)
        if not b:
            return None
        marker = b[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            fh.read(3)
            h, w = struct.unpack(">HH", fh.read(4))
            return int(w), int(h)
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        seg = struct.unpack(">H", fh.read(2))[0]
        fh.seek(seg - 2, 1)


_HDR_RES = re.compile(rb"([-+])([XY])\s+(\d+)\s+([-+])([XY])\s+(\d+)")


def _hdr_size(fh):
    data = fh.read(8192)
    for line in data.split(b"\n")[1:64]:
        m = _HDR_RES.match(line.strip())
        if m:
            a, b = int(m.group(3)), int(m.group(6))
            return (b, a) if m.group(2) == b"Y" else (a, b)
    return None


def _exr_size(fh):
    """Walk the EXR header attributes for dataWindow (box2i)."""
    for _ in range(256):
        name = _cstr(fh)
        if not name:
            return None
        typ = _cstr(fh)
        size = struct.unpack("<i", fh.read(4))[0]
        if name == b"dataWindow" and typ == b"box2i":
            x0, y0, x1, y1 = struct.unpack("<iiii", fh.read(16))
            return x1 - x0 + 1, y1 - y0 + 1
        fh.seek(size, 1)
    return None


def _cstr(fh, limit=256):
    out = bytearray()
    while len(out) < limit:
        c = fh.read(1)
        if not c or c == b"\0":
            break
        out += c
    return bytes(out)


# ----------------------------------------------------------------------------------------- model references

_IMG_NAME = re.compile(rb"([A-Za-z0-9_\-. ()\[\]]{1,200}\.(?:png|jpe?g|tga|tiff?|exr|bmp|webp|dds|psd))", re.I)


def model_refs(path):
    """Basenames (lower case) of the image files a model file names, plus material names when cheap to read.

    glTF: the JSON image URIs and material names. OBJ: the map_* lines of its .mtl files. FBX/USDA/other:
    image-looking strings in the file. Returns {"images": [...], "materials": [...]}.
    """
    ext = os.path.splitext(path)[1].lower()
    images, mats = [], []
    try:
        if ext == ".gltf":
            with open(fsutil.long(path), "r", encoding="utf-8", errors="replace") as fh:
                doc = json.load(fh)
            for im in doc.get("images", []):
                uri = im.get("uri", "")
                if uri and not uri.startswith("data:"):
                    images.append(_basename(uri))
            mats = [m.get("name", "") for m in doc.get("materials", []) if m.get("name")]
        elif ext == ".glb":
            mats, images = _glb_refs(path)
        elif ext == ".obj":
            for mtl in obj_mtl_files(path):
                with open(fsutil.long(mtl), "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        parts = line.strip().split()
                        if not parts:
                            continue
                        if parts[0].lower().startswith(("map_", "bump", "disp", "norm", "refl")) and len(parts) > 1:
                            images.append(_basename(parts[-1]))
                        elif parts[0] == "newmtl" and len(parts) > 1:
                            mats.append(" ".join(parts[1:]))
        elif ext in (".fbx", ".usda", ".usd", ".usdc", ".dae", ".abc", ".3mf"):
            size = os.path.getsize(fsutil.long(path))
            with open(fsutil.long(path), "rb") as fh:
                data = fh.read(min(size, 64 * 1024 * 1024))
            for m in _IMG_NAME.finditer(data):
                images.append(_basename(m.group(1).decode("utf-8", "replace")))
    except (OSError, ValueError, UnicodeError):
        pass
    seen, uniq = set(), []
    for i in images:
        k = i.lower().strip()
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    return {"images": uniq, "materials": mats}


def _basename(p):
    from urllib.parse import unquote
    return unquote(p).replace("\\", "/").rsplit("/", 1)[-1]


def _glb_refs(path):
    with open(fsutil.long(path), "rb") as fh:
        head = fh.read(20)
        if len(head) < 20 or head[:4] != b"glTF":
            return [], []
        clen = struct.unpack("<I", head[12:16])[0]
        doc = json.loads(fh.read(clen).decode("utf-8", "replace"))
    mats = [m.get("name", "") for m in doc.get("materials", []) if m.get("name")]
    images = [_basename(im["uri"]) for im in doc.get("images", []) if im.get("uri") and
              not im["uri"].startswith("data:")]
    return mats, images


def obj_mtl_files(path):
    """The .mtl files an OBJ names (mtllib), resolved next to it; falls back to <stem>.mtl."""
    folder = os.path.dirname(path)
    out = []
    try:
        with open(fsutil.long(path), "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if line.startswith("mtllib"):
                    name = line.split(None, 1)[1].strip() if len(line.split(None, 1)) > 1 else ""
                    p = os.path.join(folder, name)
                    if name and os.path.exists(fsutil.long(p)):
                        out.append(p)
                if i > 2000:
                    break
    except OSError:
        pass
    if not out:
        p = os.path.splitext(path)[0] + ".mtl"
        if os.path.exists(fsutil.long(p)):
            out.append(p)
    return out


# ------------------------------------------------------------------------------------------------ licences

_LIC_FIELD = re.compile(r"^\s*\*?\s*(title|author|source|license type|licence type|license|licence)\s*:\s*(.+)$",
                        re.I | re.M)


def read_license(path):
    """Author, licence and title from a vendor licence file (Sketchfab's license.txt layout).

    Returns {} when nothing is recognised: a licence is never guessed.
    """
    try:
        with open(fsutil.long(path), "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(64 * 1024)
    except OSError:
        return {}
    out = {}
    for key, val in _LIC_FIELD.findall(text):
        key = key.lower().replace("licence", "license")
        val = val.strip()
        if key == "author":
            out["author"] = re.sub(r"\s*\(https?://[^)]*\)\s*$", "", val).strip()
        elif key in ("license type", "license"):
            out["license"] = re.sub(r"\s*\(https?://[^)]*\)\s*$", "", val).strip()
        elif key == "title":
            out["title"] = val
        elif key == "source":
            out["source"] = val
    if "sketchfab" in text.lower():
        out["vendor"] = "Sketchfab"
    elif not out:
        low = text.lower()
        if "cc0" in low or "creative commons zero" in low or "public domain" in low:
            out["license"] = "CC0"
    return out
