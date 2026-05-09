"""Cloud base height predictor for Henry W. Coe State Park.

Estimates cloud base height (the lifted condensation level, LCL) from
2 m temperature and dewpoint, fed by the public Open-Meteo forecast API.
Intended as one input to a broader astronomical weather forecast for
observers at Henry Coe — knowing the height (and presence) of the cloud
deck matters for whether the marine layer will overrun the ridge or
stay below it.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

# Henry W. Coe State Park headquarters (Coe Ranch entrance).
HENRY_COE_LATITUDE = 37.1858
HENRY_COE_LONGITUDE = -121.5483
HENRY_COE_ELEVATION_M = 793.0

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Espy's coefficient: cloud base height in meters per °C of dewpoint
# depression. Good rule-of-thumb accuracy for convective cloud bases.
ESPY_COEFFICIENT_M_PER_C = 125.0


@dataclass(frozen=True)
class CloudHeightForecast:
    """A single hourly cloud-height prediction for Henry Coe."""

    time: datetime
    temperature_c: float
    dewpoint_c: float
    cloud_cover_pct: float
    cloud_cover_low_pct: float
    cloud_cover_mid_pct: float
    cloud_cover_high_pct: float
    cloud_base_height_agl_m: float

    @property
    def cloud_base_height_msl_m(self) -> float:
        return HENRY_COE_ELEVATION_M + self.cloud_base_height_agl_m

    @property
    def below_ridge(self) -> bool:
        # Coe's higher ridges top out around 1000 m MSL; a base below that
        # means the deck is sitting on or under the observing site.
        return self.cloud_base_height_msl_m < 1000.0


def lifted_condensation_level_m(temperature_c: float, dewpoint_c: float) -> float:
    """Estimate cloud base height above ground via Espy's equation.

    Returns meters AGL. Negative spreads (saturated/supersaturated air)
    are clamped to zero — fog at the surface.
    """
    spread = temperature_c - dewpoint_c
    return max(0.0, ESPY_COEFFICIENT_M_PER_C * spread)


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


def _fetch_open_meteo(hours: int) -> dict:
    params = {
        "latitude": HENRY_COE_LATITUDE,
        "longitude": HENRY_COE_LONGITUDE,
        "elevation": HENRY_COE_ELEVATION_M,
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


def predict(hours: int = 24) -> list[CloudHeightForecast]:
    """Return hourly cloud-height forecasts for Henry Coe."""
    data = _fetch_open_meteo(hours)
    hourly = data["hourly"]
    return [_build_forecast(hourly, i) for i in range(min(hours, len(hourly["time"])))]


def format_forecast(forecasts: Iterable[CloudHeightForecast]) -> str:
    forecasts = list(forecasts)
    header = (
        f"Cloud base height forecast — Henry Coe State Park "
        f"(lat {HENRY_COE_LATITUDE}, lon {HENRY_COE_LONGITUDE}, "
        f"elev {HENRY_COE_ELEVATION_M:.0f} m MSL)\n\n"
        f"{'Time (UTC)':<17} {'T°C':>5} {'Td°C':>5} {'Cov%':>5} "
        f"{'Low%':>5} {'Mid%':>5} {'High%':>6} "
        f"{'Base m AGL':>11} {'Base m MSL':>11}  Note"
    )
    rows = [header]
    for f in forecasts:
        note = "below ridge" if f.below_ridge else ""
        rows.append(
            f"{f.time.strftime('%Y-%m-%d %H:%M'):<17} "
            f"{f.temperature_c:>5.1f} {f.dewpoint_c:>5.1f} "
            f"{f.cloud_cover_pct:>5.0f} {f.cloud_cover_low_pct:>5.0f} "
            f"{f.cloud_cover_mid_pct:>5.0f} {f.cloud_cover_high_pct:>6.0f} "
            f"{f.cloud_base_height_agl_m:>11.0f} {f.cloud_base_height_msl_m:>11.0f}  {note}"
        )
    return "\n".join(rows)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Predict cloud base height at Henry W. Coe State Park."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Number of forecast hours to print (default: 24).",
    )
    args = parser.parse_args()
    print(format_forecast(predict(args.hours)))


if __name__ == "__main__":
    main()
