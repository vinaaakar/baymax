# BAYMAX visual specification

The machine-readable source of truth is [`design/baymax-visual-spec.json`](../design/baymax-visual-spec.json). The Mac app imports it directly
(`desktop/src/shared/face2d.ts`). The iPhone app mirrors it in `BaymaxKit/Face2D.swift`, and `VisualSpecParityTests` fails if any number
or test vector differs from the JSON. After changing `face2d`, regenerate the vectors with `npx vite-node scripts/gen-face-vectors.ts`
(in `desktop/`).

## Colours

| Token | Hex | Use |
|---|---|---|
| background | `#180509` | every screen and window behind the avatar |
| accent | `#B5122A` | primary buttons, borders, diagram strokes |
| highlight | `#FF334F` | rim light, live indicators, arrows |
| surface | `#080305` | deepest shade, bottom of the stage gradient |
| text | `#F5F5F2` | primary text; also the avatar's skin |
| face | `#121214` | eyes and the eye line |
| panel | `#14090C` | translucent panels and pills |

**Stage:** a vertical gradient `#1F060C → #180509 → #080305`, a dark halo from the top (`#3A0A14`), and a soft red glow behind the head at
(50 %, 36 %). Both platforms draw the same three layers (`theme.css .stage`, `Theme.stage`).

## Typography and spacing

- **Typeface:** SF Pro (system) on both platforms.
- **Sizes:** caption 12, body 14, status 13, title 17.
- **Spacing:** 4-pt unit and 16-pt gutters.
- **Shapes:** corner radius 14; controls at least 44 pt high.

## Character: flat 2D vector face

There is no 3D model, camera or scene on either platform. The Mac draws SVG (`desktop/src/character/BaymaxCharacter.tsx`); the iPhone
draws SwiftUI `Canvas` paths (`ios/BaymaxCompanion/Character/CharacterView.swift`). Both use the same geometry function.

- **Shapes:** a white rounded head (ellipse 1 × 0.74 face units) with a radial gradient (upper-left highlight to a soft grey rim) and a
  soft drop shadow, plus two black oval eyes joined by a thin line. The eyes carry a tiny highlight.
- **Stage:** deep burgundy, with a red glow behind the head that rises only with real activity.
- **Depth without 3D:** for gaze, the eye group slides across the face (up to 0.30 sideways, 0.20 up/down). The eye nearest the edge
  narrows by up to 28 % and the eyes move slightly closer together. The head shifts a little (parallax) and rolls with the pose.
  The eyes always stay inside the head; this is tested for cursor positions on and beyond the screen edges.
- **Mac companion:** the whole head, centred and large, in a 560×320 window, with only a small keyboard button at the
  bottom-left. Right-click and the menu-bar tray hold Conversation, Workspace, Sleep/Wake, Emergency Stop and Hide.
  Emergency Stop is also the global shortcut ⌘⇧. (Cmd+Shift+period).
- **iPhone, landscape (desk mode):** like the reference device, the head is a dome rising from the bottom edge. The eyes sit in
  the dome and their travel follows its curve. Only ⚙︎ (Settings & pairing) and ⌨︎ are shown, top-right. Long-press the face
  for Conversation, Current task, Sleep/Wake, Settings and Emergency Stop.
- **Alerts on both:** Emergency Stop engaged, approvals and errors appear only while they need you.
- **Fit:** in portrait the whole head is shown, filling 70 % of the width or 56 % of the height (whichever is smaller); it is never cropped.
  With the conversation open in landscape, the face moves to a narrower region beside it.
- **Evidence:**
  - `docs/images/face-2d-states.png`: the Mac renderer in Chromium for IDLE, TRACKING left-up and right-down, POINTING, RESEARCHING,
    LISTENING, SLEEPING and CONFUSED.
  - `docs/images/companion-2d-electron.png`: the real Electron companion (pointer over it, so the bars are shown) during the X11 end-to-end test.
  - `docs/images/mac-landscape.png` (560×320) and `docs/images/iphone-landscape.png` (844×390): resting landscape face mode.
  - The SwiftUI port uses the same math and test vectors but **has not been compiled or rendered here** (no Swift/Xcode toolchain).

## Expressions

These are derived only from real events: the backend state machine (`protocol/baymax-protocol.json`), live cursor samples, verified
screen targets and the running task's kind (`expressionFor` / `FaceExpression.from`).

| Expression | When | What the face does |
|---|---|---|
| IDLE | idle, no cursor samples | slow breathing and blinking, resting gaze |
| TRACKING | idle while real cursor samples arrive | eyes follow the cursor |
| LISTENING | microphone recording | ring that pulses with the real microphone level |
| THINKING | the model is working | eyes up and aside; three soft dots |
| RESEARCHING | a research task is running | dots plus a magnifier orbiting the head |
| EXECUTING | any other task is running | progress arc (real progress if reported, otherwise a moving arc) |
| SPEAKING | text-to-speech is actually playing | glow follows speech |
| POINTING | a verified `gaze.target` | eyes and head turn to it; arrowhead at the head edge and a target-lock reticle in that direction |
| SUCCESS | verified result | gentle squint (happy eyes) and glow |
| CONFUSED | ambiguous request | head tilt, one eye smaller |
| CONCERNED | approvals, permissions, E-stop | eyes lowered |
| SLEEPING | asleep | eyes closed to thin lines, dimmed, "z" |
| ERROR | failure | red ring flash |
| DISCONNECTED | no backend/Mac | dimmed, eyes half closed, link-off glyph |

## Component behaviour

- **Companion surfaces:** the avatar plus concise status pills (Mac, model, iPhone), push-to-talk, a text field, Sleep and STOP.
  Advanced panels (tasks, logs, artifacts, research, approvals, pairing, cursor debug) live in the Mac Workspace window, not on the companion.
- **iPhone:** the companion screen shows the same controls. Conversation, tasks and settings open as sheets. The cursor debug overlay is
  off by default (Settings → Cursor debug overlay).
