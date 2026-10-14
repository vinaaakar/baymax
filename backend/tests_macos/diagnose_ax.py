"""Read-only Accessibility diagnostics for one app (default TextEdit). Changes nothing: no clicks, no typing,
no new windows.

    cd ~/Developer/BAYMAX/backend
    .venv/bin/python tests_macos/diagnose_ax.py            # TextEdit
    .venv/bin/python tests_macos/diagnose_ax.py Safari

Prints: the frontmost process; the app's own window count vs the windows System Events (Accessibility) can see,
with title / subrole / main / minimized / frame; which window BAYMAX would inspect and how it chose it; and the
raw locate dump compared with what parse_ax_dump() keeps.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere: the backend package is one level up

from baymax.hostos.desktop import _AS_LOCATE, DesktopUnavailable, make_desktop, parse_window_header
from baymax.hostos.process import ProcessManager
from baymax.screen.elements import parse_ax_dump

_APP_WINDOWS = 'on run argv\n tell application (item 1 of argv) to return count of windows\nend run'
_SE_WINDOWS = """on run argv
  set out to ""
  tell application "System Events"
    tell process (item 1 of argv)
      set out to out & "visible=" & (visible as text) & " windows=" & ((count of windows) as text) & linefeed
      repeat with i from 1 to count of windows
        set w to window i
        set t to ""
        set sr to ""
        set mn to ""
        set mi to ""
        set fr to ""
        set c to ""
        try
          set t to name of w as text
        end try
        try
          set sr to subrole of w as text
        end try
        try
          set mn to (value of attribute "AXMain" of w) as text
        end try
        try
          set mi to (value of attribute "AXMinimized" of w) as text
        end try
        try
          set fr to ((item 1 of (position of w)) as text) & "," & ((item 2 of (position of w)) as text) & " " & ((item 1 of (size of w)) as text) & "x" & ((item 2 of (size of w)) as text)
        end try
        try
          set c to (count of (entire contents of w)) as text
        end try
        set out to out & "  window " & i & ": title=" & t & " | subrole=" & sr & " | main=" & mn & " | minimized=" & mi & " | frame=" & fr & " | elements=" & c & linefeed
      end repeat
    end tell
  end tell
  return out
end run"""


async def main(app: str) -> None:
    d = make_desktop(ProcessManager())
    if not hasattr(d, "_osascript"):
        print("This only runs on macOS.")
        return
    print("Accessibility trusted:", d.accessibility_trusted())
    for label, coro in (("frontmost process", d.frontmost_app()),
                        (f"{app} windows (app's own count)", d._osascript(_APP_WINDOWS, app, timeout=10)),
                        (f"{app} as seen by System Events", d._osascript(_SE_WINDOWS, app, timeout=30))):
        try:
            print(f"\n== {label}:\n{(await coro).rstrip()}")
        except DesktopUnavailable as exc:
            print(f"\n== {label}: ERROR {exc}")
    try:
        raw = await d._osascript(_AS_LOCATE, app, "400", timeout=25)
    except DesktopUnavailable as exc:
        print(f"\n== locate dump: ERROR {exc}")
        return
    lines = raw.splitlines()
    print("\n== window BAYMAX inspects:", parse_window_header(raw))
    raw_roles = Counter(ln.split("\t")[0] for ln in lines[1:] if ln.strip())
    parsed = parse_ax_dump(raw.replace(",", "."), app)
    print(f"== raw dump: {len(lines) - 1} element lines; roles {dict(raw_roles)}")
    print(f"== parse_ax_dump kept {len(parsed)}; roles {dict(Counter(e.role for e in parsed))}")
    dropped = set(raw_roles) - {e.role for e in parsed}
    if dropped:
        print("== roles present in the raw dump but dropped by the parser (no label or zero size):", sorted(dropped))
    print("\n== first 25 raw lines:")
    for ln in lines[:25]:
        print("  ", ln.replace("\t", " | ")[:200])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "TextEdit"))
