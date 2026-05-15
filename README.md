# Weather

Utilities for forecasting astronomical weather for amateur astronomers.

## Cloud height predictor

`cloud_height_predictor.py` estimates cloud base height (the lifted
condensation level) at any observing site listed in `sites.json`, using 2 m
temperature and dewpoint from DWD's ICON model via Open-Meteo
(`models=icon_seamless`) and Espy's equation (`H ≈ 125 × (T − Td)` meters). Output is restricted to
astronomical night hours (sunset to sunrise at the site's local
coordinates) and shown in the site's local timezone, grouped per night.
Each hour includes 2 m relative humidity, cloud cover by layer, and base
height in meters AGL and MSL. The Note column flags `extinction` when
the modeled cloud base sits at or below the site (observer in cloud),
`below ridge` when the deck is below the site's local ridge (if
configured), and `marine-layer risk` when surface RH exceeds 90 %.

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
