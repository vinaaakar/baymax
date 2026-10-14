# Verification matrix

Columns follow the request: **unit** and **integration** tests ran in a Linux cloud container (Python 3.13, Node, Electron under
Xvfb, JDK 21). **Real Mac** and **real iPhone** mean run on your hardware. No Mac, iPhone or Xcode was available to me, so every
hardware column is **NOT RUN** until you run [MANUAL_CHECKLIST.md](MANUAL_CHECKLIST.md).

Allowed results: PASS · FAIL · NOT RUN · N/A.

| # | Feature | Root cause fixed | Code changed | Unit test | Integration test | Real Mac | Real iPhone |
|---|---|---|---|---|---|---|---|
| 1 | Mac app launches | an outdated backend on port 8765 was silently reused | `desktop/electron/backend.ts`, `backend/baymax/buildinfo.py` | PASS | PASS: real Electron replaced an outdated backend | NOT RUN | N/A |
| 2 | Ollama availability reported correctly | phone read device-count updates as "no model" | `server/hub.py`, `AppModel.swift`, `CompanionScreen.swift` | PASS | PASS: TLS phone client sees ready/unavailable and live changes | NOT RUN | NOT RUN |
| 3 | Greeting → normal answer, no tool | model offered every tool on every turn | `intent/router.py`, `intent/relevance.py`, `assistant.py` | PASS | PASS: misbehaving fake model | NOT RUN | NOT RUN |
| 4 | "What can you do?" from the real registry | no live capability report | `capabilities.py` | PASS | PASS | NOT RUN | NOT RUN |
| 5 | Background noise never triggers an action | only 5 whisper tags filtered; silence hallucinations passed | `voice/stt.py` | PASS | PASS: real executable + WAV | NOT RUN | N/A |
| 6 | Active-window tool uses the real front app | read_window required an app; "None" reached AppleScript | `tools/system_tools.py`, `hostos/desktop.py`, `tools/screen_tools.py` | PASS | PASS (fake desktop) | NOT RUN | N/A |
| 7 | Invalid app names rejected safely ("EB Max", "None") | names only pattern-checked | `hostos/apps.py`, `tools/system_tools.py` | PASS | PASS | NOT RUN | N/A |
| 8 | Unavailable app → useful message | AppleScript -1728 surfaced raw | `hostos/desktop.py`, `hostos/apps.py` | PASS | PASS | NOT RUN | N/A |
| 9 | Real cursor event reaches the phone | no Mac→phone cursor path existed | `electron/presence*.ts`, `presence/cursor.py`, `server/hub.py` | PASS | PASS: real Electron + real X pointer → TLS phone client (Linux) | NOT RUN | NOT RUN |
| 10 | Phone gaze follows the cursor | phone gaze fixed at zero; eyes moved ~1 cm | `Gaze.swift`, `CharacterView.swift`, `BaymaxNode.swift`, spec `eyes.travel` | PASS (TS); Swift tests written, not run | N/A | N/A | NOT RUN |
| 11 | Sleep stops cursor updates and listening | mic kept running when sleep came from the other device | `presence/cursor.py`, `recorder.ts`, `common.tsx`, `AppModel.swift` | PASS | PASS: X11 cursor test + two-device session test | NOT RUN | NOT RUN |
| 12 | Wake resumes, mic stays off | — | same | PASS | PASS | NOT RUN | NOT RUN |
| 13 | "Open <app>" verified | resolver + verify running | `tools/system_tools.py` | PASS | PASS (fake desktop) | NOT RUN | N/A |
| 14 | File creation makes a real file | — | `tools/dev_tools.py` | PASS | PASS: Java files written and re-read | NOT RUN | N/A |
| 15 | Code generation runs a real test | — | `tools/dev_tools.py` | PASS | PASS: javac/java with JDK 21 | NOT RUN | N/A |
| 16 | Research returns real sources | TLS trust store missing; 404s cited | `research/tls.py`, `research/web.py` | PASS | PASS: local HTTPS + SearXNG-compatible server; public internet blocked here | NOT RUN | NOT RUN |
| 17 | No network → honest error | class-name-only errors | `research/web.py` | PASS | PASS: proxy 403 reported exactly | NOT RUN | N/A |
| 18 | Mac and iPhone share state | — | `server/hub.py` | PASS | PASS: two-device session test (simulated phone) | NOT RUN | NOT RUN |
| 19 | Disconnect/reconnect restores state | paired phone couldn't reconnect after Mac restart | `server/app.py` (listener at start), `CompanionConnection.swift` | PASS | PASS: abrupt TCP abort + reconnect | NOT RUN | NOT RUN |
| 20 | Emergency stop | — | `tasks/manager.py` | PASS | PASS: real child process killed via phone message | NOT RUN | NOT RUN |
| 21 | iOS app compiles in Xcode | — | — | N/A | N/A | N/A | **NOT RUN here** — you reported "Build Succeeded" for commit `4df568b`; later commits not yet compiled |
| 22 | Installs and launches on the iPhone | signing/Developer Mode | — | N/A | N/A | N/A | Launched by you (UI visible, earlier build) |
| 23 | Head-only on both platforms | full-body geometry; then a stale build | spec, `baymaxModel.ts`, `BaymaxNode.swift` | PASS | PASS: Electron renders at 3 sizes | NOT RUN | NOT RUN |
| 24 | Real iPhone gaze follows real cursor | see 9–10 | — | N/A | N/A | NOT RUN | NOT RUN |

Also added this round:
- "Point toward the window I am using" and "Read the title of the active window" (`screen.point_window`, `desktop.active_window`).
- ChatGPT-style prompt boxes are now matched by partial label ("Message ChatGPT").
- The iPhone shows the "following cursor" icon only while samples are actually arriving.
