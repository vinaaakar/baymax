# Implementation report

## Build environment (as inspected, not assumed)

The work was done in a cloud Linux container, **not** on the target Mac:

| Check | Result |
|---|---|
| OS | Linux 6.18 x86_64, 4 CPUs, 15 GB RAM |
| Python | 3.13.16 (system); backend venv created |
| Node / npm | 22.22.0 / 10.9.4 |
| Xcode, Swift, iOS simulator | **absent**; `download.swift.org` blocked by the egress proxy |
| Ollama, whisper.cpp, `say`, `osascript` | **absent** |
| Electron | npm install succeeded (Linux binary) |
| Chromium (Playwright), Xvfb, ffmpeg | present |
| Web search (DuckDuckGo) | blocked by the egress proxy |
| Repository | empty git repo on branch `claude/baymax-ai-companion-k1dflj` |

The project lives in this repository rather than `~/Developer/BAYMAX`. Clone it there on the Mac. The backend's
default workspace is `~/Developer/BAYMAX/workspace`.

## What was built, by phase

1. **Foundation:** repository layout; the shared protocol JSON (versioned); the Python backend with config,
   logging, sqlite migrations and the event bus; Electron/Vite/React scaffolding; the XcodeGen iOS project;
   the `doctor` command.
2. **Character and interface:**
   - the procedural Baymax-inspired character in three.js (lathe-profiled torso, arms and legs; ellipsoid head;
     two eyes and a curved connecting line);
   - a SceneKit port with the same geometry;
   - the pure animation rig (TS + Swift) with an 11-state pose table, blinks, breathing, cursor/eye tracking,
     voice-level response, reduced motion and frame-rate caps;
   - the crimson stage, the compact Mac companion window and the iPhone character screen.
3. **Communication:**
   - pairing codes;
   - TLS LAN listener with a pinned self-signed certificate;
   - HMAC challenge-response;
   - per-direction message validation, rate limits, strike counters and handshake timeout;
   - snapshot on reconnect; device revocation;
   - client reconnection with exponential backoff (TS and Swift) and NWPathMonitor-triggered reconnect on iOS.
4. **Local AI and voice:**
   - Ollama streaming client with bounded context and no cloud fallback;
   - intent router; conversation with memory;
   - whisper.cpp STT with server-side VAD; renderer push-to-talk with VAD and 16 kHz WAV;
   - `say`/Piper TTS with real playback events and interruption;
   - iOS on-device SFSpeech and AVSpeech.
5. **Safe desktop automation:**
   - AppleScript (argv-passed, injection-safe) and Quartz backend: open app with a running-check, URLs,
     cursor/click/scroll, typing into a named app, AX element listing/pressing, window text, screenshots;
   - workspace file tools with backups;
   - allowlisted command execution;
   - risk policy, approvals bound to the action hash, Emergency Stop.
6. **Creation engine:**
   - 3D: 13 mesh primitives, 31 generators / 220 phrases, classifier (colours, counts, sizes, role-specific
     sizes, styles, detail), planner, LLM-planned labelled approximations, conversational and structured
     modifications with undo, GLB export, independent validator, versioned store, reload from GLB;
   - images: AUTOMATIC1111 API with explicit result kinds;
   - research: search, SSRF-guarded fetch, extraction, cited reports;
   - coding agent with a bounded repair loop;
   - five simulations; documents, diagrams (SVG + Mermaid), CSV and JSON.
7. **Engineering workspace:** Missions (plan, events, progress, cancel), 3D studio (viewer, material/scale
   edits, instructions, undo, export), Images, Research, Simulations & Telemetry, Software, Artifacts, Logs
   (hash-chain status), Memory, iPhone Pairing (QR), Settings and permissions.
8. **Hardening:** the suites below; real-browser and real-Electron runs; the Khronos validator; fixes for
   everything those runs found.

## Verification actually performed

| What | Command | Result |
|---|---|---|
| Backend unit + integration tests | `cd backend && .venv/bin/python -m pytest -q` | **524 passed** |
| Desktop typecheck (renderer + Electron) | `npm run typecheck` | no errors |
| Desktop unit tests | `npx vitest run` | **23 passed** (3 files) |
| Desktop production build | `npm run build` | succeeded |
| Renderer in Chromium against the live backend | Playwright + SwiftShader WebGL | rendered; 10 character states captured from **real** backend conditions (see `docs/images/character-states.png`) |
| Real Electron app | Playwright `_electron` under Xvfb | launched; **spawned the Python backend itself**; companion (320×470, always-on-top) and workspace windows; preload bridge present; renderers sandboxed; backend stopped on quit |
| 3D export compliance | Khronos glTF-Validator 2.0.0-dev.3.10 on 124 exports (all generators, detail 1 and 3) | **0 errors, 0 warnings** |
| GLB loads in three.js | workspace viewer in Chromium | 48-mesh car loaded; modification round trip rendered |
| Backend packaging | fresh venv `pip install -e backend[dev,macos]` + `baymax create …` | installed; GLB validated |
| Swift syntax | tree-sitter-swift parse of all 18 Swift files | 0 syntax errors (**not** a compile) |
| Swift compile / tests, iOS build | — | **not run** (no toolchain) |

How the character-state screenshots were produced (no faked states):

| State | Real backend condition |
|---|---|
| LISTENING | `voice.state=listening` reported |
| EXECUTING | a real pytest run that sleeps 5 s |
| SUCCESS | that run's verified completion |
| CONFUSED | an unsupported "giraffe" model request |
| CONCERNED | a pending high-risk approval |
| ERROR | `open Nonexistent` failing because desktop automation is unavailable on Linux |
| SLEEPING | the sleep endpoint |
| THINKING | a transcription in progress |
| DISCONNECTED | backend unreachable |

### Test coverage by requirement

| Requirement | Tests |
|---|---|
| Animation state transitions | `test_character_state_machine.py` (spec parity, shared vectors, sleep/disconnect rules), `protocol.test.ts`, `rig_voice.test.ts`, `BaymaxKitTests.swift` |
| Intent routing | `test_intent_and_memory.py` (33 utterances + argument extraction + model-proposal validation) |
| Task lifecycle, cancellation, recovery | `test_tasks.py` |
| Tool authorization, approvals, E-Stop | `test_tasks.py`, `test_security.py`, `test_server.py`, `test_assistant.py` |
| Message validation, device auth, reconnection | `test_protocol_messages.py`, `test_server.py` (TLS + fingerprint pinning, pairing, wrong key, wrong code, version mismatch, malformed frames, revoke, reconnect), `store.test.ts` (client backoff) |
| Workspace confinement, command validation | `test_security.py`, `test_coding_agent.py`, `test_server.py` |
| Memory controls | `test_intent_and_memory.py`, `test_assistant.py`, `test_server.py` |
| Artifact generation, 3D export and reload | `test_creation3d.py` (every alias, every detail level, corruption detection, store/undo/export/reload), Khronos run |
| Error handling, model unavailable | `test_voice_llm.py` (unreachable Ollama, missing model, fake streaming server), `test_assistant.py` |

## Bugs found by verification and fixed

- Sleep/disconnect rules were keyed on target state, so `MIC_STOPPED` woke a sleeping character. They are now keyed on events (shared spec updated).
- The GLB validator crashed on a corrupted index instead of reporting it.
- The coding agent crashed when a model proposed a path outside the repository.
- The coding agent re-ran **stale `.pyc` bytecode** after a same-size edit in the same second. Fixed by cache invalidation and `PYTHONDONTWRITEBYTECODE`.
- A missing test runner (e.g. no pytest) was reported as failing tests. It is now reported as an environment error.
- Default PID motor gains never settled. The controller was re-tuned and the anti-windup redesigned.
- CSV writer line endings failed write verification.
- "Change the camera angle to the top view" picked the iso view.
- **Compound edits** ("make the body blue and increase the wheel size") painted the wheels. Instructions are now split into clauses.
- Device sessions were unhashable, so the LAN hub couldn't track connections.
- The desktop renderer had no CORS preflight path, so the real app couldn't call the API cross-origin.
- A `useSyncExternalStore` selector returned new arrays, causing a React infinite loop.
- A speech caption covered the character's face. It moved into the control stack.
- Status pills overflowed at 320 px.
- The character stuck in THINKING after a transcription produced no text.
- Approval requests couldn't show CONCERNED from CONFUSED/SUCCESS/ERROR. The shared transition table was widened.
- Hash-only navigation didn't change views.
- Opening artifact links externally would have leaked the local token into browser history. They now use a sandboxed in-app window.

## Hardware notes

Defaults target 16 GB unified memory:

- one 3B model (~3 GB);
- 4k context and 512-token output;
- whisper `base.en` (~150 MB);
- character rendering at 12–45 fps.

Running a 7B coder model alongside the app works, but leaves less headroom. Don't run two large models at once.
