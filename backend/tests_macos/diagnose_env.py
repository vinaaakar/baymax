"""Read-only check of the conditions the macOS integration tests depend on. Changes nothing: no clicks, no typing,
no windows opened, no pointer movement. Run it right before the tests, without touching the Mac while it runs:

    cd ~/Developer/BAYMAX/backend
    .venv/bin/python tests_macos/diagnose_env.py

Prints: how long a System Events query takes (slow = macOS is busy), the frontmost app by name and process id, whether
Terminal is in full screen, TextEdit's windows as macOS's window server sees them (on screen or not) versus what
Accessibility can see, whether anything moved the pointer during 3 seconds, and BAYMAX / Chrome-app processes.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baymax.hostos.desktop import make_desktop
from baymax.hostos.process import ProcessManager

_FULLSCREEN = """tell application "System Events"
  set out to ""
  repeat with w in (windows of process "Terminal")
    set fs to "?"
    try
      set fs to (value of attribute "AXFullScreen" of w) as text
    end try
    set out to out & (name of w as text) & " | full screen: " & fs & linefeed
  end repeat
  return out
end tell"""


def osa(script: str) -> tuple[float, str]:
    t = time.monotonic()
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30, check=False)
    return time.monotonic() - t, (r.stdout or r.stderr).strip()


async def main() -> None:  # noqa: ASYNC221  (a one-off diagnostic; blocking calls are fine)
    desk = make_desktop(ProcessManager())
    print("Accessibility trusted:", desk.accessibility_trusted())
    secs, n = osa('tell application "System Events" to count processes')
    print(f"System Events answered in {secs:.2f}s ({n} processes){'  <-- slow: macOS is busy' if secs > 1.5 else ''}")
    print("load average:", subprocess.run(["sysctl", "-n", "vm.loadavg"], capture_output=True, text=True, check=False).stdout.strip())

    _, front = osa('tell application "System Events" to get {name, unix id} of first process whose frontmost is true')
    print("frontmost (System Events):", front, "| frontmost pid (Accessibility):", desk._front_pid())

    _, fs = osa(_FULLSCREEN)
    print("Terminal windows:\n  " + (fs.replace("\n", "\n  ") or "(none visible to Accessibility)"))

    _, running = osa('tell application "System Events" to return exists process "TextEdit"')
    print("TextEdit running:", running)
    if running == "true":
        _, ax = osa('tell application "System Events" to return name of windows of process "TextEdit"')
        print("  windows Accessibility sees (current desktop Space only):", ax or "none")
        _, own = osa('tell application "TextEdit" to return name of documents')
        print("  documents TextEdit reports:", own or "none")
    Q = desk._quartz
    if Q is not None:
        wins = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionAll, Q.kCGNullWindowID) or []
        te = [(w.get("kCGWindowName") or "", bool(w.get("kCGWindowIsOnscreen"))) for w in wins
              if w.get("kCGWindowOwnerName") == "TextEdit" and w.get("kCGWindowLayer") == 0]
        print("  TextEdit windows in the window server (title, on this screen):", te or "none")
        p1 = await desk.cursor_position()
        await asyncio.sleep(3)
        p2 = await desk.cursor_position()
        moved = abs(p1[0] - p2[0]) + abs(p1[1] - p2[1])
        print(f"pointer {p1} -> {p2} over 3 s{'  <-- something moved the pointer (trackpad/mouse?)' if moved > 0.5 else ' (still)'}")
    ps = subprocess.run(["ps", "-axo", "pid,comm"], capture_output=True, text=True, check=False).stdout.splitlines()
    hits = [line.strip() for line in ps if any(k in line for k in ("app_mode_loader", "BAYMAX", "Baymax", "baymax", "Electron"))]
    print("BAYMAX / Electron / Chrome-app processes:")
    for line in hits[:15]:
        print("  ", line[-140:])


if __name__ == "__main__":
    asyncio.run(main())
