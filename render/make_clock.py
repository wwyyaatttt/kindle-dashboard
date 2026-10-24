"""Pre-renders every 12-hour time (1:00 through 12:59) as a PNG the Kindle can blit.

The Kindle has no good font renderer, so it draws the clock by picking
clock/HHMM.png (HH = 01..12, as `date +%I` would print it) once a minute.
Run once; the output ships inside the KUAL extension.
"""

import sys
from pathlib import Path

from common import CLOCK_H, INK, W, font, new_canvas, to_eink

TIME_FONT = font("InterDisplay-SemiBold", 188)


def render_time(label: str):
    img, d = new_canvas(W, CLOCK_H)
    # Center on the glyphs' actual ink so the time sits optically centered in the band.
    left, top, right, bottom = d.textbbox((0, 0), label, font=TIME_FONT)
    x = (W - (right - left)) // 2 - left
    y = (CLOCK_H - (bottom - top)) // 2 - top
    d.text((x, y), label, font=TIME_FONT, fill=INK)
    return to_eink(img)


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for h in range(1, 13):
        for m in range(60):
            render_time(f"{h}:{m:02d}").save(out_dir / f"{h:02d}{m:02d}.png", optimize=True)
    print(f"wrote 720 sprites to {out_dir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "kindle/extensions/dashboard/clock")
