"""With both add-ons enabled, RealWorn's Load Texture library lists every RealIndex texture set, and
loading one through RealWorn wires its maps. Skipped when RealWorn (REALWORN_DIR in .env) is not present."""

import os
import sys

import bpy
import pytest

import convert_util as cu
from realindex import prefs


def _realworn_dir():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    import _common
    env = os.path.join(_common.ROOT, ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.startswith("REALWORN_DIR"):
                return line.split("=", 1)[1].strip().strip('"')
    return os.environ.get("REALWORN_DIR")


@pytest.fixture
def realworn():
    d = _realworn_dir()
    if not d or not os.path.isdir(os.path.join(d, "realworn")):
        pytest.skip("RealWorn not available")
    if d not in sys.path:
        sys.path.insert(0, d)
    import addon_utils
    mod = addon_utils.enable("realworn", default_set=True)
    if mod is None:
        pytest.skip("RealWorn did not enable")
    yield mod
    addon_utils.disable("realworn", default_set=True)


def test_realworn_lists_realindex_sets(realworn):
    eng, pool, root, state, _dt = cu.converted()
    p = prefs.get()
    p.roots.clear()
    r = p.roots.add()
    r.path, r.library_dir = root, state
    try:
        from realworn.core import pbrset
        if not hasattr(pbrset, "realindex_sets"):
            pytest.skip("this RealWorn branch has no RealIndex source")
        import realindex.api as ri
        sets = ri.texture_sets()
        entries = pbrset.realindex_sets()
        assert {e["name"] for e in entries} == {"RealIndex: " + s["name"] for s in sets}
        items = pbrset.library_enum_items("")
        labels = [i[1] for i in items]
        assert "RealIndex: Bricks76C" not in labels and "RealIndex: Bricks076C" in labels
        acg = next(e for e in entries if e["name"] == "RealIndex: Bricks076C")
        maps = pbrset.detect(acg["path"])
        assert "color" in maps and "normal" in maps  # RealWorn reads the extracted ambientCG set directly
    finally:
        p.roots.clear()
