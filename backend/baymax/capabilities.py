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
        Capability("screen", "Look at, point at and press things on screen", "“look at the Run button”", ("screen.find_element", "screen.click_element")),
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
        elif c.key in ("apps", "project", "screen", "window") and not desktop_ok:
            c.status, c.reason = "unavailable", "desktop control works only on macOS"
        elif c.key in ("screen", "window") and ax is False:
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
