#!/usr/bin/env bash
# Build and test the iOS companion on a Mac with Xcode 15+ installed.
set -euo pipefail
cd "$(dirname "$0")"
echo "== BaymaxKit unit tests (macOS host) =="
(cd BaymaxKit && swift test)
command -v xcodegen >/dev/null || { echo "Install XcodeGen first: brew install xcodegen"; exit 1; }
xcodegen generate
SIM="${SIMULATOR:-iPhone 15}"
echo "== Building for the iOS Simulator ($SIM) =="
xcodebuild -project BaymaxCompanion.xcodeproj -scheme BaymaxCompanion -destination "platform=iOS Simulator,name=$SIM" -derivedDataPath build build
echo "Built. To run: open BaymaxCompanion.xcodeproj, pick a simulator or your iPhone, press Run."
