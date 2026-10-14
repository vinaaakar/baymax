#!/usr/bin/env bash
# Start BAYMAX on the Mac from anywhere: builds the desktop app and launches it. The app starts (or replaces an
# outdated) backend itself, so no separate backend command is needed.
#   ~/Developer/BAYMAX/scripts/start_mac.sh        (or, from the repository root:  npm --prefix desktop start)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/desktop"
[ -d node_modules ] || npm install --no-audit --no-fund
exec npm start
