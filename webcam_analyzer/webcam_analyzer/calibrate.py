"""Bootstrap a webcam_analyzer calibration: WCS + horizon mask + cam metadata.

Two paths to WCS:

1) Local solve-field (astrometry.net CLI). Pass --solve to invoke it on a
   given image and parse the resulting .wcs FITS header into JSON.

2) Manual paste. After uploading the same frame to nova.astrometry.net,
   download the wcs.fits file and pass it via --wcs-file. Same parser.

Horizon segmentation runs against a daylight frame: the ridge-to-sky edge
is the strongest vertical brightness gradient, smoothed per-column.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter1d, median_filter, sobel


@dataclass
class CalibrationData:
    """Persisted output of the calibration step."""

    camera_id: str
    image_width: int
    image_height: int
    # WCS (parsed FITS header subset, RA/Dec in degrees, pixel scale, etc).
    wcs_header: dict
    # Per-column y-coordinate of horizon (sky above, foreground below).
    horizon_y: list[int]
    # Observer location (camera lat/lon/elev), for alt/az computation.
    site_lat_deg: float
    site_lon_deg: float
    site_elevation_m: float


def segment_horizon(image_path: Path, smoothing_sigma: float = 3.0) -> np.ndarray:
    """Return a per-column array `horizon_y` where horizon_y[x] is the ridge row.

    Pixels with y < horizon_y[x] are sky; y >= horizon_y[x] is foreground.
    Works on daytime frames (sky bright, ridge dark) and night frames
    (sky dark with light pollution above, ridge darker) because both
    produce a strong horizontal brightness boundary.
    """
    img = np.asarray(Image.open(image_path).convert("L"), dtype=np.float32)
    h, w = img.shape

    # Soften noise before taking a derivative.
    smoothed = gaussian_filter1d(img, smoothing_sigma, axis=0)

    # Vertical Sobel highlights horizontal boundaries.
    grad = np.abs(sobel(smoothed, axis=0))

    # Constrain the search band: horizons live in the middle 2/3 of the
    # frame for a near-level cam. This rejects rooftop / sun lens flare.
    band_top = int(h * 0.20)
    band_bot = int(h * 0.85)
    grad[:band_top, :] = 0
    grad[band_bot:, :] = 0

    horizon_y = np.argmax(grad, axis=0).astype(np.int32)

    # Smooth across columns: real terrain is gentler than per-column noise.
    horizon_y = median_filter(horizon_y, size=21)
    return horizon_y


def sky_mask(width: int, height: int, horizon_y: np.ndarray) -> np.ndarray:
    """Boolean array of shape (H, W); True where the pixel is sky."""
    mask = np.zeros((height, width), dtype=bool)
    cols = np.arange(width)
    for x in cols:
        mask[: int(horizon_y[x]), x] = True
    return mask


def _parse_wcs_fits(wcs_path: Path) -> dict:
    """Read a FITS-format WCS file and extract the keys we need.

    Returns a JSON-friendly dict with CRVAL/CRPIX/CD-matrix plus
    convenience fields (RA/Dec center, pixel scale).
    """
    from astropy.io import fits
    from astropy.wcs import WCS

    with fits.open(wcs_path) as hdul:
        hdr = hdul[0].header
    wcs = WCS(hdr)

    keys = (
        "CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2",
        "CD1_1", "CD1_2", "CD2_1", "CD2_2",
        "CDELT1", "CDELT2", "CROTA1", "CROTA2",
        "NAXIS", "NAXIS1", "NAXIS2", "EQUINOX",
    )
    out = {k: float(hdr[k]) if k in hdr and isinstance(hdr[k], (int, float)) else hdr.get(k) for k in keys}
    out = {k: v for k, v in out.items() if v is not None}

    # astropy.wcs.utils.proj_plane_pixel_scales returns a pair of pixel scales
    # in the WCS units (degrees per pixel for celestial WCS).
    try:
        from astropy.wcs.utils import proj_plane_pixel_scales

        scales = proj_plane_pixel_scales(wcs)
        out["pixel_scale_deg"] = float(np.mean(np.abs(scales)))
        out["pixel_scale_arcsec"] = float(out["pixel_scale_deg"] * 3600.0)
    except Exception:
        pass
    return out


def run_solve_field(image_path: Path, scale_low: float | None = None, scale_high: float | None = None) -> Path:
    """Invoke the local astrometry.net `solve-field` and return path to .wcs.

    Caller is responsible for setting up the astrometry index files. The
    AlertCalifornia StarrCanyon camera has FOV ≈ 65°; pass --scale-low /
    --scale-high in degrees if you want to narrow the search.
    """
    image_path = Path(image_path)
    cmd = [
        "solve-field",
        "--no-plots",
        "--no-verify",
        "--overwrite",
        "--crpix-center",
        str(image_path),
    ]
    if scale_low is not None and scale_high is not None:
        cmd.extend(
            [
                "--scale-units", "degwidth",
                "--scale-low", str(scale_low),
                "--scale-high", str(scale_high),
            ]
        )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + "\n" + proc.stderr + "\n")
        raise RuntimeError(
            f"solve-field exited {proc.returncode}. Common cause: no index files installed for this FOV. "
            "See README troubleshooting section."
        )
    wcs_path = image_path.with_suffix(".wcs")
    if not wcs_path.exists():
        raise RuntimeError(f"solve-field finished but produced no {wcs_path}")
    return wcs_path


def calibrate(
    *,
    image_path: Path,
    camera_id: str,
    site_lat_deg: float,
    site_lon_deg: float,
    site_elevation_m: float,
    wcs_path: Path | None = None,
    solve: bool = False,
    scale_range: tuple[float, float] | None = None,
) -> CalibrationData:
    """Compute calibration data from a daylight (or clear-night) frame."""
    image_path = Path(image_path)
    img = Image.open(image_path)
    width, height = img.size

    horizon = segment_horizon(image_path).tolist()

    if wcs_path is not None:
        wcs = _parse_wcs_fits(Path(wcs_path))
    elif solve:
        low, high = scale_range or (50.0, 80.0)
        wcs_file = run_solve_field(image_path, scale_low=low, scale_high=high)
        wcs = _parse_wcs_fits(wcs_file)
    else:
        # Allow a calibration without WCS — useful for the horizon-only
        # smoke test. Detection accuracy degrades to brightness statistics.
        wcs = {}

    return CalibrationData(
        camera_id=camera_id,
        image_width=width,
        image_height=height,
        wcs_header=wcs,
        horizon_y=horizon,
        site_lat_deg=site_lat_deg,
        site_lon_deg=site_lon_deg,
        site_elevation_m=site_elevation_m,
    )


def save_calibration(data: CalibrationData, path: Path) -> None:
    path = Path(path)
    path.write_text(json.dumps(asdict(data), indent=2))


def load_calibration(path: Path) -> CalibrationData:
    raw = json.loads(Path(path).read_text())
    return CalibrationData(**raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="webcam-calibrate", description=__doc__)
    parser.add_argument("image", type=Path, help="Frame to calibrate against.")
    parser.add_argument("--camera", default="Axis-StarrCanyon1")
    parser.add_argument("--lat", type=float, default=37.18)
    parser.add_argument("--lon", type=float, default=-121.55)
    parser.add_argument("--elevation", type=float, default=795.0)
    parser.add_argument("--solve", action="store_true", help="Invoke local solve-field on the image.")
    parser.add_argument("--wcs-file", type=Path, help="Path to a pre-solved .wcs FITS header.")
    parser.add_argument("--scale-low", type=float, default=50.0, help="solve-field --scale-low (degrees of frame width).")
    parser.add_argument("--scale-high", type=float, default=80.0, help="solve-field --scale-high (degrees of frame width).")
    parser.add_argument("--output", type=Path, default=Path("calibration.json"))
    args = parser.parse_args(argv)

    if args.solve and args.wcs_file:
        parser.error("--solve and --wcs-file are mutually exclusive")

    data = calibrate(
        image_path=args.image,
        camera_id=args.camera,
        site_lat_deg=args.lat,
        site_lon_deg=args.lon,
        site_elevation_m=args.elevation,
        wcs_path=args.wcs_file,
        solve=args.solve,
        scale_range=(args.scale_low, args.scale_high) if args.solve else None,
    )
    save_calibration(data, args.output)
    print(
        f"calibration written to {args.output}: "
        f"{data.image_width}×{data.image_height}, "
        f"horizon row median={int(np.median(data.horizon_y))}, "
        f"WCS keys={len(data.wcs_header)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
