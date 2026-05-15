"""Tests for fetch.py (URL/parsing only — no network)."""
from webcam_analyzer.fetch import BASE_URL, FrameRef, WINDOW_TO_POOL, _parse_timestamp


def test_parse_timestamp_handles_fractional_seconds():
    assert _parse_timestamp("1778825413.000000000.jpg") == 1778825413.0
    assert _parse_timestamp("1700000000.500000000.jpg") == 1700000000.0


def test_frame_ref_url_round_trip():
    f = FrameRef(camera_id="Axis-StarrCanyon1", pool="1min", filename="1778825413.000000000.jpg", timestamp=1778825413.0)
    assert f.url == f"{BASE_URL}/Axis-StarrCanyon1/1min/1778825413.000000000.jpg"


def test_window_to_pool_keys_match_documented_specs():
    assert set(WINDOW_TO_POOL) == {"5-min", "15-min", "30-min", "1-hour", "3-hour", "6-hour", "12-hour"}
    assert WINDOW_TO_POOL["12-hour"] == "1min"
    assert WINDOW_TO_POOL["5-min"] == "10sec"
