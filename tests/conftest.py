"""Shared fixtures. Blender suites (tests/test_*.py) run inside Blender via tests/run_tests.py; the pure
suites (tests/pure) run with plain Python and pytest."""

import os
import shutil
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "_out")
for p in (os.path.join(ROOT, "tests"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import bpy  # noqa: F401
    HAVE_BPY = True
except ImportError:
    HAVE_BPY = False

if not HAVE_BPY:
    collect_ignore_glob = ["test_*.py"]  # only tests/pure runs outside Blender
    if "realindex" not in sys.modules:  # import realindex.core without the bpy-only package __init__
        pkg = types.ModuleType("realindex")
        pkg.__path__ = [os.path.join(ROOT, "realindex")]
        sys.modules["realindex"] = pkg


def _long(p):
    return "\\\\?\\" + os.path.abspath(p) if os.name == "nt" else p


def fresh_dir(name):
    p = os.path.join(OUT, "t", name)
    if os.path.isdir(p):
        shutil.rmtree(_long(p))
    os.makedirs(p)
    return p


@pytest.fixture
def out_dir():
    os.makedirs(OUT, exist_ok=True)
    return OUT


@pytest.fixture
def downloads():
    from fixtures import layout
    return layout.ensure_downloads()


@pytest.fixture
def root_state(request):
    """A fresh empty drop root and state folder, named after the test."""
    name = request.node.name.replace("[", "_").replace("]", "")[:60]
    base = fresh_dir(name)
    root, state = os.path.join(base, "root"), os.path.join(base, "state")
    os.makedirs(root)
    return root, state
