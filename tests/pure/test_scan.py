"""Watcher: listing, partial downloads, and the two-poll settling rule."""

import os

from realindex.core import scan


def test_walk_skips_partial_and_hidden(root_state):
    root, _ = root_state
    os.makedirs(os.path.join(root, "a", ".hidden"))
    for p in ("a/x.png", "a/y.fbx.crdownload", "a/.hidden/z.png", "b.part", "~$tmp.blend", "c.zip"):
        with open(os.path.join(root, *p.split("/")), "wb") as fh:
            fh.write(b"12345")
    assert sorted(scan.walk(root)) == ["a/x.png", "c.zip"]


def test_settler_two_polls():
    s = scan.Settler({})
    eff, ch = s.settle({"a": (1, 1)})
    assert not ch                             # first poll after start only records
    eff, ch = s.settle({"a": (1, 1), "b": (5, 1)})
    assert ch and "a" in eff and "b" not in eff  # a seen twice, b once
    s.commit(eff)
    eff, ch = s.settle({"a": (1, 1), "b": (9, 2)})
    assert not ch and "b" not in eff          # b still growing
    eff, ch = s.settle({"a": (1, 1), "b": (9, 2)})
    assert ch and eff["b"] == (9, 2)          # b stable for two polls
    s.commit(eff)
    eff, ch = s.settle({"b": (9, 2)})
    assert not ch and "a" in eff              # a missing once: not yet deleted
    eff, ch = s.settle({"b": (9, 2)})
    assert ch and "a" not in eff              # missing twice: deleted


def test_unchanged_poll_is_cheap(root_state):
    """Poll of an unchanged 10,000-file library: budget 100 ms."""
    import time
    root, _ = root_state
    for d in range(200):
        dd = os.path.join(root, "Materials", "set_%03d" % d)
        os.makedirs(dd)
        for i in range(50):
            with open(os.path.join(dd, "set_%03d_map%02d_Color.jpg" % (d, i)), "wb") as fh:
                fh.write(b"x")
    scan.walk(root)  # warm the OS cache like a running session
    best = 1e9
    s = scan.Settler()
    listing = scan.walk(root)
    s.settle(listing)
    s.commit(listing)
    for _ in range(5):
        t = time.perf_counter()
        cur = scan.walk(root)
        _eff, ch = s.settle(cur)
        best = min(best, time.perf_counter() - t)
        assert not ch
    assert len(listing) == 10000
    from perf_log import record
    record("Poll of an unchanged 10,000-file library", best * 1000, "ms", 100, "200 folders x 50 files, warm cache")
    assert best < 0.1
