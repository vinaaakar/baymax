# End-to-end checklist (MacBook Air + physical iPhone)

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

1. Run `cd desktop && npm start`.
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
