"""Detection properties (Hypothesis): for random file trees every file is owned by at most one asset, the
result is deterministic and independent of input order, nothing crashes, and renaming the root (or moving
the whole tree) changes no grouping."""

import random

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from realindex.core import detect

STEMS = ["Bricks076C_2K-JPG", "brick_wall_001", "T_ChairWood", "SM_Chair", "rock", "meadow", "scene", "OldMetal",
         "planks", "Wood", "chair", "table", "x", "Chair_A", "studio_09"]
SUFFIX = ["_Color", "_NormalGL", "_NormalDX", "_Roughness", "_Displacement", "_diff_2k", "_nor_gl_2k", "_arm_2k",
          "_rough_1k", "_D", "_N", "_R", "_BaseColor", "_Glossiness", "_Metallic", "", "_LOD0", "_LOD1", "_2k",
          "_preview"]
EXTS = [".jpg", ".png", ".exr", ".hdr", ".fbx", ".obj", ".mtl", ".gltf", ".bin", ".glb", ".blend", ".usdc", ".mtlx",
        ".txt", ".max", ".rar", ".stl", ".abc", ".zip", ".dat"]
DIRS = ["", "Inbox", "Models", "Models/Furniture", "Models/Pack/FBX", "Models/Pack/Textures", "Materials",
        "Materials/Rock/rock_2k", "Materials/Rock/rock_2k/textures", "HDRIs", "x.zip", "Models/a.zip/textures"]


class FakeProbe:
    """Deterministic stand-ins for header reads: HDR/EXR are 2:1 unless their name says 'square'."""

    def image_size(self, vf):
        return (512, 512) if "square" in vf.vpath else (1024, 512)

    def model_refs(self, vf):
        rng = random.Random(vf.vpath)
        return {"images": [rng.choice(STEMS).lower() + rng.choice(SUFFIX).lower() + ".png"], "materials": ["M_" + vf.stem]}

    def license(self, vf):
        return {"license": "CC-BY-4.0", "vendor": "Sketchfab"} if "scene" in vf.vpath else {}


file_entry = st.tuples(st.sampled_from(DIRS), st.sampled_from(STEMS), st.sampled_from(SUFFIX), st.sampled_from(EXTS))
trees = st.lists(file_entry, min_size=0, max_size=40)


def build(entries, base="D:/Root"):
    seen, files = set(), []
    for d, stem, suf, ext in entries:
        vp = (d + "/" if d else "") + stem + suf + ext
        if vp.lower() in seen:
            continue
        seen.add(vp.lower())
        files.append(detect.VFile(vp, base + "/" + vp, 100, 1, vp, "h" + vp))
    return files


def run(files, probe=None):
    return [c.to_dict() for c in detect.detect(files, probe or FakeProbe())]


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(trees, st.randoms(use_true_random=False))
def test_detection_properties(entries, rnd):
    files = build(entries)
    res = run(files)
    owned = [f for c in res for f in c["files"]]
    assert len(owned) == len(set(owned)), "a file is owned twice"
    vps = {f.vpath for f in files}
    assert set(owned) <= vps
    for c in res:
        assert set(c["uses"]) <= vps
        assert c["key"] in vps
        assert c["kind"] in ("material", "hdri", "model", "blend", "problem", "ignored")
        if c["kind"] == "material":
            assert c["maps"]
        if c["kind"] == "problem":
            assert c.get("problem")
        assert ":" not in c["catalog"]
    assert run(files) == res  # deterministic
    shuffled = list(files)
    rnd.shuffle(shuffled)
    assert run(shuffled) == res  # order independent
    assert run(build(entries, base="E:/Somewhere Else/Renamed Root")) == res  # root rename changes nothing


@settings(max_examples=100, deadline=None)
@given(trees)
def test_overrides_never_double_claim(entries):
    files = build(entries)
    if not files:
        return
    ov = {files[0].vpath: "ignore"}
    if len(files) > 1:
        ov[files[1].vpath] = "material"
    res = [c.to_dict() for c in detect.detect(files, FakeProbe(), ov)]
    owned = [f for c in res for f in c["files"]]
    assert len(owned) == len(set(owned))
    ign = [c for c in res if c["kind"] == "ignored"]
    assert any(files[0].vpath in c["files"] for c in ign)
