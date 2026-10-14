"""Desktop automation backends.

``MacDesktop`` uses only supported macOS mechanisms:

* ``open -a`` / ``open <url>`` to launch apps and URLs (no permission needed).
* AppleScript via ``osascript`` for System Events UI scripting (requires the
  Accessibility permission for the app running BAYMAX). User text is passed as
  ``argv`` to the script, never interpolated into AppleScript source.
* Quartz CGEvents through PyObjC for cursor move / click / scroll (requires
  Accessibility). If PyObjC is not installed these report unavailable.
* ``screencapture`` for screenshots (requires Screen Recording).

Nothing here disables or works around macOS privacy protections. When a
permission is missing the call raises :class:`DesktopPermissionError` with
instructions.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .process import ProcessManager


class DesktopUnavailable(Exception):
    pass


class DesktopPermissionError(DesktopUnavailable):
    pass


_APP_NAME = re.compile(r"^[\w .&'()+-]{1,80}$")
ACCESSIBILITY_HELP = ("BAYMAX needs the Accessibility permission for this. Open System Settings → Privacy & Security → "
                      "Accessibility and enable the BAYMAX app (or Terminal if you started the backend from Terminal).")
SCREEN_HELP = ("BAYMAX needs the Screen Recording permission for this. Open System Settings → Privacy & Security → "
               "Screen & System Audio Recording and enable BAYMAX.")


@dataclass
class UIElement:
    role: str
    name: str
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "name": self.name, "description": self.description}


def validate_app_name(name: str) -> str:
    name = name.strip()
    if not _APP_NAME.match(name):
        raise ValueError("invalid application name")
    return name


class DesktopBackend:
    platform = "unknown"

    def capabilities(self) -> dict[str, Any]:
        return {"platform": self.platform, "available": False}

    async def open_app(self, name: str) -> None: raise DesktopUnavailable(self._why())
    async def is_app_running(self, name: str) -> bool: raise DesktopUnavailable(self._why())
    async def open_url(self, url: str) -> None: raise DesktopUnavailable(self._why())
    async def frontmost_app(self) -> str: raise DesktopUnavailable(self._why())
    async def move_cursor(self, x: float, y: float) -> None: raise DesktopUnavailable(self._why())
    async def cursor_position(self) -> tuple[float, float]: raise DesktopUnavailable(self._why())
    async def click(self, x: float, y: float, button: str = "left", count: int = 1) -> None: raise DesktopUnavailable(self._why())
    async def scroll(self, dx: int, dy: int) -> None: raise DesktopUnavailable(self._why())
    async def type_text(self, app: str, text: str) -> None: raise DesktopUnavailable(self._why())
    async def list_elements(self, app: str, limit: int = 60) -> list[UIElement]: raise DesktopUnavailable(self._why())
    async def press_element(self, app: str, role: str, name: str) -> None: raise DesktopUnavailable(self._why())
    async def read_window_text(self, app: str, limit: int = 4000) -> str: raise DesktopUnavailable(self._why())
    async def screenshot(self, path: Path) -> Path: raise DesktopUnavailable(self._why())

    def _why(self) -> str:
        return f"desktop automation is only implemented for macOS (this host is {sys.platform})"


class UnsupportedDesktop(DesktopBackend):
    def __init__(self) -> None:
        self.platform = sys.platform


_AS_TYPE = """on run argv
  set appName to item 1 of argv
  set theText to item 2 of argv
  tell application appName to activate
  delay 0.3
  tell application "System Events" to keystroke theText
end run"""

_AS_ELEMENTS = """on run argv
  set appName to item 1 of argv
  set maxCount to (item 2 of argv) as integer
  set out to ""
  tell application "System Events"
    tell process appName
      if (count of windows) = 0 then return ""
      set els to entire contents of window 1
      set n to 0
      repeat with e in els
        set n to n + 1
        if n > maxCount then exit repeat
        try
          set r to role of e
          set nm to ""
          try
            set nm to name of e
          end try
          if nm is missing value then set nm to ""
          set d to ""
          try
            set d to description of e
          end try
          if d is missing value then set d to ""
          set out to out & r & tab & nm & tab & d & linefeed
        end try
      end repeat
    end tell
  end tell
  return out
end run"""

_AS_PRESS = """on run argv
  set appName to item 1 of argv
  set roleName to item 2 of argv
  set elName to item 3 of argv
  tell application "System Events"
    tell process appName
      set frontmost to true
      set els to (every UI element of (entire contents of window 1) whose role is roleName and name is elName)
      if (count of els) = 0 then error "element not found" number 1404
      perform action "AXPress" of item 1 of els
    end tell
  end tell
end run"""

_AS_TEXT = """on run argv
  set appName to item 1 of argv
  set out to ""
  tell application "System Events"
    tell process appName
      if (count of windows) = 0 then return ""
      repeat with e in (entire contents of window 1)
        try
          if role of e is in {"AXStaticText", "AXTextField", "AXTextArea"} then
            set v to value of e
            if v is not missing value then set out to out & (v as text) & linefeed
          end if
        end try
      end repeat
    end tell
  end tell
  return out
end run"""


class MacDesktop(DesktopBackend):
    platform = "darwin"

    def __init__(self, processes: ProcessManager) -> None:
        self.p = processes
        self._quartz: Any = None
        try:  # optional dependency
            import Quartz  # type: ignore

            self._quartz = Quartz
        except Exception:
            self._quartz = None

    def capabilities(self) -> dict[str, Any]:
        return {
            "platform": "darwin", "available": True,
            "appleScript": bool(shutil.which("osascript")),
            "pointerEvents": self._quartz is not None,
            "pointerEventsHelp": "" if self._quartz else "pip install pyobjc-framework-Quartz to enable cursor/click/scroll",
            "accessibilityTrusted": self.accessibility_trusted(),
        }

    def accessibility_trusted(self) -> bool | None:
        try:
            from ApplicationServices import AXIsProcessTrusted  # type: ignore

            return bool(AXIsProcessTrusted())
        except Exception:
            return None  # unknown without PyObjC; detected on first scripted call

    async def _osascript(self, script: str, *args: str, timeout: float = 20.0) -> str:
        res = await self.p.run(["osascript", "-e", script, *args], timeout=timeout)
        if res.ok:
            return res.stdout
        err = res.stderr
        if any(code in err for code in ("-1719", "-25211", "not allowed assistive", "-1743")):
            raise DesktopPermissionError(ACCESSIBILITY_HELP)
        if "1404" in err or "element not found" in err:
            raise DesktopUnavailable("the requested interface element was not found (the app may not expose accessibility info)")
        raise DesktopUnavailable(f"AppleScript failed: {err.strip()[-300:]}")

    async def open_app(self, name: str) -> None:
        name = validate_app_name(name)
        res = await self.p.run(["open", "-a", name], timeout=20)
        if not res.ok:
            raise DesktopUnavailable(f"could not open '{name}': {res.stderr.strip()[-200:] or 'application not found'}")

    async def is_app_running(self, name: str) -> bool:
        name = validate_app_name(name)
        out = await self._osascript('on run argv\nreturn application (item 1 of argv) is running\nend run', name)
        return out.strip() == "true"

    async def open_url(self, url: str) -> None:
        if not re.match(r"^https?://[^\s]+$", url):
            raise ValueError("only http(s) URLs can be opened")
        res = await self.p.run(["open", url], timeout=20)
        if not res.ok:
            raise DesktopUnavailable(f"could not open URL: {res.stderr.strip()[-200:]}")

    async def frontmost_app(self) -> str:
        out = await self._osascript('tell application "System Events" to get name of first process whose frontmost is true')
        return out.strip()

    def _require_quartz(self) -> Any:
        if self._quartz is None:
            raise DesktopUnavailable("pointer control needs PyObjC: pip install pyobjc-framework-Quartz")
        if self.accessibility_trusted() is False:
            raise DesktopPermissionError(ACCESSIBILITY_HELP)
        return self._quartz

    async def cursor_position(self) -> tuple[float, float]:
        Q = self._require_quartz()
        loc = Q.CGEventGetLocation(Q.CGEventCreate(None))
        return float(loc.x), float(loc.y)

    async def move_cursor(self, x: float, y: float) -> None:
        Q = self._require_quartz()
        ev = Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, (x, y), Q.kCGMouseButtonLeft)
        Q.CGEventPost(Q.kCGHIDEventTap, ev)

    async def click(self, x: float, y: float, button: str = "left", count: int = 1) -> None:
        Q = self._require_quartz()
        if button == "right":
            down, up, btn = Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGMouseButtonRight
        else:
            down, up, btn = Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGMouseButtonLeft
        for i in range(max(1, min(count, 3))):
            for kind in (down, up):
                ev = Q.CGEventCreateMouseEvent(None, kind, (x, y), btn)
                Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, i + 1)
                Q.CGEventPost(Q.kCGHIDEventTap, ev)
            await asyncio.sleep(0.05)

    async def scroll(self, dx: int, dy: int) -> None:
        Q = self._require_quartz()
        ev = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 2, int(dy), int(dx))
        Q.CGEventPost(Q.kCGHIDEventTap, ev)

    async def type_text(self, app: str, text: str) -> None:
        app = validate_app_name(app)
        await self._osascript(_AS_TYPE, app, text[:2000], timeout=30)

    async def list_elements(self, app: str, limit: int = 60) -> list[UIElement]:
        app = validate_app_name(app)
        out = await self._osascript(_AS_ELEMENTS, app, str(max(1, min(limit, 300))), timeout=30)
        elements = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                elements.append(UIElement(parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
        return elements

    async def press_element(self, app: str, role: str, name: str) -> None:
        app = validate_app_name(app)
        await self._osascript(_AS_PRESS, app, role[:40], name[:120], timeout=30)

    async def read_window_text(self, app: str, limit: int = 4000) -> str:
        app = validate_app_name(app)
        return (await self._osascript(_AS_TEXT, app, timeout=30))[:limit]

    async def screenshot(self, path: Path) -> Path:
        res = await self.p.run(["screencapture", "-x", str(path)], timeout=20)
        if not res.ok or not path.exists() or path.stat().st_size == 0:
            raise DesktopPermissionError(SCREEN_HELP)
        return path


def make_desktop(processes: ProcessManager) -> DesktopBackend:
    if sys.platform == "darwin":
        return MacDesktop(processes)
    return UnsupportedDesktop()
