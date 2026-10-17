# SPDX-License-Identifier: GPL-3.0-or-later
"""Indexer front half: turn a root listing into detection's virtual file list, extracting zips (nested ones
too) into the state cache. Pure Python."""

import os
import posixpath

from . import archive, detect, fileinfo

MAX_NESTING = 3


class Expanded:
    def __init__(self):
        self.vfiles = []          # detect.VFile
        self.archive_problems = []  # (zip vpath, src, reason)
        self.zip_hashes = {}      # root relpath of a zip -> quick hash (its cache folder)


def expand(root, state, listing, hashes, hash_fn=None):
    """listing: {relpath: (size, mtime_ns)}; hashes: {relpath: qhash} cache, filled in place for new files.

    Zips become folders: member 'a.jpg' of 'Materials/X.zip' is VFile('Materials/X.zip/a.jpg').
    """
    hash_fn = hash_fn or fileinfo.quick_hash
    out = Expanded()
    for rel in sorted(listing):
        size, mtime = listing[rel]
        real = os.path.join(root, *rel.split("/"))
        key = (rel, size, mtime)
        if hashes.get(key) is None:
            try:
                hashes[key] = hash_fn(real, size)
            except OSError:
                continue
        qh = hashes[key]
        if rel.lower().endswith(".zip"):
            out.zip_hashes[rel] = qh
            _expand_zip(out, rel, real, qh, size, mtime, rel, state, 0)
        else:
            out.vfiles.append(detect.VFile(rel, real, size, mtime, rel, qh))
    return out


def _expand_zip(out, vpath, real, qh, size, mtime, src, state, depth):
    try:
        folder = archive.extract(real, state, qh)
    except archive.ArchiveError as e:
        out.archive_problems.append((vpath, src, str(e)))
        return
    for member in archive.list_files(folder):
        mreal = os.path.join(folder, *member.split("/"))
        mv = vpath + "/" + member
        if member.lower().endswith(".zip") and depth < MAX_NESTING:
            try:
                mqh = fileinfo.quick_hash(mreal)
            except OSError:
                continue
            _expand_zip(out, mv, mreal, mqh, size, mtime, src, state, depth + 1)
            continue
        # members share the zip's size and time: replacing the zip changes every member's fingerprint
        out.vfiles.append(detect.VFile(mv, mreal, size, mtime, src, qh + ":" + member))


def archive_problem_candidates(expanded):
    out = []
    for vpath, src, reason in expanded.archive_problems:
        c = detect.Candidate(vpath, "problem", posixpath.splitext(posixpath.basename(vpath))[0], [])
        c.problem = reason
        c.meta["source"] = src
        out.append(c)
    return out


def run_detection(root, state, listing, hashes, overrides=None, probe=None):
    """Expand + detect: the whole indexing pass for one root. Returns (candidates, expanded)."""
    exp = expand(root, state, listing, hashes)
    cands = detect.detect(exp.vfiles, probe or detect.Probe(), overrides or {})
    probs = archive_problem_candidates(exp)
    claimed = {p for c in cands for p in c.files}
    for c in probs:
        if c.key not in claimed:
            c.files = [c.key]
            cands.append(c)
    cands.sort(key=lambda c: c.key)
    return cands, exp


def unused_cache_dirs(state, live_hashes):
    """Cache folders whose zip is gone (Clear Cache)."""
    cache = os.path.join(state, "cache")
    if not os.path.isdir(cache):
        return []
    live = set(live_hashes)
    return [os.path.join(cache, d) for d in sorted(os.listdir(cache)) if d not in live]


def nested_hashes(state, top_hashes):
    """Hashes of nested zips extracted from the given top-level zips (kept by Clear Cache)."""
    keep = set(top_hashes)
    todo = list(top_hashes)
    while todo:
        h = todo.pop()
        folder = archive.cache_dir(state, h)
        if not os.path.isdir(folder):
            continue
        for member in archive.list_files(folder):
            if member.lower().endswith(".zip"):
                try:
                    mh = fileinfo.quick_hash(os.path.join(folder, *member.split("/")))
                except OSError:
                    continue
                if mh not in keep:
                    keep.add(mh)
                    todo.append(mh)
    return keep

