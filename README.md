# Weather

Utilities for forecasting astronomical weather for amateur astronomers.

## Cloud height predictor

`cloud_height_predictor.py` estimates cloud base height (the lifted
condensation level) at any observing site listed in `sites.json`, using 2 m
temperature and dewpoint from DWD's ICON model via Open-Meteo
(`models=icon_seamless`) and Espy's equation (`H ≈ 125 × (T − Td)` meters). Output is restricted to
astronomical night hours (sunset to sunrise at the site's local
coordinates) and shown in the site's local timezone, grouped per night.
Each hour includes 2 m relative humidity, cloud cover by layer, base
height in meters AGL and MSL, and 10 m wind speed / gust / direction
(m/s, 16-point compass). The Note column flags `extinction` when
the modeled cloud base sits at or below the site (observer in cloud),
`below ridge` when the deck is below the site's local ridge (if
configured), `marine-layer risk` when surface RH exceeds 90 %, and
`marine layer below` when the site's *center cell* is dry/clear while
the surrounding ring is saturated (see below).

### Elevation-aware: "marine layer below"

The conservative flags above (`extinction`, `below ridge`,
`marine-layer risk`) fire on the worst case across the 3×3 neighbour
ring. That's the right behavior for false-clear safety, but at sites
sitting at an inversion top (e.g. Henry Coe at 793 m, right at the
typical Bay Area marine-layer ceiling) it flags overcast every night
the layer fills the lower-elevation valleys, even when the site
itself is clear above it. The forecast also tracks each hour's
*center-cell* moisture and cloud cover (the model's point forecast
for the configured coordinate); when the center is dry/clear while
the ring is saturated, `marine layer below` fires. When that note
appears alongside the conservative flags, treat the conservative
flags as describing cells *below* the site — observing conditions
overhead are likely good, and a webcam check is worth the click.

This was calibrated against a 2026-05-14/15 night at Henry Coe where
the ring aggregation flagged full extinction every hour while the
Cal Fire webcam (and the user's eyes) confirmed clear skies overhead.

### Neighbour-cell ring sampling

Open-Meteo snaps each requested coordinate to a single ICON grid cell
without interpolation, which on a coastal/inland boundary can land in
an anomalous "dry island" cell while every neighbour within a few km
is inside the marine layer. To avoid hiding that case behind a lucky
single-cell snap, each forecast call fetches a 3×3 ring of cells
(`±0.15°` around the configured coordinate, ≈17 km — large enough to
guarantee distinct ICON cells in every direction) and aggregates them:

- `temperature_2m`, `time`, `is_day` — taken from the center cell so
  the site's point forecast remains the reference.
- `wind_speed_10m`, `wind_gusts_10m`, `wind_direction_10m` — taken from
  the center cell too. Wind gradients across terrain are real physics
  rather than the grid-snap artifact that motivates ring-max for
  moisture/cloud, so taking the max across neighbours would
  systematically bias the site reading high.
- `dew_point_2m`, `relative_humidity_2m`, `cloud_cover` (and the low /
  mid / high splits) — element-wise **max** across the ring,
  computed independently per variable. The reported LCL is therefore
  the lowest cloud base anywhere in the ring, and the `marine-layer
  risk` / `extinction` flags fire as soon as any neighbour saturates.

Because each variable's max can come from a different cell, the
displayed `Td°C` may exceed the center `T°C` — in those cases the row
represents three separate worst-case readings rather than a single
self-consistent point forecast.

The seed config ships with Henry W. Coe State Park; add more entries to
`sites.json` as needed.

```sh
python3 cloud_height_predictor.py --list-sites
python3 cloud_height_predictor.py --site henry-coe
python3 cloud_height_predictor.py --site henry-coe --nights 3
python3 cloud_height_predictor.py --help

# Convenience wrapper for the default site
./scripts/hc_cloud_forecast.sh
./scripts/hc_cloud_forecast.sh --nights 3
```

### `sites.json` schema

```json
{
  "sites": {
    "henry-coe": {
      "name": "Henry W. Coe State Park",
      "latitude": 37.1858,
      "longitude": -121.5483,
      "elevation_m": 793.0,
      "ridge_height_m": 1000.0
    }
  }
}
```

`ridge_height_m` is optional; omit it for sites without a meaningful
local ridge.

### Tests (no network required)

```sh
python3 tests/test_cloud_height_predictor.py
```

## Multi-night terminal view (`scripts/coe_cloud.py`)

A lighter, color-only terminal tool that shows the upcoming ~16 nights as
sunset→sunrise cloud-cover bars next to a per-hour lunar-illumination bar
(grayed `·` below horizon, otherwise illumination % colored on the same
red→indigo scale as cloud cover, since bright moon is also bad for
observing). It ranks the five best upcoming nights by average cloud cover.
The script loads Coe's coordinate from `sites.json`, fetches Open-Meteo's
`gfs_seamless` model (HRRR's ~3 km grid for the first 48 h, then GFS
Global out to 16 days — the closest grid cell to Coe of any 16-day
model), and computes the moon altitude/phase inline. No LCL math, no
ring sampling, no flags. Use it for an at-a-glance "when should I go?"
decision; use `cloud_height_predictor.py` for the detailed per-hour
forecast for any night.

```sh
python3 scripts/coe_cloud.py
python3 tests/test_coe_cloud.py   # moon math + cloud_bar ANSI invariant
```
