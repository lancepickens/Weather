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

# Default crop for the wide-FOV (~65°) AlertCalifornia PTZ cams: a small
# upper-center sky patch that fits index series ≤ 4116 (which most home
# astrometry installs include). Override per-camera if needed.
DEFAULT_CROP: tuple[int, int, int, int] = (860, 100, 200, 200)
DEFAULT_CROP_SCALE_RANGE: tuple[float, float] = (5.0, 8.0)


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

    # Constrain the search band: for a near-level cam, the horizon sits
    # somewhere in rows 20–85 % of the frame. This rejects rooftop / sun
    # lens flare above and dashboard / lower-bezel artifacts below.
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

    float_keys = (
        "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2",
        "CD1_1", "CD1_2", "CD2_1", "CD2_2",
        "CDELT1", "CDELT2", "CROTA1", "CROTA2",
        "EQUINOX",
    )
    str_keys = ("CTYPE1", "CTYPE2", "RADESYS", "RADECSYS")
    out: dict = {}
    for k in float_keys:
        if k in hdr:
            out[k] = float(hdr[k])
    for k in str_keys:
        if k in hdr:
            out[k] = str(hdr[k])
    # Note: deliberately skip NAXIS / NAXIS1 / NAXIS2. Our CalibrationData
    # already records image dimensions in image_width / image_height; if
    # we round-trip NAXIS=0 from a .wcs sidecar, astropy.wcs.WCS chokes
    # because the value gets coerced to float during JSON round-trip.

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


def run_solve_field(
    image_path: Path,
    scale_low: float | None = None,
    scale_high: float | None = None,
    crop: tuple[int, int, int, int] | None = None,
    cpulimit: int = 60,
) -> tuple[Path, tuple[int, int]]:
    """Invoke the local astrometry.net `solve-field` and return (wcs_path, crop_offset).

    If ``crop = (x, y, w, h)`` is given, solve a sub-region instead of the
    full frame — useful when the camera's native FOV (e.g. 65° for the
    AlertCalifornia StarrCanyon cam) exceeds what the installed index
    series cover. The returned ``crop_offset = (x, y)`` lets the caller
    translate the cropped WCS back to full-frame pixel coordinates.
    """
    image_path = Path(image_path)
    solve_input = image_path
    offset = (0, 0)

    if crop is not None:
        x, y, w, h = crop
        img = Image.open(image_path)
        patch = img.crop((x, y, x + w, y + h))
        solve_input = image_path.with_suffix(".crop.jpg")
        patch.save(solve_input, quality=95)
        offset = (x, y)

    cmd = [
        "solve-field",
        "--no-plots",
        "--no-verify",
        "--overwrite",
        "--crpix-center",
        "--cpulimit", str(cpulimit),
        str(solve_input),
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
    if proc.returncode != 0 or "did not solve" in proc.stdout.lower():
        sys.stderr.write(proc.stdout + "\n" + proc.stderr + "\n")
        raise RuntimeError(
            f"solve-field failed (exit {proc.returncode}). Common causes: "
            "no index files for this FOV (install wider series, or use --crop "
            "to solve a smaller patch); too few stars (use a clearer frame)."
        )
    wcs_path = solve_input.with_suffix(".wcs")
    if not wcs_path.exists():
        raise RuntimeError(f"solve-field finished but produced no {wcs_path}")
    return wcs_path, offset


def _wcs_with_full_frame_offset(wcs: dict, offset: tuple[int, int]) -> dict:
    """Shift CRPIX so a cropped WCS describes pixel positions in the full frame."""
    if offset == (0, 0):
        return wcs
    out = dict(wcs)
    if "CRPIX1" in out:
        out["CRPIX1"] = float(out["CRPIX1"]) + float(offset[0])
    if "CRPIX2" in out:
        out["CRPIX2"] = float(out["CRPIX2"]) + float(offset[1])
    return out


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
    solve_crop: tuple[int, int, int, int] | None = None,
) -> CalibrationData:
    """Compute calibration data from a daylight (or clear-night) frame."""
    image_path = Path(image_path)
    img = Image.open(image_path)
    width, height = img.size

    horizon = segment_horizon(image_path).tolist()

    if wcs_path is not None:
        wcs = _parse_wcs_fits(Path(wcs_path))
    elif solve:
        crop = solve_crop if solve_crop is not None else DEFAULT_CROP
        low, high = scale_range or DEFAULT_CROP_SCALE_RANGE
        wcs_file, offset = run_solve_field(
            image_path, scale_low=low, scale_high=high, crop=crop
        )
        wcs = _wcs_with_full_frame_offset(_parse_wcs_fits(wcs_file), offset)
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
    parser.add_argument(
        "--solve-crop",
        type=str,
        default=",".join(str(v) for v in DEFAULT_CROP),
        help="Crop region for --solve as 'x,y,w,h'. Wide-FOV cams need a smaller patch "
             "to fit standard astrometry indices. Pass 'none' to solve the full frame.",
    )
    parser.add_argument(
        "--scale-low",
        type=float,
        default=DEFAULT_CROP_SCALE_RANGE[0],
        help="solve-field --scale-low in degwidth (default tuned for --solve-crop).",
    )
    parser.add_argument(
        "--scale-high",
        type=float,
        default=DEFAULT_CROP_SCALE_RANGE[1],
        help="solve-field --scale-high in degwidth (default tuned for --solve-crop).",
    )
    parser.add_argument("--output", type=Path, default=Path("calibration.json"))
    args = parser.parse_args(argv)

    if args.solve and args.wcs_file:
        parser.error("--solve and --wcs-file are mutually exclusive")

    solve_crop: tuple[int, int, int, int] | None
    if args.solve_crop.strip().lower() == "none":
        solve_crop = None
    else:
        try:
            parts = tuple(int(v.strip()) for v in args.solve_crop.split(","))
            assert len(parts) == 4
            solve_crop = parts  # type: ignore[assignment]
        except (ValueError, AssertionError):
            parser.error(f"--solve-crop must be 'x,y,w,h' or 'none' (got {args.solve_crop!r})")

    data = calibrate(
        image_path=args.image,
        camera_id=args.camera,
        site_lat_deg=args.lat,
        site_lon_deg=args.lon,
        site_elevation_m=args.elevation,
        wcs_path=args.wcs_file,
        solve=args.solve,
        scale_range=(args.scale_low, args.scale_high) if args.solve else None,
        solve_crop=solve_crop,
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
