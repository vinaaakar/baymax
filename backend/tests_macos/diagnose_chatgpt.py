"""Read-only: can BAYMAX find ChatGPT's message box quickly by asking macOS Accessibility directly (in-process) instead
of through AppleScript? Brings the app forward, then only reads - no clicks, no typing.

    cd ~/Developer/BAYMAX/backend
    .venv/bin/python tests_macos/diagnose_chatgpt.py            # ChatGPT
    .venv/bin/python tests_macos/diagnose_chatgpt.py Safari

Prints: the focused element; how many elements an in-process walk of the front window reads per second and which text
inputs it finds; and what Safari's built-in text-field search (the one VoiceOver uses) returns.
"""

from __future__ import annotations

import subprocess
import sys
import time

from ApplicationServices import (  # type: ignore
    AXUIElementCopyAttributeValue,
    AXUIElementCopyParameterizedAttributeValue,
    AXUIElementCreateApplication,
)

APP = sys.argv[1] if len(sys.argv) > 1 else "ChatGPT"
INPUTS = {"AXTextArea", "AXTextField", "AXComboBox", "AXSearchField"}


def attr(el, name):
    err, v = AXUIElementCopyAttributeValue(el, name, None)
    return v if err == 0 else None


def frame(el):
    p, s = attr(el, "AXPosition"), attr(el, "AXSize")
    try:
        import re

        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", f"{p} {s}")]
        return tuple(round(n) for n in nums[:4])
    except Exception:
        return None


def describe(el):
    return (f"{attr(el, 'AXRole')}/{attr(el, 'AXSubrole')} desc={attr(el, 'AXDescription')!r} "
            f"placeholder={attr(el, 'AXPlaceholderValue')!r} title={attr(el, 'AXTitle')!r} frame={frame(el)} "
            f"editable={attr(el, 'AXEditable') if attr(el, 'AXEditable') is not None else '-'}")


pid = subprocess.run(["osascript", "-e", f'tell application "System Events" to get unix id of process "{APP}"'],
                     capture_output=True, text=True, check=False).stdout.strip()
if not pid.isdigit():
    subprocess.run(["open", "-a", APP], check=False)
    time.sleep(3)
    pid = subprocess.run(["osascript", "-e", f'tell application "System Events" to get unix id of process "{APP}"'],
                         capture_output=True, text=True, check=False).stdout.strip()
print(f"{APP} pid {pid}")
subprocess.run(["osascript", "-e", f'tell application "System Events" to set bid to bundle identifier of process "{APP}"',
                "-e", "tell application id bid to activate"], check=False)
time.sleep(1.5)
app = AXUIElementCreateApplication(int(pid))

f = attr(app, "AXFocusedUIElement")
print("focused element:", describe(f) if f is not None else None)

win = attr(app, "AXFocusedWindow") or attr(app, "AXMainWindow")
print("window:", attr(win, "AXTitle") if win is not None else None)
if win is None:
    sys.exit("no window visible to Accessibility")

t0, seen, found, web = time.monotonic(), 0, [], []
queue = [win]
while queue and seen < 20000 and time.monotonic() - t0 < 20:
    el = queue.pop(0)
    seen += 1
    role = attr(el, "AXRole")
    if role in INPUTS or attr(el, "AXEditable") is True and role not in ("AXWebArea", "AXStaticText", "AXGroup"):
        found.append(el)
    if role == "AXWebArea":
        web.append(el)
    queue.extend(attr(el, "AXChildren") or [])
dt = time.monotonic() - t0
print(f"in-process walk: {seen} elements in {dt:.2f}s ({seen / max(dt, 1e-3):.0f}/s); complete={not queue}")
for el in found[:10]:
    print("  input:", describe(el))
print(f"web areas: {len(web)}")
for w in web[:2]:
    t0 = time.monotonic()
    try:
        err, hits = AXUIElementCopyParameterizedAttributeValue(
            w, "AXUIElementsForSearchPredicate",
            {"AXSearchKey": "AXTextFieldSearchKey", "AXResultsLimit": 10, "AXDirection": "AXDirectionNext"}, None)
    except Exception as exc:  # diagnostics only
        err, hits = str(exc), None
    print(f"  text-field search: err={err} in {time.monotonic() - t0:.2f}s")
    for el in (hits or [])[:10]:
        print("    hit:", describe(el))
