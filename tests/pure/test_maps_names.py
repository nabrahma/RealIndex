"""PBR map naming, colour spaces, tags, sizes, catalog ids, managed paths and search ranking."""

import time

import pytest

from realindex.core import library, maps, names, search


@pytest.mark.parametrize("stem,kind,prefix,res,dx", [
    ("Bricks076C_2K-JPG_Color", "color", "Bricks076C", "2K", False),
    ("Bricks076C_2K-JPG_NormalGL", "normal", "Bricks076C", "2K", False),
    ("Bricks076C_2K-JPG_NormalDX", "normal", "Bricks076C", "2K", True),
    ("Bricks076C_2K-JPG_AmbientOcclusion", "ao", "Bricks076C", "2K", False),
    ("Bricks076C_2K-JPG_Displacement", "height", "Bricks076C", "2K", False),
    ("brick_wall_001_diff_2k", "color", "brick_wall_001", "2K", False),
    ("brick_wall_001_nor_gl_2k", "normal", "brick_wall_001", "2K", False),
    ("brick_wall_001_nor_dx_4k", "normal", "brick_wall_001", "4K", True),
    ("brick_wall_001_arm_2k", "arm", "brick_wall_001", "2K", False),
    ("T_ChairWood_D", "color", "T_ChairWood", None, False),
    ("T_ChairWoodBaseColor", "color", "T_ChairWood", None, False),
    ("OldMetal_Glossiness", "gloss", "OldMetal", None, False),
    ("planks_rough_1024", "rough", "planks", "1K", False),
    ("vjkqfg_2K_Translucency", "translucency", "vjkqfg", "2K", False),
    ("vjkqfg_2K_Fuzz", "sheen", "vjkqfg", "2K", False),
    ("vjkqfg_2K_Cavity", "cavity", "vjkqfg", "2K", False),
    ("WaxCandle_SSS", "sss", "WaxCandle", None, False),
    ("Glass_Pattern_TRANSMISSION", "transmission", "Glass_Pattern", None, False),
    ("fern_leaf_translucent_2k", "translucency", "fern_leaf", "2K", False),
    ("Bricks076C", None, "Bricks076C", None, False),
    ("meadow_2k", None, "meadow", "2K", False),
    ("Chair_A", None, "Chair_A", None, False),
])
def test_parse(stem, kind, prefix, res, dx):
    p = maps.parse(stem)
    assert (p.kind, p.prefix, p.res, p.dx) == (kind, prefix, res, dx)


def test_colour_spaces():
    assert maps.colorspace("color") == "sRGB" and maps.colorspace("emission") == "sRGB"
    for k in ("rough", "gloss", "metal", "normal", "height", "ao", "arm", "opacity", "specular", "transmission",
              "sss", "translucency", "sheen", "cavity"):
        assert maps.colorspace(k) == "Non-Color"


def test_pick_prefers_gl_and_rough():
    found = [("normal", "a_NormalDX.jpg", maps.parse("a_NormalDX")), ("normal", "a_NormalGL.jpg", maps.parse("a_NormalGL")),
             ("rough", "a_Roughness.jpg", maps.parse("a_Roughness")), ("gloss", "a_Gloss.jpg", maps.parse("a_Gloss"))]
    p = maps.pick(found)
    assert p["normal"] == {"path": "a_NormalGL.jpg", "dx": False}
    assert "gloss" not in p and p["rough"]["path"] == "a_Roughness.jpg"


def test_sizes_and_tags():
    assert names.real_size_m("wall_2m") == 2.0
    assert names.real_size_m("tiles-150cm-x") == pytest.approx(1.5)
    assert names.real_size_m("rock_1.5x1.5m") == pytest.approx(1.5)
    assert names.real_size_m("Bricks076C_2K-JPG") is None
    assert names.tag_words("Chair_WoodenOak_LOD0", "Models/Furniture/Chairs") == ["chair", "wooden", "oak", "furniture",
                                                                                   "chairs"]


def test_catalog_ids_deterministic():
    a = names.catalog_id("Models/Furniture")
    assert a == names.catalog_id("/Models/Furniture/") and a != names.catalog_id("Models/Furniture/Chairs")
    t1 = library.cats_text(["Models/Furniture/Chairs", "Materials"])
    t2 = library.cats_text(["Materials", "Models/Furniture/Chairs", "Models"])
    assert t1 == t2 and "Models/Furniture:Models-Furniture" in t1


def test_assign_paths_collisions_are_order_independent():
    items = [("b/x.fbx", "Models", "Chair"), ("a/x.fbx", "Models", "Chair"), ("c.fbx", "Models", "Table")]
    p1 = library.assign_paths(items)
    p2 = library.assign_paths(list(reversed(items)))
    assert p1 == p2
    assert p1["a/x.fbx"] == "Models/Chair.blend" and p1["b/x.fbx"].startswith("Models/Chair_")
    assert names.safe_filename('a:b*c?"d') == "a_b_c__d"


def test_search_ranking_and_speed():
    import random
    rng = random.Random(1)
    words = ["brick", "wall", "wood", "oak", "chair", "rock", "moss", "metal", "rust", "tile", "floor", "sky"]
    recs = []
    for i in range(10000):
        n = "_".join(rng.choice(words) for _ in range(3)) + "_%d" % i
        recs.append({"name": n, "tags": [rng.choice(words)], "catalog": "Materials/" + rng.choice(words),
                     "vendor": rng.choice(["ambientCG", "Poly Haven", None]), "type": "material",
                     "last_used": rng.random() if i % 7 == 0 else None})
    recs.append({"name": "Brick", "tags": [], "catalog": "", "vendor": None, "type": "material"})
    items = [search.Item(r) for r in recs]
    worst = 0.0
    for q in ("b", "br", "bri", "bric", "brick", "brick w", "brick wa", "brick wall"):
        t = time.perf_counter()
        res = search.search(items, q)
        worst = max(worst, time.perf_counter() - t)
        assert res
    assert search.search(items, "brick")[0].data["name"] == "Brick"  # exact name first
    assert all("brick" in r.data["name"] and "wall" in r.data["name"] for r in search.search(items, "brick wall"))
    from perf_log import record
    record("Quick Place search, 10,000 assets (worst keystroke)", worst * 1000, "ms", 100)
    assert worst < 0.1
