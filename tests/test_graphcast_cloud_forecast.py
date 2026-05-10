"""Network-free tests for the GraphCast cloud cover forecast utility."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from graphcast_cloud_forecast import (  # noqa: E402
    CloudCoverForecast,
    DailyCloudCover,
    Site,
    _build_forecast,
    daily_summaries,
    format_daily,
    format_hourly,
    format_site_list,
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


def _hourly_payload(start_iso: str, count: int) -> dict:
    base = datetime.fromisoformat(start_iso)
    times = [(base + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(count)]
    return {
        "time": times,
        "cloud_cover": [40.0 + i for i in range(count)],
        "cloud_cover_low": [10.0 + i for i in range(count)],
        "cloud_cover_mid": [20.0 + i for i in range(count)],
        "cloud_cover_high": [10.0 + i for i in range(count)],
    }


def _forecast(t: datetime, cov: float = 50.0) -> CloudCoverForecast:
    return CloudCoverForecast(
        time=t,
        cloud_cover_pct=cov,
        cloud_cover_low_pct=cov / 5,
        cloud_cover_mid_pct=cov / 4,
        cloud_cover_high_pct=cov / 3,
    )


def test_build_forecast_from_hourly_payload():
    hourly = _hourly_payload("2026-05-09T00:00", 2)
    f0 = _build_forecast(hourly, 0)
    f1 = _build_forecast(hourly, 1)
    assert f0.time == datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc)
    assert f1.time == datetime(2026, 5, 9, 1, 0, tzinfo=timezone.utc)
    assert f0.cloud_cover_pct == 40.0
    assert f1.cloud_cover_pct == 41.0
    assert f0.cloud_cover_low_pct == 10.0
    assert f0.cloud_cover_mid_pct == 20.0
    assert f0.cloud_cover_high_pct == 10.0


def test_daily_summaries_groups_by_utc_date_and_averages():
    forecasts = [
        _forecast(datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc), cov=20.0),
        _forecast(datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc), cov=80.0),
        _forecast(datetime(2026, 5, 10, 6, 0, tzinfo=timezone.utc), cov=50.0),
    ]
    out = daily_summaries(forecasts)
    assert [s.day for s in out] == [date(2026, 5, 9), date(2026, 5, 10)]
    assert out[0].hours == 2
    assert out[0].mean_cloud_cover_pct == 50.0
    assert out[1].hours == 1
    assert out[1].mean_cloud_cover_pct == 50.0


def test_daily_summaries_empty_input():
    assert daily_summaries([]) == []


def test_format_daily_includes_site_header_and_means():
    summary = DailyCloudCover(
        day=date(2026, 5, 9),
        mean_cloud_cover_pct=42.0,
        mean_low_pct=8.0,
        mean_mid_pct=21.0,
        mean_high_pct=14.0,
        hours=24,
    )
    out = format_daily([summary], HENRY_COE)
    assert "Henry W. Coe State Park" in out
    assert "GraphCast" in out
    assert "2026-05-09" in out
    assert "42" in out
    assert " 24" in out  # hours column


def test_format_hourly_includes_each_row():
    forecasts = [
        _forecast(datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc), cov=30.0),
        _forecast(datetime(2026, 5, 9, 1, 0, tzinfo=timezone.utc), cov=60.0),
    ]
    out = format_hourly(forecasts, HENRY_COE)
    assert "Henry W. Coe State Park" in out
    assert "2026-05-09 00:00" in out
    assert "2026-05-09 01:00" in out


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


def test_main_list_sites_exits_zero():
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
