"""What BAYMAX can do *right now*, computed from the tool registry and live runtime checks (never a fixed list)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .hostos.tools import find_tool


@dataclass
class Capability:
    key: str
    title: str
    example: str
    tools: tuple[str, ...]
    status: str = "available"  # available | unavailable | needs_permission | untested
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


async def capability_report(s: Any) -> list[Capability]:
    llm = await s.llm.status()
    stt = s.stt.status()
    desk = s.desktop.capabilities()
    desktop_ok = bool(desk.get("available"))
    ax = s.desktop.accessibility_trusted() if hasattr(s.desktop, "accessibility_trusted") else None
    images = await s.images.status()
    registered = set(s.tools.names()) if hasattr(s.tools, "names") else {t.name for t in s.tools.all()}

    caps = [
        Capability("chat", "Answer questions and chat", "“explain recursion simply”", ()),
        Capability("voice", "Understand your voice (push-to-talk)", "click the mic and speak", ()),
        Capability("apps", "Open apps and websites", "“open Safari”, “open github.com”", ("apps.open", "apps.open_url")),
        Capability("project", "Open a project in your editor", "“open my BAYMAX repository in VS Code”", ("apps.open_project",)),
        Capability("screen", "Look at, point at and press things on screen", "“look at the Run button”", ("screen.find_element", "screen.point_at", "screen.click_element")),
        Capability("delegate", "Type prompts into ChatGPT, Claude or Gemini (sending only after you confirm)",
                   "“ask ChatGPT to explain recursion”", ("ai.delegate",)),
        Capability("keys", "Press safe shortcuts and list open apps", "“open a new tab in Safari”, “what apps are open?”",
                   ("desktop.shortcut", "desktop.running_apps")),
        Capability("window", "Read the window you're looking at", "“read the current window”", ("desktop.read_window",)),
        Capability("code", "Find code in a project", "“find the function that starts cursor tracking”", ("coding.find",)),
        Capability("errors", "Explain an error you copied", "“explain the error I just copied”", ("coding.explain_error",)),
        Capability("java", "Create a Java class and run its test", "“create a Java class called Invoice and test it”", ("coding.java_class",)),
        Capability("tests", "Run and fix a project's tests", "“run the tests in ~/Developer/BAYMAX/backend”", ("coding.agent",)),
        Capability("research", "Search the web and show real sources", "“search the web for Spring Boot documentation”", ("research.web",)),
        Capability("docs", "Write documents, summaries and diagrams", "“summarize this document”, “diagram the system architecture”",
                   ("documents.write", "documents.summarize", "documents.diagram")),
        Capability("3d", "Create and edit 3D models (GLB)", "“make a red sports car”, then “make it bigger”", ("creation3d.create",)),
        Capability("sim", "Run simulations with charts", "“simulate a pendulum”", ("simulation.run",)),
        Capability("images", "Generate images", "“generate an image of a lighthouse”", ("image.generate",)),
        Capability("memory", "Remember things you ask me to", "“remember that I use Java 21”", ()),
        Capability("safety", "Sleep, wake and Emergency Stop", "“go to sleep”, the STOP button", ()),
    ]
    for c in caps:
        missing = [t for t in c.tools if t not in registered]
        if missing:
            c.status, c.reason = "unavailable", f"not installed ({', '.join(missing)})"
            continue
        if c.key == "chat" and not llm.available:
            c.status, c.reason = "unavailable", llm.message
        elif c.key == "voice" and not stt.get("available"):
            c.status, c.reason = "unavailable", stt.get("message", "speech recognition unavailable")
        elif c.key in ("apps", "project", "screen", "window", "delegate", "keys") and not desktop_ok:
            c.status, c.reason = "unavailable", "desktop control works only on macOS"
        elif c.key in ("screen", "window", "delegate", "keys") and ax is False:
            c.status, c.reason = "needs_permission", "grant Accessibility in System Settings → Privacy & Security"
        elif c.key == "errors" and not llm.available:
            c.status, c.reason = "available", "shows the file and line; explanations need the local model"
        elif c.key == "java" and not find_tool("javac", "/opt/homebrew/opt/openjdk/bin/javac"):
            c.status, c.reason = "unavailable", "no Java compiler (brew install openjdk)"
        elif c.key == "research" and s.settings.research.local_only:
            c.status, c.reason = "unavailable", "local-only mode is on"
        elif c.key == "research":
            c.reason = "needs an internet connection"
        elif c.key == "images" and not images.get("available"):
            c.status, c.reason = "unavailable", "no local image generator configured"
    return caps


def capability_text(caps: list[Capability]) -> str:
    ok = [c for c in caps if c.status == "available"]
    blocked = [c for c in caps if c.status != "available"]
    lines = ["Here's what I can do right now:"]
    lines += [f"• {c.title} — e.g. {c.example}" + (f" ({c.reason})" if c.reason else "") for c in ok]
    if blocked:
        lines.append("Not available at the moment:")
        lines += [f"• {c.title}: {c.reason}" for c in blocked]
    lines.append("Anything that deletes, sends or runs risky commands asks for your approval first.")
    return "\n".join(lines)


def permission_report(s: Any) -> list[dict[str, Any]]:
    """Each macOS permission BAYMAX uses: whether it is granted (when macOS lets us check), what needs it, and the fix.
    Microphone access belongs to the desktop app (Electron asks for it); the iPhone has its own prompts."""
    desk = s.desktop
    ax = desk.accessibility_trusted() if hasattr(desk, "accessibility_trusted") else None
    sr = desk.screen_recording_allowed() if hasattr(desk, "screen_recording_allowed") else None

    def state(v: bool | None) -> str:
        return "unknown" if v is None else ("granted" if v else "missing")

    return [
        {"permission": "Accessibility", "status": state(ax), "neededFor": "reading windows, finding/pressing elements, typing, clicking",
         "settings": "System Settings → Privacy & Security → Accessibility", "fix": "enable BAYMAX (or Terminal, if you start the backend there)"},
        {"permission": "Screen Recording", "status": state(sr), "neededFor": "reading on-screen text (OCR) when an app has no accessibility info",
         "settings": "System Settings → Privacy & Security → Screen & System Audio Recording", "fix": "enable BAYMAX, then restart it"},
        {"permission": "Automation", "status": "unknown", "neededFor": "reading the browser's address bar and controlling specific apps",
         "settings": "System Settings → Privacy & Security → Automation", "fix": "macOS asks the first time; enable each app under BAYMAX"},
        {"permission": "Microphone", "status": "unknown", "neededFor": "push-to-talk on the Mac",
         "settings": "System Settings → Privacy & Security → Microphone", "fix": "enable BAYMAX; the desktop app asks on first use"},
        {"permission": "Local Network (iPhone)", "status": "unknown", "neededFor": "the iPhone companion connecting to this Mac",
         "settings": "iPhone Settings → Privacy & Security → Local Network", "fix": "enable BAYMAX on the iPhone"},
    ]
