#!/usr/bin/env python3
"""Multi-night terminal cloud-cover view for Henry W. Coe State Park.

Standalone companion to ``cloud_height_predictor.py``: the predictor
emits a detailed per-hour table for one or a few nights; this script
renders the upcoming ~16 nights as colored sunset→sunrise bars at a
glance, plus a top-5 "best upcoming" ranking by average cloud cover.

Hardcoded to the Coe coordinate and uses only ``hourly.cloud_cover``
from Open-Meteo — no LCL math, no neighbour-ring sampling, no
moisture / wind / flag logic. Use it to pick *which* night, then run
the predictor for the chosen night's hour-by-hour detail.
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta

URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=37.183&longitude=-121.55093"
    "&daily=temperature_2m_max,sunrise,sunset"
    "&hourly=cloud_cover,visibility,dew_point_2m"
    "&current=cloud_cover"
    "&timezone=America%2FLos_Angeles"
    "&past_days=3&forecast_days=16"
    "&temperature_unit=fahrenheit"
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


def cloud_color(pct):
    if pct >= 90: return RED
    if pct >= 75: return ORANGE
    if pct >= 50: return YELLOW
    if pct >= 25: return GREEN
    if pct >= 10: return CYAN
    return INDIGO


def cloud_bar(hourly_pcts):
    blocks = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    parts = []
    last_color = None
    for pct in hourly_pcts:
        level = min(7, int(pct / 100 * 8))
        color = cloud_color(pct)
        if color != last_color:
            if last_color is not None:
                parts.append(RESET)
            parts.append(color)
            last_color = color
        parts.append(blocks[level])
    if last_color is not None:
        parts.append(RESET)
    return "".join(parts)


def collect_night(sunset, sunrise, cc_by_hour):
    start = sunset.replace(minute=0, second=0, microsecond=0)
    if sunset.minute > 0:
        start += timedelta(hours=1)
    end = sunrise.replace(minute=0, second=0, microsecond=0)

    pcts = []
    cursor = start
    while cursor <= end:
        key = cursor.strftime("%Y-%m-%dT%H:%M")
        pct = cc_by_hour.get(key)
        if pct is not None:
            pcts.append(pct)
        cursor += timedelta(hours=1)
    return pcts


def main():
    try:
        with urllib.request.urlopen(URL, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"Failed to fetch weather: {e}", file=sys.stderr)
        sys.exit(1)

    tz = data.get("timezone_abbreviation") or data.get("timezone", "")
    lat = data.get("latitude", "")
    lon = data.get("longitude", "")

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
        pcts = collect_night(sunset, sunrise, cc_by_hour)
        if not pcts:
            continue
        nights.append({
            "date": datetime.fromisoformat(daily_dates[i]).date(),
            "sunset": sunset,
            "sunrise": sunrise,
            "pcts": pcts,
            "avg": sum(pcts) / len(pcts),
            "min": min(pcts),
            "max": max(pcts),
        })

    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)

    title = f"{BOLD}Coe Cloud Cover Predictor{RESET}"
    meta = f"{DIM}{lat},{lon} · {tz} · sunset→sunrise{RESET}"
    print()
    print(f"  {title}   {meta}")
    print(f"  {DIM}{'─' * 82}{RESET}")
    print(f"  {DIM}               set →rise   avg   hourly cloud cover →{RESET}")

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
        set_s = n["sunset"].strftime("%H:%M")
        rise_s = n["sunrise"].strftime("%H:%M")
        times = f"{DIM}{set_s}→{rise_s}{RESET}"

        if is_past:
            avg_s = f"{DIM}{n['avg']:>3.0f}%{RESET}"
            bar = f"{DIM}{bar}{RESET}"

        print(f"  {label}  {times}  {avg_s}  {bar}")

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
