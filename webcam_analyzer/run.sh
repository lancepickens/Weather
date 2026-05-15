#!/usr/bin/env bash
set -euo pipefail

# webcam_analyzer/run.sh
#
# End-to-end pipeline: install (if needed) → fetch → calibrate → analyze.
# Picks the median frame from the largest pool as the calibration reference
# (for a 12-hour overnight window, that's roughly 2 AM local — darkest,
# most stars visible). Override with --reference for non-overnight runs.

cd "$(dirname "$0")"

WINDOW="12-hour"
MIN_ALT="0"
MAX_VMAG="4.0"
SNR="3.0"
RECALIBRATE=0
SKIP_FETCH=0
REFERENCE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --window)       WINDOW="$2"; shift 2 ;;
    --reference)    REFERENCE="$2"; shift 2 ;;
    --skip-fetch)   SKIP_FETCH=1; shift ;;
    --recalibrate)  RECALIBRATE=1; shift ;;
    --min-alt)      MIN_ALT="$2"; shift 2 ;;
    --max-vmag)     MAX_VMAG="$2"; shift 2 ;;
    --snr)          SNR="$2"; shift 2 ;;
    -h|--help)
      cat <<'USAGE'
Usage: run.sh [options]

  --window <w>        Time window for fetch/analyze
                      (1-hour | 6-hour | 12-hour | 24-hour | 7-day)
                      default: 12-hour
  --reference <path>  Calibration frame to use
                      default: median frame of the largest pool
  --skip-fetch        Don't re-download; use the existing .frames-cache/
  --recalibrate       Force plate-solving even if calibration.json exists
  --min-alt <deg>     Minimum star altitude (default 0; cam looks ~horizontally)
  --max-vmag <mag>    Faintest catalog star to consider (default 4.0)
  --snr <ratio>       Per-window detection SNR threshold (default 3.0)
USAGE
      exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1 ;;
  esac
done

# 1. venv + editable install
if [[ ! -d .venv ]]; then
  echo "[setup] creating .venv"
  python3 -m venv .venv
fi
if [[ ! -x .venv/bin/webcam-fetch ]]; then
  echo "[setup] pip install -e ."
  .venv/bin/pip install --quiet -e .
fi

# 2. fetch
if [[ "$SKIP_FETCH" -eq 0 ]]; then
  echo "[fetch] window=$WINDOW"
  .venv/bin/webcam-fetch --window "$WINDOW"
fi

# 3. pick reference: median frame in the largest pool
if [[ -z "$REFERENCE" ]]; then
  POOL_DIR=""
  POOL_COUNT=0
  shopt -s nullglob
  for d in .frames-cache/*/*/; do
    n=$(ls -1 "$d" | wc -l | tr -d ' ')
    if (( n > POOL_COUNT )); then
      POOL_COUNT="$n"
      POOL_DIR="$d"
    fi
  done
  shopt -u nullglob

  if [[ -z "$POOL_DIR" ]]; then
    echo "No frames found in .frames-cache/; aborting." >&2
    exit 1
  fi

  MID_NAME=$(ls -1 "$POOL_DIR" | sort | awk -v n="$POOL_COUNT" 'NR == int(n/2)+1 { print; exit }')
  REFERENCE="${POOL_DIR}${MID_NAME}"
fi

if [[ ! -f "$REFERENCE" ]]; then
  echo "Reference frame not found: $REFERENCE" >&2
  exit 1
fi
echo "[reference] $REFERENCE"

# 4. calibrate (skip if calibration.json exists, unless --recalibrate)
if [[ "$RECALIBRATE" -eq 1 ]] || [[ ! -f calibration.json ]]; then
  echo "[calibrate] running solve-field (needs astrometry.net on PATH)"
  .venv/bin/webcam-calibrate "$REFERENCE" --solve
else
  echo "[calibrate] reusing calibration.json (pass --recalibrate to redo)"
fi

# 5. analyze
mkdir -p output
echo "[analyze] scoring frames in .frames-cache/"
.venv/bin/webcam-analyze \
  --reference "$REFERENCE" \
  --window    "$WINDOW" \
  --output    output/hourly.csv \
  --min-alt   "$MIN_ALT" \
  --max-vmag  "$MAX_VMAG" \
  --snr-threshold "$SNR"

echo
echo "[done] hourly summary (output/hourly.csv):"
if command -v column >/dev/null 2>&1; then
  column -t -s, output/hourly.csv
else
  cat output/hourly.csv
fi
