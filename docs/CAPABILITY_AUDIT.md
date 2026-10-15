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
| New TextEdit document | `desktop.new_document` | Automation for TextEdit | implemented | **Mac** (12/12 run): created and confirmed by its window | document apps with a scripting dictionary only |
| Safe text entry | `screen.type_into`, `ai.delegate` | Accessibility | implemented (`ai.delegate`: see below) | **Mac**: focus + Unicode multi-line typing + exact read-back, also through the `screen.type_into` tool and task manager | app auto-capitalization/autocorrect is reported, not undone |
| Active window | `desktop.active_window` | — (title: Accessibility) | implemented | **Mac**: through the tool (after fixing cf0e0b0: it used the first listed window, a Terminal strip) | |
| Read window | `desktop.read_window` | Accessibility | implemented, **unverified after fix** | unit | `_AS_TEXT` had the "entire contents" iteration bug until dc83be8 |
| Screen inspection / find | `screen.find_element`, `screen.inspect` | Accessibility; OCR needs Screen Recording | partial | **Mac**: AX elements with enabled/identifier and frame centre | OCR (Vision) path not run on the Mac |
| Pointer control | `desktop.move_cursor`, `screen.point_at` | Accessibility, PyObjC Quartz | implemented | **Mac**: move + read-back, element centre, centre of the main display through the tool | |
| Clicks | `screen.click_element`, `desktop.click` | Accessibility | partial | unit | AXPress (`_AS_PRESS`) fixed in dc83be8, not re-run on the Mac; double/right click unverified |
| ChatGPT/Claude/Gemini | `ai.delegate` | Accessibility, Automation, app or website login | **broken → fixed, unverified with ChatGPT** | unit (stages, login wall, timeout); **Mac**: the bounded interface read finishes in time (Finder, TextEdit) | sending needs approval; BAYMAX does not read the answer back |
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

- Done: `tests_macos` 12/12 passed on the MacBook (cf0e0b0).
- From the iPhone: the seven session commands above.
- ChatGPT: "Open ChatGPT and do some research on …" — expect an approval request with the exact prompt; with the ChatGPT
  app installed, the prompt typed and read back; otherwise a message naming the stage that failed.

## Reliability recovery — round 2 (pointer, window reading, ChatGPT, research)

### Root causes (traced with the real router and code)

| Observed | Path | Root cause | Fix |
|---|---|---|---|
| `Move the pointer to the top left corner` → "Pointing at “Move the pointer to the top left corner” (text recognised on screen)" | router → `screen.point_at` → `_locate` (AX, then OCR) | No rule for corners, so "top left corner" became a target to *search for*; OCR then matched the user's own command shown in BAYMAX's conversation window and moved the pointer onto it | Corners/centre are `desktop.move_cursor {position}` computed from the bounds of the display under the pointer (5 pt inset so Hot Corners never fire), read back; OCR ignores text inside BAYMAX's own windows (`drop_inside`) |
| `Move the pointer to the center of` | same | the centre rule didn't accept a cut-off "of" | rule tolerates it |
| `Read the current window` → "Safari's front window has no readable text" | `desktop.read_window` → `_AS_TEXT` | `entire contents of window 1` does not reach into Safari's web area, and "no AX text" was reported as "no text" | `_AS_TEXT` walks `UI elements` breadth-first from the main window (bounded), sets `AXManualAccessibility`; if still no text, recognises the text inside that window's frame (Vision, Screen Recording) and says so with confidence; otherwise explains which permission/limitation stopped it |
| "Opened chatgpt.ai / gpt.ai …", "Asked Safari to open chatgpt.ai" | no rule → local model → `apps.open_url` with an invented domain | model-proposed addresses were trusted | (e20a8f5) proposals must come from the user's words; a named site opens at its official address (`KNOWN_SITES`: chatgpt → https://chatgpt.com); else BAYMAX asks |
| "I can't find an app called “Chatgpt Undo Some Research On How I Walk”" | generic `open X` rule | the whole sentence became the app name ("and do" misheard as "undo") | `_research_intent` extracts destination + browser + topic; the generic rule never takes a phrase with task words as an app name (asks instead); compound commands still become plans |
| `Create a new chat and do some research on how AI works` → "couldn't work out the details" | no rule | — | no service named → `research.web` (real sources, summary, saved report); with a service ("…new chat in ChatGPT…") → `ai.delegate` with `new_chat` (⌘N in the app) |
| ChatGPT message box not found within 30 s | `ai.delegate` field search | unknown: only 18 labelled elements were readable in the ChatGPT window | `AXManualAccessibility` set before reading (e20a8f5); **needs `diagnose_ax.py ChatGPT` output** to confirm |

### Status of the affected functionality

| Capability | Status | Evidence |
|---|---|---|
| Move to coordinates | working | Mac: read back (earlier runs); unit |
| Move to centre / corners | implemented | unit (display under the pointer, external display, failure when the pointer doesn't arrive); Mac test added |
| Point at an element (AX/OCR) without clicking | working | Mac: element centre; unit: never clicks, ignores BAYMAX's own text |
| Active app / window | working | Mac 12/12 (cf0e0b0) |
| Read current window — accessibility | implemented (rewritten) | unit; Mac test on a Safari page added |
| Read current window — OCR fallback | implemented | unit; needs Screen Recording on the Mac |
| Open ChatGPT / in Safari | implemented | unit: official site when the app isn't installed; Safari when asked |
| Research (web search) | implemented | unit (local server); not run against the live web here |
| Research via ChatGPT | **partial** | prompt typed after approval; BAYMAX does not read ChatGPT's answer; message box not found in the ChatGPT app on the Mac (unresolved) |

### Manual checklist (Mac, then iPhone)

1. `BAYMAX_MACOS_INTEGRATION=1 .venv/bin/python -m pytest -v tests_macos` — 15 tests (the ChatGPT one skips if the app isn't installed).
2. `.venv/bin/python tests_macos/diagnose_ax.py ChatGPT` with a ChatGPT chat window visible — send the output.
3. From the iPhone: `Move the pointer to the top left corner` (pointer near the corner, no Mission Control), `Move the pointer to the center of the screen`, `Read the current window` with Safari on a web page, `Open ChatGPT`, `Open ChatGPT in Safari`, `Create a new chat and research how AI works` (sources listed), `Open ChatGPT in Safari and research how AI works` (approval with the exact prompt).

## Real-Mac run, Oct 15 (130203f): `tests_macos` 21/21 passed

Fixed on the way (each confirmed by this run on the MacBook, not only by simulated tests):

| Problem seen on the Mac | Cause | Fix |
|---|---|---|
| Dialog checks never ran ("failed to compile") | `st` is reserved in AppleScript (ordinal suffix) | renamed; variable-name guard test covers st/nd/rd/th |
| Typed text landed in Terminal ("me stop me stop…") | key events go to whatever app is in front | `type_text` checks the target process is frontmost before every chunk (in-process Accessibility), scripted key presses refuse (1407) |
| TextEdit / Safari / ChatGPT "no window visible" | a full-screen terminal or app window lives on its own Space | tests leave a full-screen terminal themselves; BAYMAX activates the app once and re-reads when no window is visible |
| Window by title not found | documents opened as tabs; Accessibility lists only the visible tab | Window-menu fallback (entries must be real windows in the window server), exact titles first |
| ChatGPT message box not found in 30 s | AppleScript walk: 64 elements in 30 s | in-process Accessibility walk (`hostos/ax_inprocess.py`): whole window, ~2000 elements, in ~0.2 s |
| ChatGPT read-back failed | empty zero-height text areas at the same spot | invisible matches ignored; focused, then largest, wins; read back the focused field |
| Safari page not read | text walk spent its budget on the tab bar | in-process text in document order, page before browser chrome |

Note: the "ChatGPT" app on this Mac is the website added to the Dock from Safari (process shown as "Web App"), not
OpenAI's native app. Typing is verified by read-back; sending still requires approval on the Mac.

Still unverified on hardware: the iPhone checklist in `docs/MANUAL_CHECKLIST.md` (Phase 2 section).
