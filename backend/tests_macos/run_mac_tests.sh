#!/bin/zsh
# Runs the real-Mac integration tests and saves everything they print to ~/Desktop/baymax-mac-tests.txt.
# Usage (from anywhere):  zsh ~/Developer/BAYMAX/backend/tests_macos/run_mac_tests.sh
cd "${0:A:h}/.." || exit 1
BAYMAX_MACOS_INTEGRATION=1 .venv/bin/python -m pytest -v -s tests_macos 2>&1 | tee ~/Desktop/baymax-mac-tests.txt
echo "\nSaved to ~/Desktop/baymax-mac-tests.txt"
