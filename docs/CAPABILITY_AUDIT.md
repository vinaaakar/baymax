# Capability audit — claimed vs. working (Oct 15)

"Verified on the Mac" means a test in `backend/tests_macos` passed on the real MacBook. Unit tests use a simulated
desktop and model, which proves logic, not macOS behaviour.

## How an iPhone command travels (traced in code)

| # | Stage | Code |
|---|---|---|
| 1 | iPhone input | `ios/BaymaxCompanion/App/AppModel.swift` sends `user.text` `{text, source: voice|text}` (speech recognised on the phone) |
| 2 | Mac receives | `backend/baymax/server/hub.py` `_m_user_text` (TLS + device HMAC auth) → `Assistant.handle_text(voice=True)` |
| 3 | Speech check | `assistant._handle` → `voice/stt.py clean_transcript` (drops sound tags / silence hallucinations) |
| 4 | Intent | `intent/router.py IntentRouter.route` (deterministic rules); `split_compound` for "X and Y" |
| 5 | Tool choice | rule intent → tool; otherwise `assistant._chat`: only tools passing `intent/relevance.py tool_is_relevant` are offered to the local model |
| 6 | Checks | `Tool.validate`, app resolution (`hostos/apps.py`), approvals (`security/policy.py`), Emergency Stop |
| 7 | Execution | `tasks/manager.py` runs the plan with per-tool timeouts → tool → `hostos/desktop.py MacDesktop` (AppleScript/AX, CGEvent) |
| 8 | Result | `ToolResult` with `verified` + `verification`; replies built from the result, never from the model's claim |

## Root causes of the observed failures

| Command (as recognised) | What happened | Root cause | Fix |
|---|---|---|---|
| `Open text` | "TextEdit is open." | Correct: "Text" resolved to TextEdit and `apps.open` confirmed it was running | — |
| `Who the pointer to the sudden visible` | asked to clarify | Transcription too garbled to know the target | Correct behaviour kept; "move/who the pointer to the center of the screen" now handled |
| `Identify the active window under CA` | not understood | No rule for "identify …"; trailing noise | Rule: identify/which/what … active/current/front window → `desktop.active_window` |
| `Create a new text document` | not understood | No tool or rule for new documents | New `desktop.new_document` (verified by its window title) + rule |
| `Baymax display auto` | claimed the background was set to Auto | No tool matched; the model's plain reply invented the change and the claim detector only knew "I opened…" | Detector covers set/changed/"has been set"/"is now on"…; for action requests the false reply is **replaced**: "I didn't do that … nothing on your Mac was changed" |
| `Test multiple line text with three separate` | "Repository not found: project phoenix" | The word "test" made code tools eligible; the 3B model called one and filled the repo from the remembered "Project Phoenix" fact | Code/repo tools need a code context; text-entry wording never reaches them; this phrase now asks one short question |
| `Open ChatGPT and do some research …` | timed out after 25 s | Finding ChatGPT's message box read the window with `entire contents`, which builds the whole (very large) accessibility tree before returning — longer than the 25 s limit | Bounded breadth-first read (element cap + 12 s budget, reports when cut short); 30 s overall search with stage reporting ("opened ChatGPT, but …; nothing was typed or sent") |

## Capability matrix

| Capability | Entry point | Needs | Status | Verified by | Limitations |
|---|---|---|---|---|---|
| Answer questions | `assistant._plain_chat` | Ollama `llama3.2:3b` | implemented | unit (fake model); used from iPhone | quality of a 3B model; false action claims now replaced |
| Voice input | iPhone Speech, Mac `voice/stt.py` (whisper.cpp) | mic permission, whisper model | partial | unit (filters) | garbled transcripts are only rejected or questioned, not corrected |
| Open apps | `apps.open` | — | implemented | iPhone session (TextEdit opened, confirmed running) | — |
| Open websites | `apps.open_url` | Automation (to read the URL back) | implemented | unit | URL read-back unverified on the Mac |
| New TextEdit document | `desktop.new_document` | Automation for TextEdit | implemented (new) | unit; Mac test added, **not yet run** | document apps with a scripting dictionary only |
| Safe text entry | `screen.type_into`, `ai.delegate` | Accessibility | implemented | **Mac**: focus + Unicode multi-line typing + exact read-back (`desk` API); tool-level Mac test added, not yet run | app auto-capitalization/autocorrect is reported, not undone |
| Active window | `desktop.active_window` | — (title: Accessibility) | implemented | **Mac**: `test_frontmost_app_and_window`; tool-level test added | |
| Read window | `desktop.read_window` | Accessibility | implemented, **unverified after fix** | unit | `_AS_TEXT` had the "entire contents" iteration bug until dc83be8 |
| Screen inspection / find | `screen.find_element`, `screen.inspect` | Accessibility; OCR needs Screen Recording | partial | **Mac**: AX elements with enabled/identifier and frame centre | OCR (Vision) path not run on the Mac |
| Pointer control | `desktop.move_cursor`, `screen.point_at` | Accessibility, PyObjC Quartz | implemented | **Mac**: move + read-back, element centre; centre-of-screen test added | |
| Clicks | `screen.click_element`, `desktop.click` | Accessibility | partial | unit | AXPress (`_AS_PRESS`) fixed in dc83be8, not re-run on the Mac; double/right click unverified |
| ChatGPT/Claude/Gemini | `ai.delegate` | Accessibility, Automation, app or website login | **broken → fixed, unverified** | unit (stages, login wall, timeout) | sending needs approval; BAYMAX does not read the answer back |
| Web research | `research.web` | internet (DuckDuckGo/SearXNG) | implemented | unit with a local test server | not run against the live web here; summary by the 3B model |
| Open repository in editor | `apps.open_project` | — | implemented | unit | |
| Find code / explain error | `coding.find`, `coding.explain_error` | — / model | implemented | unit | |
| Java class + test | `coding.java_class` | JDK (`brew install openjdk`) | implemented | unit runs when `javac` exists | marked unavailable without a JDK |
| Run / fix project tests | `coding.agent` | project toolchain | partial | unit | edits need approval; fix quality limited by the 3B model |
| Documents, summaries, diagrams | `documents.*` | model | implemented | unit | quality depends on the model |
| 3D models (GLB) | `creation3d.*` | — | partial | unit (14 tests) | built from part templates for known categories, not free-form generation |
| Images | `image.generate` | local Automatic1111/ComfyUI | **unavailable** (provider `none`) | — | reported as unavailable |
| Simulations | `simulation.run` | — | implemented | unit | |
| Memory | router + `memory/facts.py`, `MemoryStore` | — | implemented | unit | |
| Sleep / wake / Emergency Stop | `security/estop.py`, `services.on_estop` | — | implemented | unit; **Mac**: typing stopped mid-text by `cancel_input()` | |
| System settings (display, wallpaper, volume) | none | — | **unavailable** | false claims blocked (unit) | no tool exists; BAYMAX now says so |
| iPhone pointing sync | `gaze.target` (+ pointId/seq) | paired phone | implemented, **unverified** | protocol unit tests | no Swift toolchain here; needs a phone test |

## Still to verify on the hardware

- `BAYMAX_MACOS_INTEGRATION=1 .venv/bin/python -m pytest -v tests_macos` (12 tests: new-document → three lines → read-back
  through the tools, active window, pointer to centre, bounded interface read).
- From the iPhone: the seven session commands above.
- ChatGPT: "Open ChatGPT and do some research on …" — expect an approval request with the exact prompt; with the ChatGPT
  app installed, the prompt typed and read back; otherwise a message naming the stage that failed.
