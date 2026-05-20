#!/usr/bin/env bash
# Henry Coe convenience wrapper for cloud_height_predictor.py.
# Lives in scripts/ — cd one level up to find the predictor at the repo root.
cd "$(dirname "$0")/.."
python3 cloud_height_predictor.py --site henry-coe "$@"
