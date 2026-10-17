"""Build the installable extension zip into dist/ using Blender's extension builder.

    python scripts/package.py
"""

import os
import subprocess

from _common import ROOT, blender_bin


def main():
    out = os.path.join(ROOT, "dist")
    os.makedirs(out, exist_ok=True)
    subprocess.run([blender_bin(), "--factory-startup", "--command", "extension", "build",
                    "--source-dir", os.path.join(ROOT, "realindex"), "--output-dir", out],
                   check=True, stdin=subprocess.DEVNULL)
    print("zips:", [f for f in os.listdir(out) if f.endswith(".zip")])


if __name__ == "__main__":
    main()
