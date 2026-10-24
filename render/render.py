"""Renders the Kindle dashboard: date, local weather, moon phase, and today/tomorrow's calendar.

Writes <out>/dashboard.png (758x1024, 16-level gray), <out>/dashboard.gz (raw framebuffer rows)
and <out>/meta.txt. The time itself is drawn on the Kindle (see make_clock.py), so the clock band
is left blank. The workflow encrypts dashboard.gz and meta.txt before publishing.

Env (kept in GitHub secrets so the public repo doesn't say where we live):
  GCAL_ICS     one or more secret iCal URLs, separated by newlines or commas
  DASH_LAT, DASH_LON, DASH_PLACE
  NWS_GRID     e.g. "LIX/68,88" from https://api.weather.gov/points/LAT,LON
  NWS_STATION  nearest observation station, e.g. "KMSY"
Usage:
  python render.py OUT_DIR [--sample]
"""

import math
import os
import re
import sys
import time as clock
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from astral import LocationInfo, moon
from astral.sun import elevation, sun
from fontTools.ttLib import TTFont
from icalendar import Calendar
from PIL import Image

from common import (CARD, CLOCK_H, CLOCK_Y, FONT_DIR, H, HAIRLINE, INK, MARGIN, PAPER, W,
                    fit, font, new_canvas, save_fb_gz, text_w, to_eink)


def env(name: str, default: str) -> str:
    """Empty or missing secrets fall back to the default instead of crashing."""
    return os.environ.get(name, "").strip() or default


TZ = ZoneInfo("America/Chicago")
LAT, LON = float(env("DASH_LAT", "29.9511")), float(env("DASH_LON", "-90.0715"))
PLACE = env("DASH_PLACE", "New Orleans")
GRID = env("NWS_GRID", "LIX/68,88")
STATION = env("NWS_STATION", "KMSY")
NWS = "https://api.weather.gov"
OBSERVER = LocationInfo(latitude=LAT, longitude=LON).observer

# Grays darker than the panel's midtones; lighter ones wash out on the Paperwhite 1.
WX_MUTED = 0x55
CAL_LABEL = 0x33
CAL_MUTED = 0x55
FOOT = 0x44
UA = {"User-Agent": "kindle-dashboard (github.com/wwyyaatttt/kindle-dashboard)",
      "Accept": "application/geo+json"}

ICONS = {
    "sunny": "", "clear_night": "", "partly_cloudy_day": "",
    "partly_cloudy_night": "", "cloud": "", "rainy": "",
    "rainy_light": "", "rainy_heavy": "", "thunderstorm": "",
    "foggy": "", "weather_snowy": "", "weather_mix": "", "air": "",
    "cyclone": "", "tornado": "",
}

F = {
    "date": font("Inter-SemiBold", 30),
    "label": font("Inter-SemiBold", 21),
    "temp": font("InterDisplay-Light", 118),
    "hilo": font("Inter-Medium", 24),
    "moon": font("Inter-Medium", 22),
    "hour": font("Inter-Medium", 20),
    "hour_t": font("Inter-SemiBold", 26),
    "pop": font("Inter-SemiBold", 19),
    "ev_time": font("Inter-Medium", 21),
    "ev_title": font("Inter-SemiBold", 26),
    "empty": font("Inter-Medium", 24),
    "foot": font("Inter-Medium", 19),
}
COND_SIZES = (27, 25, 23, 21)

# Characters Inter can draw; anything else (emoji, CJK, ZWJ sequences) would render as boxes.
INTER_CMAP = set(TTFont(str(FONT_DIR / "Inter-SemiBold.ttf")).getBestCmap())


def clean_title(raw: str) -> str:
    s = "".join(ch if ord(ch) in INTER_CMAP else " " for ch in raw)
    return re.sub(r"\s+", " ", s).strip(" -–—·|") or "Busy"


# ---------- data ----------

def get(url: str, attempts: int = 3) -> dict:
    """api.weather.gov regularly returns transient 5xx; retry with backoff."""
    for i in range(attempts):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == attempts - 1:
                raise
            clock.sleep(3 * (i + 1))


def c_to_f(c: float) -> int:
    return round(c * 9 / 5 + 32)


def is_day(dt: datetime) -> bool:
    return elevation(OBSERVER, dt) > 0


def icon_for(text: str, daytime: bool) -> str:
    t = text.lower()
    if "hurricane" in t or "tropical storm" in t:
        return "cyclone"
    if "tornado" in t or "funnel" in t:
        return "tornado"
    if "thunder" in t:
        return "thunderstorm"
    if any(k in t for k in ("wintry", "sleet", "freezing", "ice pellets", "mix")):
        return "weather_mix"
    if any(k in t for k in ("snow", "flurr", "blizzard")):
        return "weather_snowy"
    if "heavy rain" in t:
        return "rainy_heavy"
    if "drizzle" in t or "light rain" in t:
        return "rainy_light"
    if "rain" in t or "shower" in t:
        return "rainy"
    if any(k in t for k in ("fog", "haze", "smoke", "mist", "dust")):
        return "foggy"
    if "partly" in t or "few clouds" in t:
        return "partly_cloudy_day" if daytime else "partly_cloudy_night"
    if "cloudy" in t or "overcast" in t:
        return "cloud"
    if "wind" in t or "breezy" in t or "blustery" in t:
        return "air"
    return "sunny" if daytime else "clear_night"


@dataclass
class Hour:
    at: datetime
    temp: int
    icon: str
    pop: int


@dataclass
class Weather:
    temp: int
    condition: str
    icon: str
    high: int | None
    low: int | None
    night: bool          # first daily period is tonight: show tonight's low and tomorrow's high
    hours: list[Hour]


def fetch_weather(now: datetime) -> Weather:
    hourly = get(f"{NWS}/gridpoints/{GRID}/forecast/hourly")["properties"]["periods"]
    daily = get(f"{NWS}/gridpoints/{GRID}/forecast")["properties"]["periods"]

    hours = []
    for p in hourly:
        at = datetime.fromisoformat(p["startTime"]).astimezone(TZ)
        end = datetime.fromisoformat(p["endTime"]).astimezone(TZ)
        if end <= now or p.get("temperature") is None:
            continue
        mid = at + (end - at) / 2
        hours.append(Hour(at, p["temperature"], icon_for(p["shortForecast"], is_day(mid)),
                          (p.get("probabilityOfPrecipitation") or {}).get("value") or 0))
    if not hours:
        raise ValueError("hourly forecast has no current periods")
    current = next(p for p in hourly if datetime.fromisoformat(p["endTime"]) > now)
    temp, condition = hours[0].temp, current["shortForecast"]

    # Prefer the live observation from the nearest station when it's fresh.
    try:
        obs = get(f"{NWS}/stations/{STATION}/observations/latest", attempts=2)["properties"]
        observed = datetime.fromisoformat(obs["timestamp"])
        if obs["temperature"]["value"] is not None and now - observed < timedelta(hours=2):
            temp = c_to_f(obs["temperature"]["value"])
            if obs.get("textDescription"):
                condition = obs["textDescription"]
    except (requests.RequestException, KeyError, TypeError, ValueError):
        pass

    night = bool(daily) and not daily[0]["isDaytime"]
    high = next((p["temperature"] for p in daily if p["isDaytime"]), None)
    low = next((p["temperature"] for p in daily if not p["isDaytime"]), None)
    return Weather(temp, condition, icon_for(condition, is_day(now)), high, low, night, hours)


@dataclass
class Event:
    day: date
    start: datetime | None   # None for all-day
    end: datetime | None
    title: str
    kind: int = 2            # 0 all-day, 1 continues from an earlier day, 2 timed


def _occurrences(cal: Calendar, start: datetime, end: datetime):
    try:
        yield from recurring_ical_events.of(cal).between(start, end)
    except Exception as e:
        # One malformed VEVENT shouldn't blank the whole calendar: fall back to plain events.
        print(f"calendar recurrence expansion failed ({type(e).__name__}); using non-recurring events", file=sys.stderr)
        for ev in cal.walk("VEVENT"):
            yield ev


def events_from_calendar(cal: Calendar, now: datetime) -> list[Event]:
    today, tomorrow = now.date(), now.date() + timedelta(days=1)
    window_start = datetime.combine(today, time.min, TZ)
    window_end = window_start + timedelta(days=2)
    out = []
    for ev in _occurrences(cal, window_start, window_end):
        try:
            if str(ev.get("STATUS", "")).upper() == "CANCELLED":
                continue
            title = clean_title(str(ev.get("SUMMARY", "")))
            s = ev["DTSTART"].dt
            if ev.get("DTEND") is not None:
                e = ev["DTEND"].dt
            elif ev.get("DURATION") is not None:
                e = s + ev["DURATION"].dt
            else:
                e = s if isinstance(s, datetime) else s + timedelta(days=1)

            if isinstance(s, datetime):
                s = (s if s.tzinfo else s.replace(tzinfo=TZ)).astimezone(TZ)
                e = (e if e.tzinfo else e.replace(tzinfo=TZ)).astimezone(TZ)
                if e < now or (e == now and e > s):
                    continue
                last_day = max(e - timedelta(seconds=1), s).date()
                for d in (today, tomorrow):
                    if not s.date() <= d <= last_day:
                        continue
                    if d > s.date():
                        day_start = datetime.combine(d, time.min, TZ)
                        if e >= day_start + timedelta(days=1):
                            out.append(Event(d, None, None, title, 0))       # spans the whole day
                        elif e > day_start + timedelta(hours=6):
                            out.append(Event(d, s, e, title, 1))           # "Until 10:00 AM"
                        # an overnight event ending before 6 AM isn't worth a row the next day
                    else:
                        out.append(Event(d, s, e, title, 2))
            else:
                if e <= s:
                    e = s + timedelta(days=1)
                for d in (today, tomorrow):
                    if s <= d < e:
                        out.append(Event(d, None, None, title, 0))
        except Exception as ex:
            print(f"skipped a malformed event ({type(ex).__name__})", file=sys.stderr)
    return out


def fetch_events(urls: list[str], now: datetime) -> list[Event] | None:
    events, failures = [], 0
    for i, url in enumerate(urls, 1):
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            events += events_from_calendar(Calendar.from_ical(r.content), now)
        except Exception as e:
            failures += 1
            print(f"calendar {i} failed: {type(e).__name__}", file=sys.stderr)  # never log the secret URL
    if failures == len(urls):
        return None

    # The same invite often appears in two shared calendars.
    seen, unique = set(), []
    for ev in events:
        key = (ev.day, ev.kind, ev.start, ev.title.casefold())
        if key not in seen:
            seen.add(key)
            unique.append(ev)
    unique.sort(key=lambda ev: (ev.day, ev.kind, ev.start or now, ev.title))
    return unique


def sample_events(now: datetime) -> list[Event]:
    t, tm = now.date(), now.date() + timedelta(days=1)
    at = lambda d, h, m=0: datetime.combine(d, time(h, m), TZ)
    hour = now.replace(minute=0, second=0, microsecond=0)
    return [
        Event(t, None, None, "Sample all-day event", 0),
        Event(t, None, None, "Second all-day event", 0),
        Event(t, hour, hour + timedelta(hours=1), "Sample meeting happening now"),
        Event(t, hour + timedelta(hours=2), hour + timedelta(hours=3), "Sample dinner with a long name that gets cut off"),
        Event(tm, at(t, 22), at(tm, 9), "Sample overnight event", 1),
        Event(tm, at(tm, 9, 30), at(tm, 10, 30), "Sample morning event"),
        Event(tm, at(tm, 15), at(tm, 16), "Sample afternoon event"),
    ]


# ---------- drawing ----------

def fmt_time(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def fmt_hour(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h} {'AM' if dt.hour < 12 else 'PM'}"


def card(d, top: int, bottom: int):
    d.rounded_rectangle((MARGIN, top, W - MARGIN, bottom), radius=30, fill=CARD)


def draw_icon(d, name: str, x: int, y: int, size: int, anchor="la"):
    d.text((x, y), ICONS[name], font=font("WeatherSymbols", size), fill=INK, anchor=anchor)


MOON_NAMES = ((1, "New Moon"), (6, "Waxing Crescent"), (8, "First Quarter"), (13, "Waxing Gibbous"),
              (15, "Full Moon"), (20, "Waning Gibbous"), (22, "Last Quarter"), (27, "Waning Crescent"),
              (28, "New Moon"))


def moon_phase(day: date) -> tuple[float, str]:
    """Returns (fraction of the lunation 0..1, name). astral's phase runs 0..28 (0 new, 14 full)."""
    days = moon.phase(day)
    name = next(n for limit, n in MOON_NAMES if days < limit)
    return days / 28, name


def draw_moon(img: Image.Image, cx: int, cy: int, r: int, phase: float):
    """Draws the moon as seen from the northern hemisphere, supersampled for clean edges.
    Like Apple's moon-phase symbols, the lit part is filled and the shadow is just an outline."""
    ss = 4
    R = r * ss
    tile = Image.new("L", (2 * R + 2 * ss, 2 * R + 2 * ss), CARD)
    px = tile.load()
    c = R + ss
    k = math.cos(2 * math.pi * phase)
    for y in range(-R, R + 1):
        half = math.sqrt(max(R * R - y * y, 0))
        for x in range(-R, R + 1):
            dist = math.hypot(x, y)
            if dist > R:
                continue
            lit = x > half * k if phase < 0.5 else x < -half * k
            px[c + x, c + y] = 0x33 if lit or dist > R - 1.5 * ss else PAPER
    small = tile.resize((tile.width // ss, tile.height // ss), Image.LANCZOS)
    img.paste(small, (cx - small.width // 2, cy - small.height // 2))


def draw_weather(img, d, wx: Weather | None, now: datetime, top: int, bottom: int):
    card(d, top, bottom)
    L, R = MARGIN + 30, W - MARGIN - 30
    if wx is None:
        d.text((L, top + 30), "Weather unavailable", font=F["hilo"], fill=WX_MUTED)
        return

    d.text((L, top + 26), PLACE.upper(), font=F["label"], fill=WX_MUTED)
    d.text((L - 6, top + 50), f"{wx.temp}°", font=F["temp"], fill=INK)

    draw_icon(d, wx.icon, R, top + 26, 92, anchor="ra")
    for size in COND_SIZES:
        cond_font = font("Inter-SemiBold", size)
        if text_w(d, wx.condition, cond_font) <= 330:
            break
    d.text((R, top + 132), fit(d, wx.condition, cond_font, 330), font=cond_font, fill=INK, anchor="ra")
    if wx.high is not None and wx.low is not None:
        hilo = f"L:{wx.low}°   Tomorrow H:{wx.high}°" if wx.night else f"H:{wx.high}°   L:{wx.low}°"
        d.text((R, top + 168), hilo, font=F["hilo"], fill=WX_MUTED, anchor="ra")

    phase, name = moon_phase(now.date())
    draw_moon(img, L + 11, top + 180, 11, phase)
    d.text((L + 30, top + 169), name, font=F["moon"], fill=WX_MUTED)

    line_y = top + 206
    d.line((L, line_y, R, line_y), fill=HAIRLINE, width=2)

    picks = [wx.hours[0]] + [h for h in wx.hours[1:] if h.at.hour % 2 == wx.hours[0].at.hour % 2][:5]
    col = (R - L) / len(picks)
    for i, h in enumerate(picks):
        cx = int(L + col * i + col / 2)
        label, icon, temp = ("Now", wx.icon, wx.temp) if i == 0 else (fmt_hour(h.at), h.icon, h.temp)
        d.text((cx, line_y + 14), label, font=F["hour"], fill=WX_MUTED, anchor="ma")
        draw_icon(d, icon, cx, line_y + 42, 38, anchor="ma")
        if h.pop >= 10:
            d.text((cx, line_y + 82), f"{h.pop}%", font=F["pop"], fill=WX_MUTED, anchor="ma")
        d.text((cx, line_y + 102), f"{temp}°", font=F["hour_t"], fill=INK, anchor="ma")


def day_rows(day_events: list[Event], now: datetime, today: bool) -> list[tuple[str, str]]:
    """(when, title) rows for one day: all-day events share a single row, then the rest in order."""
    rows = []
    allday = [e.title for e in day_events if e.kind == 0]
    if allday:
        rows.append(("All day", " · ".join(allday)))
    for e in day_events:
        if e.kind == 1:
            rows.append((f"Until {fmt_time(e.end)}", e.title))
        elif e.kind == 2:
            rows.append(("Now" if today and e.start <= now else fmt_time(e.start), e.title))
    return rows


def draw_calendar(d, events: list[Event] | None, now: datetime, top: int, bottom: int):
    card(d, top, bottom)
    L, R = MARGIN + 30, W - MARGIN - 30
    y = top + 24
    if events is None:
        d.text((L, y), "Calendar unavailable", font=F["empty"], fill=CAL_MUTED)
        return

    row_h, head_h, gap, max_y = 43, 34, 10, bottom - 12
    days = [(now.date() + timedelta(days=o), o == 0) for o in (0, 1)]
    rows = {day: day_rows([e for e in events if e.day == day], now, today) for day, today in days}
    for day, today in days:
        heading = "TODAY" if today else "TOMORROW"
        # Keep room for TOMORROW's heading and first row so a busy today never hides it.
        limit = max_y - (head_h + row_h + gap if today else 0)
        if y + head_h + row_h > max_y:
            break
        d.text((L, y), heading, font=F["label"], fill=CAL_LABEL)
        if not today:
            d.text((R, y), day.strftime("%A"), font=F["label"], fill=CAL_MUTED, anchor="ra")
        y += head_h
        day_list = rows[day]
        if not day_list:
            d.text((L, y + 4), "No more events" if today else "No events", font=F["empty"], fill=CAL_MUTED)
            y += row_h + gap
            continue
        fits = max(1, (limit - y) // row_h)
        shown = day_list if len(day_list) <= fits else day_list[:fits - 1]
        when_w = max(text_w(d, w, F["ev_time"]) for w, _ in shown) if shown else 0
        title_x = L + 18 + max(when_w + 18, 132)
        for when, title in shown:
            d.rounded_rectangle((L, y + 5, L + 5, y + row_h - 9), radius=3, fill=INK)
            d.text((L + 18, y + 8), when, font=F["ev_time"], fill=CAL_LABEL)
            d.text((title_x, y + 4), fit(d, title, F["ev_title"], R - title_x), font=F["ev_title"], fill=INK)
            y += row_h
        if len(shown) < len(day_list):
            d.text((L + 18, y + 6), f"+{len(day_list) - len(shown)} more", font=F["ev_time"], fill=CAL_LABEL)
            y += row_h
        y += gap


def render(now: datetime, wx: Weather | None, events: list[Event] | None) -> Image.Image:
    img, d = new_canvas()

    d.text((W // 2, 34), now.strftime("%A, %B ") + str(now.day), font=F["date"], fill=INK, anchor="ma")
    # Clock band (CLOCK_Y .. CLOCK_Y + CLOCK_H) stays white for the Kindle to fill.

    weather_top = CLOCK_Y + CLOCK_H + 20
    draw_weather(img, d, wx, now, weather_top, weather_top + 348)
    cal_top = weather_top + 348 + 20
    draw_calendar(d, events, now, cal_top, H - 50)

    s = sun(OBSERVER, date=now.date(), tzinfo=TZ)
    foot = f"Sunrise {fmt_time(s['sunrise'])}   ·   Sunset {fmt_time(s['sunset'])}   ·   Updated {fmt_time(now)}"
    d.text((W // 2, H - 36), foot, font=F["foot"], fill=FOOT, anchor="ma")
    return to_eink(img)


def next_offset_change(now: datetime) -> tuple[int, int]:
    """Epoch and UTC offset of the next DST transition within a year (0, 0 if none)."""
    base = int(now.timestamp()) // 3600 * 3600
    off = now.utcoffset()
    for h in range(1, 24 * 370):
        t = datetime.fromtimestamp(base + h * 3600, TZ)
        if t.utcoffset() != off:
            return base + h * 3600, int(t.utcoffset().total_seconds())
    return 0, 0


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "out")
    sample = "--sample" in sys.argv
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TZ)

    try:
        wx = fetch_weather(now)
    except Exception as e:  # keep rendering; the card says "unavailable"
        print(f"weather failed: {type(e).__name__}: {e}", file=sys.stderr)
        wx = None

    urls = [u.strip() for u in os.environ.get("GCAL_ICS", "").replace(",", "\n").splitlines() if u.strip()]
    if sample:
        events = sample_events(now)
    elif urls:
        events = fetch_events(urls, now)
    else:
        events = []

    img = render(now, wx, events)
    img.save(out / "dashboard.png", optimize=True)
    save_fb_gz(img, out / "dashboard.gz")
    # Plain key=value; the Kindle parses these as numbers, never executes them.
    change_at, change_to = next_offset_change(now)
    (out / "meta.txt").write_text(
        f"generated={int(now.timestamp())}\n"
        f"utc_offset={int(now.utcoffset().total_seconds())}\n"
        f"next_change={change_at}\nnext_offset={change_to}\n")
    print(f"rendered at {now:%Y-%m-%d %H:%M %Z}")


if __name__ == "__main__":
    main()
