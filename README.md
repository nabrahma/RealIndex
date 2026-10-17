# RealIndex

**Drop it in the folder. It shows up in Blender.**

RealIndex is a Blender 5.2 LTS extension that turns one folder on disk into a living asset library.
Download a model from Sketchfab, a texture set from ambientCG or Poly Haven, or an HDRI, drop it (zipped or
not) into your RealIndex folder, and a few seconds later it is in Blender's Asset Browser with a thumbnail,
ready to drag into the scene. No importing, no marking as asset, no saving library files, no manual previews.
Your downloaded files are never modified.

## Five-minute walk-through

1. **Install** `realindex-1.0.0.zip` (Edit > Preferences > Get Extensions > Install from Disk) and enable it.
2. **Set up** in the 3D Viewport sidebar (N) > RealIndex > *Set Up RealIndex*. It creates
   `Documents/RealIndex` with `Models`, `Materials`, `HDRIs` and `Inbox`, and registers two asset libraries:
   **RealIndex** (your converted downloads) and **RealIndex Files** (the folder itself, for .blend files whose
   assets are already marked).
3. **Drop** a download anywhere in the folder:
   - into `Inbox` or loose: RealIndex works out what it is and files it under `Materials/ambientCG`,
     `Models/Sketchfab`, `HDRIs/Poly Haven`, ... (Inbox items are moved there; every move is logged and can be
     undone from the panel);
   - into your own subfolder such as `Models/Furniture/Chairs`: that folder path becomes the catalog;
   - zipped or not: zips are extracted to a cache, the zip itself is left alone.
4. **Keep working.** The Asset Browser header shows `RealIndex: 3 new, converting 1 of 3`. Conversion runs
   in background Blender processes; Blender never freezes.
5. **Use it.** Open the Asset Browser, pick the RealIndex library, drag a model into the scene, a material onto
   an object, an HDRI into the viewport. Or assign a shortcut to **Quick Place** (Preferences > Add-ons >
   RealIndex): type a few letters and place at the surface under the mouse.

Rename or move a file and the asset follows (no reconversion). Replace a texture and the asset updates.
Delete a file and its asset is hidden, then removed once you confirm in the panel.

## What can be dropped

| Download | Becomes |
| --- | --- |
| Texture sets (a folder or zip of `_Color`, `_Roughness`, `_Normal`, `_arm_`, ... maps) | A material: Principled BSDF, correct colour spaces, gloss inverted, DirectX normals flipped, ARM/ORM split, height as bump, real-world scale from the name (`2m`, `150cm`) |
| HDRIs (`.hdr`, `.exr`, about 2:1) | A world: Environment Texture with a rotation value and strength |
| Models: `.fbx`, `.obj`+`.mtl`, `.gltf`, `.glb`, `.usd*`, `.abc`, `.stl`, `.ply` | A collection (or object) asset: unit mistakes fixed, origin at the bottom centre, textures relinked even when the names in the file do not match, LOD0 kept |
| `.blend` files | Marked assets: shown as they are in RealIndex Files. Unmarked: their top-level collections become assets |
| ambientCG, Poly Haven, Sketchfab, marketplace packs | Recognised: names, resolutions, previews, licences (CC0, or read from Sketchfab's `license.txt`) |

Anything RealIndex cannot use (an unsupported format, a corrupted zip, a texture set without a colour map)
is listed under **Problems** in the panel with the reason, *Retry* and *Ignore*. Per asset you can override
detection (Texture Set, Model, HDRI, Ignore), reconvert, show the source file, star it and edit its tags.

## Where things live

- Your drop folders are only read (Inbox filing excepted).
- Each drop folder has a **library folder** (default `%LOCALAPPDATA%\RealIndex\libraries\<folder>-<hash>`):
  the index, extracted zips, previews and one small .blend per asset referencing your textures. It is safe to
  delete: *Rebuild Library* recreates it with the same catalogs. Pick a local disk for it in Preferences to
  keep an external drive's library browsable while unplugged.
- *Publish as Remote Library* writes a packed, self-contained copy plus Blender's listing, ready for any static
  web server (Blender 5.2 remote libraries).

## For other add-ons

`<RealIndex module>.api` offers `texture_sets()`, `models()`, `hdris()` and `find(query)`: plain dicts with
names, paths, previews, tags and, for texture sets, the detected maps and real-world size. RealWorn's Load
Texture library lists RealIndex texture sets when both are enabled.

## Performance

Measured on a Ryzen 7 6800HS laptop (RTX 3060, NVMe, Windows 11, Blender 5.2.1) with real CC0 downloads,
four workers and the default 2 s poll. "Visible" means the asset file is in the library.

| Measure | Budget | Result |
| --- | --- | --- |
| ambientCG 2K zip, drop to visible (vendor preview) | 10 s | 4.8 s |
| Poly Haven 2K texture set, drop to visible (rendered preview) | 20 s | 7.1 s |
| glTF model, 50k triangles, drop to visible | 30 s | 7.3 s |
| 4K HDRI, drop to visible | 10 s | 5.0 s |
| 50 mixed downloads (55 assets) | 5 min | 43 s |
| Library size vs drop folder | under 5% | 1.7% |
| Poll of an unchanged 10,000-file folder | 100 ms | 36 ms |
| Blender UI time per RealIndex timer tick | 50 ms | 0.13 ms |
| Quick Place search over 10,000 assets | 100 ms | 24 ms |

On a busy machine conversions take longer; they never block Blender (workers run at below-normal priority).

## Development

Requirements: Blender 5.2 (set `BLENDER_BIN` in the environment or in a `.env` file at the repository root)
and Python 3.11+ with pytest and Hypothesis for the pure suite.

```text
python -m pytest tests/pure                                   # detection, sync, safety (no Blender)
"$BLENDER_BIN" -b --factory-startup --python scripts/setup_test_env.py   # once: pytest for Blender's Python
"$BLENDER_BIN" -b --factory-startup --python tests/run_tests.py          # every suite, real workers
python tests/corpus/fetch.py                                  # optional: real CC0 downloads
python scripts/perf.py                                        # performance on those downloads
python scripts/dev_detect.py FOLDER                           # what detection makes of any folder
python scripts/package.py                                     # dist/realindex-1.0.0.zip
```

Test fixtures are generated from code (`tests/fixtures/make_downloads.py`): every vendor quirk, model format
and broken download the detector has to handle, with golden results in `tests/golden/`. Regenerate goldens
after a deliberate detection change with `RI_UPDATE_GOLDEN=1 python -m pytest tests/pure/test_detect_golden.py`
and review the diff.

Layout: `realindex/core` is pure Python (detection, index, sync engine, worker pool), `realindex/worker` runs
inside background Blender processes (importing, building materials and worlds, previews), the rest is the
add-on UI.

## Licence

GPL-3.0-or-later. Fixtures are generated; the optional corpus downloads are CC0 (ambientCG, Poly Haven).
