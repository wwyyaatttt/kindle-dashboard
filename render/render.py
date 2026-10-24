"""Renders the Kindle dashboard: date, local weather, and today/tomorrow's calendar.

Writes <out>/dashboard.png (758x1024, 16-level gray) and <out>/meta.txt.
The time itself is drawn on the Kindle (see make_clock.py), so the clock band is left blank.

Env (kept in GitHub secrets so the public repo doesn't say where we live):
  GCAL_ICS     one or more secret iCal URLs, separated by newlines or commas
  DASH_LAT, DASH_LON, DASH_PLACE
  NWS_GRID     e.g. "LIX/68,88" from https://api.weather.gov/points/LAT,LON
  NWS_STATION  nearest observation station, e.g. "KMSY"
Usage:
  python render.py OUT_DIR [--sample]
"""

import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from astral import LocationInfo
from astral.sun import sun
from icalendar import Calendar

from common import (CARD, CLOCK_H, CLOCK_Y, H, HAIRLINE, INK, MARGIN, SECONDARY, TERTIARY, W,
                    fit, font, new_canvas, text_w, to_eink)

TZ = ZoneInfo("America/Chicago")
LAT, LON = float(os.environ.get("DASH_LAT", "29.9511")), float(os.environ.get("DASH_LON", "-90.0715"))
PLACE = os.environ.get("DASH_PLACE", "New Orleans")
GRID = os.environ.get("NWS_GRID", "LIX/68,88")
STATION = os.environ.get("NWS_STATION", "KMSY")
NWS = "https://api.weather.gov"
UA = {"User-Agent": "kindle-dashboard (github.com/wwyyaatttt/kindle-dashboard)",
      "Accept": "application/geo+json"}

ICONS = {
    "sunny": "", "clear_night": "", "partly_cloudy_day": "",
    "partly_cloudy_night": "", "cloud": "", "rainy": "",
    "rainy_light": "", "rainy_heavy": "", "thunderstorm": "",
    "foggy": "", "weather_snowy": "", "weather_mix": "", "air": "",
}

F = {
    "date": font("Inter-SemiBold", 30),
    "label": font("Inter-SemiBold", 21),
    "temp": font("InterDisplay-Light", 118),
    "cond": font("Inter-SemiBold", 27),
    "hilo": font("Inter-Medium", 24),
    "hour": font("Inter-Medium", 20),
    "hour_t": font("Inter-SemiBold", 26),
    "pop": font("Inter-SemiBold", 17),
    "day": font("Inter-SemiBold", 22),
    "ev_time": font("Inter-Medium", 21),
    "ev_title": font("Inter-SemiBold", 26),
    "empty": font("Inter-Medium", 24),
    "foot": font("Inter-Regular", 18),
}


# ---------- data ----------

def get(url: str) -> dict:
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return r.json()


def c_to_f(c: float) -> int:
    return round(c * 9 / 5 + 32)


def icon_for(text: str, daytime: bool) -> str:
    t = text.lower()
    if "thunder" in t:
        return "thunderstorm"
    if any(k in t for k in ("snow", "flurr", "blizzard")):
        return "weather_snowy"
    if any(k in t for k in ("sleet", "freezing", "ice")):
        return "weather_mix"
    if "heavy rain" in t:
        return "rainy_heavy"
    if "drizzle" in t or "light rain" in t:
        return "rainy_light"
    if "rain" in t or "shower" in t:
        return "rainy"
    if any(k in t for k in ("fog", "haze", "smoke", "mist", "dust")):
        return "foggy"
    if "partly" in t or "mostly cloudy" in t and "sun" in t:
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
    hours: list[Hour]


def fetch_weather(now: datetime) -> Weather:
    hourly = get(f"{NWS}/gridpoints/{GRID}/forecast/hourly")["properties"]["periods"]
    daily = get(f"{NWS}/gridpoints/{GRID}/forecast")["properties"]["periods"]

    hours = []
    for p in hourly:
        at = datetime.fromisoformat(p["startTime"]).astimezone(TZ)
        if at + timedelta(hours=1) <= now:
            continue
        hours.append(Hour(at, p["temperature"], icon_for(p["shortForecast"], p["isDaytime"]),
                          (p.get("probabilityOfPrecipitation") or {}).get("value") or 0))
    first = hourly[0]
    temp, condition = first["temperature"], first["shortForecast"]
    icon = icon_for(condition, first["isDaytime"])

    # Prefer the live observation from Lakefront Airport when it's fresh.
    try:
        obs = get(f"{NWS}/stations/{STATION}/observations/latest")["properties"]
        observed = datetime.fromisoformat(obs["timestamp"])
        if obs["temperature"]["value"] is not None and now - observed < timedelta(hours=2):
            temp = c_to_f(obs["temperature"]["value"])
            if obs.get("textDescription"):
                condition = obs["textDescription"]
                icon = icon_for(condition, first["isDaytime"])
    except (requests.RequestException, KeyError, TypeError, ValueError):
        pass

    high = next((p["temperature"] for p in daily if p["isDaytime"]), None)
    low = next((p["temperature"] for p in daily if not p["isDaytime"]), None)
    return Weather(temp, condition, icon, high, low, hours)


@dataclass
class Event:
    day: date
    start: datetime | None   # None for all-day
    end: datetime | None
    title: str


def fetch_events(urls: list[str], now: datetime) -> list[Event]:
    today = now.date()
    window_start = datetime.combine(today, time.min, TZ)
    window_end = window_start + timedelta(days=2)
    events = []
    for url in urls:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        cal = Calendar.from_ical(r.content)
        for ev in recurring_ical_events.of(cal).between(window_start, window_end):
            title = str(ev.get("SUMMARY", "Busy")).strip() or "Busy"
            s, e = ev["DTSTART"].dt, (ev.get("DTEND") or ev["DTSTART"]).dt
            if isinstance(s, datetime):
                s = (s if s.tzinfo else s.replace(tzinfo=TZ)).astimezone(TZ)
                e = (e if e.tzinfo else e.replace(tzinfo=TZ)).astimezone(TZ)
                if e <= now:
                    continue
                for d in (today, today + timedelta(days=1)):
                    if s.date() <= d <= (e - timedelta(seconds=1)).date():
                        events.append(Event(d, s, e, title))
            else:
                for d in (today, today + timedelta(days=1)):
                    if s <= d < e:
                        events.append(Event(d, None, None, title))
    events.sort(key=lambda ev: (ev.day, ev.start is not None, ev.start or now, ev.title))
    return events


def sample_events(now: datetime) -> list[Event]:
    t = now.date()
    at = lambda d, h, m=0: datetime.combine(d, time(h, m), TZ)
    tm = t + timedelta(days=1)
    return [
        Event(t, None, None, "Sample all-day event"),
        Event(t, at(t, now.hour), at(t, now.hour + 1), "Sample meeting happening now"),
        Event(t, at(t, min(now.hour + 2, 22)), at(t, 23), "Sample dinner with a long name that gets cut off"),
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


def draw_weather(d, wx: Weather | None, top: int, bottom: int):
    card(d, top, bottom)
    L, R = MARGIN + 30, W - MARGIN - 30
    if wx is None:
        d.text((L, top + 30), "Weather unavailable", font=F["cond"], fill=SECONDARY)
        return

    d.text((L, top + 26), PLACE.upper(), font=F["label"], fill=SECONDARY)
    d.text((L - 6, top + 50), f"{wx.temp}°", font=F["temp"], fill=INK)

    draw_icon(d, wx.icon, R, top + 26, 92, anchor="ra")
    cond = fit(d, wx.condition, F["cond"], 330)
    d.text((R, top + 132), cond, font=F["cond"], fill=INK, anchor="ra")
    if wx.high is not None and wx.low is not None:
        d.text((R, top + 168), f"H:{wx.high}°   L:{wx.low}°", font=F["hilo"], fill=SECONDARY, anchor="ra")

    line_y = top + 206
    d.line((L, line_y, R, line_y), fill=HAIRLINE, width=2)

    picks = wx.hours[::2][:6]
    if not picks:
        return
    col = (R - L) / len(picks)
    for i, h in enumerate(picks):
        cx = int(L + col * i + col / 2)
        label, icon, temp = ("Now", wx.icon, wx.temp) if i == 0 else (fmt_hour(h.at), h.icon, h.temp)
        d.text((cx, line_y + 14), label, font=F["hour"], fill=SECONDARY, anchor="ma")
        draw_icon(d, icon, cx, line_y + 42, 38, anchor="ma")
        if h.pop >= 10:
            d.text((cx, line_y + 83), f"{h.pop}%", font=F["pop"], fill=SECONDARY, anchor="ma")
        d.text((cx, line_y + 102), f"{temp}°", font=F["hour_t"], fill=INK, anchor="ma")


def draw_calendar(d, events: list[Event] | None, now: datetime, top: int, bottom: int):
    card(d, top, bottom)
    L, R = MARGIN + 30, W - MARGIN - 30
    y = top + 24
    if events is None:
        d.text((L, y), "Calendar unavailable", font=F["empty"], fill=SECONDARY)
        return

    row_h, max_y = 43, bottom - 12
    for offset, heading in ((0, "TODAY"), (1, "TOMORROW")):
        day = now.date() + timedelta(days=offset)
        if y + 34 + row_h > max_y:
            break
        d.text((L, y), heading, font=F["label"], fill=SECONDARY)
        if offset:
            d.text((R, y), day.strftime("%A"), font=F["label"], fill=TERTIARY, anchor="ra")
        y += 34
        day_events = [e for e in events if e.day == day]
        if not day_events:
            d.text((L, y + 4), "No more events" if offset == 0 else "No events", font=F["empty"], fill=TERTIARY)
            y += row_h + 10
            continue
        for i, ev in enumerate(day_events):
            remaining = len(day_events) - i
            if y + row_h > max_y or (remaining > 1 and y + 2 * row_h > max_y):
                d.text((L + 18, y + 6), f"+{remaining} more", font=F["ev_time"], fill=SECONDARY)
                y += row_h
                break
            if ev.start is None:
                when = "All day"
            elif ev.start <= now:
                when = "Now"
            else:
                when = fmt_time(ev.start)
            d.rounded_rectangle((L, y + 5, L + 5, y + row_h - 9), radius=3, fill=INK)
            d.text((L + 18, y + 8), when, font=F["ev_time"], fill=SECONDARY)
            d.text((L + 150, y + 4), fit(d, ev.title, F["ev_title"], R - L - 150), font=F["ev_title"], fill=INK)
            y += row_h
        y += 10


def render(now: datetime, wx: Weather | None, events: list[Event] | None):
    img, d = new_canvas()

    d.text((W // 2, 34), now.strftime("%A, %B ") + str(now.day), font=F["date"], fill=INK, anchor="ma")
    # Clock band (CLOCK_Y .. CLOCK_Y + CLOCK_H) stays white for the Kindle to fill.

    weather_top = CLOCK_Y + CLOCK_H + 20
    draw_weather(d, wx, weather_top, weather_top + 348)
    cal_top = weather_top + 348 + 20
    draw_calendar(d, events, now, cal_top, H - 50)

    s = sun(LocationInfo(latitude=LAT, longitude=LON).observer, date=now.date(), tzinfo=TZ)
    foot = f"Sunrise {fmt_time(s['sunrise'])}   ·   Sunset {fmt_time(s['sunset'])}   ·   Updated {fmt_time(now)}"
    d.text((W // 2, H - 36), foot, font=F["foot"], fill=TERTIARY, anchor="ma")
    return to_eink(img)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "out")
    sample = "--sample" in sys.argv
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TZ)

    try:
        wx = fetch_weather(now)
    except Exception as e:  # keep rendering; the card says "unavailable"
        print(f"weather failed: {e!r}", file=sys.stderr)
        wx = None

    urls = [u.strip() for u in os.environ.get("GCAL_ICS", "").replace(",", "\n").splitlines() if u.strip()]
    if sample:
        events = sample_events(now)
    elif urls:
        try:
            events = fetch_events(urls, now)
        except Exception as e:
            print(f"calendar failed: {type(e).__name__}", file=sys.stderr)  # never log the secret URL
            events = None
    else:
        events = []

    render(now, wx, events).save(out / "dashboard.png", optimize=True)
    # Plain key=value; the Kindle parses these as numbers, never executes them.
    offset = int(now.utcoffset().total_seconds())
    (out / "meta.txt").write_text(f"generated={int(now.timestamp())}\nutc_offset={offset}\n")
    print(f"rendered {out / 'dashboard.png'} at {now:%Y-%m-%d %H:%M %Z}")


if __name__ == "__main__":
    main()
