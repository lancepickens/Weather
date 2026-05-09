"""Network-free tests for the cloud height predictor math and formatting."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from cloud_height_predictor import (  # noqa: E402
    CloudHeightForecast,
    HENRY_COE_ELEVATION_M,
    _build_forecast,
    format_forecast,
    lifted_condensation_level_m,
)


def test_lcl_typical_summer_afternoon():
    # 25°C / 10°C dewpoint -> 15°C spread -> ~1875 m AGL.
    assert lifted_condensation_level_m(25.0, 10.0) == 1875.0


def test_lcl_saturated_air_clamps_to_zero():
    assert lifted_condensation_level_m(12.0, 12.0) == 0.0
    assert lifted_condensation_level_m(8.0, 10.0) == 0.0


def test_msl_height_adds_park_elevation():
    f = CloudHeightForecast(
        time=datetime(2026, 5, 9, 3, 0, tzinfo=timezone.utc),
        temperature_c=20.0,
        dewpoint_c=10.0,
        cloud_cover_pct=30.0,
        cloud_cover_low_pct=10.0,
        cloud_cover_mid_pct=15.0,
        cloud_cover_high_pct=5.0,
        cloud_base_height_agl_m=1250.0,
    )
    assert f.cloud_base_height_msl_m == HENRY_COE_ELEVATION_M + 1250.0
    assert f.below_ridge is False


def test_below_ridge_flag_for_marine_layer():
    f = CloudHeightForecast(
        time=datetime(2026, 5, 9, 6, 0, tzinfo=timezone.utc),
        temperature_c=12.0,
        dewpoint_c=11.0,
        cloud_cover_pct=95.0,
        cloud_cover_low_pct=95.0,
        cloud_cover_mid_pct=10.0,
        cloud_cover_high_pct=0.0,
        cloud_base_height_agl_m=125.0,
    )
    # Base ~125 m AGL + 793 m MSL = 918 m, below the ~1000 m ridge.
    assert f.below_ridge is True


def test_build_forecast_from_hourly_payload():
    hourly = {
        "time": ["2026-05-09T00:00", "2026-05-09T01:00"],
        "temperature_2m": [18.0, 17.0],
        "dew_point_2m": [8.0, 9.0],
        "cloud_cover": [40, 60],
        "cloud_cover_low": [10, 30],
        "cloud_cover_mid": [20, 20],
        "cloud_cover_high": [10, 10],
    }
    f0 = _build_forecast(hourly, 0)
    f1 = _build_forecast(hourly, 1)
    assert f0.cloud_base_height_agl_m == 1250.0  # (18-8)*125
    assert f1.cloud_base_height_agl_m == 1000.0  # (17-9)*125
    assert f0.time == datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc)


def test_format_forecast_includes_header_and_rows():
    forecasts = [
        CloudHeightForecast(
            time=datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc),
            temperature_c=18.0,
            dewpoint_c=8.0,
            cloud_cover_pct=40.0,
            cloud_cover_low_pct=10.0,
            cloud_cover_mid_pct=20.0,
            cloud_cover_high_pct=10.0,
            cloud_base_height_agl_m=1250.0,
        )
    ]
    out = format_forecast(forecasts)
    assert "Henry Coe State Park" in out
    assert "2026-05-09 00:00" in out
    assert "1250" in out


if __name__ == "__main__":
    import traceback

    failures = 0
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError:
            failures += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    if failures:
        sys.exit(1)
