"""The BAYMAX system architecture, as implemented in this repository (kept in sync with docs/ARCHITECTURE.md)."""

from __future__ import annotations

IPHONE = "iPhone app | SwiftUI + SceneKit bust, gaze"
MAC_UI = "Mac companion | Electron + three.js, cursor"
HUB = "Device hub | TLS WSS, pairing, HMAC auth"
LOCAL = "Local API | 127.0.0.1, bearer token"
ASSIST = "Assistant | intent router, conversation"
TASKS = "Task manager | approvals, E-stop, verify"
CURSOR = "Cursor relay | normalise, 30 Hz, sleep gate"
TOOLS = "Tool registry | typed, validated tools"
OLLAMA = "Ollama | llama3.2:3b, local tool calls"
WHISPER = "whisper.cpp | local speech-to-text"
MACOS = "macOS automation | AX tree, Vision OCR, apps"
RESEARCH = "Web research | verified TLS, real sources"
CREATE = "Creation | 3D GLB, docs, diagrams, code"

LAYERS = [[IPHONE, MAC_UI], [HUB, LOCAL], [ASSIST, TASKS, CURSOR], [TOOLS, OLLAMA, WHISPER], [MACOS, RESEARCH, CREATE]]
EDGES = [
    (IPHONE, HUB, "protocol v2"), (MAC_UI, LOCAL, "REST + WS"), (HUB, ASSIST, "requests"), (LOCAL, ASSIST, "chat / voice"),
    (LOCAL, CURSOR, "cursor samples"), (CURSOR, HUB, "cursor.update"), (ASSIST, TASKS, "plans"), (ASSIST, OLLAMA, "chat / tools"),
    (LOCAL, WHISPER, "audio"), (TASKS, TOOLS, "run step"), (TOOLS, MACOS, ""), (TOOLS, RESEARCH, ""), (TOOLS, CREATE, ""),
]


def is_architecture_request(text: str) -> bool:
    t = text.lower()
    return "architecture" in t and any(w in t for w in ("system", "baymax", "this app", "this project", "your", "the app"))
