# Changelog

## 1.1.0 (2026-10-18)

- Height maps become true displacement for Cycles: a Displacement node into the material output, with the
  vendor's own scale and midlevel when its .blend is beside the maps (Poly Haven), otherwise 3% of the texture's
  real-world size. *Height Maps: Bump Only* in the preferences keeps the previous bump.
- *Set Up Displacement* adds an adaptive Subdivision modifier; Quick Place does it automatically for displaced
  materials. The sidebar shows the material's Displacement and Midlevel.
- Specular maps are wired to Specular IOR Level, converting spec/gloss reflectance (F0) maps.
- New maps: transmission, subsurface (SSS), translucency (thin wall), sheen/fuzz; cavity is recognised. Megascans
  surfaces no longer leave their Cavity, Fuzz and Translucency maps under Problems.
- Non-square texture sets keep square pixels.

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
