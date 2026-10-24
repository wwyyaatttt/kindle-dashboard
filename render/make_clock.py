"""Pre-renders every 12-hour time (1:00 through 12:59), plus the status banners, for the Kindle.

The Kindle has no good font renderer, so it draws the clock by picking
clock/HHMM.gz (HH = 01..12, as `date +%I` would print it) once a minute and
copying its raw framebuffer rows into the clock band. Banners go into the
footer band (BANNER_Y .. H) when data is stale, the battery is low, or no
dashboard has arrived yet.
Run once; the output ships inside the KUAL extension.
"""

import io
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import ImageFont

from common import (BANNER_H, CLOCK_H, FONT_DIR, INK, PAPER, W, font, new_canvas, save_fb_gz,
                    to_eink)

TIME_SIZE = 188


def tabular_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Inter with its tabular digits (zero.tf ...) mapped to 0-9, so minutes don't shift sideways."""
    tt = TTFont(str(FONT_DIR / f"{name}.ttf"))
    digits = "zero one two three four five six seven eight nine".split()
    for table in tt["cmap"].tables:
        if table.isUnicode():
            for i, g in enumerate(digits):
                if 0x30 + i in table.cmap:
                    table.cmap[0x30 + i] = f"{g}.tf"
    buf = io.BytesIO()
    tt.save(buf)
    buf.seek(0)
    return ImageFont.truetype(buf, size)


TIME_FONT = tabular_font("InterDisplay-SemiBold", TIME_SIZE)


def render_time(label: str):
    img, d = new_canvas(W, CLOCK_H)
    # Fixed baseline and advance-width centering: only the changed digits move.
    _, cap_top, _, cap_bottom = d.textbbox((0, 0), "0", font=TIME_FONT, anchor="ls")
    baseline = (CLOCK_H - (cap_bottom - cap_top)) // 2 - cap_top
    d.text((W // 2, baseline), label, font=TIME_FONT, fill=INK, anchor="ms")
    return to_eink(img)


BANNERS = {
    "stale": "Weather and calendar haven’t updated recently",
    "battery": "Battery low · please plug in the Kindle",
    "stale_battery": "Not updating · battery low",
    "nodata": "Waiting for the first update…",
}


def render_banner(text: str):
    img, d = new_canvas(W, BANNER_H)
    f = font("Inter-SemiBold", 21)
    pad_x, h = 22, 38
    tw = int(d.textlength(text, font=f))
    x0, y0 = (W - tw) // 2 - pad_x, (BANNER_H - h) // 2
    d.rounded_rectangle((x0, y0, x0 + tw + 2 * pad_x, y0 + h), radius=h // 2, fill=INK)
    d.text((W // 2, y0 + h // 2), text, font=f, fill=PAPER, anchor="mm")
    return to_eink(img)


def main(ext_dir: Path) -> None:
    clock_dir, banner_dir = ext_dir / "clock", ext_dir / "banner"
    clock_dir.mkdir(parents=True, exist_ok=True)
    banner_dir.mkdir(parents=True, exist_ok=True)
    for h in range(1, 13):
        for m in range(60):
            img = render_time(f"{h}:{m:02d}")
            img.save(clock_dir / f"{h:02d}{m:02d}.png", optimize=True)
            save_fb_gz(img, clock_dir / f"{h:02d}{m:02d}.gz")
    for name, text in BANNERS.items():
        img = render_banner(text)
        img.save(banner_dir / f"{name}.png", optimize=True)
        save_fb_gz(img, banner_dir / f"{name}.gz")
    save_fb_gz(new_canvas(W, BANNER_H)[0], banner_dir / "blank.gz")   # footer before any dashboard arrives
    print(f"wrote 720 clock sprites and {len(BANNERS)} banners to {ext_dir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "kindle/extensions/dashboard")
