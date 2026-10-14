# Known limitations and incomplete items

Read this alongside the status table in the README. Items are grouped by why they are incomplete.

## Not verifiable in the build environment (Linux container, no Apple tooling)

| Item | State | What's needed |
|---|---|---|
| iPhone app compiles and runs | **Not compiled.** No Swift toolchain or Xcode was available, and the Swift download host was blocked. All 18 Swift files pass a tree-sitter syntax parse, but type checking has not happened. Expect a small number of compile fixes on first build. | Run `ios/build.sh` on the Mac, or the `ios` CI job on a macOS runner |
| BaymaxKit Swift tests | Written, not executed | `cd ios/BaymaxKit && swift test` |
| Installation on a physical iPhone | Not done | Xcode with your Apple team (see SETUP §6) |
| Mac app on macOS | Electron app was launched on **Linux** under Xvfb: it started the backend, created both windows, and rendered the character. macOS-specific parts (tray template icon, dock hiding, `askForMediaAccess`, notarisation) were not exercised. | `cd desktop && npm start` on the Mac |
| macOS desktop automation | AppleScript/Quartz code paths are untested on a real Mac. Logic is tested with a fake desktop backend. | Grant Accessibility and try the examples in the user guide |
| Ollama conversation | Client tested against a fake streaming Ollama server. No real model was run, so response quality, latency and JSON-proposal reliability are unmeasured. | `ollama pull llama3.2:3b` |
| whisper.cpp transcription, `say` TTS, iPhone speech | Pipelines are unit-tested (WAV, VAD, process lifecycle, interruption). No real audio was captured or synthesised. | Real devices |
| Live web research | DuckDuckGo and other sites were blocked by the sandbox proxy. Parsing, extraction, SSRF guard and report generation were tested with fixtures. | Network access on the Mac |
| SPEAKING animation screenshot | Not captured, because no TTS engine exists in the container | Real Mac/iPhone |

## Functional gaps (by design or not yet built)

- **No wake word.** Push-to-talk only. This is deliberate for privacy, and wake-word detection would need an always-on microphone.
- **Image generation** works only with an AUTOMATIC1111-compatible local server. ComfyUI workflows and
  diffusers-in-process are not implemented. Without a server, image requests are refused, not faked.
- **3D creation** produces procedural *blockouts*, not photoreal or production assets. There are no textures and no
  booleans/holes (holes are visual inserts), and no glTF JSON+bin export (GLB only). Objects outside the 31 generators need
  the local model for a labelled primitive approximation.
- **Research** without a model gives extractive excerpts only, with no synthesis or claim comparison.
- **The coding agent's** edit quality depends on the local model. Small models (3B) often fail multi-file
  changes, though the bounded loop and backups keep this safe. It runs the first detected test command only.
- **Mail** sending uses Apple Mail via AppleScript. Delivery is not confirmed. There is no iMessage integration.
- **Hardware control** is a disabled stub. There is no driver for any device, and firmware flashing is not implemented.
- **Remote access** outside the local network is not supported: the phone only connects to private LAN addresses.
- **iPhone background behaviour:** the app disconnects and stops the microphone when backgrounded, and
  reconnects on foreground. There are no push notifications for approvals.
- **Eye tracking on the iPhone** follows a fixed forward gaze. There is no face/gaze tracking, by design, to avoid camera use.
- **The workspace** has no per-file diff viewer or in-app code editor. Changed files and backups are listed instead.
- **Packaging:** `npm run dist:mac` is configured but untested. The app is unsigned unless you add a signing identity.
- **Localization:** English only.

## Quality caveats

- The character is an original procedural model. It is recognisably Baymax-inspired but not a professional sculpt.
- The 3D classifier is keyword-based. Unusual phrasing may need rewording, and BAYMAX will then say it doesn't understand.
- The intent router uses rules plus model fallback. Without the model, free-form requests that match no rule get an
  honest "I can't reach my local model" reply.
