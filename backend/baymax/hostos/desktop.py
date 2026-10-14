"""Desktop automation backends.

``MacDesktop`` uses only supported macOS mechanisms:

* ``open -a`` / ``open <url>`` to launch apps and URLs (no permission needed).
* AppleScript via ``osascript`` for System Events UI scripting (requires the
  Accessibility permission for the app running BAYMAX). User text is passed as
  ``argv`` to the script, never interpolated into AppleScript source.
* Quartz CGEvents through PyObjC for cursor move / click / scroll (requires
  Accessibility). If PyObjC is not installed these report unavailable.
* ``screencapture`` for screenshots and Apple Vision OCR for screen text (require
  Screen Recording); CoreGraphics for display geometry and window owners.

Nothing here disables or works around macOS privacy protections. When a
permission is missing the call raises :class:`DesktopPermissionError` with
instructions.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .apps import AppInfo
from ..screen.elements import Display, ScreenElement, parse_ax_dump, vision_box_to_global
from .process import ProcessManager

log = logging.getLogger(__name__)


class DesktopUnavailable(Exception):
    def __init__(self, message: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail  # raw macOS error text (for logs and diagnostics; never shown as the chat reply)


class DesktopPermissionError(DesktopUnavailable):
    pass


_APP_NAME = re.compile(r"^[\w .&'()+-]{1,80}$")
ACCESSIBILITY_HELP = ("BAYMAX needs the Accessibility permission for this. Open System Settings → Privacy & Security → "
                      "Accessibility and enable the BAYMAX app (or Terminal if you started the backend from Terminal).")
AUTOMATION_HELP = ("macOS isn't letting BAYMAX control {app}. Open System Settings → Privacy & Security → Automation, find BAYMAX "
                   "(or Terminal if you started the backend from Terminal) and enable {app}.")
SCREEN_HELP = ("BAYMAX needs the Screen Recording permission for this. Open System Settings → Privacy & Security → "
               "Screen & System Audio Recording and enable BAYMAX.")


@dataclass
class UIElement:
    role: str
    name: str
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "name": self.name, "description": self.description}


_PLACEHOLDER_APPS = {"none", "null", "nil", "undefined", "n/a", "na", "unknown", "app", "application", "current", "frontmost",
                     "front", "active", "this", "that", "it", "window", "screen", "the app", "current app", "current window"}


class AppNameError(ValueError):
    pass


def validate_app_name(name: Any) -> str:
    """A real application name, or AppNameError. Never lets None / "None" / placeholders reach AppleScript."""
    if not isinstance(name, str):
        raise AppNameError("no application was specified")
    name = name.strip().strip("\"'“”")
    if not name or name.lower() in _PLACEHOLDER_APPS:
        raise AppNameError("no application was specified")
    if not _APP_NAME.match(name):
        raise AppNameError(f"“{name[:40]}” is not a valid application name")
    return name


@dataclass
class ActiveWindow:
    """The window the user is working in (never BAYMAX's own). Frame in global points."""
    app: str
    title: str | None  # None when the title could not be read (reason in title_note)
    x: float
    y: float
    width: float
    height: float
    title_note: str = ""


class InputCancelled(DesktopUnavailable):
    """Typing or pointer input was stopped part-way (Emergency Stop / task cancel). Never retried."""


class DesktopBackend:
    platform = "unknown"
    last_ax_window: dict[str, Any] | None = None  # set by accessibility_elements(): the window that was inspected
    typing_interval_s = 0.012  # pause between typed chunks (≤10 characters each); configurable cadence
    _input_gen = 0  # bumped by cancel_input(); in-flight typing/clicks check it between events

    def cancel_input(self) -> None:
        """Stop any typing or clicking in progress at the next event boundary (called by Emergency Stop; never
        depends on the model). Key and button events are always posted as down/up pairs, so nothing is left held."""
        self._input_gen += 1

    def _check_input(self, gen: int) -> None:
        if self._input_gen != gen:
            raise InputCancelled("input was stopped before it finished")

    def capabilities(self) -> dict[str, Any]:
        return {"platform": self.platform, "available": False}

    async def open_app(self, name: str) -> None: raise DesktopUnavailable(self._why())
    async def is_app_running(self, name: str) -> bool: raise DesktopUnavailable(self._why())
    async def open_url(self, url: str, app: str | None = None) -> None: raise DesktopUnavailable(self._why())
    async def browser_url(self, app: str) -> str | None: raise DesktopUnavailable(self._why())
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
    async def list_apps(self) -> tuple[list[AppInfo], list[AppInfo]]: raise DesktopUnavailable(self._why())
    async def open_path_in_app(self, app: str, path: Path) -> None: raise DesktopUnavailable(self._why())
    async def window_titles(self, app: str) -> list[str]: raise DesktopUnavailable(self._why())
    async def clipboard_text(self) -> str: raise DesktopUnavailable(self._why())
    async def front_document(self) -> Path | None: raise DesktopUnavailable(self._why())
    # Screen understanding (baymax.screen): real geometry only, never guessed.
    async def displays(self) -> list[Display]: raise DesktopUnavailable(self._why())
    async def target_app(self) -> str: raise DesktopUnavailable(self._why())
    async def active_window(self) -> "ActiveWindow": raise DesktopUnavailable(self._why())
    async def accessibility_elements(self, app: str, limit: int = 400) -> list[ScreenElement]: raise DesktopUnavailable(self._why())
    async def focus_element(self, app: str, role: str, x: float, y: float) -> None: raise DesktopUnavailable(self._why())
    async def press_return(self, app: str) -> None: raise DesktopUnavailable(self._why())
    async def press_keys(self, app: str, key: str, modifiers: tuple[str, ...] = ()) -> None: raise DesktopUnavailable(self._why())
    async def element_value(self, app: str, role: str, x: float, y: float) -> str: raise DesktopUnavailable(self._why())
    async def ocr_elements(self) -> list[ScreenElement]: raise DesktopUnavailable(self._why())

    def _why(self) -> str:
        return f"desktop automation is only implemented for macOS (this host is {sys.platform})"


class UnsupportedDesktop(DesktopBackend):
    def __init__(self) -> None:
        self.platform = sys.platform


def parse_window_header(dump: str) -> dict[str, Any] | None:
    """The `#window<TAB>count<TAB>how<TAB>name<TAB>subrole` header of an _AS_LOCATE dump: how many windows System Events
    could see, how one was chosen (main / focused / first / none), and its title and subrole."""
    for line in dump.splitlines()[:1]:
        parts = line.split("\t")
        if parts and parts[0] == "#window" and len(parts) >= 5:
            try:
                count = int(parts[1])
            except ValueError:
                count = -1
            return {"windows": count, "chosen": parts[2], "name": parts[3], "subrole": parts[4].strip()}
    return None


def keystroke_plan(text: str, chunk: int = 10) -> list[tuple[str, str]]:
    """How text is typed: ("text", ≤chunk characters) events, with line breaks as ("newline", "") — sent as Shift+Return,
    which inserts a line break in chat boxes and editors instead of submitting. Unicode is passed through unchanged."""
    out: list[tuple[str, str]] = []
    for i, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        if i:
            out.append(("newline", ""))
        for j in range(0, len(line), chunk):
            out.append(("text", line[j:j + chunk]))
    return out


# Keys and modifiers BAYMAX may press (shortcuts are further restricted by the shortcut tool's allowlist).
KEY_CODES = {"return": 36, "tab": 48, "escape": 53, "delete": 51, "left": 123, "right": 124, "down": 125, "up": 126, "space": 49}
MODIFIERS = ("command", "shift", "option", "control")

_AS_KEYS = """on run argv
  set appName to item 1 of argv
  set k to item 2 of argv
  set mods to {}
  repeat with i from 3 to count of argv
    set m to item i of argv
    if m is "command" then set end of mods to command down
    if m is "shift" then set end of mods to shift down
    if m is "option" then set end of mods to option down
    if m is "control" then set end of mods to control down
  end repeat
  tell application "System Events"
    tell process appName to set frontmost to true
    delay 0.2
    if (length of k) > 1 then
      key code (k as integer) using mods
    else
      keystroke k using mods
    end if
  end tell
end run"""

# Fallback typing (no Quartz): each line typed, Shift+Return between lines.
_AS_TYPE_LINES = """on run argv
  set appName to item 1 of argv
  tell application "System Events"
    tell process appName to set frontmost to true
    delay 0.2
    repeat with i from 2 to count of argv
      keystroke (item i of argv)
      if i < (count of argv) then keystroke return using shift down
    end repeat
  end tell
end run"""

_AS_ACTIVATE = """on run argv
  set appName to item 1 of argv
  -- Activate like `tell application "TextEdit" to activate`: unlike System Events' frontmost flag, this also switches to
  -- the desktop Space holding the app's windows (Accessibility only sees windows on the current Space).
  try
    tell application "System Events" to set bid to bundle identifier of process appName
    tell application id bid to activate
  end try
  tell application "System Events" to tell process appName to set frontmost to true
end run"""

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

# Front window + menu bar of a process with each element's frame (global points). One line per element:
# role, name, description, value (static text only), x, y, width, height — tab separated.
_AS_LOCATE = """on run argv
  set appName to item 1 of argv
  set maxCount to (item 2 of argv) as integer
  set out to ""
  set n to 0
  tell application "System Events"
    tell process appName
      set els to {}
      set chosenWin to my pickWindow(appName)
      set w to item 1 of chosenWin
      set wName to ""
      set wSub to ""
      if w is not missing value then
        try
          set wName to my oneLine(name of w as text)
        end try
        try
          set wSub to subrole of w as text
        end try
        set els to entire contents of w
      end if
      -- header (fewer than 8 columns, so element parsers skip it): which window was read
      set out to "#window" & tab & (item 3 of chosenWin) & tab & (item 2 of chosenWin) & tab & wName & tab & wSub & linefeed
      try
        set els to els & (menu bar items of menu bar 1)
      end try
      repeat with e in els
        if n ≥ maxCount then exit repeat
        try
          set r to role of e
          set p to position of e
          set z to size of e
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
          if d is "" and r is in {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"} then
            try
              set d to value of attribute "AXPlaceholderValue" of e
              if d is missing value then set d to ""
            end try
          end if
          set v to ""
          if r is in {"AXStaticText", "AXLink", "AXCell", "AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"} then
            try
              set v to value of e
              if v is missing value then set v to ""
              set v to my oneLine(v as text)
            end try
          end if
          set en to ""
          set ident to ""
          if r is in {"AXButton", "AXTextField", "AXTextArea", "AXComboBox", "AXSearchField", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXLink", "AXTab", "AXMenuItem"} then
            try
              if enabled of e then
                set en to "1"
              else
                set en to "0"
              end if
            end try
            try
              set ident to value of attribute "AXIdentifier" of e
              if ident is missing value then set ident to ""
              set ident to my oneLine(ident as text)
            end try
          end if
          set out to out & r & tab & nm & tab & d & tab & v & tab & (item 1 of p) & tab & (item 2 of p) & tab & (item 1 of z) & tab & (item 2 of z) & tab & en & tab & ident & linefeed
          set n to n + 1
        end try
      end repeat
    end tell
  end tell
  return out
end run

on oneLine(t)
  set AppleScript's text item delimiters to {tab, return, linefeed}
  set parts to text items of t
  set AppleScript's text item delimiters to " "
  set t to parts as text
  set AppleScript's text item delimiters to ""
  if length of t > 500 then set t to text 1 thru 500 of t
  return t
end oneLine

-- Which window to inspect: the app's main window (its document window; panels such as the Open dialog are never
-- main), else its focused window, else window 1. Locate, focus and read-back all use this, so they look at the same
-- window. Returns {window or missing value, how it was chosen, number of windows System Events can see}.
on pickWindow(appName)
  tell application "System Events"
    tell process appName
      set n to count of windows
      if n = 0 then return {missing value, "none", 0}
      repeat with i from 1 to n
        try
          if (value of attribute "AXMain" of window i) is true then return {window i, "main", n}
        end try
      end repeat
      try
        set fw to value of attribute "AXFocusedWindow"
        if fw is not missing value then return {fw, "focused", n}
      end try
      return {window 1, "first", n}
    end tell
  end tell
end pickWindow"""

# Focus the element with this role at this position (from the accessibility dump), so typing goes into it.
_AS_FOCUS = """on run argv
  set appName to item 1 of argv
  set roleName to item 2 of argv
  set tx to (item 3 of argv) as number
  set ty to (item 4 of argv) as number
  -- Activate like `tell application "TextEdit" to activate`: unlike System Events' frontmost flag, this also switches to
  -- the desktop Space holding the app's windows (Accessibility only sees windows on the current Space).
  try
    tell application "System Events" to set bid to bundle identifier of process appName
    tell application id bid to activate
  end try
  tell application "System Events"
    tell process appName
      set frontmost to true
      set w to missing value
      repeat 15 times -- up to ~3 s for the window to become visible to Accessibility after activation
        set w to item 1 of my pickWindow(appName)
        if w is not missing value then exit repeat
        delay 0.2
      end repeat
      if w is missing value then error "no window" number 1406
      try
        perform action "AXRaise" of w
      end try
      delay 0.1
      -- "entire contents" is a plain list: it can't be filtered with "whose" (error -1700), so check each element.
      -- 1) find the element (errors while scanning other elements are skipped; nothing after the match is swallowed)
      set hit to missing value
      set nearest to ""
      set bestD to 1.0E+9
      -- fetch the list once: iterating the specifier itself makes every item an unresolvable reference
      set els to entire contents of w
      repeat with e in els
        try
          if role of e is roleName then
            set p to position of e
            set dd to ((item 1 of p) - tx) ^ 2 + ((item 2 of p) - ty) ^ 2
            if dd < bestD then
              set bestD to dd
              set nearest to ((item 1 of p) as text) & "," & ((item 2 of p) as text)
            end if
            if dd < 36 then
              set hit to contents of e
              exit repeat
            end if
          end if
        end try
      end repeat
      if hit is missing value then
        if nearest is "" then error "element not found: no " & roleName & " in the window" number 1404
        error "element not found: nearest " & roleName & " is at " & nearest number 1404
      end if
      -- 2) give it keyboard focus and confirm; report macOS's own error if it refuses
      set setErr to ""
      try
        set focused of hit to true
      on error errMsg number errNum
        set setErr to (errNum as text) & " " & errMsg
      end try
      delay 0.15
      set wasFocused to false
      try
        set wasFocused to (focused of hit)
      end try
      if wasFocused is true then return "ok"
      return "unfocused" & tab & setErr
    end tell
  end tell
end run

-- Which window to inspect: the app's main window (its document window; panels such as the Open dialog are never
-- main), else its focused window, else window 1. Locate, focus and read-back all use this, so they look at the same
-- window. Returns {window or missing value, how it was chosen, number of windows System Events can see}.
on pickWindow(appName)
  tell application "System Events"
    tell process appName
      set n to count of windows
      if n = 0 then return {missing value, "none", 0}
      repeat with i from 1 to n
        try
          if (value of attribute "AXMain" of window i) is true then return {window i, "main", n}
        end try
      end repeat
      try
        set fw to value of attribute "AXFocusedWindow"
        if fw is not missing value then return {fw, "focused", n}
      end try
      return {window 1, "first", n}
    end tell
  end tell
end pickWindow"""

# Exact text of one element (line breaks preserved) — used to verify what was typed.
_AS_VALUE = """on run argv
  set appName to item 1 of argv
  set roleName to item 2 of argv
  set tx to (item 3 of argv) as number
  set ty to (item 4 of argv) as number
  -- Activate like `tell application "TextEdit" to activate`: unlike System Events' frontmost flag, this also switches to
  -- the desktop Space holding the app's windows (Accessibility only sees windows on the current Space).
  try
    tell application "System Events" to set bid to bundle identifier of process appName
    tell application id bid to activate
  end try
  tell application "System Events"
    tell process appName
      set w to missing value
      repeat 15 times -- up to ~3 s for the window to become visible to Accessibility after activation
        set w to item 1 of my pickWindow(appName)
        if w is not missing value then exit repeat
        delay 0.2
      end repeat
      if w is missing value then error "no window" number 1406
      -- fetch the list once: iterating the specifier itself makes every item an unresolvable reference
      set els to entire contents of w
      repeat with e in els
        try
          if role of e is roleName then
            set p to position of e
            if (((item 1 of p) - tx) ^ 2 + ((item 2 of p) - ty) ^ 2) < 36 then
              set v to value of e
              if v is missing value then return ""
              return v as text
            end if
          end if
        end try
      end repeat
    end tell
  end tell
  error "element not found" number 1404
end run

-- Which window to inspect: the app's main window (its document window; panels such as the Open dialog are never
-- main), else its focused window, else window 1. Locate, focus and read-back all use this, so they look at the same
-- window. Returns {window or missing value, how it was chosen, number of windows System Events can see}.
on pickWindow(appName)
  tell application "System Events"
    tell process appName
      set n to count of windows
      if n = 0 then return {missing value, "none", 0}
      repeat with i from 1 to n
        try
          if (value of attribute "AXMain" of window i) is true then return {window i, "main", n}
        end try
      end repeat
      try
        set fw to value of attribute "AXFocusedWindow"
        if fw is not missing value then return {fw, "focused", n}
      end try
      return {window 1, "first", n}
    end tell
  end tell
end pickWindow"""

_AS_RETURN = """on run argv
  set appName to item 1 of argv
  tell application "System Events"
    tell process appName to set frontmost to true
    delay 0.2
    key code 36
  end tell
end run"""

# Window titles of an app; matches the process by name or displayed name ("Visual Studio Code" runs as "Code").
_AS_TITLES = """on run argv
  set appName to item 1 of argv
  set alt to item 2 of argv
  set out to ""
  tell application "System Events"
    set ps to (every process whose name is appName or name is alt or displayed name is appName)
    if (count of ps) = 0 then return ""
    repeat with w in (windows of item 1 of ps)
      try
        set out to out & (name of w) & linefeed
      end try
    end repeat
  end tell
  return out
end run"""

# File behind the front window of the app the user is looking at (AXDocument is a file:// URL).
_AS_DOCUMENT = """on run argv
  set appName to item 1 of argv
  tell application "System Events"
    tell process appName
      if (count of windows) = 0 then return ""
      try
        return value of attribute "AXDocument" of window 1
      on error
        return ""
      end try
    end tell
  end tell
end run"""

_AS_WINDOW = """on run argv
  tell application "System Events"
    tell process (item 1 of argv)
      if (count of windows) = 0 then return ""
      set w to window 1
      set p to position of w
      set z to size of w
      set t to ""
      try
        set t to name of w
      end try
      if t is missing value then set t to ""
      return t & tab & (item 1 of p) & tab & (item 2 of p) & tab & (item 1 of z) & tab & (item 2 of z)
    end tell
  end tell
end run"""

_AS_PROCESS = """on run argv
  set appName to item 1 of argv
  set alt to item 2 of argv
  tell application "System Events"
    set ps to (every process whose name is appName or name is alt or displayed name is appName)
    if (count of ps) = 0 then return ""
    return name of item 1 of ps
  end tell
end run"""

PROCESS_ALIASES = {"Visual Studio Code": "Code", "Google Chrome": "Google Chrome", "IntelliJ IDEA": "idea"}

_AS_PRESS = """on run argv
  set appName to item 1 of argv
  set roleName to item 2 of argv
  set elName to item 3 of argv
  tell application "System Events"
    tell process appName
      set frontmost to true
      -- iterate: "whose" can't filter the "entire contents" list (error -1700)
      -- fetch the list once: iterating the specifier itself makes every item an unresolvable reference
      set els to entire contents of window 1
      repeat with e in els
        try
          if role of e is roleName then
            set nm to ""
            try
              set nm to name of e
            end try
            set ds to ""
            try
              set ds to description of e
            end try
            set vl to ""
            try
              set vl to value of e as text
            end try
            if nm is elName or ds is elName or vl is elName then
              perform action "AXPress" of e
              return "ok"
            end if
          end if
        end try
      end repeat
      error "element not found" number 1404
    end tell
  end tell
end run"""

_AS_TEXT = """on run argv
  set appName to item 1 of argv
  set out to ""
  tell application "System Events"
    tell process appName
      if (count of windows) = 0 then return ""
      -- fetch the list once: iterating the specifier itself makes every item an unresolvable reference
      set els to entire contents of window 1
      repeat with e in els
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
        if getattr(res, "timed_out", False):
            raise DesktopUnavailable(f"macOS didn't respond within {timeout:.0f} seconds (the app may be busy or showing a dialog)")
        err = res.stderr
        try:
            if "syntax error" in err.lower() or re.search(r"\(-27\d\d\)", err):  # the script itself didn't compile
                log.error("BAYMAX AppleScript did not compile: %s", err.strip()[-500:])
                raise DesktopUnavailable("an internal macOS script failed to compile (details are in the BAYMAX log)")
            if re.search(r"\(1406\)", err) or "no window" in err:
                log.info("AppleScript reported no window: %s", err.strip()[-300:])
                who = args[0] if args else "that app"
                raise DesktopUnavailable(f"{who} has no window I can see on this desktop (its windows may be on another desktop "
                                         "Space or in full screen)")
            # Diagnose the API that actually failed: -1743 is Automation (Apple Events to one app), -1719/-25211 are Accessibility.
            if "-1743" in err or "not authorized to send apple events" in err.lower():
                target = args[0] if args else "that app"
                raise DesktopPermissionError(AUTOMATION_HELP.format(app=target))
            if any(code in err for code in ("-1719", "-25211", "not allowed assistive")):
                raise DesktopPermissionError(ACCESSIBILITY_HELP)
            if "-1728" in err and "process" in err:
                m = re.search(r'process "([^"]*)"', err)
                raise DesktopUnavailable(f"{m.group(1) if m else 'that app'} isn't running (or has no window open)")
            if re.search(r"\(1404\)", err) or "element not found" in err:
                raise DesktopUnavailable("the requested interface element was not found (the app may not expose accessibility info)")
            log.warning("AppleScript failed: %s", err.strip()[-500:])  # full detail for developers, not the chat
            raise DesktopUnavailable("macOS couldn't complete that step (details are in the BAYMAX log)")
        except DesktopUnavailable as exc:
            exc.detail = exc.detail or err.strip()[-400:]
            raise
        raise AssertionError("unreachable")

    async def open_app(self, name: str) -> None:
        name = validate_app_name(name)
        res = await self.p.run(["open", "-a", name], timeout=20)
        if not res.ok:
            raise DesktopUnavailable(f"could not open '{name}': {res.stderr.strip()[-200:] or 'application not found'}")

    async def is_app_running(self, name: str) -> bool:
        name = validate_app_name(name)
        out = await self._osascript('on run argv\nreturn application (item 1 of argv) is running\nend run', name)
        return out.strip() == "true"

    async def open_url(self, url: str, app: str | None = None) -> None:
        if not re.match(r"^https?://[^\s]+$", url):
            raise ValueError("only http(s) URLs can be opened")
        argv = ["open", "-a", validate_app_name(app), url] if app else ["open", url]  # argv list: no shell, no injection
        res = await self.p.run(argv, timeout=20)
        if not res.ok:
            raise DesktopUnavailable(f"macOS could not open the page{' in ' + app if app else ''}")

    async def browser_url(self, app: str) -> str | None:
        """URL of the front tab, to verify navigation (needs the Automation permission for that browser)."""
        app = validate_app_name(app)
        script = ('on run argv\n tell application (item 1 of argv)\n if (count of windows) = 0 then return ""\n'
                  + (' return URL of current tab of front window\n' if app == "Safari" else ' return URL of active tab of front window\n')
                  + ' end tell\nend run')
        try:
            out = (await self._osascript(script, app, timeout=8)).strip()
        except DesktopUnavailable:
            return None
        return out or None

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
        gen = self._input_gen
        for i in range(max(1, min(count, 3))):
            self._check_input(gen)
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
        """Type into the focused element of `app` (the caller focuses and verifies the field). Unicode-safe via
        CGEventKeyboardSetUnicodeString; line breaks are Shift+Return so chat boxes are not submitted early."""
        app = validate_app_name(app)
        text = text[:4000]
        gen = self._input_gen  # a stop pressed from here on (even during activation) prevents/aborts the typing
        Q = self._quartz
        if Q is None or not hasattr(Q, "CGEventKeyboardSetUnicodeString"):
            self._check_input(gen)  # (the osascript child itself is killed by Emergency Stop)
            await self._osascript(_AS_TYPE_LINES, app, *text.replace("\r\n", "\n").split("\n"), timeout=60)
            return
        await self._osascript(_AS_ACTIVATE, app, timeout=10)
        await asyncio.sleep(0.2)
        if self.accessibility_trusted() is False:
            raise DesktopPermissionError(ACCESSIBILITY_HELP)
        for kind, chunk in keystroke_plan(text):
            self._check_input(gen)  # Emergency Stop between chunks: stops mid-text, no key left down
            if kind == "newline":
                for down in (True, False):
                    ev = Q.CGEventCreateKeyboardEvent(None, KEY_CODES["return"], down)
                    Q.CGEventSetFlags(ev, Q.kCGEventFlagMaskShift)
                    Q.CGEventPost(Q.kCGHIDEventTap, ev)
            else:
                for down in (True, False):
                    ev = Q.CGEventCreateKeyboardEvent(None, 0, down)
                    Q.CGEventKeyboardSetUnicodeString(ev, len(chunk.encode("utf-16-le")) // 2, chunk)
                    Q.CGEventPost(Q.kCGHIDEventTap, ev)
            await asyncio.sleep(self.typing_interval_s)

    async def press_keys(self, app: str, key: str, modifiers: tuple[str, ...] = ()) -> None:
        app = validate_app_name(app)
        key = key.lower()
        if not (key in KEY_CODES or re.fullmatch(r"[a-z0-9,.\[\]/=-]", key)):
            raise DesktopUnavailable(f"key “{key}” is not supported")
        if any(m not in MODIFIERS for m in modifiers):
            raise DesktopUnavailable("unsupported modifier")
        await self._osascript(_AS_KEYS, app, str(KEY_CODES[key]) if key in KEY_CODES else key, *modifiers, timeout=15)

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

    async def process_name(self, app: str) -> str:
        """System Events process name for an app name ("Visual Studio Code" -> "Code"); DesktopUnavailable if not running."""
        app = validate_app_name(app)
        out = (await self._osascript(_AS_PROCESS, app, PROCESS_ALIASES.get(app, app), timeout=10)).strip()
        if not out:
            raise DesktopUnavailable(f"{app} isn't running")
        return out

    async def read_window_text(self, app: str, limit: int = 4000) -> str:
        proc = await self.process_name(app)
        return (await self._osascript(_AS_TEXT, proc, timeout=30))[:limit]

    async def list_apps(self) -> tuple[list[AppInfo], list[AppInfo]]:
        """(running regular apps, installed apps). Running apps come from NSWorkspace (no permission needed)."""
        running: list[AppInfo] = []
        try:
            from AppKit import NSWorkspace  # type: ignore  # pyobjc-framework-Cocoa (installed with Quartz)

            for a in NSWorkspace.sharedWorkspace().runningApplications():
                if int(a.activationPolicy()) == 0 and a.localizedName():  # NSApplicationActivationPolicyRegular
                    name = str(a.localizedName())
                    running.append(AppInfo(name, name, str(a.bundleIdentifier() or ""), True))
        except Exception:
            out = await self._osascript('tell application "System Events" to get name of every process whose background only is false',
                                        timeout=10)
            running = [AppInfo(n.strip(), n.strip(), "", True) for n in out.split(",") if n.strip()]
        installed = await asyncio.get_running_loop().run_in_executor(None, scan_installed_apps)
        return [r for r in running if r.name not in _SELF_APPS], installed

    async def open_path_in_app(self, app: str, path: Path) -> None:
        app = validate_app_name(app)
        res = await self.p.run(["open", "-a", app, str(path)], timeout=20)
        if not res.ok:
            raise DesktopUnavailable(f"could not open {path.name} in {app}: {res.stderr.strip()[-200:] or 'application not found'}")

    async def window_titles(self, app: str) -> list[str]:
        app = validate_app_name(app)
        out = await self._osascript(_AS_TITLES, app, PROCESS_ALIASES.get(app, app), timeout=15)
        return [line.strip() for line in out.splitlines() if line.strip()]

    async def clipboard_text(self) -> str:
        res = await self.p.run(["pbpaste"], timeout=5)
        return res.stdout if res.ok else ""

    async def front_document(self) -> Path | None:
        from urllib.parse import unquote, urlparse

        app = await self.target_app()
        url = (await self._osascript(_AS_DOCUMENT, validate_app_name(app), timeout=10)).strip()
        if not url.startswith("file://"):
            return None
        return Path(unquote(urlparse(url).path))

    async def displays(self) -> list[Display]:
        if self._quartz is None:
            raise DesktopUnavailable("display geometry needs PyObjC: pip install 'baymax-backend[macos]'")
        return _quartz_displays(self._quartz)

    async def target_app(self) -> str:
        """The app the user is looking at: the front-most normal window that isn't BAYMAX itself
        (talking to BAYMAX focuses its own window). Window owner names need no extra permission."""
        Q = self._quartz
        if Q is not None:
            info = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements, Q.kCGNullWindowID) or []
            for w in info:
                owner = str(w.get("kCGWindowOwnerName") or "")
                if int(w.get("kCGWindowLayer", 1)) == 0 and owner and owner not in _SELF_APPS:
                    return owner
        front = (await self.frontmost_app()).strip()
        if not front or front in _SELF_APPS:
            raise DesktopUnavailable("I couldn't tell which app you mean (BAYMAX itself is in front). Click the window first, or name the app.")
        return front

    async def active_window(self) -> ActiveWindow:
        """Front-most normal window that isn't BAYMAX. App and frame come from CoreGraphics (no permission needed);
        the title needs Screen Recording (CoreGraphics) or Accessibility (System Events)."""
        Q = self._quartz
        if Q is not None:
            info = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements, Q.kCGNullWindowID) or []
            for w in info:
                owner = str(w.get("kCGWindowOwnerName") or "")
                if int(w.get("kCGWindowLayer", 1)) != 0 or not owner or owner in _SELF_APPS:
                    continue
                b = w.get("kCGWindowBounds") or {}
                win = ActiveWindow(owner, None, float(b.get("X", 0)), float(b.get("Y", 0)), float(b.get("Width", 0)), float(b.get("Height", 0)))
                title = str(w.get("kCGWindowName") or "")
                if title:
                    win.title = title
                    return win
                try:  # no Screen Recording: ask Accessibility for the title instead
                    proc = await self.process_name(owner)
                    parts = (await self._osascript(_AS_WINDOW, proc, timeout=10)).strip().split("\t")
                    win.title = parts[0] if parts and parts[0] else ""
                except DesktopPermissionError:
                    win.title_note = "reading window titles needs the Accessibility permission (or Screen Recording)"
                except DesktopUnavailable as exc:
                    win.title_note = str(exc)
                return win
        app = await self.target_app()
        proc = await self.process_name(app)
        out = (await self._osascript(_AS_WINDOW, proc, timeout=10)).strip()
        if not out:
            raise DesktopUnavailable(f"{app} has no open window")
        t, x, y, w_, h = (out.replace(",", ".").split("\t") + ["0"] * 5)[:5]
        return ActiveWindow(app, t, float(x), float(y), float(w_), float(h))

    async def accessibility_elements(self, app: str, limit: int = 400) -> list[ScreenElement]:
        app = validate_app_name(app)
        out = await self._osascript(_AS_LOCATE, app, str(max(1, min(limit, 1500))), timeout=25)
        self.last_ax_window = parse_window_header(out)  # which window was read (diagnostics)
        return parse_ax_dump(out.replace(",", "."), app)  # some locales print decimal commas

    async def focus_element(self, app: str, role: str, x: float, y: float) -> None:
        app = validate_app_name(app)
        if role not in ("AXTextArea", "AXTextField", "AXComboBox", "AXSearchField"):
            raise DesktopUnavailable("only text inputs can be focused for typing")
        argv = (app, role, str(round(x)), str(round(y)))
        out = (await self._osascript(_AS_FOCUS, *argv, timeout=20)).strip()
        if out == "ok":
            return
        why = out.partition("\t")[2].strip()
        # The field exists at this exact position in the app's main window, but setting AXFocused was refused (some
        # text views don't accept it). Fall back to one plain click just inside that same verified field, then confirm
        # focus again. Never type unless macOS reports the field focused.
        if getattr(self, "_quartz", None) is not None and await self._on_a_display(x + 4, y + 4):
            await self.click(x + 4, y + 4)
            await asyncio.sleep(0.15)
            out = (await self._osascript(_AS_FOCUS, *argv, timeout=20)).strip()
            if out == "ok":
                return
            why = out.partition("\t")[2].strip() or why
        raise DesktopUnavailable(f"{app} didn't let me put the cursor in that field", detail=why)

    async def _on_a_display(self, x: float, y: float) -> bool:
        try:
            return any(d.contains(x, y) for d in await self.displays())
        except DesktopUnavailable:
            return False

    async def element_value(self, app: str, role: str, x: float, y: float) -> str:
        """Exact text of the element at (x, y) with this role, line breaks included (for verifying typed text)."""
        app = validate_app_name(app)
        out = await self._osascript(_AS_VALUE, app, role, str(round(x)), str(round(y)), timeout=20)
        return out[:-1] if out.endswith("\n") else out  # osascript adds one trailing newline

    async def press_return(self, app: str) -> None:
        app = validate_app_name(app)
        await self._osascript(_AS_RETURN, app, timeout=15)

    def screen_recording_allowed(self) -> bool | None:
        Q = self._quartz
        if Q is None or not hasattr(Q, "CGPreflightScreenCaptureAccess"):
            return None
        return bool(Q.CGPreflightScreenCaptureAccess())

    async def ocr_elements(self) -> list[ScreenElement]:
        Q = self._quartz
        if Q is None:
            raise DesktopUnavailable("text recognition needs PyObjC: pip install 'baymax-backend[macos]'")
        if self.screen_recording_allowed() is False:
            Q.CGRequestScreenCaptureAccess()  # shows the macOS prompt once; never bypassed
            raise DesktopPermissionError(SCREEN_HELP)
        try:
            import Vision  # type: ignore  # noqa: F401
        except ImportError:
            raise DesktopUnavailable("text recognition needs PyObjC Vision: pip install 'baymax-backend[macos]'") from None
        loop = asyncio.get_running_loop()
        out: list[ScreenElement] = []
        for d in _quartz_displays(Q):  # fresh capture of every display, every request (no stale screenshots)
            out += await loop.run_in_executor(None, _vision_ocr, Q, d)
        return out

    async def screenshot(self, path: Path) -> Path:
        res = await self.p.run(["screencapture", "-x", str(path)], timeout=20)
        if not res.ok or not path.exists() or path.stat().st_size == 0:
            raise DesktopPermissionError(SCREEN_HELP)
        return path


_SELF_APPS = {"BAYMAX", "Electron", "baymax"}
APP_DIRS = ("/Applications", "/Applications/Utilities", "/System/Applications", "/System/Applications/Utilities",
            str(Path.home() / "Applications"))
_installed_cache: tuple[float, list[AppInfo]] = (0.0, [])


def scan_installed_apps(dirs: tuple[str, ...] = APP_DIRS) -> list[AppInfo]:
    """Installed .app bundles (name + bundle identifier from Info.plist). Cached for a minute."""
    import plistlib

    global _installed_cache
    if time.time() - _installed_cache[0] < 60 and dirs == APP_DIRS:
        return _installed_cache[1]
    out: list[AppInfo] = []
    for d in dirs:
        try:
            entries = list(Path(d).iterdir())
        except OSError:
            continue
        for e in entries:
            if e.suffix != ".app":
                continue
            bundle = ""
            try:
                with (e / "Contents" / "Info.plist").open("rb") as f:
                    bundle = str(plistlib.load(f).get("CFBundleIdentifier", ""))
            except Exception:
                pass
            out.append(AppInfo(e.stem, "", bundle, False))
    if dirs == APP_DIRS:
        _installed_cache = (time.time(), out)
    return out


def _quartz_displays(Q: Any) -> list[Display]:
    err, ids, count = Q.CGGetActiveDisplayList(16, None, None)
    if err:
        raise DesktopUnavailable(f"could not list displays (CoreGraphics error {err})")
    out = []
    for did in list(ids)[:count]:
        b = Q.CGDisplayBounds(did)
        w, h = float(b.size.width), float(b.size.height)
        px = float(Q.CGDisplayPixelsWide(did)) or w
        out.append(Display(int(did), float(b.origin.x), float(b.origin.y), w, h, round(px / w, 2) if w else 1.0))
    return out


def _vision_ocr(Q: Any, display: Display) -> list[ScreenElement]:
    """On-device text recognition of one display (Apple Vision). Words get their own boxes."""
    import Vision  # type: ignore  # pyobjc-framework-Vision

    image = Q.CGDisplayCreateImage(display.id)
    if image is None:
        raise DesktopPermissionError(SCREEN_HELP)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)  # UI labels, identifiers
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise DesktopUnavailable(f"text recognition failed: {err}")
    now = time.time()
    out: list[ScreenElement] = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        cand = cands[0]
        text, conf = str(cand.string()), float(cand.confidence())
        bb = obs.boundingBox()
        x, y, w, h = vision_box_to_global((bb.origin.x, bb.origin.y, bb.size.width, bb.size.height), display)
        out.append(ScreenElement("text", text, x, y, w, h, "ocr", conf, "", "", now))
        pos = 0
        for word in text.split(" "):
            start = text.find(word, pos)
            pos = start + len(word)
            if not word or len(text.split(" ")) == 1:
                continue
            rect, _ = cand.boundingBoxForRange_error_((start, len(word)), None)
            if rect is None:
                continue
            wb = rect.boundingBox()
            x, y, w, h = vision_box_to_global((wb.origin.x, wb.origin.y, wb.size.width, wb.size.height), display)
            out.append(ScreenElement("text", word, x, y, w, h, "ocr", conf, "", "", now))
    return out


def make_desktop(processes: ProcessManager) -> DesktopBackend:
    if sys.platform == "darwin":
        return MacDesktop(processes)
    return UnsupportedDesktop()
