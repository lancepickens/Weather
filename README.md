# Weather

Utilities for forecasting astronomical weather for amateur astronomers.

## Cloud height predictor — Henry Coe State Park

`cloud_height_predictor.py` estimates cloud base height (the lifted
condensation level) at Henry W. Coe State Park, using 2 m temperature and
dewpoint from the Open-Meteo forecast API and Espy's equation
(`H ≈ 125 × (T − Td)` meters). Output includes hourly cloud cover by layer
plus base height in meters AGL and MSL, and flags hours when the deck would
sit below the park's ridges (~1000 m).

```sh
python3 cloud_height_predictor.py --hours 24
```

Tests (no network required):

```sh
python3 tests/test_cloud_height_predictor.py
```
