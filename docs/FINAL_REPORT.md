# BAYMAX repair round — final report

**Branch:** `claude/baymax-ai-companion-k1dflj`. This round is commits `5ba3489 … HEAD` on top of `43c61c8`, which is what you pushed
to `main`.

**Where this work ran:** a Linux cloud container. It had no Mac, no iPhone, no Xcode or Swift toolchain, no Ollama and no
whisper.cpp, and the sandbox proxy blocked the public internet.

**What that means:**

- Nothing here was run on macOS or on a physical iPhone.
- The Electron app was run for real under X11 (Xvfb) as a stand-in for macOS.
- The phone was simulated by a client speaking the real TLS protocol.

**Statuses used:** VERIFIED, PARTIALLY VERIFIED, NOT IMPLEMENTED, BLOCKED.

## 1. Summary of actual changes

1. **Cursor → iPhone gaze.**
   - The Electron main process samples the real pointer and display geometry.
   - The backend validates it, normalises it per display, rate-limits it to 30 Hz, drops it while asleep, and relays it as `cursor.update`.
   - Protocol v2 is negotiated per session, so v1 phones keep working.
   - The iPhone maps the cursor to head and eye direction with the same gaze function as the Mac (TS and Swift ports with mirrored tests).
   - Debug views on both devices show the raw values.
2. **Bust avatar on both platforms.**
   - The full-body figure is gone: no legs, belly seam or floor are modelled.
   - One shared visual spec (`design/baymax-visual-spec.json`) drives geometry, framing, palette and states on both apps.
   - This fixes the iPhone full-body render, which came from a fixed SceneKit camera on a tall screen.
3. **Screen understanding.**
   - "Look at / point at / where is / click the X" reads the accessibility tree with real frames, then falls back to Apple Vision OCR.
   - Matching handles role hints, ordinals, duplicates and ambiguity (it asks a question instead of guessing).
   - Coordinates are converted correctly across multiple displays and Retina scaling.
   - The result goes out as a `gaze.target`, so both avatars look and point.
   - Clicking uses AXPress only, never blind coordinates, and is verified by comparing the accessibility tree before and after.
4. **Voice and the local model.**
   - `whisper-cli` is found in Homebrew paths even when the app is launched from Finder.
   - The speech model is discovered and validated (ggml header, size, Homebrew test stubs excluded).
   - Ollama's native tool calling is used, with every call validated against the tool registry; a JSON-proposal fallback covers models
     without tool support.
   - A model reply that *claims* an action without calling a tool is flagged.
5. **Developer commands.**
   - Open a project in an editor, verified by the window title.
   - Find a function or class (finds `startCursorTracking` in this repo).
   - Explain an error from the clipboard or the terminal, showing the code at file:line.
   - Summarize a document (text, rich text, PDF), saved as a file.
   - Create a Java class plus a test: compiled and run with javac/java and verified by the exit code.
   - Architecture diagrams no longer require explicit steps.
6. **Research.**
   - HTTPS is verified against the macOS trust store (truststore → certifi). This fixes `CERTIFICATE_VERIFY_FAILED` without disabling
     verification.
   - Errors are precise (TLS cause, proxy refusal, HTTP status).
   - 4xx/5xx pages are no longer cited as sources.
   - Real source URLs are shown in the chat and synced to the iPhone (`research.result`).
   - `/api/research/check` tests the provider for real.
7. **Sleep, E-stop and recovery.**
   - Sleep or E-stop cancels in-progress microphone capture on the Mac (audio discarded) and stops speech in and out on the iPhone, even
     when triggered from the other device.
   - Wake never re-opens the microphone.
   - A pinned-certificate mismatch now stops reconnecting and tells you to re-pair.
8. **Setup and diagnostics.**
   - `setup_mac.sh` picks Homebrew Python ≥ 3.11 (macOS's 3.9 would fail) and validates the whisper model download.
   - `baymax doctor` reports Accessibility and Screen Recording state, displays, and the TLS trust source.

## 2. Files changed and why

The full stat is 84 files, about 5.4k lines.

| Area | Files | Why |
|---|---|---|
| Protocol | `protocol/baymax-protocol.json` (renamed from `.v1`), `backend/baymax/protocol/messages.py`, `desktop/src/shared/protocol.ts`, `ios/BaymaxKit/.../ProtocolMessages.swift` | v2 messages `cursor.update`, `gaze.target`, `research.result`; per-session version negotiation |
| Cursor presence | `backend/baymax/presence/cursor.py`, `server/app.py`, `server/hub.py`, `services.py`, `events.py`, `assistant.py`, `desktop/electron/presence*.ts`, `main.ts`, `preload.ts` | real pointer → relay → phone; sleep gate; no history spam |
| Gaze and pointing | `desktop/src/shared/gaze.ts`, `character/rig.ts`, `ios/BaymaxKit/.../Gaze.swift`, `CharacterRig.swift`, `ios/.../GazeSource.swift`, `CharacterView.swift`, `AppModel.swift` | the same mapping on both devices; pointing gesture |
| Visual spec and bust | `design/baymax-visual-spec.json`, `desktop/src/shared/framing.ts`, `character/baymaxModel.ts`, `BaymaxCharacter.tsx`, `theme.css`, `ios/BaymaxKit/.../Framing.swift`, `ios/.../BaymaxNode.swift`, `Theme.swift` | head-and-shoulders composition, identical framing |
| Debug views | `ios/.../CursorDebugPanel.swift`, `CompanionScreen.swift`, `Sheets.swift`, `desktop/src/views/workspace/System.tsx` | received coordinates, normalised values, gaze, connection, timestamps |
| Screen understanding | `backend/baymax/screen/*`, `hostos/desktop.py`, `tools/screen_tools.py`, `intent/router.py` | AX + OCR element finding, ambiguity, permissions, pointing, verified pressing |
| Voice and LLM | `hostos/tools.py`, `voice/stt.py`, `llm/ollama.py`, `llm/prompts.py`, `assistant.py`, `config.py`, `desktop/electron/backend.ts`, `doctor.py` | whisper and model discovery; native tool calling; honesty guard |
| Developer tools | `coding/projects.py`, `tools/dev_tools.py`, `documents/writer.py`, `documents/architecture.py`, `tools/creation_tools.py` | the JARVIS-style commands from the brief |
| Research | `research/tls.py`, `research/web.py`, `tools/creation_tools.py`, `pyproject.toml` | trust store, diagnostics, real citations, availability check |
| Sleep / E-stop clients | `desktop/src/voice/recorder.ts`, `components/common.tsx`, `ios/.../AppModel.swift`, `ios/.../CompanionConnection.swift` | microphone stops on sleep/E-stop from either device; re-pair on certificate change |
| Setup | `scripts/setup_mac.sh`, `docs/SETUP.md` | Python ≥ 3.11, model validation, permissions, physical-iPhone steps |
| Tests | `backend/tests/test_presence.py`, `test_research_live_local.py`, `test_voice_tools.py`, `test_screen.py`, `test_dev_tools.py`, `test_session_sync.py`; `desktop/tests/gaze_presence.test.ts`, `bust_framing.test.ts`; Swift `GazeTests`, `VisualSpecParityTests`; `scripts/e2e/cursor_pipeline_x11.py` | evidence below |
| Docs | `docs/AUDIT.md`, `VISUAL_SPEC.md`, `MANUAL_CHECKLIST.md`, this report, `images/bust-composition-desktop-renderer.png` | audit, specification, checklist |

## 3. Launch the backend and the desktop app (on the Mac)

```bash
cd ~/Developer/BAYMAX
git fetch origin && git checkout claude/baymax-ai-companion-k1dflj   # or merge it into main first
./scripts/setup_mac.sh                       # first time: venv (Python ≥3.11), npm install, Ollama + llama3.2:3b, whisper + model
backend/.venv/bin/python -m baymax doctor    # what works right now and why not
cd desktop && npm start                      # builds and launches; starts the backend automatically
```

To run the backend alone: `backend/.venv/bin/python -m baymax serve` (add `--lan` to open the iPhone listener without starting pairing).

## 4. Build and install the iPhone app

```bash
cd ~/Developer/BAYMAX/ios
brew install xcodegen
(cd BaymaxKit && swift test)        # protocol, gaze and visual-spec parity tests — never run in this container
xcodegen generate && open BaymaxCompanion.xcodeproj
```

Then in Xcode:

1. Connect the iPhone, tap **Trust**, and enable **Developer Mode** (Settings → Privacy & Security).
2. Target → Signing & Capabilities → choose your Apple ID team. Change the bundle id if Xcode says it is taken.
3. Select your iPhone and press Run (⌘R).
4. With a free account, trust the developer on the phone: Settings → General → VPN & Device Management.
5. Allow **Local Network** when asked.

Details are in [SETUP.md §6](SETUP.md).

## 5. Required macOS permissions

| Permission | For | Notes |
|---|---|---|
| Microphone | push-to-talk | asked on first use |
| Accessibility | reading and pressing UI elements, typing, window titles, reading terminal text | System Settings → Privacy & Security → Accessibility → BAYMAX (or Terminal) |
| Screen & System Audio Recording | OCR fallback for apps without accessibility info; screenshots | macOS prompts once; never bypassed |
| Automation | AppleScript control of System Events and Mail | prompted per app |
| Firewall / Local Network | iPhone → Mac port 8766 | allow incoming connections if asked |

Cursor tracking needs none of these.

## 6. Pairing guide

1. Mac: Workspace → **iPhone Pairing** → **Pair iPhone**. This shows a QR code and a 6-digit code, valid for 5 minutes and 5 attempts.
2. iPhone: gear → **Scan pairing QR code**. Manual entry of IP, port, fingerprint and code also works.

**What happens:**

- The phone checks that the Mac's TLS certificate matches the fingerprint in the QR code.
- It receives a device key, which goes into the iOS Keychain.
- Later connections use challenge-response with that key.
- The QR code carries no long-lived secret: only the address, the certificate fingerprint and the one-time code.

**Why pinning:**

- A LAN IP address cannot get a publicly trusted certificate, so the phone pins the Mac's self-signed certificate (P-256, valid 825 days,
  renewed 7 days before expiry).
- After a renewal, the phone stops and asks you to re-pair. It never silently trusts a new certificate.

**Revoke:** Mac → Workspace → iPhone Pairing → Revoke. On the phone: Settings → Forget this Mac.

## 7. Test results

### Automated tests (this container)

| Suite | Result |
|---|---|
| Backend pytest (Python 3.13) | **571 passed** (was 524 before this round) |
| Desktop vitest + `tsc` (renderer and Electron) + production build | **36 passed**, typecheck clean, build OK |
| Swift (BaymaxKit tests, iOS app) | **BLOCKED**: no Swift toolchain. All 22 Swift files parse with zero syntax errors (tree-sitter); that is not a compile |

### Integration tests with real processes and sockets (this container)

- **Real Electron → backend → TLS phone (X11):**
  - the real pointer moved to 7 positions;
  - max normalisation error 0.00005;
  - median latency 35 ms;
  - sleep paused the stream and wake resumed it;
  - relay stats: 9 received / 9 published / 0 rejected.
- **Voice → tool:**
  - a real `whisper-cli` executable on disk (a script standing in for whisper.cpp) was given a real WAV;
  - an Ollama-protocol HTTP server returned `tool_calls`;
  - `apps.open` ran and was verified.
- **Research:**
  - local HTTPS with an untrusted certificate failed with a clear message (verification stays on);
  - after trusting the certificate, the fetch succeeded;
  - a SearXNG-compatible search with real pages kept only the fetched URLs (redirects recorded at their final URL, 404 excluded).
- **Developer tools:**
  - Java class compiled and its test run with **JDK 21** (all PASS);
  - a generated PDF summarized via `pdftotext`;
  - code search over this repository found `startCursorTracking`;
  - the architecture diagram SVG parsed as XML.
- **Two-device session:**
  - sleep and wake from either side;
  - phone E-stop **killed a real `sleep 30` child process** and refused new work;
  - abrupt phone disconnect plus reconnect resynchronised state;
  - only the Mac could reset the stop.

### Simulated components

- The iPhone in integration tests is a Python client speaking the real protocol over TLS.
- macOS accessibility and OCR are replaced by a fake desktop that returns dumps in the exact format of the AppleScript dumper.
- whisper.cpp and Ollama are replaced by protocol-faithful stand-ins.

### macOS tests

**None were run.** No Mac was available.

### Real-iPhone tests

**None were run.** No iPhone was available.

### Remaining failures and known gaps

- No automated test fails.
- Gaps:
  - Live public-web research could not be reached (sandbox proxy 403s every host).
  - Under X11, Chromium updates the global pointer only while it is over Electron's windows. The e2e test therefore moves the pointer
    inside the companion window. On macOS the same API reads `NSEvent.mouseLocation` globally; confirm with checklist §3.

### Performance

The idle backend measured **~65 MB RSS and 0 % CPU over 10 s** in this container (not an M4).

On the M4, record:

- Electron, Python and Ollama memory and CPU (checklist §8);
- Ollama `llama3.2:3b` is expected to use about 3 GB while loaded; `keep_alive` unloads it after 10 minutes idle.

## Acceptance tests (from your brief)

| # | Acceptance test | Existing implementation | Actual problem (before) | Files changed | Test performed | Result | Remaining manual steps |
|---|---|---|---|---|---|---|---|
| 1 | Same Baymax identity and background on Mac and iPhone | separate hand-tuned scenes | numbers drifted; iPhone framing differed | visual spec, both renderers | Electron renders captured; TS + Swift parity tests written (Swift not run) | **PARTIALLY VERIFIED** | run the iPhone app; compare with the Mac (checklist §1) |
| 2 | Only the head and upper portion visible | full-body figure | iPhone showed the full body | `baymaxModel.ts`, `BaymaxNode.swift`, framing | rig bounding box above 0.8 m in five states; renders at 3 sizes | **PARTIALLY VERIFIED** | confirm on the iPhone screen |
| 3 | Desktop companion usable with other apps in front | always-on-top frameless window | — | — | window seen and driven under X11 | **PARTIALLY VERIFIED** | checklist §1.3 on macOS |
| 4 | Real Mac cursor moves the iPhone gaze | not implemented | gaze fixed at zero | presence, relay, gaze ports | real Electron + real pointer + TLS phone client: PASS | **PARTIALLY VERIFIED** | checklist §3 with the real iPhone |
| 5 | Debug panel shows real coordinates and timestamps | none | — | `CursorDebugPanel.swift`, `System.tsx` | Mac card typechecked and built; iOS panel not compiled | **PARTIALLY VERIFIED** | checklist §3 |
| 6 | Voice reaches the local assistant and invokes a real tool | rules + JSON proposals | Homebrew whisper not found from GUI; no tool calling | `tools.py`, `stt.py`, `ollama.py`, `assistant.py` | real executable + WAV + Ollama-protocol server → verified tool | **PARTIALLY VERIFIED** | real whisper.cpp + Ollama on the Mac (checklist §5) |
| 7 | "Open Safari" launches Safari | `open -a` + running check | — | — | fake desktop; macOS path not executed | **PARTIALLY VERIFIED** | checklist §5 |
| 8 | File generation produces a real, readable file | 3D/docs/diagrams existed | architecture diagram refused | `dev_tools.py`, `creation_tools.py`, `writer.py` | Java compiled and run (JDK 21); PDF summary file; SVG parsed; GLB validated (earlier) | **VERIFIED** (in this container) | — |
| 9 | Research retrieves real sources when online | DDG HTML + fetch | TLS verify failures on python.org Pythons; 404s cited | `research/tls.py`, `web.py` | real HTTP stack against local servers; public internet blocked here | **PARTIALLY VERIFIED** | checklist §5 (Spring Boot), §6 |
| 10 | Network/TLS failures explained, security not weakened | generic messages | class-name-only errors | `tls.py`, `web.py` | untrusted-certificate handshake fails with a precise reason; proxy 403 reported | **VERIFIED** | — |
| 11 | Both devices reflect real task and animation states | state events to both | — | — | simulated phone receives the real state stream | **PARTIALLY VERIFIED** | watch both screens during checklist §5 |
| 12 | Sleep suspends listeners; wake restores configured features | partial | mic kept running when sleep came from the other device | `recorder.ts`, `common.tsx`, `AppModel.swift` | backend + Electron cursor suspension verified on X11; client mic changes typechecked / not compiled | **PARTIALLY VERIFIED** | checklist §7.1–7.2 |
| 13 | E-stop blocks new operations and cancels supported work | implemented | — | — | real child process killed via a phone protocol message | **PARTIALLY VERIFIED** (buttons not pressed on hardware) | checklist §7.3 |
| 14 | Disconnect/reconnect does not corrupt the session | implemented | silent retry loop after a certificate change | `CompanionConnection.swift` | abrupt TCP abort + reconnect resync test | **PARTIALLY VERIFIED** | checklist §2.4, §7.4 |
| 15 | Starts with no paid AI API or cloud credentials | local-only | — | — | backend started and all suites ran here with no keys | **VERIFIED** | — |

### Other capabilities

| Capability | Status | Note |
|---|---|---|
| Screen understanding ("look at the Run button") | **PARTIALLY VERIFIED** | matching, coordinates, permissions and gaze verified with AX-format fixtures; the macOS AX/Vision code has not executed (checklist §4) |
| Open project in VS Code, find code, explain error, summarize, Java | **PARTIALLY VERIFIED** | logic verified for real on Linux; the macOS-specific parts (`open -a`, window titles, `pbpaste`, AXDocument, textutil/PDFKit) need the Mac |
| iOS app compile and run | **BLOCKED** | no Xcode in this environment |
| Image generation | **NOT IMPLEMENTED** as a built-in model | works only if you run a local AUTOMATIC1111-compatible server; BAYMAX says so otherwise |
| Wake word | **NOT IMPLEMENTED** | push-to-talk only, by design |

## 8. What needs you

1. Run `./scripts/setup_mac.sh`, then `backend/.venv/bin/python -m baymax doctor`, and send me its output.
2. Build the iPhone app onto your phone (§4) and pair it (§6).
3. Grant **Accessibility**, and when asked **Screen Recording**, to BAYMAX/Electron.
4. Work through [MANUAL_CHECKLIST.md](MANUAL_CHECKLIST.md). For anything that fails, send the step number and the exact message.
5. Push this branch. Pushes from this session are refused by GitHub (403). The updated `baymax-repo.bundle` in the repository root
   contains all commits. Follow the same steps as last time, but check out `claude/baymax-ai-companion-k1dflj`.
