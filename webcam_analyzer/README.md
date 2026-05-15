# webcam_analyzer

Cloud-cover ground truth from AlertCalifornia webcam frames, derived by
counting catalog stars actually visible on each frame versus the stars
that should be above the horizon at that timestamp.

The default target camera is **Axis-StarrCanyon1** — physically at
Henry W. Coe State Park (37.18, –121.55, 795 m), the same elevation and
within 10 m of the observing site that `cloud_height_predictor` targets.
Other AlertCalifornia cameras work with `--camera <id>`.

## Pipeline

1. `webcam-fetch` pulls a rolling timelapse manifest (12-hour by
   default) and downloads every frame into a local cache (~720 frames @
   1/min, ~210 MB).
2. `webcam-calibrate` runs once against a *daylight* reference frame:
   automatic horizon segmentation (per-column ridge row) plus a WCS
   solution (either via local `solve-field` or a manually-pasted
   `wcs.fits` from nova.astrometry.net).
3. `webcam-analyze` scores every cached frame: foreground
   cross-correlation absorbs PTZ drift, the WCS projects above-horizon
   bright stars to pixel coords for that frame's timestamp, and a
   per-position SNR check tallies detected vs expected. The hourly
   median of `1 − detected/expected` is the cloud score.

## Install

```sh
cd webcam_analyzer
python3 -m venv .venv
.venv/bin/pip install -e .
```

Deps: `astropy`, `photutils`, `numpy`, `scipy`, `Pillow`.

## Usage

```sh
# 1. Pull the 12-hour manifest + frames.
.venv/bin/webcam-fetch --camera Axis-StarrCanyon1 --window 12-hour

# 2a. Calibrate with a local astrometry.net install.
.venv/bin/webcam-calibrate \
  .frames-cache/Axis-StarrCanyon1/1min/<daylight-frame>.jpg \
  --solve --scale-low 50 --scale-high 80

# 2b. OR: upload the frame to nova.astrometry.net, download wcs.fits,
#     and point the calibrator at it.
.venv/bin/webcam-calibrate \
  .frames-cache/Axis-StarrCanyon1/1min/<daylight-frame>.jpg \
  --wcs-file path/to/wcs.fits

# 3. Score the cached window and write hourly CSV.
.venv/bin/webcam-analyze \
  --reference .frames-cache/Axis-StarrCanyon1/1min/<daylight-frame>.jpg \
  --output output/hourly.csv
```

The output CSV columns:

| column | meaning |
| --- | --- |
| `hour_local` | Local clock hour bucket (default tz offset −7). |
| `n_frames` | Frames scored in this hour. |
| `median_cloud_score` | `1 − detected/expected`, hourly median. 0 = clear, 1 = overcast. |
| `p90_cloud_score` | Same, but 90th percentile (catches intermittent cloud bursts). |
| `median_expected` | How many bright stars *should* have been visible above horizon. |
| `median_detected` | How many were actually found at predicted positions. |
| `median_sky_mean` | Sky-region mean brightness (light pollution / cloud reflection). |
| `median_sky_stddev` | Sky-region brightness stddev (high = stars, low = featureless cloud). |

## Tests

```sh
.venv/bin/python -m pytest tests/ -v
```

All tests run without network. Horizon segmentation tests use
procedurally-generated frames; alt/az tests check that astropy reports
Polaris at lat 37.18° at the expected altitude (~37°).

## Troubleshooting

* **`solve-field` fails with "no index files"** — install the index
  series matching the cam's FOV. For Axis-StarrCanyon1 (FOV ≈ 65°), use
  series 4117–4119. Or skip the local solve and use
  nova.astrometry.net's free web solver, then pass the resulting
  `wcs.fits` via `--wcs-file`.
* **Horizon detection looks noisy** — make sure the calibration frame is
  a *daylight* frame; night frames have weaker sky/ridge contrast. The
  algorithm constrains the search to rows 20–85 % of frame height, so
  rooftop / lens-flare artifacts inside that band still throw it off.
* **All frames score `expected=0`** — calibration has no WCS yet
  (`wcs_header` is empty). Either run with `--solve` or paste a
  `wcs.fits`.
* **`is_currently_patrolling=1` in camera metadata** — the cam is
  panning. Drift correction has a search radius of ±20 px; if the
  panned orientation differs by more, frames will mismatch. Filter
  manifest by checking az/tilt before downloading.

## Camera URL pattern (reverse-engineered)

```
https://cameras.alertcalifornia.org/public-camera-data/<camera_id>/<pool>/<window>.json
https://cameras.alertcalifornia.org/public-camera-data/<camera_id>/<pool>/<filename>.jpg
https://cameras.alertcalifornia.org/public-camera-data/all_cameras-v3.json
```

Pools: `10sec` (for 5/15/30-min windows) and `1min` (for 1/3/6/12-hour windows).
