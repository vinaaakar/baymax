# End-to-end checklist (MacBook Air + physical iPhone)

## Quick check (10 minutes)

1. **Mac:** quit BAYMAX, then run `~/Developer/BAYMAX/scripts/start_mac.sh` (works from any folder). If an older backend is still running, the app now
   replaces it automatically ("stopping stale backend" in the log).
   - **Pass:** only Baymax's head on a small rounded neck, on dark red, with no torso and no arms. Resize the window: the head stays
     whole and centred.
2. **Mac chat:** type each message and check the reply.
   - **"Hi, Baymax."** → a greeting; no tool runs.
   - **"What can you do?"** → a list built from what is actually available, including what isn't and why.
   - **"Read the current window."** (Terminal clicked first) → Terminal's text, or a clear Accessibility message.
   - **"read the EB Max window"** → "sounds like my own name … Which app do you mean?". No AppleScript error.
   - **"Open Visual Studio Code."** → "Visual Studio Code is open."
3. **iPhone:** in Xcode press ▶︎.
   - **Pass:** the same head-only Baymax on the same red, before pairing.
   - After pairing, the top pill shows **"Mac AI · llama3.2:3b"** (or **"Mac AI unavailable"** if Ollama is stopped), not "No local model".
4. **iPhone → Mac:** type "why is the sky blue" on the phone. **Pass:** the answer from the Mac's model appears on the phone and the Mac.
5. **Cursor:** turn on Settings → Cursor debug overlay on the phone, then move the Mac mouse. **Pass:** the eyes follow, and "last event"
   stays under ~100 ms.

These checks can only be done on your hardware. Each has an observable pass condition. Run them in order. Write down any step that
fails, with the exact message shown.

## 0. Preconditions

```bash
cd ~/Developer/BAYMAX
git pull                                   # branch claude/baymax-ai-companion-k1dflj (or main after merging)
./scripts/setup_mac.sh                     # first time only
backend/.venv/bin/python -m baymax doctor  # llm.available, stt.available should be true
```

- **Pass:** `doctor` shows `"llm": {"available": true, "model": "llama3.2:3b"}` and `"stt": {"available": true}`.

## 1. Launch and appearance

1. Run `~/Developer/BAYMAX/scripts/start_mac.sh`.
2. The floating companion appears bottom-right:
   - dark crimson background (#180509);
   - Baymax's **head and shoulders only**, with no legs, belly or floor;
   - status pills (Mac, model, iPhone).
3. Click Safari to bring it to the front. The companion stays visible on top, and you can still use Safari. Drag the companion by its
   background, resize it, then close it: it hides. Menu-bar icon → Show Companion brings it back.
4. Build and run the iPhone app (SETUP.md §6). The phone shows the **same** bust and background.
   - **Pass:** both look like the same character, and neither shows a full body.

## 2. Pairing and live link

1. Mac: Workspace → iPhone Pairing → **Pair iPhone**.
2. iPhone: gear → **Scan pairing QR code**.
3. **Pass:**
   - the iPhone pill turns "Connected";
   - the Mac's "Paired devices" lists the phone as **connected**;
   - typing "hello" on the phone shows the reply on both devices.
4. Turn the iPhone's Wi-Fi off for 10 s, then on. **Pass:** it reconnects by itself within ~10 s, with no re-pairing.

## 3. Cursor → iPhone gaze

1. iPhone: Settings → **Where is this iPhone?** (set it to where the phone actually sits). Turn on **Cursor debug overlay**.
2. Mac: Workspace → iPhone Pairing → **Cursor presence (debug)** card.
3. Move the mouse slowly to each corner of the built-in display.
4. **Pass:**
   - the iPhone's head and eyes turn toward the cursor, with no visible lag;
   - the debug overlay shows the display size (e.g. 1512×982), `vx`/`vy` from about 0 to 1, and "last event" under ~100 ms;
   - the Mac card's "sent to iPhone" count increases.
5. With an external monitor, move the cursor onto it. **Pass:** `display` changes, and `vx` goes below 0 or above 1 depending on where the
   monitor is arranged; the head turns further that way.
6. Leave the mouse still for 3 s. **Pass:** the head drifts back to rest, and "last event" grows (no fake samples).

## 4. Screen understanding

1. Open a project in VS Code. Say or type: **"Baymax, look at the Run and Debug button"**.
   - **Pass:** the reply names the element and where it is; the Mac companion and the iPhone turn and point toward it.
   - If BAYMAX says Accessibility is needed, grant it (SETUP.md §5) and retry.
2. Say **"look at the Run button"** where several "Run" controls exist. **Pass:** it asks which one (CONFUSED face) instead of guessing.
3. In an app whose controls are drawn rather than accessible (a game, a canvas web app), ask for a visible word.
   - **Pass:** it uses text recognition; you are asked for Screen Recording once.
4. Ask for something that isn't on screen. **Pass:** "I couldn't find … I'm not pointing at anything."

## 5. Voice and commands (push-to-talk)

Click the mic, speak, then stop. Each command must show a result you can check yourself:

| Say | Pass condition |
|---|---|
| "Open Safari" | Safari comes to the front; the reply says "Safari is open." |
| "Open my BAYMAX repository in VS Code" | VS Code opens the BAYMAX folder; the reply quotes the window title |
| "Find the function responsible for cursor tracking" | lists `startCursorTracking — desktop/electron/main.ts:<line>` |
| (copy a compiler error, then) "Explain this compiler error" | names the file:line and explains it (needs Ollama) |
| "Create a Java class and test it" | reply lists PASS lines; the files exist in `~/Developer/BAYMAX/workspace/java/Greeter/` (needs a JDK: `brew install openjdk`) |
| "Generate a diagram explaining the system architecture" | SVG artifact appears in Workspace → Artifacts and opens |
| "Summarize this document" (with a PDF open in Preview) | summary appears and is saved as a file |
| "Search for official Spring Boot documentation" | sources with real `spring.io` URLs appear on the Mac and on the iPhone |
| "Create a 3D model of a chair and export it" | GLB in Artifacts; opens in the viewer |
| something unclear, e.g. "do the thing" | asks one question; no action is taken |

While speaking, both avatars show LISTENING. Then THINKING, then EXECUTING while the task runs. SUCCESS appears only for verified
results.

## 6. Research and network honesty

1. Turn Wi-Fi off and ask "search the web for Swift concurrency". **Pass:** it says the search failed and why, and shows no results.
2. Turn Wi-Fi on and run `curl -s -H "Authorization: Bearer $(cat ~/.baymax/local_token)" http://127.0.0.1:8765/api/research/check`.
   - **Pass:** `"available": true` and a `trust` source, e.g. "system trust store (truststore)".

## 7. Sleep, Emergency Stop, recovery

1. Start push-to-talk on the Mac, then press **Sleep** on the iPhone.
   - **Pass:** the Mac's mic stops immediately (the mic indicator in the menu bar goes off), both avatars sleep, and the iPhone
     debug overlay shows "sleeping (stream paused)" while the mouse moves.
2. Wake from the Mac. **Pass:** the gaze resumes, but the microphone does **not** turn on by itself.
3. Start a long task: "run the tests in ~/Developer/BAYMAX/backend" (approve it on the Mac; the run takes ~20 s).
   While it runs, press **STOP** on the iPhone.
   - **Pass:** the task shows Cancelled, a new request is refused while stopped, and only the Mac can reset the stop.
4. Quit and relaunch the Mac app. **Pass:** the iPhone shows Disconnected, then reconnects automatically.

## 8. Performance snapshot (record it)

- Activity Monitor: note the memory and CPU of BAYMAX (Electron), Python and Ollama:
  - idle;
  - while moving the mouse with the phone connected;
  - during an Ollama reply.
- Backend idle reference measured in the Linux test container: ~65 MB RSS and 0 % CPU over 10 s (not an M4 measurement).


## Computer-use checks on your Mac (new)

Run the opt-in native suite first (it opens a new TextEdit document; close it without saving afterwards):

```
cd ~/Developer/BAYMAX/backend
BAYMAX_MACOS_INTEGRATION=1 .venv/bin/python -m pytest -v tests_macos
```

Then type these into BAYMAX and check the result:

| Type | Expect |
|---|---|
| `Hi Baymax` | a greeting; nothing opens |
| `What is Newton's third law?` | an explanation; nothing opens |
| `What applications are currently open?` | the real list with the front app marked |
| `Open Gemini in Safari` | Safari opens gemini.google.com; reply says it was confirmed (or which permission is missing) |
| `Open a new tab in Safari` | a new tab opens |
| `Open ChatGPT and type this prompt: Explain autonomous computer-use agents.` | the text appears in ChatGPT's box; nothing is sent |
| `Ask ChatGPT to explain recursion` | an approval with the exact prompt; after you approve, it's sent |
| `Draft a post saying 'test'`, then `post it on X` | the draft is shown; then an approval before anything is published (deny it if you don't want to post) |
| `Find the Projects tab in Claude` (Claude open) | the iPhone face looks toward it; nothing is clicked |
| `Open Safari and then open Terminal` | both open, in order |
| Press ⌘⇧. during a multi-step request | it stops; later steps don't run |
| Open `http://127.0.0.1:8765/api/requests` (with the token) or check the BAYMAX log | each request's stages |
| (ChatGPT or any chat app in front) `Point to the Send button` | the real mouse pointer moves onto Send; the Mac face and the iPhone face point at it; nothing is clicked |
| `Move the mouse to the search field` | the pointer moves onto the search field; nothing is typed or clicked |
| (a web form in Safari) `Fill the email field with test@example.com` | that field (found by its label) gets the text; the reply says it was read back; the form is NOT submitted |
| `Switch to TextEdit and read it` | TextEdit comes to the front and BAYMAX reads its text |
| `Go to the Python documentation and find information about dictionaries` | a research result with a summary and the docs.python.org sources it read |
| Ask BAYMAX to type a long text into TextEdit, then press ⌘⇧. (or STOP) while it types | typing stops mid-text; no key stays held; the iPhone pointer indicator disappears |

### iPhone pointing sync (needs the phone paired and on the same Wi-Fi)

| Do | Expect |
|---|---|
| `Point to the Send button` with the phone app open | within about a second the phone face looks/points the same way as the Mac face, then returns to following the cursor after ~5 s |
| Ask twice quickly for two different buttons | the phone shows the second one (older events arriving late are dropped) |
| Turn Wi-Fi off on the phone, ask BAYMAX to point, then turn Wi-Fi back on after 10 s | the phone does NOT replay the old point after reconnecting |
| Press STOP while pointing | the indicator clears on both screens |

### Phase 2 — workflows and desktop state (real Mac + iPhone)

Run first: `cd backend && BAYMAX_MACOS_INTEGRATION=1 .venv/bin/python -m pytest -v tests_macos` (20 tests; close TextEdit test
documents without saving). Then, from the iPhone with BAYMAX running the current build (Settings → Mac → Backend build):

| # | Do | Expect |
|---|---|---|
| 1 | Put one file `phase2-check.txt` in ~/Documents; say `Find the file named phase2-check.txt and open it` | found, then opened; result verified |
| 2 | Copy it to ~/Desktop too; say the same | “Several files match … Which one?” with both paths; nothing opens. Reply `number 2` → the Desktop copy opens |
| 3 | Say `find the file named phase2-check.txt and open it`, then delete the file before step 2 is reached (or use a name that doesn't exist) | stops; open never runs; the reason is on the phone |
| 4 | Lock the Mac (⌃⌘Q), then from the iPhone say `open Safari` | phone shows “Waiting for you to unlock the Mac…”; unlock within 60 s → Safari opens; stay locked → “Your Mac is locked … Unlock it yourself” |
| 5 | Let the display sleep (`pmset displaysleepnow` in Terminal), then `open Safari` from the iPhone | display wakes; if the lock screen appears, the task waits / stops as in 4. It never unlocks |
| 6 | Open two TextEdit documents; say `switch to the Untitled window in TextEdit` | asks which one when titles match several; with the full title, that window comes to the front |
| 7 | In TextEdit press ⌘S (Save sheet open), then say `type "hi" into the text area in TextEdit` | “TextEdit is showing a Save dialog … I don't click dialog buttons”; cancel the sheet within 60 s → typing continues |
| 8 | Check the iPhone for each of the above | the same reason the Mac shows; final status matches |
| 9 | Press STOP while step 4 is waiting | task cancelled; nothing runs |
