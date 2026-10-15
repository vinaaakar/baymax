"""Which way of bringing an app forward makes its full-screen window visible to Accessibility? Run it from a Terminal in
a normal (not full-screen) window while the app (default Safari) has a FULL-SCREEN window on its own Space:

    cd ~/Developer/BAYMAX/backend
    .venv/bin/python tests_macos/diagnose_spaces.py            # Safari
    .venv/bin/python tests_macos/diagnose_spaces.py ChatGPT

It only switches apps/Spaces (no clicks inside apps, no typing). After each method it comes back to Terminal.
"""

from __future__ import annotations

import subprocess
import sys
import time

APP = sys.argv[1] if len(sys.argv) > 1 else "Safari"


def osa(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30, check=False)
    return (r.stdout or r.stderr).strip()


def state() -> str:
    front = osa('tell application "System Events" to get name of first process whose frontmost is true')
    n = osa(f'tell application "System Events" to count windows of process "{APP}"')
    return f"front={front!r} {APP} windows visible to Accessibility={n}"


BID = osa(f'tell application "System Events" to get bundle identifier of process "{APP}"')
METHODS = {
    "A. tell application id … to activate": f'tell application id "{BID}" to activate',
    "B. System Events: set frontmost to true": f'tell application "System Events" to set frontmost of process "{APP}" to true',
    "C. tell application id … to reopen + activate": f'tell application id "{BID}"\nreopen\nactivate\nend tell',
    "D. open -a (like clicking its Dock icon)": None,
}


def back_to_terminal() -> None:
    osa('tell application "Terminal" to activate')
    time.sleep(1.5)


print(f"{APP} ({BID})")
print("start:", state())
for label, script in METHODS.items():
    back_to_terminal()
    before = state()
    if script is None:
        subprocess.run(["open", "-a", APP], check=False)
    else:
        osa(script)
    time.sleep(1.5)
    print(f"{label}\n   before: {before}\n   after:  {state()}")
back_to_terminal()
