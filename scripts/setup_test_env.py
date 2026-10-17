"""Install pytest and hypothesis for Blender's bundled Python into tests/_vendor.

    $BLENDER_BIN --background --factory-startup --python scripts/setup_test_env.py
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "tests", "_vendor")

subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade",
                "--target", VENDOR, "pytest", "hypothesis"], check=True)
print("[RealIndex] test deps installed into", VENDOR)
