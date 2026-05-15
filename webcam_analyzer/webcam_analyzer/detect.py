"""Per-frame analysis: align to reference, project catalog, score detection.

For each frame we:
  1. Cross-correlate the foreground (below the horizon) against the
     reference frame to recover any small PTZ drift (translation only).
  2. Use the calibration WCS to project each above-horizon catalog star
     to expected pixel coords, applying the recovered drift.
  3. Inspect a small window around each predicted pixel for a point
     source above local background noise.
  4. Return per-frame: expected count, detected count, brightness stats.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import median_filter
from scipy.signal import correlate2d

from .calibrate import CalibrationData
from .catalog import StarPrediction, predict_visible


@dataclass
class FrameScore:
    timestamp_unix: float
    expected_stars: int
    detected_stars: int
    sky_mean: float
    sky_stddev: float
    drift_x: int
    drift_y: int

    @property
    def detection_ratio(self) -> float:
        if self.expected_stars == 0:
            return float("nan")
        return self.detected_stars / self.expected_stars

    @property
    def cloud_score(self) -> float:
        """1.0 = total cloud cover (no stars seen); 0.0 = clear."""
        if self.expected_stars == 0:
            return float("nan")
        return 1.0 - (self.detected_stars / self.expected_stars)


def _load_gray(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"), dtype=np.float32)


def estimate_drift(frame: np.ndarray, reference: np.ndarray, horizon_y: np.ndarray, search_px: int = 20) -> tuple[int, int]:
    """Foreground cross-correlate frame vs reference → (dy, dx) integer shift.

    Only the foreground region (below the horizon) is used, since sky
    pixels move (stars) and would bias the correlation. Cropped to the
    central 60% of width to skip edge artifacts and shrink the workload.
    """
    h, w = frame.shape
    median_horizon = int(np.median(horizon_y))
    y0 = min(h - 50, median_horizon + 30)
    y1 = min(h, y0 + 200)  # 200-row band of foreground
    x0, x1 = int(w * 0.20), int(w * 0.80)
    f = frame[y0:y1, x0:x1]
    r = reference[y0:y1, x0:x1]
    if f.size == 0 or r.size == 0:
        return (0, 0)

    # Mean-subtract to make correlate2d robust to lighting changes.
    f = f - f.mean()
    r = r - r.mean()

    # Cross-correlate, but restrict by slicing — full 200x... correlation is
    # O(N^2) and expensive. Use a small search window instead.
    corr = np.zeros((2 * search_px + 1, 2 * search_px + 1), dtype=np.float64)
    for dy in range(-search_px, search_px + 1):
        for dx in range(-search_px, search_px + 1):
            # Slice the reference shifted by (dy, dx) and dot-product with the frame.
            ry0 = max(0, -dy)
            ry1 = min(r.shape[0], r.shape[0] - dy)
            rx0 = max(0, -dx)
            rx1 = min(r.shape[1], r.shape[1] - dx)
            fy0 = max(0, dy)
            fy1 = fy0 + (ry1 - ry0)
            fx0 = max(0, dx)
            fx1 = fx0 + (rx1 - rx0)
            if ry1 <= ry0 or rx1 <= rx0:
                continue
            corr[dy + search_px, dx + search_px] = np.sum(
                f[fy0:fy1, fx0:fx1] * r[ry0:ry1, rx0:rx1]
            )

    best = np.unravel_index(int(np.argmax(corr)), corr.shape)
    dy_best = int(best[0] - search_px)
    dx_best = int(best[1] - search_px)
    return (dy_best, dx_best)


def project_stars_to_pixels(
    stars: list[StarPrediction], calibration: CalibrationData
) -> list[tuple[StarPrediction, float, float]]:
    """Use the WCS in calibration.wcs_header to map (RA, Dec) → (x, y)."""
    wcs_dict = calibration.wcs_header
    if not wcs_dict:
        return []

    from astropy.io.fits import Header
    from astropy.wcs import WCS

    hdr = Header()
    for k, v in wcs_dict.items():
        if k in ("pixel_scale_deg", "pixel_scale_arcsec"):
            continue
        hdr[k] = v
    try:
        wcs = WCS(hdr)
    except Exception:
        return []

    ras = np.array([s.ra_deg for s in stars])
    decs = np.array([s.dec_deg for s in stars])
    xs, ys = wcs.world_to_pixel_values(ras, decs)

    out: list[tuple[StarPrediction, float, float]] = []
    for s, x, y in zip(stars, xs, ys):
        if np.isfinite(x) and np.isfinite(y):
            out.append((s, float(x), float(y)))
    return out


def detect_at_position(
    image: np.ndarray, x: float, y: float, window: int = 7, snr_threshold: float = 4.0
) -> bool:
    """Is there a point source within `window` pixels of (x, y)?

    Local-max check: take a window centered at (x, y), measure
    (peak − median) / mad. Above `snr_threshold` counts as a detection.
    """
    h, w = image.shape
    xi, yi = int(round(x)), int(round(y))
    half = window // 2
    y0, y1 = max(0, yi - half), min(h, yi + half + 1)
    x0, x1 = max(0, xi - half), min(w, xi + half + 1)
    if y1 - y0 < 3 or x1 - x0 < 3:
        return False
    patch = image[y0:y1, x0:x1]
    peak = float(patch.max())
    background = float(np.median(patch))
    mad = float(np.median(np.abs(patch - background))) or 1.0
    return (peak - background) > snr_threshold * mad


def score_frame(
    frame_path: Path,
    timestamp_unix: float,
    reference_image: np.ndarray,
    calibration: CalibrationData,
    min_alt_deg: float = 10.0,
    max_vmag: float = 3.0,
    snr_threshold: float = 4.0,
) -> FrameScore:
    """Score a single frame's cloud cover via predicted-vs-detected stars."""
    img = _load_gray(frame_path)
    horizon_y = np.array(calibration.horizon_y, dtype=np.int32)
    dy, dx = estimate_drift(img, reference_image, horizon_y)

    # Background-subtracted sky for star detection: subtract a heavy
    # median (~21 px) to remove static gradients (light pollution, etc.).
    sky_bg = median_filter(img, size=21)
    sky_residual = img - sky_bg

    # Compute sky-region brightness statistics (above-horizon pixels).
    sky_mask_rows = np.arange(img.shape[0])[:, None] < horizon_y[None, :]
    sky_pixels = img[sky_mask_rows]
    sky_mean = float(sky_pixels.mean()) if sky_pixels.size else float("nan")
    sky_stddev = float(sky_pixels.std()) if sky_pixels.size else float("nan")

    # Predicted stars at this timestamp.
    stars = predict_visible(
        timestamp_unix=timestamp_unix,
        lat_deg=calibration.site_lat_deg,
        lon_deg=calibration.site_lon_deg,
        elevation_m=calibration.site_elevation_m,
        min_alt_deg=min_alt_deg,
        max_vmag=max_vmag,
    )

    projected = project_stars_to_pixels(stars, calibration)

    expected = 0
    detected = 0
    for star, x_pred, y_pred in projected:
        x = x_pred + dx
        y = y_pred + dy
        if not (0 <= x < img.shape[1] and 0 <= y < img.shape[0]):
            continue
        col = int(np.clip(x, 0, img.shape[1] - 1))
        if y >= horizon_y[col]:
            continue  # below horizon at this column
        expected += 1
        if detect_at_position(sky_residual, x, y, snr_threshold=snr_threshold):
            detected += 1

    return FrameScore(
        timestamp_unix=timestamp_unix,
        expected_stars=expected,
        detected_stars=detected,
        sky_mean=sky_mean,
        sky_stddev=sky_stddev,
        drift_x=dx,
        drift_y=dy,
    )
