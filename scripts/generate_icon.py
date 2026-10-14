"""Draw the DataBizex application icon with Pillow.

    python scripts/generate_icon.py

An original mark: a rounded deep-blue tile with three white ascending bars and a
short check-mark stroke (data, growth, quality). No letters. Drawn at 1024 px and
scaled down for smooth edges. Writes assets/icon.png (256 px) and assets/icon.ico
(16, 32, 48 and 256 px).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 1024
TOP, BOTTOM = (0x2a, 0x5f, 0xa5), (0x17, 0x3c, 0x6c)  # theme accent hover -> pressed
WHITE = (255, 255, 255, 255)


def draw(size: int = SIZE) -> Image.Image:
    s = size / 1024
    # Vertical gradient, then cut to a rounded square.
    gradient = Image.new("RGBA", (size, size))
    pixels = gradient.load()
    for y in range(size):
        t = y / (size - 1)
        colour = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,)
        for x in range(size):
            pixels[x, y] = colour
    mask = Image.new("L", (size, size), 0)
    margin = round(40 * s)
    ImageDraw.Draw(mask).rounded_rectangle((margin, margin, size - margin, size - margin),
                                          radius=round(200 * s), fill=255)
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)

    pen = ImageDraw.Draw(icon)
    # Three ascending bars on a shared baseline.
    base, width, gap, left = round(800 * s), round(150 * s), round(70 * s), round(200 * s)
    for i, height in enumerate((210, 340, 480)):
        x0 = left + i * (width + gap)
        pen.rounded_rectangle((x0, base - round(height * s), x0 + width, base), radius=round(28 * s), fill=WHITE)
    # A short check-mark stroke above the lower bars.
    check = [(round(250 * s), round(390 * s)), (round(340 * s), round(470 * s)), (round(500 * s), round(270 * s))]
    stroke = round(52 * s)
    pen.line(check, fill=WHITE, width=stroke, joint="curve")
    for x, y in (check[0], check[-1]):  # round caps
        r = stroke // 2
        pen.ellipse((x - r, y - r, x + r, y + r), fill=WHITE)
    return icon


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    big = draw()
    png = big.resize((256, 256), Image.LANCZOS)
    png.save(ASSETS / "icon.png", optimize=True)
    big.resize((256, 256), Image.LANCZOS).save(ASSETS / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    print(f"Wrote {ASSETS / 'icon.png'} and {ASSETS / 'icon.ico'}")


if __name__ == "__main__":
    main()
