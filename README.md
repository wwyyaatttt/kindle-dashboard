# kindle-dashboard

An Apple-style wall dashboard for a jailbroken Kindle Paperwhite (5th gen, firmware 5.6.1.1):
a big clock, local weather from the National Weather Service, and today's/tomorrow's events from Google Calendar.

![preview](docs/preview.png)

## How it works

- **`render/render.py`** draws a 758×1024, 16-level-gray PNG (date, weather card, calendar card).
  A GitHub Action runs it every 10 minutes and publishes `dashboard.png` + `meta.txt` to GitHub Pages
  under an unlisted path (`/<DASH_TOKEN>/`).
- **The clock is drawn on the Kindle.** `render/make_clock.py` pre-renders all 720 times as PNGs;
  the Kindle blits one per minute with a flash-free GC16 update.
- **`kindle/extensions/dashboard`** is a KUAL extension. *Start dashboard* stops the Kindle UI,
  disables the screensaver, and loops: clock every minute, image every 10 minutes, full refresh hourly.
  Hold the power button ~20 s to reboot back to the normal Kindle UI.

## Secrets (Settings → Secrets and variables → Actions)

| Secret | Example |
|---|---|
| `GCAL_ICS` | Google Calendar "secret address in iCal format" (several allowed, one per line) |
| `DASH_LAT`, `DASH_LON` | `29.95`, `-90.07` |
| `DASH_PLACE` | label on the weather card |
| `NWS_GRID` | from `https://api.weather.gov/points/LAT,LON` → `gridId/gridX,gridY`, e.g. `LIX/68,88` |
| `NWS_STATION` | nearest observation station, e.g. `KMSY` |
| `DASH_TOKEN` | random string; the image lives at `https://<user>.github.io/<repo>/<DASH_TOKEN>/dashboard.png` |

## Installing on the Kindle

1. `python render/make_clock.py` (writes `kindle/extensions/dashboard/clock/`).
2. Copy `fbink` from a KOReader `koreader-kindle-*.zip` release into `kindle/extensions/dashboard/bin/`.
3. Put the image's base URL (without `/dashboard.png`) in `kindle/extensions/dashboard/url.txt`.
4. Copy `kindle/extensions/dashboard` to `/mnt/us/extensions/` and launch it from KUAL.

The Kindle's 2015 CA bundle can't verify modern certificates, so it fetches with `curl -k`.
