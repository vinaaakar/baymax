"""Read an app's window through the macOS Accessibility API in-process (PyObjC ApplicationServices) instead of through
AppleScript/System Events. Measured on the Mac (Oct 15): the whole ChatGPT window, 1983 elements, in 0.17 s, where the
AppleScript walk read 64 elements in 30 s and never reached the message box.

Read-only except `focus()`, which sets AXFocused on one text input the caller already verified. Output uses the same
line format as the AppleScript dumper (`_AS_LOCATE`), so parse_ax_dump / parse_window_header are unchanged.

`AS` is the ApplicationServices module (or a test double with the same functions).
"""

from __future__ import annotations

import re
import time
from typing import Any

TEXT_INPUTS = ("AXTextArea", "AXTextField", "AXComboBox", "AXSearchField")
MAX_NODES = 8000  # well above ChatGPT's 1983; bounds pathological trees
BUDGET_S = 4.0


def attr(AS: Any, el: Any, name: str) -> Any:
    try:
        err, v = AS.AXUIElementCopyAttributeValue(el, name, None)
    except Exception:  # an element that went away mid-walk
        return None
    return v if err == 0 else None


def _pair(AS: Any, v: Any, kind: str) -> tuple[float, float] | None:
    """AXValue (CGPoint / CGSize) -> two floats."""
    if v is None:
        return None
    try:
        const = AS.kAXValueCGPointType if kind == "point" else AS.kAXValueCGSizeType
        ok, val = AS.AXValueGetValue(v, const, None)
        if ok:
            return (float(val.x), float(val.y)) if kind == "point" else (float(val.width), float(val.height))
    except Exception:
        pass
    keys = ("x", "y") if kind == "point" else ("w", "h")
    m = re.search(rf"{keys[0]}[:=]\s*(-?[\d.]+)\D+{keys[1]}[:=]\s*(-?[\d.]+)", str(v))
    return (float(m.group(1)), float(m.group(2))) if m else None


def frame(AS: Any, el: Any) -> tuple[float, float, float, float] | None:
    p, s = _pair(AS, attr(AS, el, "AXPosition"), "point"), _pair(AS, attr(AS, el, "AXSize"), "size")
    return (p[0], p[1], s[0], s[1]) if p and s else None


def _one_line(v: Any, n: int) -> str:
    if v is None:
        return ""
    t = v if isinstance(v, str) else str(v) if isinstance(v, (int, float)) else ""
    return re.sub(r"[\t\r\n ]+", " ", t)[:n]


def app_element(AS: Any, pid: int) -> Any:
    el = AS.AXUIElementCreateApplication(pid)
    try:
        AS.AXUIElementSetMessagingTimeout(el, 2.0)  # an unresponsive app can't stall BAYMAX for long
    except Exception:
        pass
    try:  # Chromium/Electron apps build their web accessibility tree only once an assistive client asks
        AS.AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
    except Exception:
        pass
    return el


def pick_window(AS: Any, app_el: Any) -> tuple[Any, str, int]:
    """(window, how chosen, number of windows Accessibility can see) - main, then focused, then the first."""
    wins = list(attr(AS, app_el, "AXWindows") or [])
    for how, name in (("main", "AXMainWindow"), ("focused", "AXFocusedWindow")):
        w = attr(AS, app_el, name)
        if w is not None:
            return w, how, len(wins)
    return (wins[0], "first", len(wins)) if wins else (None, "none", 0)


def _walk(AS: Any, root: Any, max_nodes: int, budget_s: float):
    """Breadth-first (element, role) pairs under root (root itself excluded); a final (None, "#cut") when bounded."""
    t0, queue, i = time.monotonic(), [root], 0
    while i < len(queue):
        if i >= max_nodes or time.monotonic() - t0 > budget_s:
            yield None, "#cut"
            return
        el = queue[i]
        i += 1
        if i > 1:
            yield el, attr(AS, el, "AXRole") or ""
        queue.extend(attr(AS, el, "AXChildren") or [])


def describe(AS: Any, el: Any, role: str) -> str:
    """One element as `role, name, description, value, x, y, w, h, enabled, identifier` (tab-separated), or ""."""
    f = frame(AS, el)
    if not role or f is None:
        return ""
    en = attr(AS, el, "AXEnabled")
    cells = [role, _one_line(attr(AS, el, "AXTitle"), 200), _one_line(attr(AS, el, "AXDescription"), 200),
             _one_line(attr(AS, el, "AXValue"), 2000), *(f"{v:.1f}" for v in f),
             "1" if en is True else "0" if en is False else "", _one_line(attr(AS, el, "AXIdentifier"), 120)]
    if role in TEXT_INPUTS and not cells[1] and not cells[2]:
        cells[2] = _one_line(attr(AS, el, "AXPlaceholderValue"), 200)  # e.g. "Message ChatGPT"
    return "\t".join(cells) + "\n"


def dump(AS: Any, app_el: Any, limit: int, max_nodes: int = MAX_NODES, budget_s: float = BUDGET_S) -> str:
    """Header + element lines of the app's main window, like _AS_LOCATE. At most `limit` lines, except that text inputs
    are always included (a chat box deep in a large page must not be cut off by the line limit)."""
    win, how, count = pick_window(AS, app_el)
    lines: list[str] = []
    cut, others = "0", 0
    name = sub = ""
    if win is not None:
        name, sub = _one_line(attr(AS, win, "AXTitle"), 300), _one_line(attr(AS, win, "AXSubrole"), 60)
        for el, role in _walk(AS, win, max_nodes, budget_s):
            if role == "#cut":
                cut = "1"
                break
            if role not in TEXT_INPUTS and others >= limit:
                continue
            ln = describe(AS, el, role)
            if ln:
                lines.append(ln)
                others += role not in TEXT_INPUTS
    menu_bar = attr(AS, app_el, "AXMenuBar")
    for item in (attr(AS, menu_bar, "AXChildren") or []) if menu_bar is not None else []:
        ln = describe(AS, item, attr(AS, item, "AXRole") or "")
        if ln:
            lines.append(ln)
    return f"#window\t{count}\t{how}\t{name}\t{sub}\t{cut}\n" + "".join(lines)


def find(AS: Any, app_el: Any, role: str, x: float, y: float, max_nodes: int = MAX_NODES,
         budget_s: float = BUDGET_S) -> Any:
    """The element of this role whose top-left is within 6 points of (x, y) in the main window, or None (same rule as
    the AppleScript focus/read scripts)."""
    win, _, _ = pick_window(AS, app_el)
    if win is None:
        return None
    for el, r in _walk(AS, win, max_nodes, budget_s):
        if r == "#cut":
            return None
        if r == role:
            f = frame(AS, el)
            if f and (f[0] - x) ** 2 + (f[1] - y) ** 2 < 36:
                return el
    return None


def value_text(AS: Any, el: Any) -> str:
    v = attr(AS, el, "AXValue")
    return v if isinstance(v, str) else ""


def focus(AS: Any, el: Any) -> bool:
    """Give keyboard focus to `el` (a text input the caller located and checked) and confirm it took."""
    try:
        AS.AXUIElementSetAttributeValue(el, "AXFocused", True)
    except Exception:
        return False
    return attr(AS, el, "AXFocused") is True
