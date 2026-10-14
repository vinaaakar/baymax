# Installation and setup

Target hardware: MacBook Air M4, 16 GB unified memory, macOS 14+ and an iPhone on iOS 17+.
Both devices must be on the same local network for the companion link.

## 1. Requirements

| Tool | Why | Install |
|---|---|---|
| Homebrew | installs everything below | https://brew.sh |
| Python 3.11+ | backend | `brew install python@3.12` |
| Node.js 20+ | desktop app build | `brew install node` |
| Ollama | local language model | `brew install ollama` |
| whisper.cpp | local speech recognition on the Mac | `brew install whisper-cpp` |
| Xcode 15+ | build the iPhone app | Mac App Store |
| XcodeGen | generates the Xcode project | `brew install xcodegen` |

`scripts/setup_mac.sh` performs steps 2–4 and prints what it does. Run it from the repository root.

## 2. Backend

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e "backend[dev,macos]"   # the macos extra adds PyObjC for pointer control/permission checks
backend/.venv/bin/python -m baymax doctor               # shows which features are available and why not
```

The backend stores its data in `~/.baymax/`:

- `config.toml`: settings
- `baymax.db`: tasks, memory, audit log, artifacts
- `local_token`: the desktop API token, mode 0600
- `secrets/` or the Keychain: device keys
- `tls/`: the self-signed LAN certificate

Generated files go to `~/Developer/BAYMAX/workspace/artifacts/`.

You can run the backend on its own with `backend/.venv/bin/python -m baymax serve`. Add `--lan` to enable the iPhone listener without pairing first.

## 3. Configure the local AI model

```bash
brew services start ollama          # or: ollama serve
ollama pull llama3.2:3b             # default model (~2 GB download, ~3 GB RAM in use)
```

To change the model, either use Workspace → Settings → Model, or edit `~/.baymax/config.toml`:

```toml
[llm]
model = "llama3.2:3b"        # any model installed in Ollama
num_ctx = 4096               # bounded context keeps memory predictable
max_output_tokens = 512
```

Recommended models for 16 GB (approximate resident memory, Q4 weights):

| Model | Memory | Use |
|---|---|---|
| `llama3.2:3b` | ~3 GB | default: conversation, intent, planning |
| `llama3.2:1b` | ~1.5 GB | if memory is tight; weaker at JSON/planning |
| `qwen2.5-coder:7b` | ~5.5 GB | better code edits; slower |
| `llama3.1:8b` | ~6 GB | higher quality chat; leaves less headroom |

Avoid running two 7B+ models at once on 16 GB. BAYMAX sends one generation at a time.
If Ollama is not running, BAYMAX says so; it never falls back to a cloud service.

## 4. Speech on the Mac

```bash
mkdir -p ~/.baymax/models
curl -L -o ~/.baymax/models/ggml-base.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
```

To use another model, set `voice.whisper_model` in `config.toml`. `ggml-small.en.bin` is more accurate and slower.
Speech output uses the built-in macOS `say` command. Piper is optional: set `voice.tts_engine = "piper"`, plus `piper_model`.

## 5. Launch the Mac application

```bash
cd desktop
npm install
npm start            # type-checks, builds, then launches Electron
```

The app starts the backend if it isn't already running, shows the floating companion, and adds a menu-bar
icon (two eyes joined by a line). The menu has: show/hide companion, conversation, workspace, sleep/wake,
Emergency Stop, and quit.

To package a `.app` / `.dmg`: `npm run dist:mac` (needs macOS; unsigned unless you configure signing).

**macOS permissions**, requested only when a feature needs them:

- **Microphone**: requested on first push-to-talk.
- **Accessibility**: needed for clicking, typing and reading other apps. Grant it in System Settings → Privacy & Security → Accessibility.
- **Automation**: prompted per app when BAYMAX controls it via AppleScript, e.g. Mail.
- **Screen Recording**: needed only for screenshots.

BAYMAX never disables or works around these protections. If a permission is missing, the task fails with instructions.

## 6. Build and install the iPhone app

```bash
cd ios
(cd BaymaxKit && swift test)        # logic + protocol parity tests on the Mac
xcodegen generate                   # creates BaymaxCompanion.xcodeproj
open BaymaxCompanion.xcodeproj
```

In Xcode:

1. Select the **BaymaxCompanion** target → Signing & Capabilities.
2. Choose your team. Change the bundle id if `com.baymax.companion` is taken.
3. Pick your iPhone or a simulator, then press **Run**.

On first launch iOS asks for Local Network access. Allow it, or the app cannot reach the Mac.
Microphone, Speech Recognition and Camera permissions are requested when you first use those features.

`ios/build.sh` runs the package tests and a simulator build from the command line.

## 7. Pair the iPhone with the Mac

1. On the Mac, open Workspace → **iPhone Pairing** → **Pair iPhone**. This turns on the TLS listener
   (port 8766) and shows a QR code and a 6-digit code. Both are valid for 5 minutes and 5 attempts.
2. On the iPhone, open the gear icon → **Scan pairing QR code**. Or enter the IP address, port,
   fingerprint and code manually.
3. The phone checks that the Mac's TLS certificate matches the fingerprint in the QR code. It then pairs and
   stores its device key in the iOS Keychain. Later connections authenticate automatically with
   challenge-response.

To unpair: on the Mac, Workspace → iPhone Pairing → Revoke. On the phone, Settings → Forget this Mac.

If the phone can't connect:

- Check both devices are on the same Wi-Fi.
- Check the macOS firewall allows incoming connections to the backend's Python.
- Check that Local Network access is enabled on the iPhone (Settings → Privacy & Security → Local Network → BAYMAX).

## 8. Optional: local image generation

Run any server with the AUTOMATIC1111 web-UI API, for example *Stable Diffusion WebUI Forge* launched with `--api`.
Then set:

```toml
[images]
provider = "automatic1111"
base_url = "http://127.0.0.1:7860"
```

SDXL-class models are slow on a 16 GB MacBook Air; SD 1.5 or SDXL-Turbo-class models are more practical.

## 9. Tests

```bash
./scripts/test_all.sh
node scripts/khronos_validate.cjs ~/Developer/BAYMAX/workspace/artifacts/models   # independent GLB check
```
