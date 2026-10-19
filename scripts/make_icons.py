"""Regenerate every icon from assets/icon-source.jpg (square artwork with a rounded tile).

    python scripts/make_icons.py
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "icon-source.jpg")
MARGIN = 0.012          # trim the carbon-fibre border around the tile
RADIUS = 0.19           # corner radius as a fraction of the size


def tile(size):
    im = Image.open(SRC).convert("RGB")
    w, h = im.size
    side = min(w, h)
    m = int(side * MARGIN)
    left, top = (w - side) // 2 + m, (h - side) // 2 + m
    im = im.crop((left, top, left + side - 2 * m, top + side - 2 * m)).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * RADIUS), fill=255)
    out = im.convert("RGBA")
    out.putalpha(mask)
    return out


def main():
    big = tile(1024)
    big.resize((512, 512), Image.LANCZOS).save(os.path.join(ROOT, "assets", "juggler.png"), optimize=True)
    big.resize((256, 256), Image.LANCZOS).save(
        os.path.join(ROOT, "assets", "juggler.ico"),
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.resize((180, 180), Image.LANCZOS).save(os.path.join(ROOT, "juggler", "static", "icon.png"), optimize=True)
    for s in (16, 32, 48, 128):
        big.resize((s, s), Image.LANCZOS).save(os.path.join(ROOT, "extension", "icons", f"icon{s}.png"), optimize=True)
    print("icons written")


if __name__ == "__main__":
    main()
