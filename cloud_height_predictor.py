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
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Iterable

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "sites.json"

# Espy's coefficient: cloud base height in meters per °C of dewpoint
# depression. Good rule-of-thumb accuracy for convective cloud bases.
ESPY_COEFFICIENT_M_PER_C = 125.0

# Surface RH above this is a marine-layer / fog risk even when the model's
# integrated cloud_cover field reads 0% — catches grid-snap "dry island"
# artifacts at coastal/inland boundaries.
MARINE_LAYER_RH_THRESHOLD_PCT = 90.0

# Lat/lon offset for the 3×3 neighbour ring sampled around each site.
# ICON-Global is served on a ~0.125° grid via Open-Meteo, so 0.15°
# (~17 km at mid-latitudes) is large enough to guarantee that each ring
# position snaps to a distinct grid cell from the center.
NEIGHBOUR_OFFSET_DEG = 0.15


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
    """A single hourly cloud-height prediction.

    The ring-aggregated ``cloud_cover*``, ``relative_humidity_pct``, and
    ``dewpoint_c`` reflect the worst case across a 3×3 neighbour ring
    (see ``_aggregate_cells``). The matching ``center_*`` fields carry
    what the model says at the site's exact configured coordinate.
    Comparing the two lets callers distinguish "site in marine layer"
    from "site above an undercast that only fills the surrounding
    lower-elevation cells" — a distinction the ring-max view alone
    cannot make.
    """

    time: datetime
    temperature_c: float
    dewpoint_c: float
    relative_humidity_pct: float
    cloud_cover_pct: float
    cloud_cover_low_pct: float
    cloud_cover_mid_pct: float
    cloud_cover_high_pct: float
    cloud_base_height_agl_m: float
    # Center-cell view (the model's point forecast for the site coordinate).
    center_dewpoint_c: float = 0.0
    center_relative_humidity_pct: float = 0.0
    center_cloud_cover_pct: float = 0.0
    center_cloud_cover_low_pct: float = 0.0
    center_cloud_base_height_agl_m: float = 0.0


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


def is_extinction(forecast: CloudHeightForecast, site: Site) -> bool:
    """True when the cloud base sits at or below the site — observer is in cloud."""
    return cloud_base_height_msl_m(forecast, site) <= site.elevation_m


def is_marine_layer_risk(forecast: CloudHeightForecast) -> bool:
    """True when surface RH is high enough that fog/stratus is plausible
    even if the model's integrated cloud_cover field reads near zero."""
    return forecast.relative_humidity_pct > MARINE_LAYER_RH_THRESHOLD_PCT


# Thresholds for diagnosing an "undercast below site" pattern: marine
# layer / fog filling the surrounding lower-elevation cells while the
# configured site coordinate is dry and clear. Tuned against the
# 2026-05-14/15 Henry Coe night where the cam confirmed clear skies
# despite full marine-layer flags from the ring aggregator.
UNDERCAST_CENTER_RH_MAX_PCT = 70.0
UNDERCAST_CENTER_COVER_MAX_PCT = 30.0


def is_marine_layer_below(forecast: CloudHeightForecast) -> bool:
    """True when the center cell is dry/clear while the neighbour ring is saturated.

    Pattern: site sits on or above an inversion top — fog/stratus fills
    the lower-elevation cells in every direction, but the site itself
    pokes above the layer. When this fires alongside the conservative
    ``extinction`` / ``below ridge`` / ``marine-layer risk`` flags, the
    conservative flags are almost always about cells *below* the site;
    overhead observing conditions are likely good.
    """
    center_dry = (
        forecast.center_relative_humidity_pct < UNDERCAST_CENTER_RH_MAX_PCT
        and forecast.center_cloud_cover_pct < UNDERCAST_CENTER_COVER_MAX_PCT
    )
    ring_saturated = forecast.relative_humidity_pct > MARINE_LAYER_RH_THRESHOLD_PCT
    return center_dry and ring_saturated


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


def _build_forecast(
    hourly: dict, index: int, tz: tzinfo = timezone.utc
) -> CloudHeightForecast:
    temp = hourly["temperature_2m"][index]
    dew = hourly["dew_point_2m"][index]
    # Center fields default to ring values when the payload omits them
    # (older test fixtures or pre-aggregation callers).
    center_dew = hourly.get("center_dew_point_2m", hourly["dew_point_2m"])[index]
    center_rh = hourly.get("center_relative_humidity_2m", hourly["relative_humidity_2m"])[index]
    center_cov = hourly.get("center_cloud_cover", hourly["cloud_cover"])[index]
    center_low = hourly.get("center_cloud_cover_low", hourly["cloud_cover_low"])[index]
    return CloudHeightForecast(
        time=datetime.fromisoformat(hourly["time"][index]).replace(tzinfo=tz),
        temperature_c=temp,
        dewpoint_c=dew,
        relative_humidity_pct=hourly["relative_humidity_2m"][index],
        cloud_cover_pct=hourly["cloud_cover"][index],
        cloud_cover_low_pct=hourly["cloud_cover_low"][index],
        cloud_cover_mid_pct=hourly["cloud_cover_mid"][index],
        cloud_cover_high_pct=hourly["cloud_cover_high"][index],
        cloud_base_height_agl_m=lifted_condensation_level_m(temp, dew),
        center_dewpoint_c=center_dew,
        center_relative_humidity_pct=center_rh,
        center_cloud_cover_pct=center_cov,
        center_cloud_cover_low_pct=center_low,
        center_cloud_base_height_agl_m=lifted_condensation_level_m(temp, center_dew),
    )


def _neighbour_coords(site: Site) -> list[tuple[float, float]]:
    """Return center + 8 ring coordinates around the site.

    Center is index 0 by convention; aggregation routines take center-only
    fields (T, is_day, time) from this position.
    """
    offsets = (-NEIGHBOUR_OFFSET_DEG, 0.0, NEIGHBOUR_OFFSET_DEG)
    center = (site.latitude, site.longitude)
    ring = [
        (site.latitude + dlat, site.longitude + dlon)
        for dlat in offsets
        for dlon in offsets
        if not (dlat == 0.0 and dlon == 0.0)
    ]
    return [center, *ring]


_RING_MAX_KEYS = (
    "dew_point_2m",
    "relative_humidity_2m",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
)
_CENTER_KEYS = ("time", "temperature_2m", "is_day")
# Center-cell values we keep alongside the ring-max view, exposed to
# callers under a ``center_`` prefix on the aggregated payload.
_CENTER_DIAGNOSTIC_KEYS = (
    "dew_point_2m",
    "relative_humidity_2m",
    "cloud_cover",
    "cloud_cover_low",
)


def _aggregate_cells(cells: list[dict]) -> dict:
    """Collapse a list of per-cell Open-Meteo payloads into one.

    Center cell supplies time, temperature_2m, is_day, and the top-level
    timezone fields. Cloud-cover layers, dew_point_2m, and
    relative_humidity_2m become the element-wise max across all cells —
    the conservative worst-case observable from anywhere in the ring.

    The center cell's own moisture/cloud values are *also* preserved
    under ``center_<key>`` so callers can diagnose the "site above
    undercast" pattern (center dry, ring saturated).
    """
    center = cells[0]
    hourly = {key: center["hourly"][key] for key in _CENTER_KEYS}
    for key in _RING_MAX_KEYS:
        series = [c["hourly"][key] for c in cells]
        hourly[key] = [max(values) for values in zip(*series)]
    for key in _CENTER_DIAGNOSTIC_KEYS:
        hourly[f"center_{key}"] = list(center["hourly"][key])
    return {
        "utc_offset_seconds": center.get("utc_offset_seconds", 0),
        "timezone": center.get("timezone"),
        "timezone_abbreviation": center.get("timezone_abbreviation"),
        "hourly": hourly,
    }


def _fetch_open_meteo(site: Site, forecast_days: int) -> dict:
    coords = _neighbour_coords(site)
    lats = ",".join(f"{la}" for la, _ in coords)
    lons = ",".join(f"{lo}" for _, lo in coords)
    params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": ",".join(
            [
                "temperature_2m",
                "dew_point_2m",
                "relative_humidity_2m",
                "cloud_cover",
                "cloud_cover_low",
                "cloud_cover_mid",
                "cloud_cover_high",
                "is_day",
            ]
        ),
        "past_days": 1,
        "forecast_days": forecast_days,
        "timezone": "auto",
        "models": "icon_seamless",
    }
    url = f"{OPEN_METEO_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        cells = json.load(resp)
    return _aggregate_cells(cells)


def _site_timezone(payload: dict) -> tzinfo:
    offset = timedelta(seconds=int(payload.get("utc_offset_seconds", 0)))
    name = payload.get("timezone_abbreviation") or payload.get("timezone") or "UTC"
    return timezone(offset, name)


def _group_into_nights(
    hourly: dict,
    tz: tzinfo,
    nights: int,
    now: datetime,
) -> list[list[CloudHeightForecast]]:
    """Group future hourly forecasts into contiguous night runs (is_day == 0)."""
    groups: list[list[CloudHeightForecast]] = []
    current: list[CloudHeightForecast] = []
    for i in range(len(hourly["time"])):
        t = datetime.fromisoformat(hourly["time"][i]).replace(tzinfo=tz)
        if t < now:
            continue
        if hourly["is_day"][i] == 0:
            current.append(_build_forecast(hourly, i, tz))
        elif current:
            groups.append(current)
            if len(groups) >= nights:
                return groups
            current = []
    if current and len(groups) < nights:
        groups.append(current)
    return groups[:nights]


def predict(site: Site, nights: int = 1) -> list[list[CloudHeightForecast]]:
    """Return hourly cloud-height forecasts grouped by upcoming night."""
    data = _fetch_open_meteo(site, forecast_days=nights + 1)
    hourly = data["hourly"]
    tz = _site_timezone(data)
    return _group_into_nights(hourly, tz, nights, datetime.now(tz))


def _night_label(night: list[CloudHeightForecast]) -> str:
    """Label a night by the evening's local date (the date before sunrise)."""
    last = night[-1].time
    # A night ends at sunrise on the day after sunset. Subtract 12 h from the
    # final hour to land squarely in the prior evening regardless of DST.
    evening = last - timedelta(hours=12)
    return evening.strftime("%Y-%m-%d")


def format_forecast(
    nights: Iterable[Iterable[CloudHeightForecast]], site: Site
) -> str:
    nights = [list(n) for n in nights if list(n)]
    ridge_note = (
        f", ridge {site.ridge_height_m:.0f} m" if site.ridge_height_m is not None else ""
    )
    if not nights:
        return (
            f"Cloud base height forecast — {site.name} "
            f"(lat {site.latitude}, lon {site.longitude}, "
            f"elev {site.elevation_m:.0f} m MSL{ridge_note})\n\n"
            "No upcoming night-hours available in the forecast window."
        )
    tz_label = nights[0][0].time.tzname() or "UTC"
    time_col = f"Time ({tz_label})"
    time_col_width = max(17, len(time_col))
    header = (
        f"Cloud base height forecast — {site.name} "
        f"(lat {site.latitude}, lon {site.longitude}, "
        f"elev {site.elevation_m:.0f} m MSL{ridge_note})\n\n"
        f"{time_col:<{time_col_width}} {'T°C':>5} {'Td°C':>5} {'RH%':>5} "
        f"{'Cov%':>5} {'Low%':>5} {'Mid%':>5} {'High%':>6} "
        f"{'Base m AGL':>11} {'Base m MSL':>11}  Note"
    )
    rows = [header]
    for i, night in enumerate(nights):
        if i > 0:
            rows.append("")
        rows.append(f"-- Night of {_night_label(night)} (sunset → sunrise) --")
        for f in night:
            notes = []
            if is_extinction(f, site):
                notes.append("extinction")
            if is_below_ridge(f, site):
                notes.append("below ridge")
            if is_marine_layer_risk(f):
                notes.append("marine-layer risk")
            if is_marine_layer_below(f):
                notes.append("marine layer below")
            rows.append(
                f"{f.time.strftime('%Y-%m-%d %H:%M'):<{time_col_width}} "
                f"{f.temperature_c:>5.1f} {f.dewpoint_c:>5.1f} "
                f"{f.relative_humidity_pct:>5.0f} "
                f"{f.cloud_cover_pct:>5.0f} {f.cloud_cover_low_pct:>5.0f} "
                f"{f.cloud_cover_mid_pct:>5.0f} {f.cloud_cover_high_pct:>6.0f} "
                f"{f.cloud_base_height_agl_m:>11.0f} "
                f"{cloud_base_height_msl_m(f, site):>11.0f}  {', '.join(notes)}"
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
            "Open-Meteo forecasts. Output covers only astronomical night hours "
            "(sunset to sunrise) at the site's local coordinates."
        ),
        epilog=(
            "Examples:\n"
            "  cloud_height_predictor --list-sites\n"
            "  cloud_height_predictor --site henry-coe\n"
            "  cloud_height_predictor --site henry-coe --nights 3\n"
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
        "--nights",
        type=int,
        default=1,
        metavar="N",
        help="Number of upcoming nights to forecast (default: %(default)s).",
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

    if args.nights < 1:
        print("error: --nights must be >= 1.", file=sys.stderr)
        return 2

    site = sites[args.site]
    print(format_forecast(predict(site, args.nights), site))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
