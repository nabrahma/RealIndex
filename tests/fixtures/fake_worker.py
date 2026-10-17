"""Stand-in for a background Blender worker (worker failure suite): behaviour chosen by the asset name.

    python fake_worker.py job.json
'crash' in the name: exit 3 without a result. 'hang': sleep past any timeout. Otherwise: succeed.
"""
import json
import os
import sys
import time

job = json.load(open(sys.argv[-1], encoding="utf-8"))
name = job.get("name", "")
if "crash" in name:
    sys.exit(3)
if "hang" in name:
    time.sleep(60)
with open(job["out_blend"], "wb") as fh:
    fh.write(b"BLENDER-fake")
with open(job["result"], "w", encoding="utf-8") as fh:
    json.dump({"ok": True, "info": {}, "warnings": []}, fh)
