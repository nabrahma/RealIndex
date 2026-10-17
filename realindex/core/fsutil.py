# SPDX-License-Identifier: GPL-3.0-or-later
"""File-system helpers: Windows long paths, the per-user state folder, and guarded deletes.

RealIndex deletes only inside a state folder it owns. remove_tree() refuses any path that is not
inside a folder carrying the RealIndex marker file. Pure Python.
"""

import hashlib
import os
import shutil
import sys

MARKER = "realindex_state.txt"
IS_WIN = sys.platform == "win32"


def long(path, force=False):
    """A path Windows accepts beyond 260 characters (\\\\?\\ prefix); unchanged elsewhere or when short.
    force: prefix short paths too (a folder walk whose children may pass the limit)."""
    if not IS_WIN or (len(path) < 240 and not force) or path.startswith("\\\\?\\"):
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def norm(path):
    return os.path.normcase(os.path.abspath(path))


def data_home():
    """Per-user data folder for RealIndex (outside every drop root: Blender's library scan also lists .blend files in hidden folders)."""
    if os.environ.get("REALINDEX_DATA"):
        return os.environ["REALINDEX_DATA"]
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "RealIndex")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/RealIndex")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "realindex")


def default_state_dir(root):
    """State folder for a drop root: <data home>/libraries/<root name>-<hash of its path>."""
    name = os.path.basename(os.path.normpath(root)) or "root"
    digest = hashlib.sha1(norm(root).encode("utf-8")).hexdigest()[:8]
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "root"
    return os.path.join(data_home(), "libraries", "%s-%s" % (safe, digest))


def ensure_state_dir(state, root):
    os.makedirs(state, exist_ok=True)
    marker = os.path.join(state, MARKER)
    if not os.path.exists(marker):
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("RealIndex state folder for drop root:\n%s\nSafe to delete: Rebuild recreates it.\n" % root)
    for sub in ("library", "cache", "previews", "logs", "jobs", "trash"):
        os.makedirs(os.path.join(state, sub), exist_ok=True)
    return state


def inside(path, folder):
    p, f = norm(path), norm(folder)
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def owned_state(path):
    """The state folder (with marker) that contains path, or None."""
    cur = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(cur, MARKER)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def remove_tree(path):
    """Delete a folder inside a RealIndex state folder; refuses anything else (and the state root itself)."""
    st = owned_state(path)
    if st is None or norm(st) == norm(path):
        raise PermissionError("RealIndex refuses to delete outside its state folder: %s" % path)
    if os.path.isdir(long(path)):
        shutil.rmtree(long(path), ignore_errors=True)


def remove_file(path):
    if owned_state(path) is None:
        raise PermissionError("RealIndex refuses to delete outside its state folder: %s" % path)
    try:
        os.remove(long(path))
    except FileNotFoundError:
        pass


def dir_size(path):
    total = 0
    for dirpath, _dirs, files in os.walk(long(path)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def set_hidden(path):
    """Mark a folder hidden on Windows (cosmetic; Blender's library scan ignores the flag)."""
    if not IS_WIN:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
    except Exception:
        pass


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
