"""Tests for point-source detection."""
import numpy as np

from webcam_analyzer.detect import detect_at_position, estimate_drift


def _frame_with_star(x: int, y: int, brightness: float = 200.0, noise_sigma: float = 5.0) -> np.ndarray:
    rng = np.random.default_rng(0)
    img = rng.normal(50, noise_sigma, (200, 300)).astype(np.float32)
    img[y - 1 : y + 2, x - 1 : x + 2] += brightness
    return img


def test_detect_at_position_hits_actual_star():
    img = _frame_with_star(150, 80)
    assert detect_at_position(img, x=150, y=80, snr_threshold=4.0) is True


def test_detect_at_position_misses_empty_sky():
    rng = np.random.default_rng(1)
    img = rng.normal(50, 5, (200, 300)).astype(np.float32)
    assert detect_at_position(img, x=150, y=80, snr_threshold=4.0) is False


def test_detect_at_position_tolerates_small_misalignment():
    img = _frame_with_star(150, 80)
    # The predicted position is one pixel off — a 7-px window should still catch it.
    assert detect_at_position(img, x=151, y=81, window=7, snr_threshold=4.0) is True


def test_estimate_drift_recovers_known_shift():
    rng = np.random.default_rng(2)
    h, w = 400, 800
    ref = rng.normal(50, 30, (h, w)).astype(np.float32)
    # Stamp a unique foreground pattern below row 250.
    ref[260:340, 200:600] += rng.normal(0, 80, (80, 400))

    dy, dx = 4, -3
    shifted = np.zeros_like(ref)
    shifted[max(0, dy) : h + min(0, dy), max(0, dx) : w + min(0, dx)] = ref[
        max(0, -dy) : h + min(0, -dy), max(0, -dx) : w + min(0, -dx)
    ]
    horizon_y = np.full(w, 250, dtype=np.int32)
    rec_dy, rec_dx = estimate_drift(shifted, ref, horizon_y=horizon_y, search_px=10)
    assert (rec_dy, rec_dx) == (dy, dx)
