# SPDX-License-Identifier: GPL-3.0-or-later
"""Watcher (SYN): list a drop root by path, size and modified time, and decide which changes have settled.

walk() lists the whole tree with os.scandir; on Windows each directory entry already carries its size and
modified time, so an unchanged 10,000-file root costs one directory read per folder and no per-file stat
. Settling: a new or changed file is only handed to the indexer once two consecutive polls
saw the same size and modified time (half-downloaded files are not grabbed), and a deletion once two polls
missed the file. Pure Python, no bpy: runs on the service thread.
"""

import os

from . import fsutil

# Browser and download-manager partial files: never indexed.
PARTIAL_EXTS = (".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload", ".!ut", ".!qb",
                ".aria2", ".filepart", ".dtapart")
SKIP_DIRS = {"$recycle.bin", "system volume information", "__macosx", ".git", ".svn", ".realindex",
             "@eadir", ".trash", ".trashes", ".spotlight-v100", ".fseventsd"}


def walk(root):
    """{posix relpath: (size, mtime_ns)} for every file under root (hidden and partial files skipped)."""
    out = {}
    stack = [("", fsutil.long(root, force=True))]
    while stack:
        rel, path = stack.pop()
        try:
            it = os.scandir(path)
        except OSError:
            continue
        with it:
            for e in it:
                name = e.name
                if name.startswith("."):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        if name.lower() not in SKIP_DIRS:
                            stack.append((rel + name + "/", e.path))
                        continue
                    if name.lower().endswith(PARTIAL_EXTS) or name.startswith("~$"):
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                out[rel + name] = (st.st_size, st.st_mtime_ns)
    return out


def readable(path):
    """False while another program holds the file open for writing without sharing (Windows copy in flight)."""
    try:
        with open(fsutil.long(path), "rb") as fh:
            fh.read(1)
        return True
    except PermissionError:
        return False
    except OSError:
        return False


class Settler:
    """Tracks consecutive polls; settled() returns the indexed view updated with every change seen twice."""

    def __init__(self, indexed=None):
        self.indexed = dict(indexed or {})   # what the index currently believes
        self.prev = None                     # the previous poll's listing

    def settle(self, current, root=None):
        """Return (new_indexed, changed) given this poll's listing."""
        prev = self.prev
        self.prev = current
        if prev is None:
            return self.indexed, False
        eff = dict(self.indexed)
        changed = False
        for p, v in current.items():
            if self.indexed.get(p) == v:
                continue
            if prev.get(p) != v:
                continue  # still moving: wait for a second identical poll
            if root is not None and not readable(os.path.join(root, p)):
                continue
            eff[p] = v
            changed = True
        for p in self.indexed:
            if p not in current and p not in prev:
                del eff[p]
                changed = True
        return eff, changed

    def commit(self, indexed):
        self.indexed = dict(indexed)

    def pending(self, current):
        """Paths that differ from the index but have not settled yet (for the status line)."""
        return sorted(p for p, v in current.items() if self.indexed.get(p) != v)
