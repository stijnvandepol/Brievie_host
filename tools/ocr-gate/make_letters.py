"""Draw the synthetic letters as A4 pages at 200 dpi, a clean and a scanned variant.

Usage: python make_letters.py PATH_OUT  (deterministic: one random.Random per letter)
"""

import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIDTH, HEIGHT = 1654, 2339  # A4 at 200 dpi, the pipeline's render size
DPI = 200
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MARGIN = 160


def _font(path: str, points: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, round(points * DPI / 72))


def draw_letter(letter: dict[str, object]) -> Image.Image:
    page = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(page)
    body = _font(FONT, 11)
    y = MARGIN
    draw.text((MARGIN, y), str(letter["sender"]), font=_font(BOLD, 16), fill=0)
    y += 70
    draw.text((MARGIN, y), "Postbus 100, 1000 AA Voorbeeldstad", font=_font(FONT, 9), fill=60)
    y += 160
    for line in letter["body"]:  # type: ignore[attr-defined]
        draw.text((MARGIN, y), line, font=body, fill=0)
        y += 52
    table = letter.get("table")
    if table:
        y += 30
        columns = len(table[0])  # type: ignore[index]
        cell = (WIDTH - 2 * MARGIN) // columns
        for row in table:  # type: ignore[attr-defined]
            draw.line((MARGIN, y - 8, WIDTH - MARGIN, y - 8), fill=0, width=2)
            for index, value in enumerate(row):
                draw.text((MARGIN + index * cell + 10, y), value, font=body, fill=0)
            y += 56
        draw.line((MARGIN, y - 8, WIDTH - MARGIN, y - 8), fill=0, width=2)
        y += 30
    for line in letter.get("after", []):  # type: ignore[attr-defined]
        draw.text((MARGIN, y), line, font=body, fill=0)
        y += 52
    small = _font(FONT, 8)
    y = max(y + 60, HEIGHT - 400)
    for line in letter.get("small", []):  # type: ignore[attr-defined]
        draw.text((MARGIN, y), line, font=small, fill=0)
        y += 32
    return page


def scanned(page: Image.Image, rng: random.Random) -> Image.Image:
    rotated = page.rotate(0.8, resample=Image.Resampling.BICUBIC, fillcolor=255, expand=False)
    blurred = rotated.filter(ImageFilter.GaussianBlur(radius=0.6))
    pixels = blurred.load()
    for _ in range(WIDTH * HEIGHT // 50):
        x, y = rng.randrange(WIDTH), rng.randrange(HEIGHT)
        pixels[x, y] = max(0, pixels[x, y] - rng.randrange(20, 60))
    return blurred


def main(out: str) -> None:
    letters = json.loads((Path(__file__).resolve().parent / "letters.json").read_text(encoding="utf-8"))
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    for letter in letters:
        rng = random.Random(letter["id"])
        page = draw_letter(letter)
        page.save(target / f"{letter['id']}-clean.png")
        # The scan goes through JPEG quality 70 and back to PNG for Tesseract.
        jpeg = target / f"{letter['id']}-scan.jpg"
        scanned(page, rng).save(jpeg, "JPEG", quality=70)
        Image.open(jpeg).save(target / f"{letter['id']}-scan.png")


if __name__ == "__main__":
    main(sys.argv[1])
