"""Helpers shared by the dev scripts (no bpy import)."""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def blender_bin():
    """Blender executable from $BLENDER_BIN or the repo .env file."""
    if os.environ.get("BLENDER_BIN"):
        return os.environ["BLENDER_BIN"]
    env = os.path.join(ROOT, ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.strip().startswith("BLENDER_BIN"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("Set BLENDER_BIN in the environment or .env")
