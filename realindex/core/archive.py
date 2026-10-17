# SPDX-License-Identifier: GPL-3.0-or-later
"""Zip extraction into the state cache (rule 1, "Archives first"). The zip itself is never touched.

cache/<quick hash of the zip>/ holds the members; a '.complete' marker makes half-finished extractions
(Blender closed mid-way) redo cleanly. Nested zips are extracted into their own cache folders by the
caller. Pure Python."""

import os
import zipfile

from . import fsutil

COMPLETE = ".complete"


class ArchiveError(Exception):
    pass


def cache_dir(state, qhash):
    return os.path.join(state, "cache", qhash)


def is_extracted(state, qhash):
    return os.path.exists(os.path.join(cache_dir(state, qhash), COMPLETE))


def extract(zip_path, state, qhash):
    """Extract zip_path into cache/<qhash>/ and return that folder. Raises ArchiveError for corrupt archives.

    Members with absolute paths or '..' (zip slip) are skipped; names are decoded as UTF-8 when the zip says
    so, else as cp437 the way Windows Explorer writes them.
    """
    dest = cache_dir(state, qhash)
    if os.path.exists(os.path.join(dest, COMPLETE)):
        return dest
    if os.path.isdir(dest):
        fsutil.remove_tree(dest)
    os.makedirs(dest, exist_ok=True)
    try:
        with zipfile.ZipFile(fsutil.long(zip_path)) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise ArchiveError("Corrupted archive (bad member %s)" % bad)
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                parts = [p for p in name.split("/") if p not in ("", ".")]
                if not parts or ".." in parts or ":" in parts[0] or name.startswith("/"):
                    continue
                if parts[0] == "__MACOSX" or parts[-1].startswith("._"):
                    continue
                target = os.path.join(dest, *parts)
                if info.is_dir():
                    os.makedirs(fsutil.long(target), exist_ok=True)
                    continue
                os.makedirs(fsutil.long(os.path.dirname(target)), exist_ok=True)
                with zf.open(info) as src, open(fsutil.long(target), "wb") as out:
                    while True:
                        buf = src.read(1 << 20)
                        if not buf:
                            break
                        out.write(buf)
    except ArchiveError:
        fsutil.remove_tree(dest)
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError, NotImplementedError, ValueError) as e:
        fsutil.remove_tree(dest)
        raise ArchiveError("Corrupted archive (%s)" % (str(e) or type(e).__name__))
    with open(os.path.join(dest, COMPLETE), "w") as fh:
        fh.write(os.path.basename(zip_path))
    return dest


def list_files(folder):
    """Relative posix paths of every file under an extracted folder (marker excluded)."""
    out = []
    base = fsutil.long(folder, force=True)
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d != "__MACOSX")
        rel = os.path.relpath(dirpath, base)
        for f in sorted(files):
            if rel == "." and f == COMPLETE:
                continue
            out.append(f if rel == "." else (rel.replace("\\", "/") + "/" + f))
    return out
