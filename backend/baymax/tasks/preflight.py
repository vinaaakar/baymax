"""Checks run immediately before a desktop step executes (after any approval): is the Mac's session usable, and is
the target app blocked by a dialog? Unlocking, entering passwords and clicking dialog buttons are always left to the
user; BAYMAX only waits (bounded, cancellable) and re-checks."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from ..hostos.desktop import DesktopPermissionError, DesktopUnavailable
from ..security.policy import Risk
from ..tools.base import ToolResult

DESKTOP_PREFIXES = ("desktop.", "screen.", "apps.", "ai.")
DESKTOP_TOOLS = ("files.open",)
POLL_S = 2.0


def needs_desktop(tool_name: str) -> bool:
    return tool_name.startswith(DESKTOP_PREFIXES) or tool_name in DESKTOP_TOOLS


async def _state(d: Any) -> dict[str, str]:
    try:
        st = await d.session_state()
        return st if st.get("state") in ("available", "locked", "display_asleep", "unknown") else {
            "state": "unknown", "detail": "unexpected session state"}
    except (DesktopUnavailable, DesktopPermissionError) as exc:
        return {"state": "unknown", "detail": str(exc)}


def describe_modals(app: str, modals: list[dict[str, str]]) -> str:
    def one(m: dict[str, str]) -> str:
        what = {"sheet": "a sheet", "dialog": "a dialog", "save_dialog": "a Save dialog", "open_dialog": "an Open dialog",
                "authentication": "a password / authentication prompt", "system_prompt": "a macOS system prompt"}.get(m["kind"], "a dialog")
        title = f" “{m['title']}”" if m.get("title") else ""
        btn = f" (buttons: {m['buttons']})" if m.get("buttons") else ""
        owner = "macOS" if m["kind"] in ("authentication", "system_prompt") else m["app"]
        return f"{owner} is showing {what}{title}{btn}"
    text = "; ".join(one(m) for m in modals[:3])
    more = f" and {len(modals) - 3} more" if len(modals) > 3 else ""
    return text + more


async def check(services: Any, tool: Any, args: dict[str, Any], *, step_title: str,
                progress: Callable[[str], None], checkpoint: Callable[[], None]) -> ToolResult | None:
    """None = go ahead; a ToolResult = stop this step with that explanation."""
    if not needs_desktop(tool.name):
        return None
    d, sec = services.desktop, services.settings.security
    deadline = time.monotonic() + max(0, int(sec.desktop_wait_s))
    woke = False
    # ---- 1. session: locked / display asleep
    while True:
        st = await _state(d)
        if st["state"] == "available":
            break
        if st["state"] == "unknown":
            progress(f"I couldn't check whether the Mac is locked ({st['detail']}); continuing.")
            break
        if st["state"] == "display_asleep" and sec.wake_display_for_tasks and not woke:
            progress("The Mac's display is asleep; waking it (this never unlocks it).")
            woke = True
            try:
                await d.wake_display()
            except (DesktopUnavailable, DesktopPermissionError) as exc:
                progress(f"I couldn't wake the display: {exc}")
            await asyncio.sleep(1.0)
            continue
        if time.monotonic() >= deadline:
            if st["state"] == "locked":
                return ToolResult(False, f"Your Mac is locked ({st['detail']}), so I stopped before “{step_title}”. Unlock it "
                                         "yourself — I never unlock it or enter passwords — then ask me again.",
                                  {"errorKind": "session_locked", "detail": st["detail"]})
            return ToolResult(False, f"The Mac's display is asleep, so I stopped before “{step_title}”. Wake it, then ask me again.",
                              {"errorKind": "display_asleep", "detail": st["detail"]})
        progress("Waiting for you to unlock the Mac…" if st["state"] == "locked" else "Waiting for the Mac's display to wake…")
        checkpoint()
        await asyncio.sleep(POLL_S)
    # ---- 2. blocking dialogs (only before steps that act on the UI, not before read-only ones)
    if tool.classify(args)[0] is Risk.LOW:
        return None
    target = str(args.get("app") or args.get("browser") or "")
    if not target:
        try:
            target = await d.frontmost_app()
        except (DesktopUnavailable, DesktopPermissionError):
            target = ""
    if not target:
        return None
    while True:
        try:
            modals = await d.modal_dialogs(target)
        except (DesktopUnavailable, DesktopPermissionError) as exc:
            progress(f"I couldn't check {target} for open dialogs ({exc}); continuing.")
            return None
        if not modals:
            return None
        text = describe_modals(target, modals)
        if time.monotonic() >= deadline:
            return ToolResult(False, f"{text}, so I stopped before “{step_title}”. I don't click dialog buttons for you — "
                                     "please handle it on the Mac, then ask me again.",
                              {"errorKind": "blocking_dialog", "dialogs": modals[:5], "app": target})
        progress(f"Waiting: {text}. Please handle it on the Mac — I won't click its buttons.")
        checkpoint()
        await asyncio.sleep(POLL_S)
