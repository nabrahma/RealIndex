# Changelog

## 1.0.0 (2026-10-17)

First release, for Blender 5.2 LTS.

- Watches one or more drop folders and turns each download into a native Blender asset: texture sets into
  materials, HDRIs into worlds, models (FBX, OBJ, glTF/GLB, USD, Alembic, STL, PLY) into collections, and .blend
  files into marked assets.
- Recognises ambientCG, Poly Haven, Sketchfab and common marketplace layouts: names, resolutions, vendor
  previews and licences.
- Reads zips without touching them, fixes units and origins, relinks textures with mismatched names and keeps LOD0.
- Folder paths become catalogs. Renames and moves follow the asset without reconverting.
- Optional Inbox filing, with a log and undo.
- Problems list with Retry and Ignore, per-asset detection override, tags, stars and reconvert.
- Quick Place: search and place at the surface under the mouse.
- Publish as a Blender 5.2 remote library.
- Read-only Python API for other add-ons (`texture_sets()`, `models()`, `hdris()`, `find()`); RealWorn lists
  RealIndex texture sets.
