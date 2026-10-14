# User guide

## The companion

The character always shows what BAYMAX is actually doing:

| Pose | Meaning |
|---|---|
| Gentle breathing, eyes follow your cursor | **Idle**, ready |
| Leaning in, head tilted, glow follows your voice | **Listening**: the microphone is on |
| Looking up, slow sway | **Thinking**: working out your request |
| Nodding gently | **Speaking**: a reply is being read aloud |
| Head down, arm working, pulsing glow | **Executing** a task (see the task chip for real progress) |
| Arms raised, small bounce | **Success**: shown only after the result was *verified* |
| Head tilted to the side | **Confused**: needs clarification from you |
| Arms in, head lowered | **Concerned**: an approval or warning needs your attention |
| Drooping, eyes closed, dimmed | **Sleeping**: you put it to sleep |
| Short head shake | **Error**: something failed; the reason is in the reply and the task log |
| Dim, half-closed eyes | **Disconnected** from the Mac backend |

If a task finishes but its outcome could not be checked (for example a mouse click), BAYMAX goes back to
idle without the success pose and says "I couldn't verify the outcome".

## Talking to BAYMAX

- **Push to talk:** click or tap the microphone. It stops on its own after a short pause, or click again.
  The microphone is only on while the button is lit.
- **Type:** use the text box. On the iPhone, tap the keyboard icon.
- **Stop:** say or type "stop" / "cancel" to cancel running tasks and stop speech.
- **Emergency Stop:** the red STOP button, the menu bar item, or saying "emergency stop". It cancels all
  tasks, kills running commands, stops speech, and *blocks* further actions until you reset it **on the Mac**.
- **Sleep / Wake:** "go to sleep" / "wake up", the Sleep button, or tap the sleeping character.
  While asleep BAYMAX ignores requests other than "wake up" and "emergency stop".

## Things you can ask

| Category | Examples |
|---|---|
| Apps & web | "open Safari", "open vscode", "go to https://example.com" |
| Desktop control* | "click at 400, 300", "move the mouse to 100 200", "scroll down 5", `type "hello" into Notes`, `press "Send" in Mail`, "read the window of Notes", "take a screenshot" |
| 3D models | "make a red sports car", "build a three-storey house with a garage", "a hexacopter drone", "an office chair", "a ball bearing", "a wine bottle"; then "make the body blue", "increase the wheel size", "make it more detailed", "view from the top", "export this as GLB", "undo" |
| Research | "research whisper.cpp performance on Apple Silicon", "search the web for …" |
| Simulations | "simulate a projectile at 30 m/s and 45 degrees", "simulate a motor to 2000 rpm", "simulate the drone altitude hold at 10 m" |
| Documents | "write a technical document about our pairing protocol", "draw a flowchart: design -> print -> test" |
| Code | "run the tests in ./myproject", "fix the failing tests in ./myproject", "inspect the repository at ./myproject" |
| Commands | "run \`npm test\` in web" (allowlisted development commands only) |
| Email* | "send an email to sam@example.com saying the build passed" (always asks for approval) |
| Memory | "remember that I prefer metric units", "what do you remember about me?", "forget that I prefer metric units" |

\*Needs the macOS Accessibility or Automation permission.

Supported 3D objects: airplane, bearing, bed, boat, bolt, bookshelf, bottle, bracket, bridge, cabinet, car
(sedan/SUV/sports/pickup/van/bus), chair, crate/box, cup/mug/glass/vase/bowl/jar/can/bucket, drone, gear,
house, lamp, laptop, nut/washer, pipe, robot, robotic arm, rocket, shoe/boot/high heel, skyscraper/tower, sofa,
spring, stool, table/desk, tree.

For anything else BAYMAX says it has no generator. It won't substitute a different object. If you say
"make an approximate giraffe" and the local model is running, it builds a clearly labelled approximation from
primitives instead.

## Approvals

Risky actions pause and ask first. These include sending messages, deleting files, installing software,
publishing (`git push`), and acting outside the workspace. The request appears in the companion, the
conversation and Missions, with the exact action.

- Approve or Deny on the Mac.
- The iPhone can always deny. It can approve high-risk actions only if you enable that in Mac Settings.
- An unanswered request expires after 5 minutes and is treated as denied.

## The engineering workspace

Open it from the companion (⌘ button) or the menu bar. Sections, also reachable with ⌘1–⌘9:

- **Missions**: every task with its plan, status, real progress, verification and event log; cancel here.
- **3D Creation**: create, view (orbit, camera presets, wireframe), recolour, scale parts, describe changes, undo, export.
- **Images**: generate with a configured local generator; each image is labelled *generated*, *edited*, *reference* or *placeholder*.
- **Research**: cited reports. Quoted excerpts are kept separate from model-written analysis, which is labelled inference.
- **Simulations & Telemetry**: run models; charts are labelled *SIMULATED DATA*.
- **Software**: run tests or the coding agent on a repository inside the workspace.
- **Artifacts**: every file BAYMAX produced, with its validation status.
- **Logs**: the tamper-evident audit log.
- **Memory**: view, edit or delete what BAYMAX remembers.
- **iPhone Pairing**, **Settings**: model, voice, local-only mode, permissions.

## Privacy controls

- **Local-only mode** (Settings) turns off all web research.
- **Memory** only stores what you explicitly ask it to remember. Passwords, keys and card numbers are refused.
- **Voice:** Mac audio is transcribed locally by whisper.cpp. iPhone audio is transcribed on the iPhone and
  never uploaded; if on-device recognition is unavailable, voice input is turned off.
