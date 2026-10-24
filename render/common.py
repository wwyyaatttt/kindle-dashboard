"""Shared layout constants and drawing helpers for the dashboard and the clock sprites."""

import gzip
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Kindle Paperwhite (5th gen) panel: 758x1024, 16 gray levels.
W, H = 758, 1024
MARGIN = 36

# The Kindle draws the time itself, once a minute, into this band.
# The server-rendered dashboard leaves it blank.
CLOCK_Y = 72
CLOCK_H = 182
# The Kindle can overlay a status banner (stale data, low battery) on the footer band.
BANNER_H = 50
BANNER_Y = H - BANNER_H

INK = 0
SECONDARY = 0x66
TERTIARY = 0x99
CARD = 0xEE
HAIRLINE = 0xCC
PAPER = 0xFF

FONT_DIR = Path(__file__).parent / "fonts"


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"{name}.ttf"), size)


def new_canvas(w: int = W, h: int = H) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("L", (w, h), PAPER)
    return img, ImageDraw.Draw(img)


def to_eink(img: Image.Image) -> Image.Image:
    """Snap to the panel's 16 gray levels so antialiasing renders predictably."""
    return img.point(lambda v: round(v / 17) * 17)


# The framebuffer is 8-bit gray, 0 = black, with 768-byte rows (758 visible pixels + padding).
FB_STRIDE = 768


def save_fb_gz(img: Image.Image, path: Path) -> None:
    """Write raw framebuffer rows, gzipped, so the Kindle can dd them straight onto the screen."""
    padded = Image.new("L", (FB_STRIDE, img.height), PAPER)
    padded.paste(img, (0, 0))
    path.write_bytes(gzip.compress(padded.tobytes(), compresslevel=9, mtime=0))


def text_w(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont) -> int:
    return int(draw.textlength(s, font=f))


def fit(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont, max_w: int) -> str:
    """Truncate with an ellipsis so the string fits in max_w pixels."""
    if text_w(draw, s, f) <= max_w:
        return s
    while s and text_w(draw, s + "…", f) > max_w:
        s = s[:-1]
    return s.rstrip() + "…"
