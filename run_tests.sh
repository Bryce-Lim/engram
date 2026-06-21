#!/usr/bin/env bash
# Run the Precog test suite. Keeps the simulated server fast for CI.
set -euo pipefail
cd "$(dirname "$0")"
export PRECOG_DEMO_LATENCY="${PRECOG_DEMO_LATENCY:-0.15}"
python3 -m unittest discover -s tests "$@"
