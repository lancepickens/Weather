#!/usr/bin/env python3
"""Multi-night terminal cloud-cover view for Henry W. Coe State Park.

Standalone companion to ``cloud_height_predictor.py``: the predictor
emits a detailed per-hour table for one or a few nights; this script
renders the upcoming ~16 nights as colored sunset→sunrise bars at a
glance, plus a top-5 "best upcoming" ranking by average cloud cover.

Hardcoded to the Coe coordinate. Cloud cover comes from Open-Meteo
(``ncep_aigfs025``); lunar altitude & illumination are computed
inline with low-precision Meeus formulae (Open-Meteo provides no
lunar fields). No LCL math, no neighbour-ring sampling, no moisture
/ wind / flag logic. Use it to pick *which* night, then run the
predictor for the chosen night's hour-by-hour detail.
"""
import json
import math
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Los_Angeles")

# Single source of truth for the observer coordinate: load from sites.json
# so this stays in sync with cloud_height_predictor.py. LAT/LON feed both
# the API request and the moon-altitude math.
_SITES_PATH = Path(__file__).resolve().parent.parent / "sites.json"
_SITE = json.loads(_SITES_PATH.read_text())["sites"]["henry-coe"]
LAT = float(_SITE["latitude"])
LON = float(_SITE["longitude"])

URL = (
    f"https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    f"&daily=sunrise,sunset"
    f"&hourly=cloud_cover"
    f"&timezone=America%2FLos_Angeles"
    f"&past_days=3&forecast_days=16"
    f"&models=ncep_aigfs025"
)

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RED     = "\033[38;5;203m"
ORANGE  = "\033[38;5;215m"
YELLOW  = "\033[38;5;227m"
GREEN   = "\033[38;5;114m"
CYAN    = "\033[38;5;117m"
BLUE    = "\033[38;5;75m"
INDIGO  = "\033[38;5;105m"
MAGENTA = "\033[38;5;213m"
PURPLE  = "\033[38;5;183m"
GRAY    = "\033[38;5;238m"

BLOCKS = ("▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")


def cloud_color(pct):
    if pct >= 90: return RED
    if pct >= 75: return ORANGE
    if pct >= 50: return YELLOW
    if pct >= 25: return GREEN
    if pct >= 10: return CYAN
    return INDIGO


def cloud_bar(hourly_pcts):
    """Per-hour cloud-cover blocks. Inner state machine never emits RESET on
    color transitions (only at end) so outer DIM wrapping for past nights
    composes correctly."""
    parts = []
    last_color = None
    for pct in hourly_pcts:
        level = min(7, int(pct / 100 * 8))
        color = cloud_color(pct)
        if color != last_color:
            parts.append(color)
            last_color = color
        parts.append(BLOCKS[level])
    if last_color is not None:
        parts.append(RESET)
    return "".join(parts)


def collect_night(sunset, sunrise, cc_by_hour):
    start = sunset.replace(minute=0, second=0, microsecond=0)
    if sunset.minute > 0:
        start += timedelta(hours=1)
    end = sunrise.replace(minute=0, second=0, microsecond=0)

    hours, pcts = [], []
    cursor = start
    while cursor <= end:
        key = cursor.strftime("%Y-%m-%dT%H:%M")
        pct = cc_by_hour.get(key)
        if pct is not None:
            hours.append(cursor)
            pcts.append(pct)
        cursor += timedelta(hours=1)
    return hours, pcts


def _julian_date(dt_utc):
    y, m = dt_utc.year, dt_utc.month
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    day = dt_utc.day + (dt_utc.hour + dt_utc.minute / 60) / 24
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + day + b - 1524.5


def _moon_geometry(dt_utc):
    """Geocentric moon altitude (deg) at (LAT, LON) and illuminated fraction (0..1).
    Low-precision Meeus (chapters 47/48) — accuracy ~0.1° altitude, ~1% illum."""
    jd = _julian_date(dt_utc)
    T = (jd - 2451545.0) / 36525.0
    d2r = math.pi / 180

    Lp = (218.3164477 + 481267.88123421 * T) % 360
    D  = (297.8501921 + 445267.1114034  * T) % 360
    M  = (357.5291092 + 35999.0502909   * T) % 360
    Mp = (134.9633964 + 477198.8675055  * T) % 360
    F  = (93.2720950  + 483202.0175233  * T) % 360

    lam = (Lp + 6.289 * math.sin(d2r * Mp)
              - 1.274 * math.sin(d2r * (2 * D - Mp))
              + 0.658 * math.sin(d2r * 2 * D)
              - 0.214 * math.sin(d2r * 2 * Mp)
              - 0.186 * math.sin(d2r * M)
              - 0.114 * math.sin(d2r * 2 * F))
    bet = (5.128 * math.sin(d2r * F)
              + 0.281 * math.sin(d2r * (Mp + F))
              - 0.278 * math.sin(d2r * (F - Mp))
              - 0.173 * math.sin(d2r * (2 * D - F)))

    eps = 23.4393 - 0.0130042 * T
    lam_r, bet_r, eps_r = d2r * lam, d2r * bet, d2r * eps
    sin_dec = (math.sin(bet_r) * math.cos(eps_r)
               + math.cos(bet_r) * math.sin(eps_r) * math.sin(lam_r))
    dec = math.asin(max(-1.0, min(1.0, sin_dec)))
    ra = math.atan2(
        math.sin(lam_r) * math.cos(eps_r) - math.tan(bet_r) * math.sin(eps_r),
        math.cos(lam_r),
    )

    gmst = (280.46061837
            + 360.98564736629 * (jd - 2451545.0)
            + 0.000387933 * T * T
            - T * T * T / 38710000.0) % 360
    lst = d2r * ((gmst + LON) % 360)
    H = lst - ra
    phi = d2r * LAT
    sin_alt = math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(H)
    alt = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))

    # Phase angle (Meeus 48.4) — degrees
    i = (180 - D
         - 6.289 * math.sin(d2r * Mp)
         + 2.100 * math.sin(d2r * M)
         - 1.274 * math.sin(d2r * (2 * D - Mp))
         - 0.658 * math.sin(d2r * 2 * D)
         - 0.214 * math.sin(d2r * 2 * Mp)
         - 0.110 * math.sin(d2r * D))
    illum = (1 + math.cos(d2r * i)) / 2

    return alt, illum


def moon_for_night(hours_local, utc_offset_sec):
    """Return (avg_pct, bar). Mirrors cloud_bar: per-hour blocks, height = illum%,
    color = cloud_color (high illum = bad for observing); below-horizon hours show
    a gray '·'. Inner state machine never emits RESET, so outer DIM wrapping for
    past nights composes correctly."""
    parts = []
    last_color = None
    contribs = []
    for h in hours_local:
        alt, illum = _moon_geometry(h - timedelta(seconds=utc_offset_sec))
        if alt <= 0:
            color, ch = GRAY, "·"
            contribs.append(0.0)
        else:
            pct = illum * 100
            contribs.append(pct)
            color = cloud_color(pct)
            ch = BLOCKS[min(7, int(pct / 100 * 8))]
        if color != last_color:
            parts.append(color)
            last_color = color
        parts.append(ch)
    if last_color is not None:
        parts.append(RESET)
    avg = sum(contribs) / len(contribs) if contribs else 0.0
    return avg, "".join(parts)


def main():
    try:
        with urllib.request.urlopen(URL, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"Failed to fetch weather: {e}", file=sys.stderr)
        sys.exit(1)

    # DST-aware abbreviation from zoneinfo. Open-Meteo's
    # timezone_abbreviation today returns offset-style "GMT-7" rather than
    # "PDT", so we derive the label locally.
    tz = datetime.now(LOCAL_TZ).tzname()
    lat = data.get("latitude", "")
    lon = data.get("longitude", "")
    # Constant per response — fine for the 16-day window; would need per-hour
    # resolution if the horizon ever crossed a DST transition.
    utc_offset = data.get("utc_offset_seconds", 0)

    daily_dates = data["daily"]["time"]
    sunrises = data["daily"]["sunrise"]
    sunsets = data["daily"]["sunset"]

    cc_by_hour = dict(zip(data["hourly"]["time"], data["hourly"]["cloud_cover"]))

    nights = []
    for i in range(len(daily_dates) - 1):
        sunset_str = sunsets[i]
        sunrise_str = sunrises[i + 1]
        if not sunset_str or not sunrise_str:
            continue
        sunset = datetime.fromisoformat(sunset_str)
        sunrise = datetime.fromisoformat(sunrise_str)
        hours, pcts = collect_night(sunset, sunrise, cc_by_hour)
        if not pcts:
            continue
        moon_avg, moon_bar_s = moon_for_night(hours, utc_offset)
        nights.append({
            "date": datetime.fromisoformat(daily_dates[i]).date(),
            "sunset": sunset,
            "sunrise": sunrise,
            "pcts": pcts,
            "avg": sum(pcts) / len(pcts),
            "min": min(pcts),
            "max": max(pcts),
            "moon_avg": moon_avg,
            "moon_bar": moon_bar_s,
        })

    today = datetime.now(LOCAL_TZ).date()
    tomorrow = today + timedelta(days=1)

    title = f"{BOLD}Coe Cloud Cover Predictor{RESET}"
    meta = f"{DIM}{lat},{lon} · {tz} · sunset→sunrise{RESET}"
    print()
    print(f"  {title}   {meta}")
    print(f"  {DIM}{'─' * 82}{RESET}")
    print(f"  {DIM}               set →rise   cld   cloud cover →           moon  lit when up →{RESET}")

    for n in nights:
        d = n["date"]
        is_past = d < today

        if d == today:
            label = f"{BOLD}{MAGENTA}▸ TONIGHT   {RESET}"
        elif d == tomorrow:
            label = f"{BOLD}{PURPLE}▸ TOMORROW  {RESET}"
        else:
            s = d.strftime("%a %b %d")
            label = f"  {DIM}{s}{RESET}" if is_past else f"  {s}"

        bar = cloud_bar(n["pcts"])
        avg_s = f"{cloud_color(n['avg'])}{n['avg']:>3.0f}%{RESET}"
        m_avg_s = f"{cloud_color(n['moon_avg'])}{n['moon_avg']:>3.0f}%{RESET}"
        m_bar = n["moon_bar"]
        set_s = n["sunset"].strftime("%H:%M")
        rise_s = n["sunrise"].strftime("%H:%M")
        times = f"{DIM}{set_s}→{rise_s}{RESET}"

        if is_past:
            avg_s = f"{DIM}{n['avg']:>3.0f}%{RESET}"
            bar = f"{DIM}{bar}{RESET}"
            m_avg_s = f"{DIM}{n['moon_avg']:>3.0f}%{RESET}"
            m_bar = f"{DIM}{m_bar}{RESET}"

        print(f"  {label}  {times}  {avg_s}  {bar}   {m_avg_s}  {m_bar}")

    future = [n for n in nights if n["date"] >= today]
    best = sorted(future, key=lambda n: n["avg"])[:5]

    print(f"  {DIM}{'─' * 82}{RESET}")
    print(f"  {BOLD}Best upcoming nights{RESET} {DIM}(lowest avg cloud cover){RESET}")
    for i, n in enumerate(best, 1):
        s = n["date"].strftime("%a %b %d")
        avg_s = f"{cloud_color(n['avg'])}{n['avg']:>3.0f}%{RESET}"
        min_s = f"{cloud_color(n['min'])}{n['min']:>3.0f}%{RESET}"
        max_s = f"{cloud_color(n['max'])}{n['max']:>3.0f}%{RESET}"
        marker = f"{BOLD}{MAGENTA}★{RESET}" if i == 1 else f"{BOLD}{i}.{RESET}"
        print(f"  {marker} {s}  avg {avg_s}  {DIM}min{RESET} {min_s} {DIM}max{RESET} {max_s}")
    print()


if __name__ == "__main__":
    main()
