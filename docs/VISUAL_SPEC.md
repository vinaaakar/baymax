# BAYMAX visual specification

The machine-readable source of truth is [`design/baymax-visual-spec.json`](../design/baymax-visual-spec.json). The Mac app imports it directly
(`desktop/src/shared/framing.ts`, `desktop/src/character/baymaxModel.ts`). The iPhone app mirrors it in `BaymaxKit/Framing.swift`,
and `VisualSpecParityTests` fails if any number differs from the JSON.

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

## Avatar: head composition

- **Shown:** only the head on a small rounded neck/shoulder collar (radius 0.32 m, y 1.50–1.78). No torso, arms, legs or floor exist
  in either scene, so nothing is cropped or hidden behind a shape.
- **Head:** white ellipsoid with radii 0.26 × 0.185 × 0.22, pivoting at the neck.
- **Face:** two black eyes joined by a thin line that follows the curvature of the head.
- **Arms:** none. Pointing is done with the head and eyes.
- **Eyes:** follow the gaze by rotating up to 0.12 rad (sideways) and 0.08 rad (up/down) about the neck pivot, so they slide along the
  face instead of sinking into it. In IDLE the eyes lead (90 % of the look) and the head follows a little.
- **Lighting:** neutral white key and fill with a restrained red room bounce. The red rim light rises only with real activity.
- **Layout:** on both devices the face has its own region between the status bar and the controls; nothing is drawn over it.
  The iPhone is landscape-first (Desk mode, on by default); in landscape the conversation opens beside a smaller face.
- **Framing (identical on both platforms):**
  - Vertical FOV is 28°.
  - Half-height = min(max(minHalf, 0.44 / aspect), 1.2), where minHalf goes from 0.5 (square) to 0.36 (aspect ≥ 1.6), so landscape
    face regions (iPhone desk mode) show a larger head.
  - The head-and-collar centre sits 42 % from the top, but the frame top never cuts the head (head top + 0.16 margin, which leaves room for status pills).
  - The camera looks straight ahead.

  Test vectors for aspect ratios 0.46, 0.68, 1.0, 1.6 and 2.4 are in the JSON.
- Evidence: `docs/images/head-landscape-desktop-renderer.png` shows the real Electron renderer at phone (390×844), Mac companion
  (320×470) and wide (1000×640) sizes. The SceneKit port uses the same numbers but **has not been rendered here** (no iOS toolchain).

## Animation states

These are driven only by real events: the state machine in `protocol/baymax-protocol.json`, shared by Python, TypeScript and Swift.

| State | Behaviour |
|---|---|
| IDLE | slow breathing and blinking; gaze follows the Mac cursor when it is streamed |
| LISTENING | leans in; glow follows the real microphone level |
| THINKING | head tilts; slow glow |
| SPEAKING | nods while TTS is actually playing |
| EXECUTING | focused forward pose while a task runs |
| SUCCESS | short happy lift, only after a verified result |
| CONFUSED | tilt and shrug when a request is ambiguous |
| CONCERNED | head lowered for approvals, permissions and E-stop |
| SLEEPING | eyes closed, dimmed; cursor stream, microphone and speech stopped |
| ERROR | brief recoil, red glow |
| DISCONNECTED | dimmed, eyes half-closed |

**Pointing:** when a `gaze.target` arrives (for example "look at the Run button"), the head and eyes turn to the target and the arm on
that side lifts, for the duration the backend specifies.

## Component behaviour

- **Companion surfaces:** the avatar plus concise status pills (Mac, model, iPhone), push-to-talk, a text field, Sleep and STOP.
  Advanced panels (tasks, logs, artifacts, research, approvals, pairing, cursor debug) live in the Mac Workspace window, not on the companion.
- **iPhone:** the companion screen shows the same controls. Conversation, tasks and settings open as sheets. The cursor debug overlay is
  off by default (Settings → Cursor debug overlay).
