#!/usr/bin/env bash
# Runs every automated test suite that the current machine supports.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
status=0
PY="$ROOT/backend/.venv/bin/python"; [ -x "$PY" ] || PY=python3

echo "== Backend (pytest) =="
(cd "$ROOT/backend" && PATH="$(dirname "$PY"):$PATH" "$PY" -m pytest -q) || status=1

echo "== Desktop (typecheck + vitest + build) =="
(cd "$ROOT/desktop" && npm run typecheck && npx vitest run && npm run build) || status=1

if command -v swift >/dev/null; then
  echo "== BaymaxKit (swift test) =="
  (cd "$ROOT/ios/BaymaxKit" && swift test) || status=1
else
  echo "== BaymaxKit skipped: Swift toolchain not installed =="
fi
if command -v xcodebuild >/dev/null && command -v xcodegen >/dev/null; then
  echo "== iOS app (simulator build) =="
  (cd "$ROOT/ios" && xcodegen generate && xcodebuild -project BaymaxCompanion.xcodeproj -scheme BaymaxCompanion \
     -destination "generic/platform=iOS Simulator" -derivedDataPath build CODE_SIGNING_ALLOWED=NO build -quiet) || status=1
else
  echo "== iOS app build skipped: Xcode/XcodeGen not installed =="
fi
exit $status
