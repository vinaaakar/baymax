#!/usr/bin/env bash
# One-time setup on the Mac (MacBook Air M4). Installs nothing silently: every step prints what it does.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${BAYMAX_MODEL:-llama3.2:3b}"
WHISPER_MODEL="${WHISPER_MODEL:-base.en}"

echo "== BAYMAX setup in $ROOT =="
command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh"; exit 1; }

echo "-- Python backend (venv in backend/.venv)"
# macOS ships Python 3.9 (Xcode tools); the backend needs 3.11+. Prefer Homebrew's interpreter explicitly.
PYTHON=""
for cand in python3.13 python3.12 python3.11 /opt/homebrew/bin/python3 python3; do
  if command -v "$cand" >/dev/null && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then PYTHON="$cand"; break; fi
done
if [ -z "$PYTHON" ]; then brew install python@3.12; PYTHON="$(brew --prefix)/bin/python3.12"; fi
echo "   using $PYTHON ($("$PYTHON" --version))"
"$PYTHON" -m venv "$ROOT/backend/.venv"
"$ROOT/backend/.venv/bin/pip" install -q --upgrade pip
"$ROOT/backend/.venv/bin/pip" install -q -e "$ROOT/backend[dev,macos]"

echo "-- Desktop app dependencies"
command -v npm >/dev/null || brew install node
(cd "$ROOT/desktop" && npm install --no-audit --no-fund)

echo "-- Local language model (Ollama, $MODEL)"
command -v ollama >/dev/null || brew install ollama
pgrep -x ollama >/dev/null || (brew services start ollama || (ollama serve >/dev/null 2>&1 &))
sleep 2
ollama pull "$MODEL"

echo "-- Local speech recognition (whisper.cpp, $WHISPER_MODEL)"
command -v whisper-cli >/dev/null || brew install whisper-cpp
mkdir -p "$HOME/.baymax/models"
MODEL_FILE="$HOME/.baymax/models/ggml-$WHISPER_MODEL.bin"
if [ ! -f "$MODEL_FILE" ]; then
  # Homebrew's whisper-cpp does not include a speech model. Official source: the whisper.cpp model repository.
  curl -L --fail -o "$MODEL_FILE.part" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$WHISPER_MODEL.bin"
  mv "$MODEL_FILE.part" "$MODEL_FILE"
fi
# Validate the file (ggml header, plausible size) before BAYMAX ever loads it.
"$ROOT/backend/.venv/bin/python" -c "import sys; from pathlib import Path; from baymax.hostos.tools import check_whisper_model as c; \
p = c(Path(sys.argv[1])); print('   model OK' if p is None else '   model INVALID: ' + p); sys.exit(p is not None)" "$MODEL_FILE" \
  || { echo "   removing the invalid download; re-run this script"; rm -f "$MODEL_FILE"; exit 1; }

echo "-- iOS tooling (optional)"
command -v xcodegen >/dev/null || brew install xcodegen || true

echo
"$ROOT/backend/.venv/bin/python" -m baymax doctor
echo
echo "Done. Start the app with:  $ROOT/scripts/start_mac.sh"
