"""Network-free tests for scripts/coe_cloud.py — moon math + bar rendering."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "scripts")),
)

from coe_cloud import (  # noqa: E402
    BLOCKS,
    LAT,
    LON,
    RESET,
    _julian_date,
    _moon_geometry,
    cloud_bar,
    moon_for_night,
)


# Reference epochs from NASA's Six Millennium Catalog of Phases of the Moon.
# All times naive UTC, matching the _moon_geometry contract.
NEW_MOON_2026_05 = datetime(2026, 5, 16, 20, 1)
FULL_MOON_2026_05 = datetime(2026, 5, 31, 18, 45)


def test_illumination_near_zero_at_new_moon():
    _, illum = _moon_geometry(NEW_MOON_2026_05)
    assert illum < 0.01, f"illum at new moon = {illum}, expected < 0.01"


def test_illumination_near_one_at_full_moon():
    _, illum = _moon_geometry(FULL_MOON_2026_05)
    assert illum > 0.99, f"illum at full moon = {illum}, expected > 0.99"


def test_full_moon_below_horizon_at_local_noon():
    # Full moon rises near sunset, sets near sunrise. At local noon on the
    # full-moon day the moon is near the antimeridian → well below horizon.
    local_noon_utc = datetime(2026, 5, 31, 19, 0)  # 12:00 PDT
    alt, _ = _moon_geometry(local_noon_utc)
    assert alt < -30, f"full moon alt at local noon = {alt}°, expected < -30°"


def test_full_moon_above_horizon_at_local_midnight():
    # Full moon should be well above the horizon at local midnight. Note the
    # peak altitude is only ~25° on this date because the moon's declination
    # is near its southern lunar-standstill extreme — so the threshold is
    # set well below transit altitude to stay robust across years.
    local_midnight_utc = datetime(2026, 6, 1, 7, 0)  # 00:00 PDT
    alt, _ = _moon_geometry(local_midnight_utc)
    assert alt > 15, f"full moon alt at local midnight = {alt}°, expected > 15°"


def test_illumination_monotonic_through_first_quarter():
    # From new moon to first quarter (~7 days), illumination grows monotonically.
    samples = [
        _moon_geometry(NEW_MOON_2026_05 + timedelta(days=d))[1]
        for d in (1, 3, 5, 7)
    ]
    for a, b in zip(samples, samples[1:]):
        assert b > a, f"illum should be monotonically increasing: {samples}"


def test_julian_date_known_epoch():
    # J2000.0 epoch = 2000-01-01 12:00 TT ≈ 2451545.0 UT (TT-UT delta ignored
    # at low-precision; the 64.184 s offset is well under our ~0.1° tolerance).
    jd = _julian_date(datetime(2000, 1, 1, 12, 0))
    assert abs(jd - 2451545.0) < 1e-6, f"JD(J2000) = {jd}"


def test_lat_lon_loaded_from_sites_json():
    # Should match sites.json's henry-coe entry, not a hand-typed constant.
    assert abs(LAT - 37.1858) < 1e-6
    assert abs(LON - (-121.5483)) < 1e-6


def test_blocks_has_eight_levels():
    assert len(BLOCKS) == 8


def test_cloud_bar_empty_input():
    assert cloud_bar([]) == ""


def test_cloud_bar_no_inner_reset_on_color_transition():
    """Regression: past-night DIM wrapping depends on cloud_bar emitting RESET
    only at the very end, never on color transitions."""
    # Spans INDIGO (0-10), CYAN (10-25), GREEN (25-50), YELLOW (50-75),
    # ORANGE (75-90), RED (90+) — six distinct colors, five transitions.
    bar = cloud_bar([5, 15, 30, 60, 80, 95])
    assert bar.endswith(RESET)
    inner = bar[: -len(RESET)]
    assert RESET not in inner, f"cloud_bar emitted inner RESET: {bar!r}"


def test_cloud_bar_single_color_input():
    bar = cloud_bar([5, 6, 7])
    assert bar.endswith(RESET)
    inner = bar[: -len(RESET)]
    assert RESET not in inner


def test_moon_for_night_returns_avg_and_bar():
    # 6 hours starting at local 22:00 PDT on full-moon evening.
    base = datetime(2026, 5, 31, 22, 0)  # naive local PDT
    hours = [base + timedelta(hours=h) for h in range(6)]
    utc_offset = -7 * 3600  # PDT
    avg, bar = moon_for_night(hours, utc_offset)
    # Full moon should be up for most or all of these hours and ~100% lit.
    assert 80 < avg <= 100, f"full-moon avg = {avg}, expected ~100"
    assert bar.endswith(RESET)
    inner = bar[: -len(RESET)]
    assert RESET not in inner, f"moon bar emitted inner RESET: {bar!r}"


def test_moon_for_night_zero_avg_when_all_below_horizon():
    # New moon, daytime hours (UTC ~17-22 = 10-15 PDT). Moon at conjunction
    # with sun → near sun's position → below horizon at night.
    base = datetime(2026, 5, 16, 4, 0)  # 21:00 PDT May 15 (~5h before new moon)
    hours = [base + timedelta(hours=h) for h in range(6)]
    utc_offset = -7 * 3600
    avg, _ = moon_for_night(hours, utc_offset)
    # Won't be exactly zero (some moon contribution near new), but should be tiny.
    assert avg < 5, f"new-moon-night avg = {avg}, expected < 5%"


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
