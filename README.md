# Weather

Utilities for forecasting astronomical weather for amateur astronomers.

## Cloud height predictor

`cloud_height_predictor.py` estimates cloud base height (the lifted
condensation level) at any observing site listed in `sites.json`, using 2 m
temperature and dewpoint from the Open-Meteo forecast API and Espy's
equation (`H ≈ 125 × (T − Td)` meters). Output is restricted to
astronomical night hours (sunset to sunrise at the site's local
coordinates) and shown in the site's local timezone, grouped per night.
Each hour includes cloud cover by layer plus base height in meters AGL
and MSL, and the Note column flags `extinction` when the modeled cloud
base sits at or below the site (observer in cloud) and `below ridge`
when the deck is below the site's local ridge (if configured).

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
