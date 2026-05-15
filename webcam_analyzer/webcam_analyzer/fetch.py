"""Fetch AlertCalifornia camera manifests and frames.

Public-camera-data URLs reverse-engineered from cameras.alertcalifornia.org:
  manifest: /public-camera-data/{camera}/{pool}/{spec}    (JSON)
  frame:    /public-camera-data/{camera}/{pool}/{name}.jpg

Window specs (and the pool they live in):
  10sec pool:  5-min.json, 15-min.json, 30-min.json
  1min  pool:  1-hour.json, 3-hour.json, 6-hour.json, 12-hour.json
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE_URL = "https://cameras.alertcalifornia.org/public-camera-data"

WINDOW_TO_POOL = {
    "5-min": "10sec",
    "15-min": "10sec",
    "30-min": "10sec",
    "1-hour": "1min",
    "3-hour": "1min",
    "6-hour": "1min",
    "12-hour": "1min",
}


@dataclass(frozen=True)
class FrameRef:
    """A single frame from a camera's timelapse manifest."""

    camera_id: str
    pool: str
    filename: str
    timestamp: float  # unix epoch seconds, parsed from filename

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.camera_id}/{self.pool}/{self.filename}"


def _parse_timestamp(filename: str) -> float:
    # AlertCalifornia frame names look like "1778825413.000000000.jpg".
    base = filename.rsplit(".jpg", 1)[0]
    return float(base.split(".")[0])


def fetch_manifest(camera_id: str, window: str = "12-hour", timeout: int = 30) -> list[FrameRef]:
    """Download the rolling timelapse manifest and return ordered FrameRefs."""
    if window not in WINDOW_TO_POOL:
        raise ValueError(f"unknown window {window!r}; must be one of {sorted(WINDOW_TO_POOL)}")
    pool = WINDOW_TO_POOL[window]
    url = f"{BASE_URL}/{camera_id}/{pool}/{window}.json"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = json.load(resp)
    return [
        FrameRef(camera_id=camera_id, pool=pool, filename=name, timestamp=_parse_timestamp(name))
        for name in data.get("frames", [])
    ]


def fetch_camera_metadata(camera_id: str, timeout: int = 30) -> dict:
    """Pull all_cameras-v3.json and return the GeoJSON feature for one camera."""
    url = f"{BASE_URL}/all_cameras-v3.json"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = json.load(resp)
    for feat in data.get("features", []):
        if feat.get("properties", {}).get("id") == camera_id:
            return feat
    raise KeyError(f"camera {camera_id!r} not found in all_cameras-v3.json")


def download_frame(frame: FrameRef, cache_dir: Path, timeout: int = 30) -> Path:
    """Download a frame to cache_dir/<camera>/<pool>/<filename>; skip if cached."""
    target = cache_dir / frame.camera_id / frame.pool / frame.filename
    if target.exists() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(frame.url, timeout=timeout) as resp:
        tmp.write_bytes(resp.read())
    tmp.rename(target)
    return target


def download_window(
    camera_id: str,
    window: str = "12-hour",
    cache_dir: Path | str = ".frames-cache",
    progress: bool = False,
) -> list[tuple[FrameRef, Path]]:
    """Convenience: fetch manifest and download every frame; return [(ref, path)]."""
    cache_dir = Path(cache_dir)
    frames = fetch_manifest(camera_id, window=window)
    out: list[tuple[FrameRef, Path]] = []
    for i, f in enumerate(frames, 1):
        path = download_frame(f, cache_dir)
        out.append((f, path))
        if progress and (i % 50 == 0 or i == len(frames)):
            print(f"  fetched {i}/{len(frames)}")
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="webcam-fetch", description="Download AlertCalifornia timelapse frames.")
    parser.add_argument("--camera", default="Axis-StarrCanyon1", help="Camera id (default: %(default)s).")
    parser.add_argument(
        "--window",
        default="12-hour",
        choices=sorted(WINDOW_TO_POOL),
        help="Rolling window to fetch (default: %(default)s).",
    )
    parser.add_argument("--cache-dir", default=".frames-cache", help="Frame cache directory (default: %(default)s).")
    parser.add_argument("--manifest-only", action="store_true", help="Print manifest summary; don't download frames.")
    args = parser.parse_args(argv)

    frames = fetch_manifest(args.camera, window=args.window)
    if not frames:
        print("error: manifest is empty", flush=True)
        return 1
    first, last = frames[0], frames[-1]
    span = last.timestamp - first.timestamp
    print(
        f"{args.camera}: {len(frames)} frames, "
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(first.timestamp))} UTC → "
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(last.timestamp))} UTC "
        f"(span {span/3600:.2f} h)"
    )
    if args.manifest_only:
        return 0
    download_window(args.camera, window=args.window, cache_dir=args.cache_dir, progress=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
