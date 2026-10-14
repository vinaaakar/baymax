#!/usr/bin/env bash
# One-time setup on the Mac (MacBook Air M4). Installs nothing silently: every step prints what it does.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${BAYMAX_MODEL:-llama3.2:3b}"
WHISPER_MODEL="${WHISPER_MODEL:-base.en}"

echo "== BAYMAX setup in $ROOT =="
command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh"; exit 1; }

echo "-- Python backend (venv in backend/.venv)"
command -v python3 >/dev/null || brew install python@3.12
python3 -m venv "$ROOT/backend/.venv"
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
if [ ! -f "$HOME/.baymax/models/ggml-$WHISPER_MODEL.bin" ]; then
  curl -L --fail -o "$HOME/.baymax/models/ggml-$WHISPER_MODEL.bin" \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$WHISPER_MODEL.bin"
fi

echo "-- iOS tooling (optional)"
command -v xcodegen >/dev/null || brew install xcodegen || true

echo
"$ROOT/backend/.venv/bin/python" -m baymax doctor
echo
echo "Done. Start the app with:  cd desktop && npm start"
