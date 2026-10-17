# SPDX-License-Identifier: GPL-3.0-or-later
"""Quick Place search: every typed word must match a name word, a tag, the catalog or the vendor;
results ranked by match quality, then recent use. Pure Python; 10,000 assets well under 100 ms."""

import re

from . import names

_SPLIT = re.compile(r"\s+")


class Item:
    """A searchable asset. Precomputes lower-case words once so each keystroke is cheap."""
    __slots__ = ("data", "name_l", "words", "tags", "cat_l", "vendor_l", "last_used", "favourite", "type")

    def __init__(self, data):
        self.data = data
        self.name_l = (data.get("name") or "").lower()
        self.words = [w.lower() for w in names.split_words(data.get("name") or "")] + names.tokens(self.name_l)
        self.tags = [t.lower() for t in data.get("tags") or []]
        self.cat_l = (data.get("catalog") or "").lower()
        self.vendor_l = (data.get("vendor") or "").lower()
        self.last_used = data.get("last_used") or 0.0
        self.favourite = bool(data.get("favourite"))
        self.type = data.get("type")


def _term_score(term, it):
    if it.name_l == term:
        return 10.0
    if it.name_l.startswith(term):
        return 6.0
    if any(w.startswith(term) for w in it.words):
        return 4.0
    if term in it.name_l:
        return 3.0
    if term in it.tags:
        return 2.5
    if any(t.startswith(term) for t in it.tags):
        return 2.0
    if term in it.vendor_l:
        return 1.5
    if term in it.cat_l:
        return 1.0
    return 0.0


def search(items, query, types=None, catalog="", vendor="", recent=False, favourites=False, limit=48):
    """items: [Item]. Returns the best matching Items, best first."""
    terms = [t for t in _SPLIT.split(query.lower().strip()) if t]
    cat = catalog.lower()
    out = []
    for it in items:
        if types and it.type not in types:
            continue
        if cat and not (it.cat_l == cat or it.cat_l.startswith(cat + "/")):
            continue
        if vendor and it.vendor_l != vendor.lower():
            continue
        if favourites and not it.favourite:
            continue
        if recent and not it.last_used:
            continue
        score = 0.0
        for t in terms:
            s = _term_score(t, it)
            if s <= 0:
                break
            score += s
        else:
            out.append((-score, -it.last_used, not it.favourite, it.name_l, it))
    out.sort(key=lambda r: r[:4])
    return [r[4] for r in out[:limit]]
