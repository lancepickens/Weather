"""Test the bright-star catalog and alt/az projection (requires astropy)."""
from webcam_analyzer.catalog import BRIGHT_STARS, predict_visible


def test_catalog_has_polaris_and_brightest_stars():
    names = {row[0] for row in BRIGHT_STARS}
    assert "Polaris" in names
    assert "Sirius" in names
    assert "Vega" in names
    assert "Arcturus" in names


def test_polaris_visible_above_horizon_from_henry_coe():
    # 2026-05-15 06:00 UTC = 23:00 PT — Polaris is always above horizon at lat 37°.
    stars = predict_visible(
        timestamp_unix=1778832000.0,
        lat_deg=37.18,
        lon_deg=-121.55,
        elevation_m=795.0,
        min_alt_deg=0.0,
        max_vmag=2.5,
    )
    polaris = next((s for s in stars if s.name == "Polaris"), None)
    assert polaris is not None
    # At latitude 37.18, Polaris alt ≈ 37° ± a small NCP offset.
    assert 35.0 < polaris.alt_deg < 40.0
    # Polaris is nearly due north, azimuth ≈ 0°.
    assert (polaris.az_deg < 5.0) or (polaris.az_deg > 355.0)


def test_max_vmag_filters_dim_stars():
    bright = predict_visible(1778832000.0, 37.18, -121.55, 795.0, min_alt_deg=0.0, max_vmag=1.0)
    relaxed = predict_visible(1778832000.0, 37.18, -121.55, 795.0, min_alt_deg=0.0, max_vmag=3.0)
    assert len(bright) <= len(relaxed)
    assert all(s.vmag <= 1.0 for s in bright)


def test_min_alt_filters_below_horizon():
    above_10 = predict_visible(1778832000.0, 37.18, -121.55, 795.0, min_alt_deg=10.0, max_vmag=3.0)
    above_60 = predict_visible(1778832000.0, 37.18, -121.55, 795.0, min_alt_deg=60.0, max_vmag=3.0)
    assert len(above_60) <= len(above_10)
    assert all(s.alt_deg >= 10.0 for s in above_10)
    assert all(s.alt_deg >= 60.0 for s in above_60)
