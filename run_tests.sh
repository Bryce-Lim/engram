#!/usr/bin/env bash
# Run the Engram test suite. Keeps the simulated server fast for CI.
set -euo pipefail
cd "$(dirname "$0")"
export ENGRAM_DEMO_LATENCY="${ENGRAM_DEMO_LATENCY:-0.15}"
python3 -m unittest discover -s tests "$@"
