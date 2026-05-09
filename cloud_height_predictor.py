"""Cloud base height predictor for astronomical observing sites.

Estimates cloud base height (the lifted condensation level, LCL) from
2 m temperature and dewpoint, fed by the public Open-Meteo forecast API.
Sites are loaded from a JSON config (default: ``sites.json`` next to this
script) so the same utility can serve any observing location alongside
the rest of the astronomical-weather toolkit in this repo.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "sites.json"

# Espy's coefficient: cloud base height in meters per °C of dewpoint
# depression. Good rule-of-thumb accuracy for convective cloud bases.
ESPY_COEFFICIENT_M_PER_C = 125.0


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
class CloudHeightForecast:
    """A single hourly cloud-height prediction."""

    time: datetime
    temperature_c: float
    dewpoint_c: float
    cloud_cover_pct: float
    cloud_cover_low_pct: float
    cloud_cover_mid_pct: float
    cloud_cover_high_pct: float
    cloud_base_height_agl_m: float


def lifted_condensation_level_m(temperature_c: float, dewpoint_c: float) -> float:
    """Estimate cloud base height above ground via Espy's equation.

    Returns meters AGL. Negative spreads (saturated/supersaturated air)
    are clamped to zero — fog at the surface.
    """
    spread = temperature_c - dewpoint_c
    return max(0.0, ESPY_COEFFICIENT_M_PER_C * spread)


def cloud_base_height_msl_m(forecast: CloudHeightForecast, site: Site) -> float:
    return site.elevation_m + forecast.cloud_base_height_agl_m


def is_below_ridge(forecast: CloudHeightForecast, site: Site) -> bool:
    """True when the forecast cloud base sits below the site's local ridge."""
    if site.ridge_height_m is None:
        return False
    return cloud_base_height_msl_m(forecast, site) < site.ridge_height_m


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


def _build_forecast(hourly: dict, index: int) -> CloudHeightForecast:
    temp = hourly["temperature_2m"][index]
    dew = hourly["dew_point_2m"][index]
    return CloudHeightForecast(
        time=datetime.fromisoformat(hourly["time"][index]).replace(tzinfo=timezone.utc),
        temperature_c=temp,
        dewpoint_c=dew,
        cloud_cover_pct=hourly["cloud_cover"][index],
        cloud_cover_low_pct=hourly["cloud_cover_low"][index],
        cloud_cover_mid_pct=hourly["cloud_cover_mid"][index],
        cloud_cover_high_pct=hourly["cloud_cover_high"][index],
        cloud_base_height_agl_m=lifted_condensation_level_m(temp, dew),
    )


def _fetch_open_meteo(site: Site, hours: int) -> dict:
    params = {
        "latitude": site.latitude,
        "longitude": site.longitude,
        "elevation": site.elevation_m,
        "hourly": ",".join(
            [
                "temperature_2m",
                "dew_point_2m",
                "cloud_cover",
                "cloud_cover_low",
                "cloud_cover_mid",
                "cloud_cover_high",
            ]
        ),
        "forecast_days": max(1, (hours + 23) // 24),
        "timezone": "UTC",
    }
    url = f"{OPEN_METEO_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.load(resp)


def predict(site: Site, hours: int = 24) -> list[CloudHeightForecast]:
    """Return hourly cloud-height forecasts for the given site."""
    data = _fetch_open_meteo(site, hours)
    hourly = data["hourly"]
    return [_build_forecast(hourly, i) for i in range(min(hours, len(hourly["time"])))]


def format_forecast(forecasts: Iterable[CloudHeightForecast], site: Site) -> str:
    forecasts = list(forecasts)
    ridge_note = (
        f", ridge {site.ridge_height_m:.0f} m" if site.ridge_height_m is not None else ""
    )
    header = (
        f"Cloud base height forecast — {site.name} "
        f"(lat {site.latitude}, lon {site.longitude}, "
        f"elev {site.elevation_m:.0f} m MSL{ridge_note})\n\n"
        f"{'Time (UTC)':<17} {'T°C':>5} {'Td°C':>5} {'Cov%':>5} "
        f"{'Low%':>5} {'Mid%':>5} {'High%':>6} "
        f"{'Base m AGL':>11} {'Base m MSL':>11}  Note"
    )
    rows = [header]
    for f in forecasts:
        note = "below ridge" if is_below_ridge(f, site) else ""
        rows.append(
            f"{f.time.strftime('%Y-%m-%d %H:%M'):<17} "
            f"{f.temperature_c:>5.1f} {f.dewpoint_c:>5.1f} "
            f"{f.cloud_cover_pct:>5.0f} {f.cloud_cover_low_pct:>5.0f} "
            f"{f.cloud_cover_mid_pct:>5.0f} {f.cloud_cover_high_pct:>6.0f} "
            f"{f.cloud_base_height_agl_m:>11.0f} "
            f"{cloud_base_height_msl_m(f, site):>11.0f}  {note}"
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
        prog="cloud_height_predictor",
        description=(
            "Predict cloud base height (LCL) at a configured observing site using "
            "Open-Meteo forecasts. Useful as one input to a broader astronomical "
            "weather forecast."
        ),
        epilog=(
            "Examples:\n"
            "  cloud_height_predictor --list-sites\n"
            "  cloud_height_predictor --site henry-coe\n"
            "  cloud_height_predictor --site henry-coe --hours 12\n"
            "  cloud_height_predictor --config /path/to/sites.json --site henry-coe"
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
        "--hours",
        type=int,
        default=24,
        metavar="N",
        help="Number of forecast hours to print (default: %(default)s).",
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
    print(format_forecast(predict(site, args.hours), site))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
