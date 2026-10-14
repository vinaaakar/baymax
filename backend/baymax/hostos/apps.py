"""Resolve what the user called an application to a real, installed/running macOS app.

Nothing unverified reaches AppleScript: a name must match an app that is actually running or installed
(by display name, process name, bundle name or a well-known alias). Speech-recognition fragments that sound
like the assistant's own name ("EB Max", "Bimax") are recognised as BAYMAX itself, not an app.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# Common spoken/typed names -> the app's real name. Generic aliases only (no per-user list).
ALIASES = {
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "visual studio": "Visual Studio Code", "code": "Visual Studio Code",
    "chrome": "Google Chrome", "google chrome": "Google Chrome", "iterm": "iTerm", "iterm2": "iTerm", "terminal": "Terminal",
    "settings": "System Settings", "system preferences": "System Settings", "preferences": "System Settings",
    "intellij": "IntelliJ IDEA", "idea": "IntelliJ IDEA", "word": "Microsoft Word", "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint", "outlook": "Microsoft Outlook", "teams": "Microsoft Teams", "edge": "Microsoft Edge",
    "brave": "Brave Browser", "zoom": "zoom.us", "android studio": "Android Studio", "finder": "Finder", "files": "Finder",
    "text edit": "TextEdit", "textedit": "TextEdit", "app store": "App Store", "activity monitor": "Activity Monitor",
}
_SELF = re.compile(r"^(hey |hi |ok )?(baymax|bay ?max|bay-max|bimax|bimex|beymax|baymex|beemax|bymax|eb ?max|e ?b ?max|b ?max|bee ?max|"
                   r"bae ?max|may ?max|baymax app|electron)$")


def _norm(s: str) -> str:
    s = s.lower().strip().strip("\"'“”")
    s = re.sub(r"\.app$", "", s)
    s = re.sub(r"\b(the|app|application)\b", " ", s)
    return " ".join(re.findall(r"[a-z0-9+.#]+", s))


@dataclass(frozen=True)
class AppInfo:
    name: str  # display name, e.g. "Visual Studio Code"
    process: str = ""  # System Events / running process name, e.g. "Code"
    bundle_id: str = ""
    running: bool = False

    def keys(self) -> set[str]:
        k = {_norm(self.name)}
        if self.process:
            k.add(_norm(self.process))
        if self.bundle_id:
            k.add(_norm(self.bundle_id.rsplit(".", 1)[-1]))
        return {x for x in k if x}


@dataclass
class AppResolution:
    status: str  # resolved | self | unknown | ambiguous | none
    app: AppInfo | None = None
    suggestions: list[str] = field(default_factory=list)

    def question(self, heard: str) -> str:
        if self.status == "self":
            return f"“{heard}” sounds like my own name rather than an app. Which app do you mean?"
        if self.status == "ambiguous":
            return f"“{heard}” could be {', '.join(self.suggestions[:-1])} or {self.suggestions[-1]}. Which one?"
        hint = f" Did you mean {' or '.join(self.suggestions)}?" if self.suggestions else ""
        return f"I can't find an app called “{heard}” on this Mac.{hint}"


def merge(running: list[AppInfo], installed: list[AppInfo]) -> list[AppInfo]:
    """One entry per app. A running app (process name, e.g. "Code") is linked to its installed bundle (display name,
    e.g. "Visual Studio Code") through the bundle identifier."""
    by_bundle = {a.bundle_id: a for a in installed if a.bundle_id}
    by_name: dict[str, AppInfo] = {}
    for a in installed:
        by_name.setdefault(_norm(a.name), a)
    for r in running:
        inst = by_bundle.get(r.bundle_id) if r.bundle_id else None
        display = inst.name if inst else r.name
        merged = AppInfo(display, r.process or r.name, r.bundle_id, True)
        by_name.pop(_norm(r.name), None)
        by_name[_norm(display)] = merged
    return list(by_name.values())


def resolve_app(heard: str | None, running: list[AppInfo], installed: list[AppInfo]) -> AppResolution:
    if not isinstance(heard, str) or not _norm(heard) or _norm(heard) in ("none", "null", "nil", "undefined", "current", "this", "front"):
        return AppResolution("none")
    q = _norm(heard)
    if _SELF.match(q):
        return AppResolution("self")
    apps = merge(running, installed)
    target = _norm(ALIASES.get(q, heard))
    exact = [a for a in apps if target in a.keys() or q in a.keys()]
    if exact:
        return AppResolution("resolved", sorted(exact, key=lambda a: not a.running)[0])
    if q in ALIASES:  # a known app that simply isn't installed
        return AppResolution("unknown", suggestions=[])
    scored = []
    for a in apps:
        best = max(difflib.SequenceMatcher(None, q, k).ratio() for k in a.keys())
        if len(q) >= 4 and any(k.startswith(q) or q in k for k in a.keys()):
            best = max(best, 0.86)
        scored.append((best, a))
    scored.sort(key=lambda p: (-p[0], not p[1].running))
    strong = [p for p in scored if p[0] >= 0.84]
    if len(strong) == 1 or (len(strong) > 1 and strong[0][0] - strong[1][0] >= 0.08):
        return AppResolution("resolved", strong[0][1])
    if len(strong) > 1:
        return AppResolution("ambiguous", suggestions=[a.name for _, a in strong[:3]])
    return AppResolution("unknown", suggestions=[a.name for s, a in scored[:2] if s >= 0.6])
