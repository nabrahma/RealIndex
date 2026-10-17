"""Dev only: download a handful of real CC0 assets for the realism check and the performance budgets.

    python tests/corpus/fetch.py            # into tests/_out/corpus (ignored by git)

ambientCG 2K-JPG zips (as downloaded from the site), Poly Haven 2K texture sets (as the site's folder
download: <id>_2k/textures/...) and Poly Haven HDRIs (4K .hdr and 2K .exr). Files already present are
skipped. Everything here is CC0.
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "tests", "_out", "corpus")
UA = {"User-Agent": "RealIndex-dev-corpus/1.0 (CC0 realism check)"}

AMBIENTCG = ["Bricks076C", "Wood049", "Rock035", "PavingStones138", "Metal032", "Fabric026"]
PH_TEXTURES = ["rock_wall_08", "brown_planks_09", "aerial_rocks_02"]
PH_HDRIS = [("kloofendal_48d_partly_cloudy_puresky", "4k", "hdr"), ("studio_small_09", "2k", "exr")]
PH_MAPS = {"Diffuse": "jpg", "Rough": "jpg", "nor_gl": "jpg", "nor_dx": "jpg", "arm": "jpg", "Displacement": "png"}


def get(url, dst):
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    req = urllib.request.Request(url, headers=UA)
    tmp = dst + ".download"
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as fh:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            fh.write(b)
    os.replace(tmp, dst)
    print("  got", os.path.relpath(dst, OUT), os.path.getsize(dst) // 1024, "KB")
    return dst


def jget(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    print("ambientCG")
    for a in AMBIENTCG:
        name = "%s_2K-JPG.zip" % a
        try:
            get("https://ambientcg.com/get?file=" + name, os.path.join(OUT, "ambientcg", name))
        except Exception as e:
            print("  skip", name, e)
    print("Poly Haven textures")
    for t in PH_TEXTURES:
        try:
            files = jget("https://api.polyhaven.com/files/" + t)
            for key, fmt in PH_MAPS.items():
                entry = files.get(key, {}).get("2k", {}).get(fmt)
                if entry:
                    get(entry["url"], os.path.join(OUT, "polyhaven", t + "_2k", "textures",
                                                   entry["url"].rsplit("/", 1)[1]))
        except Exception as e:
            print("  skip", t, e)
    print("Poly Haven HDRIs")
    for h, res, fmt in PH_HDRIS:
        try:
            entry = jget("https://api.polyhaven.com/files/" + h)["hdri"][res][fmt]
            get(entry["url"], os.path.join(OUT, "hdris", entry["url"].rsplit("/", 1)[1]))
        except Exception as e:
            print("  skip", h, e)
    print("corpus in", OUT)


if __name__ == "__main__":
    sys.exit(main())
