"""Detection golden suite: each fixture's grouping, types, maps and vendor match a stored expected JSON exactly.

Every new vendor quirk gets a fixture and a golden first. Regenerate after a deliberate change with
RI_UPDATE_GOLDEN=1 and review the diff."""

import json
import os

import pytest

from conftest import fresh_dir
from fixtures import layout
from realindex.core import fsutil, indexer, scan

GOLDEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "golden")
UPDATE = os.environ.get("RI_UPDATE_GOLDEN") == "1"


def detect_root(root, state):
    fsutil.ensure_state_dir(state, root)
    cands, _ = indexer.run_detection(root, state, scan.walk(root), {})
    return [c.to_dict() for c in cands]


def check(name, result):
    path = os.path.join(GOLDEN, name + ".json")
    text = json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False)
    if UPDATE or not os.path.exists(path):
        os.makedirs(GOLDEN, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        if not UPDATE:
            pytest.skip("golden %s written for review" % name)
    with open(path, encoding="utf-8") as fh:
        expected = json.load(fh)
    assert result == expected


def fixture_ids():
    layout.ensure_downloads()
    return layout.fixture_ids()


@pytest.mark.parametrize("fid", fixture_ids())
def test_fixture_golden(fid):
    base = fresh_dir("golden_" + fid)
    root, state = os.path.join(base, "root"), os.path.join(base, "state")
    layout.copy_fixture(fid, root)
    check(fid, detect_root(root, state))


def test_merged_golden():
    base = fresh_dir("golden_merged")
    root, state = os.path.join(base, "root"), os.path.join(base, "state")
    layout.merge(root)
    check("merged", detect_root(root, state))


def test_expectations():
    """Readable assertions on top of the goldens: what a person would say about each download."""
    base = fresh_dir("golden_expect")
    root, state = os.path.join(base, "root"), os.path.join(base, "state")
    layout.merge(root)
    res = {c["name"]: c for c in detect_root(root, state)}
    acg = res["Bricks076C"]
    assert acg["kind"] == "material" and acg["vendor"] == "ambientCG" and acg["catalog"] == "Materials/ambientCG"
    assert set(acg["maps"]) == {"color", "normal", "rough", "height", "ao"} and not acg["maps"]["normal"]["dx"]
    assert acg["meta"]["license"] == "CC0" and acg["meta"]["preview"].endswith("Bricks076C.png")
    ph = res["rock_wall_08"]
    assert ph["vendor"] == "Poly Haven" and ph["maps"]["normal"]["dx"] and "arm" in ph["maps"]
    assert res["meadow"]["kind"] == "hdri" and res["meadow"]["vendor"] == "Poly Haven"
    chair = res["Wooden Chair"]
    assert chair["kind"] == "model" and chair["vendor"] == "Sketchfab" and chair["meta"]["license"] == "CC-BY-4.0"
    assert chair["meta"]["author"] == "Jane Maker" and chair["catalog"] == "Models/Furniture"
    barrel = res["SM_Barrel"]
    assert barrel["catalog"] == "Models/MedievalPack" and len(barrel["lods"]) == 1
    assert sorted(os.path.basename(f) for f in barrel["files"] if f.endswith(".png")) == [
        "T_BarrelWood_D.png", "T_BarrelWood_N.png", "T_BarrelWood_R.png"]
    crate = res["SM_Crate"]
    assert sorted(os.path.basename(f) for f in crate["files"] if f.endswith(".png")) == [
        "T_CrateOak_BaseColor.png", "T_CrateOak_Normal.png"]
    assert res["planks 1K"]["meta"]["res"] == "1K" and res["planks 2K"]["meta"]["res"] == "2K"
    assert res["OldMetal"]["maps"].keys() >= {"color", "gloss", "metal", "normal"}
    tiles = res["grès_tiles"]
    assert tiles["meta"]["size_m"] == 2.0
    assert res["Tiles012"]["vendor"] == "ambientCG"  # nested zip
    assert res["table"]["kind"] == "model" and any(f.endswith("table.mtl") for f in res["table"]["files"])
    assert res["AssetPack"]["kind"] == "blend" and res["Props"]["kind"] == "blend"
    probs = {c["name"]: c["problem"] for c in res.values() if c["kind"] == "problem"}
    assert "export to FBX or glTF" in probs["old_lamp"]
    assert "Corrupted archive" in probs["rocks_pack"]
    assert "not part of a texture set" in probs["decal_grunge"]
    assert "readme" not in " ".join(res)  # known extras are silent
    assert res["Concrete_Long_Path_Material"]["kind"] == "material"
