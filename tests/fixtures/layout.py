"""Fixture layout helpers shared by the generator, the pure tests and the Blender suites (no bpy)."""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DOWNLOADS = os.path.join(ROOT, "tests", "_out", "downloads")

# Deep enough that the absolute path of its files passes Windows' 260-character limit.
LONG_DIR = "Materials/Long/" + "/".join(["very_long_folder_name_%02d_for_windows_path_limits" % i for i in range(5)])

# What conversion must produce for the model fixtures (sizes in metres after normalisation).
EXPECT = {
    "Models/Parts/Bracket.stl": {"dims": (0.8, 0.1, 0.3)},
    "Models/Parts/Gear.ply": {"dims": (0.5, 0.5, 0.05)},
    "Models/Furniture/Table/table.obj": {"dims": (1.2, 0.7, 0.745)},
}


def blender_bin():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from _common import blender_bin as bb
    return bb()


def ensure_downloads(only=None):
    """Generate the fixtures once (needs Blender); returns the downloads folder."""
    if not os.path.exists(os.path.join(DOWNLOADS, "FIXTURES_OK")):
        args = [blender_bin(), "--background", "--factory-startup", "--python",
                os.path.join(HERE, "make_downloads.py"), "--", DOWNLOADS]
        subprocess.run(args, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    return DOWNLOADS


def fixture_ids():
    with open(os.path.join(DOWNLOADS, "FIXTURES_OK")) as fh:
        return [l.strip() for l in fh if l.strip()]


def _long(p):
    return "\\\\?\\" + os.path.abspath(p) if os.name == "nt" else p


def copy_fixture(fid, root):
    """Copy one fixture's mini root into root (merging)."""
    shutil.copytree(_long(os.path.join(DOWNLOADS, fid)), _long(root), dirs_exist_ok=True)


def merge(root, ids=None):
    for fid in ids or fixture_ids():
        copy_fixture(fid, root)
    return root
