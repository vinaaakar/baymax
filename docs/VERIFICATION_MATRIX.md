# Verification matrix

Automated tests ran in a Linux cloud container: Python 3.13, Node 22, real Electron 38 under Xvfb, and Chromium. No Mac, iPhone,
Xcode or Swift toolchain was available (Swift downloads are blocked by this environment's network policy). So:
- every **actual macOS** and **actual iPhone** column is **NOT RUN** until you run [MANUAL_CHECKLIST.md](MANUAL_CHECKLIST.md);
- Swift code (BaymaxKit tests and the app) is **written but not compiled here**.

| Feature | Root cause | Fix applied | Automated test | Tested on actual macOS | Tested on actual iPhone | Remaining issue |
|---|---|---|---|---|---|---|
| "Hello" → greeting, no tools | model offered every tool on every turn | router greeting intent; tools filtered by relevance before the model sees them | PASS `test_greetings_never_touch_tools` | NOT RUN | NOT RUN | — |
| "What is Newton's third law?" → answer, no tools | same | constrained tool selection: no relevant tool → plain chat with no tools offered | PASS `test_knowledge_question_is_answered_without_offering_tools` | NOT RUN | NOT RUN | answer quality depends on llama3.2:3b |
| "Can you hear me?" reports the real microphone/transcription state | typed question got a canned "Yes, I can hear you" | typed: reports whisper.cpp readiness from `stt.status()`; voice: confirms with the transcript | PASS `test_typed_can_you_hear_me_reports_real_voice_status` | NOT RUN | NOT RUN | — |
| "What can you do?" → real capability readiness | — | live registry and readiness report | PASS `test_capabilities_come_from_the_registry_without_tools` | NOT RUN | NOT RUN | — |
| "What can you do with the current screen?" → real inspection | routed to the generic capability list | new `screen.inspect`: front window, real accessibility controls, browser URL; honest permission message | PASS `test_what_can_you_do_with_the_current_screen_inspects_it_for_real`, `test_screen_inspection_is_honest_without_accessibility` | NOT RUN | N/A | — |
| "Open Safari" → launched and verified running | — | app resolver plus a running check | PASS (fake desktop) | NOT RUN | N/A | — |
| "Open Gemini in Safari" → Safari opens gemini.google.com, verified | the whole phrase "Gemini In Safari" was treated as an app name | destination resolver (`hostos/sites.py`) is separate from the browser; `open -a Safari <url>`; verified from Safari's address bar | PASS `test_open_site_in_browser_separates_destination_from_app`, `test_open_gemini_in_safari_is_verified_from_the_address_bar`, `test_unconfirmed_page_is_not_claimed` | NOT RUN | N/A | reading the address bar needs the Automation permission for Safari; without it BAYMAX says it couldn't confirm |
| "Open YouTube" (no browser named) | — | known site → URL in the default browser, reported as unverified | PASS `test_open_known_site_without_browser_opens_the_url_not_an_app` | NOT RUN | N/A | — |
| "Read the current window" → real front app | "None" passed to AppleScript | front-app resolution, ask when unknown | PASS (fake desktop) | NOT RUN | N/A | — |
| "Find the Projects tab" → real UI search | — | accessibility tree, then OCR; ambiguous → question; partial-label matching | PASS `test_screen.py` | NOT RUN | N/A | — |
| E-stop blocks actions, not conversation | — | checked before each action only | PASS `test_estop_blocks_actions_but_not_conversation` | NOT RUN | NOT RUN | — |
| No tool names / raw AppleScript in replies | "I'll use desktop.read_window…" announcements; raw osascript errors | announcements removed; AppleScript errors logged, a plain message is shown | PASS `test_no_internal_tool_names_in_replies` | NOT RUN | NOT RUN | — |
| Background noise never triggers an action | silence hallucinations ("Thank you.") passed | sound tags and hallucinations filtered | PASS (unit + real whisper executable earlier) | NOT RUN | N/A | — |
| Real cursor → Mac face pupils | — | 2D face driven by real cursor samples | PASS real Electron + real X pointer: renderer `svg-2d`, no WebGL canvas, TRACKING, pupil x −0.076 (left) / +0.090 (right) | NOT RUN | N/A | X11 only reports the pointer inside Electron windows; macOS reads it globally |
| Real cursor → iPhone pupils | phone gaze fixed at zero (earlier) | cursor relay over TLS; same 2D face math on the phone | PASS relay (real Electron → TLS phone client, max error 0.00005); Swift parity tests written, not run | NOT RUN | NOT RUN | SwiftUI renderer not compiled here |
| 2D companion (no 3D) on both platforms | three.js and SceneKit renderers | Mac: SVG face; iPhone: SwiftUI `Canvas` face; one spec (`face2d`) with test vectors; `baymaxModel.ts`, `framing.ts`, `Framing.swift` and the SceneKit node removed | PASS `face2d.test.ts` (vectors, eyes stay on the face, no `three`/SceneKit imports); Swift `VisualSpecParityTests` written, not run | NOT RUN | NOT RUN | needs an Xcode build on your Mac |
| Expressions: IDLE, TRACKING, LISTENING, THINKING, RESEARCHING, EXECUTING, SPEAKING, POINTING, SLEEPING, ERROR, DISCONNECTED | no researching, tracking or pointing indicators | `expressionFor` / `FaceExpression.from` from real state, live cursor, verified targets and the task kind (`task.update.kind` now sent to the phone) | PASS expression vectors (TS), Chromium renders in `docs/images/face-2d-states.png` | NOT RUN | NOT RUN | — |
| Diagnostics panel | lacked expression and pupils | iPhone panel shows coordinates, normalized values, gaze, expression, pupil offsets, connection, event age | N/A (UI) | N/A | NOT RUN | — |
| Sleep/wake stops and resumes cursor and mic | — | — | PASS real Electron (cursor stream paused while asleep) | NOT RUN | NOT RUN | — |
| Research returns real sources | TLS trust store; 404s cited | truststore + certifi; 404s rejected | PASS local HTTPS; public internet blocked here | NOT RUN | NOT RUN | — |
| iOS app compiles | — | — | N/A | N/A | NOT RUN — the last build you reported was an earlier commit | — |
