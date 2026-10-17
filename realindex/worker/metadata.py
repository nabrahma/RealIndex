# SPDX-License-Identifier: GPL-3.0-or-later
"""Asset marking and metadata inside the background worker."""

import bpy

# Blender 5.2.1 accepts only Link, Append and Pack from Python (Append (Reuse Data) is refused); every type
# appends, and Quick Place reuses materials and worlds already in the file.
IMPORT_METHOD = {"model": "APPEND", "blend": "APPEND", "material": "APPEND", "hdri": "APPEND"}


def apply(idb, job, asset_type, tags=None):
    """Mark idb as an asset and fill its catalog, tags, author, licence, description and import method."""
    if idb.asset_data is None:
        idb.asset_mark()
    ad = idb.asset_data
    if job.get("catalog_id"):
        ad.catalog_id = job["catalog_id"]
    meta = job.get("meta", {})
    ad.author = meta.get("author", "") or ""
    ad.license = meta.get("license", "") or ""
    ad.description = meta.get("description", "") or ""
    ad.copyright = meta.get("copyright", "") or ""
    want = list(dict.fromkeys(list(tags if tags is not None else job.get("tags", []))))
    for t in list(ad.tags):
        if t.name not in want:
            ad.tags.remove(t)
    have = {t.name for t in ad.tags}
    for t in want:
        if t not in have:
            ad.tags.new(t, skip_if_exists=True)
    method = IMPORT_METHOD.get(asset_type)
    if method:
        ad.preferred_import_method = method
        ad.use_preferred_import_method = True
    idb["ri_asset_id"] = job.get("asset_id", "")
    return ad


def asset_ids():
    """Every ID in the open file that is marked as an asset."""
    out = []
    for attr in ("collections", "objects", "materials", "worlds", "node_groups", "meshes", "images", "actions",
                 "brushes", "palettes", "scenes", "texts", "curves"):
        for idb in getattr(bpy.data, attr, []):
            if idb.asset_data is not None and idb.library is None:
                out.append(idb)
    return out
