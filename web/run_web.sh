#!/usr/bin/env bash
# Launch the Precog web UI: builds the frontend if needed, then serves it
# together with the live comparison API from one Python process.
#
#   ./web/run_web.sh [PORT]
#
# Requires Node 16 (this host's glibc is too old for Node 18/20). The script
# auto-selects it via nvm if available.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8765}"

# Select a Node that runs on this host (16.x). Honor nvm if present.
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1091
  export NVM_DIR="$HOME/.nvm"
  source "$HOME/.nvm/nvm.sh" >/dev/null 2>&1 || true
  nvm use 16 >/dev/null 2>&1 || true
fi

if [ ! -f web/static/index.html ]; then
  echo "[run_web] building frontend (first run)…"
  ( cd web/frontend && npm install --no-audit --no-fund && npm run build )
fi

echo "[run_web] starting server on http://127.0.0.1:${PORT}"
exec python3 web/server.py --port "${PORT}"
