#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 cloud_height_predictor.py --site henry-coe "$@"
