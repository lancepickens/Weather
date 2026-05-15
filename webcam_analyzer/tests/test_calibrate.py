"""Tests for horizon segmentation against synthetic frames."""
from pathlib import Path

import numpy as np
from PIL import Image

from webcam_analyzer.calibrate import segment_horizon, sky_mask


def _make_frame(tmp_path: Path, horizon_row: int = 540) -> Path:
    """Synthesize a 1920x1080 grayscale frame with sky above, ground below."""
    h, w = 1080, 1920
    img = np.zeros((h, w), dtype=np.uint8)
    img[:horizon_row, :] = 180  # bright sky
    img[horizon_row:, :] = 50   # dark ground
    rng = np.random.default_rng(0)
    img = np.clip(img + rng.normal(0, 5, img.shape), 0, 255).astype(np.uint8)
    p = tmp_path / "frame.png"
    Image.fromarray(img).save(p)
    return p


def test_segment_horizon_finds_flat_ridge(tmp_path):
    p = _make_frame(tmp_path, horizon_row=540)
    horizon = segment_horizon(p)
    # The detected row should be very close to the ground truth.
    assert horizon.shape == (1920,)
    median_y = int(np.median(horizon))
    assert abs(median_y - 540) <= 3, f"expected ≈540, got {median_y}"


def test_segment_horizon_handles_tilted_ridge(tmp_path):
    h, w = 1080, 1920
    img = np.zeros((h, w), dtype=np.uint8)
    # Slanted horizon: y = 500 + 100*(x/w)
    for x in range(w):
        y0 = int(500 + 100 * (x / w))
        img[:y0, x] = 180
        img[y0:, x] = 50
    p = tmp_path / "tilted.png"
    Image.fromarray(img).save(p)
    horizon = segment_horizon(p)
    # Leftmost columns ≈ 500, rightmost ≈ 600.
    assert abs(int(horizon[100]) - 505) <= 8
    assert abs(int(horizon[-100]) - 595) <= 8


def test_sky_mask_excludes_below_horizon():
    horizon = np.full(100, 50, dtype=np.int32)
    mask = sky_mask(width=100, height=100, horizon_y=horizon)
    assert mask[:50, :].all()
    assert not mask[50:, :].any()
