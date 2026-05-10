"""Long-range cloud cover forecast using Open-Meteo's GraphCast model.

Pulls multi-day cloud-cover forecasts from DeepMind's GraphCast model
served by the public Open-Meteo API, and rolls them up into daily means
(or prints the hourly series). Runs alongside ``cloud_height_predictor``
as a separate command-line tool, sharing ``sites.json`` next to this
script for site definitions.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
GRAPHCAST_MODEL = "graphcast"
GRAPHCAST_MAX_DAYS = 10
DEFAULT_FORECAST_DAYS = 10
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "sites.json"


@dataclass(frozen=True)
class Site:
    """An observing site loaded from the sites config."""

    id: str
    name: str
    latitude: float
    longitude: float
    elevation_m: float
    ridge_height_m: float | None = None


@dataclass(frozen=True)
class CloudCoverForecast:
    """A single hourly GraphCast cloud-cover prediction."""

    time: datetime
    cloud_cover_pct: float
    cloud_cover_low_pct: float
    cloud_cover_mid_pct: float
    cloud_cover_high_pct: float


@dataclass(frozen=True)
class DailyCloudCover:
    """Daily roll-up of hourly cloud-cover values (UTC days)."""

    day: date
    mean_cloud_cover_pct: float
    mean_low_pct: float
    mean_mid_pct: float
    mean_high_pct: float
    hours: int


def load_sites(path: Path | str = DEFAULT_CONFIG_PATH) -> dict[str, Site]:
    """Load and validate the sites config, returning an id -> Site mapping."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ValueError(f"sites config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"sites config {path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict) or "sites" not in raw or not isinstance(raw["sites"], dict):
        raise ValueError(f"sites config {path} must contain a top-level 'sites' object")

    required = ("name", "latitude", "longitude", "elevation_m")
    sites: dict[str, Site] = {}
    for site_id, entry in raw["sites"].items():
        if not isinstance(entry, dict):
            raise ValueError(f"site '{site_id}' must be an object")
        missing = [k for k in required if k not in entry]
        if missing:
            raise ValueError(
                f"site '{site_id}' is missing required field(s): {', '.join(missing)}"
            )
        sites[site_id] = Site(
            id=site_id,
            name=entry["name"],
            latitude=float(entry["latitude"]),
            longitude=float(entry["longitude"]),
            elevation_m=float(entry["elevation_m"]),
            ridge_height_m=(
                float(entry["ridge_height_m"]) if entry.get("ridge_height_m") is not None else None
            ),
        )
    return sites


def _build_forecast(hourly: dict, index: int) -> CloudCoverForecast:
    return CloudCoverForecast(
        time=datetime.fromisoformat(hourly["time"][index]).replace(tzinfo=timezone.utc),
        cloud_cover_pct=hourly["cloud_cover"][index],
        cloud_cover_low_pct=hourly["cloud_cover_low"][index],
        cloud_cover_mid_pct=hourly["cloud_cover_mid"][index],
        cloud_cover_high_pct=hourly["cloud_cover_high"][index],
    )


def _fetch_open_meteo(site: Site, days: int) -> dict:
    params = {
        "latitude": site.latitude,
        "longitude": site.longitude,
        "elevation": site.elevation_m,
        "models": GRAPHCAST_MODEL,
        "hourly": ",".join(
            [
                "cloud_cover",
                "cloud_cover_low",
                "cloud_cover_mid",
                "cloud_cover_high",
            ]
        ),
        "forecast_days": days,
        "timezone": "UTC",
    }
    url = f"{OPEN_METEO_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.load(resp)


def predict(site: Site, days: int = DEFAULT_FORECAST_DAYS) -> list[CloudCoverForecast]:
    """Return GraphCast hourly cloud-cover forecasts for the given site."""
    days = max(1, min(days, GRAPHCAST_MAX_DAYS))
    data = _fetch_open_meteo(site, days)
    hourly = data["hourly"]
    return [_build_forecast(hourly, i) for i in range(len(hourly["time"]))]


def daily_summaries(forecasts: Iterable[CloudCoverForecast]) -> list[DailyCloudCover]:
    """Group hourly forecasts by UTC date and return per-day means."""
    by_day: dict[date, list[CloudCoverForecast]] = {}
    for f in forecasts:
        by_day.setdefault(f.time.date(), []).append(f)
    out: list[DailyCloudCover] = []
    for day, items in sorted(by_day.items()):
        n = len(items)
        out.append(
            DailyCloudCover(
                day=day,
                mean_cloud_cover_pct=sum(i.cloud_cover_pct for i in items) / n,
                mean_low_pct=sum(i.cloud_cover_low_pct for i in items) / n,
                mean_mid_pct=sum(i.cloud_cover_mid_pct for i in items) / n,
                mean_high_pct=sum(i.cloud_cover_high_pct for i in items) / n,
                hours=n,
            )
        )
    return out


def format_daily(summaries: Iterable[DailyCloudCover], site: Site) -> str:
    summaries = list(summaries)
    header = (
        f"GraphCast long-range cloud cover (daily means) — {site.name} "
        f"(lat {site.latitude}, lon {site.longitude}, elev {site.elevation_m:.0f} m MSL)\n\n"
        f"{'Day (UTC)':<12} {'Cov%':>5} {'Low%':>5} {'Mid%':>5} {'High%':>6} {'Hrs':>4}"
    )
    rows = [header]
    for s in summaries:
        rows.append(
            f"{s.day.isoformat():<12} "
            f"{s.mean_cloud_cover_pct:>5.0f} {s.mean_low_pct:>5.0f} "
            f"{s.mean_mid_pct:>5.0f} {s.mean_high_pct:>6.0f} {s.hours:>4}"
        )
    return "\n".join(rows)


def format_hourly(forecasts: Iterable[CloudCoverForecast], site: Site) -> str:
    forecasts = list(forecasts)
    header = (
        f"GraphCast long-range cloud cover (hourly) — {site.name} "
        f"(lat {site.latitude}, lon {site.longitude}, elev {site.elevation_m:.0f} m MSL)\n\n"
        f"{'Time (UTC)':<17} {'Cov%':>5} {'Low%':>5} {'Mid%':>5} {'High%':>6}"
    )
    rows = [header]
    for f in forecasts:
        rows.append(
            f"{f.time.strftime('%Y-%m-%d %H:%M'):<17} "
            f"{f.cloud_cover_pct:>5.0f} {f.cloud_cover_low_pct:>5.0f} "
            f"{f.cloud_cover_mid_pct:>5.0f} {f.cloud_cover_high_pct:>6.0f}"
        )
    return "\n".join(rows)


def format_site_list(sites: dict[str, Site]) -> str:
    if not sites:
        return "(no sites configured)"
    width = max(len(sid) for sid in sites)
    lines = []
    for sid, s in sites.items():
        ridge = f", ridge {s.ridge_height_m:.0f} m" if s.ridge_height_m is not None else ""
        lines.append(
            f"  {sid.ljust(width)}  {s.name}  "
            f"({s.latitude:.4f}, {s.longitude:.4f}, {s.elevation_m:.0f} m{ridge})"
        )
    return "\n".join(lines)


def _build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="graphcast_cloud_forecast",
        description=(
            "Long-range cloud cover forecast for a configured observing site, "
            "using DeepMind's GraphCast model served by Open-Meteo. Companion "
            "to cloud_height_predictor for multi-day planning."
        ),
        epilog=(
            "Examples:\n"
            "  graphcast_cloud_forecast --list-sites\n"
            "  graphcast_cloud_forecast --site henry-coe\n"
            "  graphcast_cloud_forecast --site henry-coe --days 7\n"
            "  graphcast_cloud_forecast --site henry-coe --hourly"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--site",
        metavar="ID",
        help="Site id to forecast for (see --list-sites for available ids).",
    )
    parser.add_argument(
        "--list-sites",
        action="store_true",
        help="Print the configured sites and exit.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_FORECAST_DAYS,
        metavar="N",
        help=(
            f"Number of forecast days, 1-{GRAPHCAST_MAX_DAYS} (default: %(default)s). "
            "Open-Meteo currently caps GraphCast at 10 days."
        ),
    )
    parser.add_argument(
        "--hourly",
        action="store_true",
        help="Print the full hourly series instead of daily means.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        metavar="PATH",
        help="Path to the sites config JSON (default: sites.json next to this script).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        sites = load_sites(args.config)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list_sites:
        print("Configured sites:")
        print(format_site_list(sites))
        return 0

    if args.site is None:
        print("error: --site is required (or pass --list-sites).", file=sys.stderr)
        print("Available sites:", file=sys.stderr)
        print(format_site_list(sites), file=sys.stderr)
        return 2

    if args.site not in sites:
        print(f"error: unknown site '{args.site}'.", file=sys.stderr)
        print("Available sites:", file=sys.stderr)
        print(format_site_list(sites), file=sys.stderr)
        return 2

    site = sites[args.site]
    forecasts = predict(site, args.days)
    if args.hourly:
        print(format_hourly(forecasts, site))
    else:
        print(format_daily(daily_summaries(forecasts), site))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
