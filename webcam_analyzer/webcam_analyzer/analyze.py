"""Nightly analyzer: pull manifest, score every frame, emit hourly CSV."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import statistics
from pathlib import Path

import numpy as np
from PIL import Image

from .calibrate import load_calibration
from .detect import FrameScore, score_frame
from .fetch import download_window, fetch_manifest


def _to_local_hour_key(ts: float, tz_offset_hours: float) -> str:
    local = dt.datetime.fromtimestamp(ts, tz=dt.timezone(dt.timedelta(hours=tz_offset_hours)))
    return local.strftime("%Y-%m-%d %H:00")


def aggregate_hourly(scores: list[FrameScore], tz_offset_hours: float = -7.0) -> list[dict]:
    """Bin frame scores by local hour and report median cloud score plus counts."""
    buckets: dict[str, list[FrameScore]] = {}
    for s in scores:
        if np.isnan(s.cloud_score):
            continue
        key = _to_local_hour_key(s.timestamp_unix, tz_offset_hours)
        buckets.setdefault(key, []).append(s)

    rows: list[dict] = []
    for key in sorted(buckets):
        bucket = buckets[key]
        cs = [b.cloud_score for b in bucket if not np.isnan(b.cloud_score)]
        rows.append(
            {
                "hour_local": key,
                "n_frames": len(bucket),
                "median_cloud_score": round(statistics.median(cs), 3) if cs else None,
                "p90_cloud_score": (round(float(np.percentile(cs, 90)), 3) if cs else None),
                "median_expected": int(statistics.median(b.expected_stars for b in bucket)),
                "median_detected": int(statistics.median(b.detected_stars for b in bucket)),
                "median_sky_mean": round(statistics.median(b.sky_mean for b in bucket), 1),
                "median_sky_stddev": round(statistics.median(b.sky_stddev for b in bucket), 2),
            }
        )
    return rows


def analyze_window(
    camera_id: str,
    calibration_path: Path,
    reference_image_path: Path,
    window: str = "12-hour",
    cache_dir: Path = Path(".frames-cache"),
    min_alt_deg: float = 10.0,
    max_vmag: float = 3.0,
    snr_threshold: float = 4.0,
    skip_n: int = 0,
    progress: bool = True,
) -> list[FrameScore]:
    """End-to-end: fetch manifest + frames, score every frame, return list."""
    calibration = load_calibration(calibration_path)
    reference = np.asarray(Image.open(reference_image_path).convert("L"), dtype=np.float32)

    frames_and_paths = download_window(camera_id, window=window, cache_dir=cache_dir, progress=progress)

    out: list[FrameScore] = []
    for i, (ref, path) in enumerate(frames_and_paths):
        if skip_n and (i % (skip_n + 1)) != 0:
            continue
        try:
            score = score_frame(
                frame_path=path,
                timestamp_unix=ref.timestamp,
                reference_image=reference,
                calibration=calibration,
                min_alt_deg=min_alt_deg,
                max_vmag=max_vmag,
                snr_threshold=snr_threshold,
            )
        except Exception as e:
            if progress:
                print(f"  skipped {path.name}: {e}")
            continue
        out.append(score)
        if progress and (len(out) % 50 == 0):
            print(f"  scored {len(out)} frames")
    return out


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        raise ValueError("no rows to write")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="webcam-analyze", description=__doc__)
    parser.add_argument("--camera", default="Axis-StarrCanyon1")
    parser.add_argument("--calibration", type=Path, default=Path("calibration.json"))
    parser.add_argument("--reference", type=Path, required=True, help="Calibration frame used for foreground alignment.")
    parser.add_argument("--window", default="12-hour", choices=("5-min", "15-min", "30-min", "1-hour", "3-hour", "6-hour", "12-hour"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".frames-cache"))
    parser.add_argument("--output", type=Path, default=Path("output/hourly.csv"))
    parser.add_argument("--tz-offset", type=float, default=-7.0, help="Local timezone offset from UTC in hours.")
    parser.add_argument("--min-alt", type=float, default=10.0)
    parser.add_argument("--max-vmag", type=float, default=3.0)
    parser.add_argument("--snr-threshold", type=float, default=4.0)
    parser.add_argument(
        "--skip",
        type=int,
        default=0,
        help="Score every (skip+1)th frame to speed up (default: 0 = every frame).",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    scores = analyze_window(
        camera_id=args.camera,
        calibration_path=args.calibration,
        reference_image_path=args.reference,
        window=args.window,
        cache_dir=args.cache_dir,
        min_alt_deg=args.min_alt,
        max_vmag=args.max_vmag,
        snr_threshold=args.snr_threshold,
        skip_n=args.skip,
        progress=not args.quiet,
    )
    rows = aggregate_hourly(scores, tz_offset_hours=args.tz_offset)
    write_csv(rows, args.output)
    print(f"wrote {len(rows)} hourly rows to {args.output} (from {len(scores)} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
