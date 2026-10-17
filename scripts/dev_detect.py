"""Print what detection makes of a folder (or of the merged fixture root), without Blender.

    python scripts/dev_detect.py                 # merged fixtures in tests/_out/golden_root
    python scripts/dev_detect.py D:/SomeFolder   # any folder (read only; cache goes to tests/_out)
"""
import os
import shutil
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]
if "realindex" not in sys.modules:
    pkg = types.ModuleType("realindex")
    pkg.__path__ = [os.path.join(ROOT, "realindex")]
    sys.modules["realindex"] = pkg

from realindex.core import fsutil, indexer, scan  # noqa: E402


def main():
    out = os.path.join(ROOT, "tests", "_out")
    state = os.path.join(out, "dev_detect_state")
    if len(sys.argv) > 1:
        root = os.path.abspath(sys.argv[1])
    else:
        from fixtures import layout
        layout.ensure_downloads()
        root = os.path.join(out, "golden_root")
        if os.path.isdir(root):
            shutil.rmtree(fsutil.long(root))
        layout.merge(root)
    fsutil.ensure_state_dir(state, root)
    listing = scan.walk(root)
    print(len(listing), "files under", root)
    cands, _exp = indexer.run_detection(root, state, listing, {})
    for c in cands:
        print("%-8s %-40s %-32r %-11s files=%-2d uses=%d maps=%s lods=%d %s" % (
            c.kind, c.catalog, c.name, c.vendor, len(c.files), len(c.uses), ",".join(sorted(c.maps)),
            len(c.lods), c.problem or ""))


if __name__ == "__main__":
    main()
