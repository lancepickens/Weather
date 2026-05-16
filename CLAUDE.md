# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Two coupled tools for astronomical weather forecasting at a specific observing
site (Henry W. Coe State Park, by default):

1. **`cloud_height_predictor.py`** (repo root) — a self-contained Python script
   that turns DWD ICON forecast data from Open-Meteo into a per-hour cloud
   ceiling forecast restricted to astronomical night, in the site's local
   timezone.
2. **`webcam_analyzer/`** — a pip-installable subproject that produces
   *ground truth* from AlertCalifornia webcam timelapses by counting catalog
   stars actually visible in each frame versus those predicted to be above
   the horizon.

The two are deliberately co-located: the default `webcam_analyzer` target
(camera `Axis-StarrCanyon1`, 37.18 / -121.55 / 795 m) is within 10 m
elevation of the default predictor site `henry-coe` in `sites.json`. The
webcam stream is intended as ground truth to calibrate and validate the
forecast.

## Common commands

```sh
# Forecast (writes to stdout)
./hc_cloud_forecast.sh                                  # Henry Coe convenience wrapper
python3 cloud_height_predictor.py --site henry-coe      # any site in sites.json
python3 cloud_height_predictor.py --list-sites
python3 cloud_height_predictor.py --site henry-coe --nights 3

# Forecast tests (standalone runner — no pytest needed)
python3 tests/test_cloud_height_predictor.py

# Webcam analyzer — end-to-end (fetch → calibrate → analyze)
./webcam_analyzer/run.sh                                # one-shot pipeline
./webcam_analyzer/run.sh --skip-fetch                   # re-analyze cached frames
./webcam_analyzer/run.sh --recalibrate                  # force new plate-solve
./webcam_analyzer/run.sh --help                         # all flags

# Webcam analyzer — manual steps (when iterating on a single stage)
cd webcam_analyzer && .venv/bin/webcam-fetch --window 12-hour
.venv/bin/webcam-calibrate <frame.jpg> --solve
.venv/bin/webcam-analyze --reference <frame.jpg> \
    --min-alt 0 --max-vmag 4.0 --snr-threshold 3.0 \
    --output output/hourly.csv

# Webcam analyzer tests (pytest-style; pytest is not in the default deps)
cd webcam_analyzer && .venv/bin/pip install pytest && .venv/bin/pytest tests/
cd webcam_analyzer && .venv/bin/pytest tests/test_calibrate.py::test_sky_mask_excludes_below_horizon
```

`run.sh` handles `.venv` creation and `pip install -e .` idempotently — first
run sets everything up, subsequent runs reuse it.

## Architecture notes that aren't obvious from reading one file

### Cloud height predictor: ring-max aggregation

Open-Meteo snaps each requested coordinate to a single ICON grid cell with no
interpolation. At coastal/inland boundaries this can hit a "dry island" cell
while every neighbour within a few km is inside the marine layer.

`cloud_height_predictor.py` mitigates this by fetching a 3×3 neighbour ring
(`NEIGHBOUR_OFFSET_DEG = 0.15`, ≈17 km — large enough to guarantee distinct
ICON cells in every direction) and taking the **element-wise max** of RH,
dewpoint, cloud cover, and the layer splits. Temperature, `is_day`, and the
10 m wind triple (`wind_speed_10m`, `wind_gusts_10m`, `wind_direction_10m`)
come from the *center* cell. Wind specifically is **not** ring-maxed: gradients
across terrain are real physics rather than the grid-snap artifact ring-max
exists to defeat, so taking the max across neighbours would systematically
bias the site reading high. Because each ring-max variable's max can come
from a different cell, the displayed `Td°C` may exceed the center `T°C` —
that's intentional; the row represents three separate worst-case readings
rather than one self-consistent point forecast.

### Cloud height predictor: column layout

The output table emits, per hour: `T°C`, `Td°C`, `RH%`, `Cov%`, `Low%`,
`Mid%`, `High%`, `Base m AGL`, `Base m MSL`, `Wnd m/s`, `Gst m/s`, `Dir`
(16-point compass), and the Note column. Wind columns were added on top
of the existing layout; existing tests assert via substring matches so
adding more columns is non-breaking, but renaming an existing column
header would break the `assert "RH%" in out` style tests.

### Cloud height predictor: timezone resolution

`_site_timezone` prefers `zoneinfo.ZoneInfo(payload["timezone"])` (e.g.
`America/Los_Angeles`) over the offset-based fallback, because Open-Meteo's
`timezone_abbreviation` field today returns `GMT-7` rather than `PDT`. The
ZoneInfo path is DST-aware, so a multi-night forecast crossing a DST
boundary will label hours correctly. The fixed-offset fallback (using
`utc_offset_seconds`) is kept for payloads that omit the IANA name or
when `tzdata` doesn't recognize it.

### The four flag predicates and how they interact

| Flag | Source | Meaning |
|------|--------|---------|
| `extinction` | ring-max LCL ≤ site elevation | Observer is inside or under cloud somewhere in the ring |
| `below ridge` | ring-max LCL ≤ `ridge_height_m` | Deck obscures the local horizon |
| `marine-layer risk` | ring-max RH > 90 % | Saturated air anywhere in the ring |
| `marine layer below` | center RH < 70 % **and** center cover < 30 % **and** ring RH > 90 % | Site sits at an inversion top: ring is socked in, but the configured point is dry/clear |

When `marine layer below` fires alongside the conservative flags, the
conservative flags are describing cells *below* the site — conditions
overhead are likely good. The center-cell view exists specifically to
generate this signal; without it, ring-max aggregation produces a false
overcast at sites perched on the marine-layer ceiling (Henry Coe sits at
793 m, right at the typical Bay Area inversion top). The thresholds were
calibrated against the 2026-05-14/15 webcam ground truth at the Coe site.

### Webcam analyzer: why three stages exist separately

The pipeline is `fetch → calibrate (once) → analyze (per-frame)` and each
stage exists as its own CLI because the slow parts shouldn't repeat:

- **Plate solving is slow.** The cam is fixed-pointing, so a single
  `calibration.json` (WCS + horizon mask) is reused across every night until
  the cam is repositioned.
- **Fetch and analyze are decoupled.** A 12-hour 1-min cache is ~720 frames
  / ~210 MB; you usually want to re-tune detection thresholds without
  re-downloading.

### Webcam analyzer: crop-and-solve for wide-FOV calibration

The cam has a ~65° FOV but `solve-field`'s default `index-4115` series only
covers scale ranges up to ~5-8°. `calibrate.run_solve_field` crops a 200×200
center patch (default `DEFAULT_CROP = (860, 100, 200, 200)`, scale 5-8°),
solves on the crop, then translates the WCS back to full-frame coordinates
by shifting `CRPIX1`/`CRPIX2` via `_wcs_with_full_frame_offset`. If you
change the crop window or the cam's pointing, both the crop and the scale
range may need to be re-tuned.

`_parse_wcs_fits` deliberately reads only listed `float_keys` / `str_keys`
and skips `NAXIS`/`NAXIS1`/`NAXIS2` — including them as floats triggers
`ValueError: Unknown format code 'd'` deep inside `astropy.wcs.WCS`.

### Webcam analyzer: tuned defaults for this cam

The Axis-StarrCanyon1 cam looks roughly horizontally — measured altitude
range is about **-19° to +18°**. The library defaults (`min_alt=10`) filter
every catalog star out and produce `cloud_score == 1.0` for every frame.
The values to use against this cam are `--min-alt 0 --max-vmag 4.0
--snr-threshold 3.0`, which `run.sh` and the README both use. If you point
this at a different camera, expect to re-tune.

### Webcam analyzer: drift correction is foreground-only

`detect.estimate_drift` cross-correlates only the *below-horizon* region of
the frame against the reference. Sky pixels move (stars rotate) and would
bias the correlation toward sidereal motion rather than mechanical PTZ
drift. The cross-correlation is a manual nested loop over a small search
window (±20 px); this is intentional — full `correlate2d` on a 200×~1500
band is much more expensive than the windowed dot product.

## Configuration

- `sites.json` (predictor): add observing sites here. `ridge_height_m` is
  optional; omit for sites without a meaningful local ridge.
- `webcam_analyzer/calibration.json` (analyzer): regenerated by
  `webcam-calibrate`. Treat as a build artifact; don't hand-edit.
- The webcam analyzer subproject has its own `.venv/`, dependencies, and
  test suite — don't mix it with the root project.
