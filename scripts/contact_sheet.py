"""Contact sheet of a state folder's previews, labelled with asset names (dev aid, needs Pillow).

    python scripts/contact_sheet.py STATE_DIR OUT.png
"""
import os
import sqlite3
import sys

from PIL import Image, ImageDraw


def main():
    state, out = sys.argv[1], sys.argv[2]
    con = sqlite3.connect(os.path.join(state, "index.db"))
    rows = con.execute("SELECT id, name, type, status FROM assets WHERE status='ok' ORDER BY type, name").fetchall()
    cell, cols = 200, 6
    tiles = []
    for aid, name, typ, _st in rows:
        p = os.path.join(state, "previews", aid + ".png")
        if os.path.exists(p):
            tiles.append((Image.open(p).convert("RGBA").resize((cell, cell)), "%s (%s)" % (name, typ)))
    rows_n = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows_n * (cell + 18)), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    for i, (im, label) in enumerate(tiles):
        x, y = (i % cols) * cell, (i // cols) * (cell + 18)
        bg = Image.new("RGBA", im.size, (60, 60, 60, 255))
        bg.alpha_composite(im)
        sheet.paste(bg.convert("RGB"), (x, y))
        d.text((x + 4, y + cell + 3), label[:34], fill=(230, 230, 230))
    sheet.save(out)
    print(out, len(tiles), "previews")


if __name__ == "__main__":
    main()
