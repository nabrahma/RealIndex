# SPDX-License-Identifier: GPL-3.0-or-later
"""RealIndex read-only API for other add-ons.

    import importlib
    ri = importlib.import_module(<the enabled RealIndex module> + ".api")
    for t in ri.texture_sets():
        t["name"], t["folder"], t["maps"]["color"], t["size_m"], t["preview"], t["tags"]

Every function returns plain dicts: name, type, catalog, vendor, licence, author, tags, preview (png path or
None), source (path in the drop folder), sources (files on disk), managed (asset .blend) and, for texture
sets, maps {kind: path}, folder and size_m (real-world size in metres). Nothing here writes anything.
"""

from .core import api as _api

API_VERSION = 1


def _states():
    from . import runtime
    return runtime.states()


def texture_sets():
    return _api.load(_states(), ["material"])


def models():
    return _api.load(_states(), ["model", "blend"])


def hdris():
    return _api.load(_states(), ["hdri"])


def find(query, limit=48):
    """Assets whose name, tags, catalog or vendor match every word of query, best first."""
    return _api.find(_states(), query, limit=limit)
