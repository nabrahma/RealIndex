# Contributing to RealIndex

Bug reports, vendor samples and pull requests are all welcome.

## Reporting a download that does not work

Most problems are a download RealIndex reads wrongly. Open a
[bug report](https://github.com/nabrahma/RealIndex/issues/new?template=bug_report.yml) with where the download
came from, its file list and the reason shown under **Problems**. A link to a free sample of the same kind is the
most useful thing you can add.

## Setting up

You need Blender 5.2 and Python 3.11 or newer.

```text
git clone https://github.com/nabrahma/RealIndex
cd RealIndex
echo BLENDER_BIN="C:/path/to/blender.exe" > .env        # or export BLENDER_BIN
python -m pip install pytest hypothesis
"$BLENDER_BIN" -b --factory-startup --python scripts/setup_test_env.py   # pytest for Blender's Python
```

To try your working copy in Blender, link or copy the `realindex` folder into your extensions directory
(`.../extensions/user_default/realindex`), or build the zip with `python scripts/package.py` and install it
from disk.

## Running the tests

```text
python -m pytest tests/pure                                           # fast, no Blender UI
"$BLENDER_BIN" -b --factory-startup --python tests/run_tests.py       # everything, with real workers
"$BLENDER_BIN" -b --factory-startup --python tests/run_tests.py -- tests/test_conversion.py -k gltf
```

The first run generates the test downloads into `tests/_out/downloads` (it needs Blender for that).

## How the code is organised

- `realindex/core` is plain Python: detection (`detect.py`, `maps.py`), scanning, the SQLite index, the sync
  engine and the worker pool. It never imports `bpy`.
- `realindex/worker` runs inside background Blender processes: importing models, building materials and worlds,
  rendering previews and writing the asset .blend files.
- The rest is the add-on itself: preferences, operators, panels and Quick Place.

A few rules keep RealIndex safe to point at a real downloads folder:

1. Nothing is ever written, moved or deleted inside a drop folder, apart from filing Inbox items when that
   preference is on. Everything RealIndex makes lives in the root's library folder. The safety tests check this.
2. Importing and preview rendering only happen in background workers. Blender's UI process only appends from the
   managed .blend files.
3. The output is native Blender assets. Quick Place is the only custom browsing UI.

## Detection changes

Every new vendor quirk starts with a test:

1. Add a fixture that reproduces the download in `tests/fixtures/make_downloads.py`.
2. Regenerate the goldens with `RI_UPDATE_GOLDEN=1 python -m pytest tests/pure/test_detect_golden.py`.
3. Review the diff in `tests/golden/`: only the files you meant to change should move.
4. Change `core/detect.py` or `core/maps.py` until the golden is right.

## Pull requests

- Keep each pull request to one change, with tests.
- Both suites should pass. CI runs them on Windows with Blender 5.2.
- Check any Blender API you use against Blender 5.2 itself: importers and the asset system changed in 5.x.
- Performance budgets are listed in the README. Run `python scripts/perf.py` if your change touches scanning,
  the engine or the workers.

By contributing you agree that your work is released under the GPL-3.0-or-later licence of this project.
