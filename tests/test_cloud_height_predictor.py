"""Network-free tests for the cloud height predictor math, config, formatting."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from cloud_height_predictor import (  # noqa: E402
    CloudHeightForecast,
    Site,
    _build_forecast,
    _site_timezone,
    cloud_base_height_msl_m,
    format_forecast,
    format_site_list,
    is_below_ridge,
    is_extinction,
    lifted_condensation_level_m,
    load_sites,
    main,
)


HENRY_COE = Site(
    id="henry-coe",
    name="Henry W. Coe State Park",
    latitude=37.1858,
    longitude=-121.5483,
    elevation_m=793.0,
    ridge_height_m=1000.0,
)


def _forecast(temp: float, dew: float) -> CloudHeightForecast:
    return CloudHeightForecast(
        time=datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc),
        temperature_c=temp,
        dewpoint_c=dew,
        cloud_cover_pct=40.0,
        cloud_cover_low_pct=10.0,
        cloud_cover_mid_pct=20.0,
        cloud_cover_high_pct=10.0,
        cloud_base_height_agl_m=lifted_condensation_level_m(temp, dew),
    )


def test_lcl_typical_summer_afternoon():
    assert lifted_condensation_level_m(25.0, 10.0) == 1875.0


def test_lcl_saturated_air_clamps_to_zero():
    assert lifted_condensation_level_m(12.0, 12.0) == 0.0
    assert lifted_condensation_level_m(8.0, 10.0) == 0.0


def test_msl_height_adds_site_elevation():
    f = _forecast(20.0, 10.0)  # AGL = 1250
    assert cloud_base_height_msl_m(f, HENRY_COE) == HENRY_COE.elevation_m + 1250.0


def test_below_ridge_flag_for_marine_layer():
    f = _forecast(12.0, 11.0)  # AGL = 125, MSL = 918, below 1000 ridge
    assert is_below_ridge(f, HENRY_COE) is True


def test_below_ridge_flag_above_ridge():
    f = _forecast(20.0, 10.0)  # MSL > 1000
    assert is_below_ridge(f, HENRY_COE) is False


def test_extinction_when_cloud_base_at_site_elevation():
    # Saturated air → AGL = 0, MSL == elevation → observer is in cloud.
    f = _forecast(12.0, 12.0)
    assert is_extinction(f, HENRY_COE) is True


def test_no_extinction_when_cloud_base_above_site():
    f = _forecast(20.0, 10.0)  # AGL = 1250
    assert is_extinction(f, HENRY_COE) is False


def test_format_forecast_flags_extinction_note():
    out = format_forecast([_forecast(10.0, 10.0)], HENRY_COE)
    assert "extinction" in out


def test_is_below_ridge_returns_false_when_ridge_unset():
    flat_site = Site(
        id="flat",
        name="Flat Plain",
        latitude=0.0,
        longitude=0.0,
        elevation_m=10.0,
        ridge_height_m=None,
    )
    f = _forecast(12.0, 11.0)
    assert is_below_ridge(f, flat_site) is False


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
    assert f0.cloud_base_height_agl_m == 1250.0
    assert f1.cloud_base_height_agl_m == 1000.0
    assert f0.time == datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc)


def test_build_forecast_honors_local_timezone():
    hourly = {
        "time": ["2026-05-09T08:00"],
        "temperature_2m": [18.0],
        "dew_point_2m": [8.0],
        "cloud_cover": [40],
        "cloud_cover_low": [10],
        "cloud_cover_mid": [20],
        "cloud_cover_high": [10],
    }
    pdt = timezone(timedelta(hours=-7), "PDT")
    f = _build_forecast(hourly, 0, pdt)
    assert f.time == datetime(2026, 5, 9, 8, 0, tzinfo=pdt)
    assert f.time.tzname() == "PDT"


def test_site_timezone_parses_open_meteo_payload():
    tz = _site_timezone(
        {
            "utc_offset_seconds": -25200,
            "timezone": "America/Los_Angeles",
            "timezone_abbreviation": "PDT",
        }
    )
    assert tz.utcoffset(None) == timedelta(hours=-7)
    assert tz.tzname(None) == "PDT"


def test_site_timezone_defaults_to_utc_when_missing():
    tz = _site_timezone({})
    assert tz.utcoffset(None) == timedelta(0)
    assert tz.tzname(None) == "UTC"


def test_format_forecast_shows_local_timezone_in_header():
    pdt = timezone(timedelta(hours=-7), "PDT")
    local_forecast = CloudHeightForecast(
        time=datetime(2026, 5, 9, 20, 0, tzinfo=pdt),
        temperature_c=18.0,
        dewpoint_c=8.0,
        cloud_cover_pct=40.0,
        cloud_cover_low_pct=10.0,
        cloud_cover_mid_pct=20.0,
        cloud_cover_high_pct=10.0,
        cloud_base_height_agl_m=lifted_condensation_level_m(18.0, 8.0),
    )
    out = format_forecast([local_forecast], HENRY_COE)
    assert "Time (PDT)" in out
    assert "2026-05-09 20:00" in out


def test_format_forecast_includes_site_header_and_rows():
    out = format_forecast([_forecast(18.0, 8.0)], HENRY_COE)
    assert "Henry W. Coe State Park" in out
    assert "ridge 1000" in out
    assert "2026-05-09 00:00" in out
    assert "1250" in out  # AGL


def test_load_sites_parses_config():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "sites.json"
        path.write_text(
            json.dumps(
                {
                    "sites": {
                        "henry-coe": {
                            "name": "Henry W. Coe State Park",
                            "latitude": 37.1858,
                            "longitude": -121.5483,
                            "elevation_m": 793.0,
                            "ridge_height_m": 1000.0,
                        },
                        "flat": {
                            "name": "Flat Plain",
                            "latitude": 0.0,
                            "longitude": 0.0,
                            "elevation_m": 10.0,
                        },
                    }
                }
            )
        )
        sites = load_sites(path)
    assert set(sites) == {"henry-coe", "flat"}
    assert sites["henry-coe"].ridge_height_m == 1000.0
    assert sites["flat"].ridge_height_m is None
    assert sites["henry-coe"].id == "henry-coe"


def test_load_sites_rejects_missing_required_field():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "sites.json"
        path.write_text(
            json.dumps(
                {"sites": {"broken": {"name": "Broken", "latitude": 1.0, "longitude": 2.0}}}
            )
        )
        try:
            load_sites(path)
        except ValueError as exc:
            assert "elevation_m" in str(exc)
        else:
            raise AssertionError("expected ValueError for missing elevation_m")


def test_load_sites_rejects_invalid_json():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "sites.json"
        path.write_text("{ not json")
        try:
            load_sites(path)
        except ValueError as exc:
            assert "valid JSON" in str(exc)
        else:
            raise AssertionError("expected ValueError for invalid JSON")


def test_load_sites_missing_file():
    try:
        load_sites(Path("/nonexistent/path/sites.json"))
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing file")


def test_format_site_list_includes_ids_and_names():
    out = format_site_list({"henry-coe": HENRY_COE})
    assert "henry-coe" in out
    assert "Henry W. Coe State Park" in out
    assert "ridge 1000" in out


def _write_seed_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "sites": {
                    "henry-coe": {
                        "name": "Henry W. Coe State Park",
                        "latitude": 37.1858,
                        "longitude": -121.5483,
                        "elevation_m": 793.0,
                        "ridge_height_m": 1000.0,
                    }
                }
            }
        )
    )


def test_main_list_sites_exits_zero(capsys=None):
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "sites.json"
        _write_seed_config(cfg)
        rc = main(["--config", str(cfg), "--list-sites"])
    assert rc == 0


def test_main_missing_site_exits_nonzero():
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "sites.json"
        _write_seed_config(cfg)
        rc = main(["--config", str(cfg)])
    assert rc == 2


def test_main_unknown_site_exits_nonzero():
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "sites.json"
        _write_seed_config(cfg)
        rc = main(["--config", str(cfg), "--site", "bogus"])
    assert rc == 2


def test_main_bad_config_exits_nonzero():
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "sites.json"
        cfg.write_text("not json")
        rc = main(["--config", str(cfg), "--list-sites"])
    assert rc == 2


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
        except Exception:
            failures += 1
            print(f"ERROR {t.__name__}")
            traceback.print_exc()
    if failures:
        sys.exit(1)
