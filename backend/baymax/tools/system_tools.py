"""Application, desktop, file, command, mail and hardware tools."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from ..coding.files import EditError, FileOps
from ..hostos.apps import AppInfo, resolve_app
from ..hostos.sites import BROWSERS, resolve_destination, same_site
from ..hostos.desktop import DesktopPermissionError, DesktopUnavailable
from ..security.commands import CommandRejected, validate_command
from ..security.user_files import check_user_file, user_files_root
from ..security.policy import Risk
from ..security.workspace import WorkspaceViolation
from .base import Arg, Tool, ToolContext, ToolError, ToolResult, ToolUnavailable


async def resolve_app_arg(ctx: ToolContext, heard: str | None, *, must_run: bool) -> tuple[AppInfo | None, ToolResult | None]:
    """Turn what the user/model called an app into a real installed/running app, or a clarifying ToolResult.
    Unverified names never reach AppleScript."""
    try:
        running, installed = await ctx.services.desktop.list_apps()
    except DesktopPermissionError as exc:
        raise ToolUnavailable(str(exc)) from None
    except DesktopUnavailable as exc:
        raise ToolUnavailable(str(exc)) from None
    r = resolve_app(heard, running, installed)
    if r.status == "resolved" and r.app is not None:
        if must_run and not r.app.running:
            msg = f"{r.app.name} isn't running. Say “open {r.app.name}” first, then ask again."
            return None, ToolResult(False, msg, {"app": r.app.name, "errorKind": "not_found"}, needs_clarification=msg)
        return r.app, None
    if r.status == "none":
        return None, None
    q = r.question(str(heard))
    kind = "not_found" if r.status == "unknown" else "invalid_arguments"
    return None, ToolResult(False, q, {"heard": heard, "suggestions": r.suggestions, "errorKind": kind}, needs_clarification=q)


async def _desktop(call):
    try:
        return await call
    except DesktopPermissionError as exc:
        raise ToolUnavailable(str(exc)) from None
    except DesktopUnavailable as exc:
        raise ToolUnavailable(str(exc)) from None
    except ValueError as exc:
        raise ToolError(str(exc)) from None


class OpenAppTool(Tool):
    name = "apps.open"
    description = "Open (launch or focus) a macOS application by name."
    risk = Risk.MEDIUM
    args = {"app": Arg("str", description="application name, e.g. Safari", max_len=80)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        app, problem = await resolve_app_arg(ctx, args["app"], must_run=False)
        if problem:
            dest = resolve_destination(args["app"])
            if dest is not None and dest.kind == "site" and problem.data.get("errorKind") == "not_found":
                # "open Gemini": no such app installed, but it's a well-known website
                return await OpenUrlTool().run(ctx, {"url": args["app"], "browser": ""})
            return problem
        if app is None:
            q = "Which app should I open?"
            return ToolResult(False, q, {"errorKind": "invalid_arguments"}, needs_clarification=q)
        name = app.name
        await _desktop(d.open_app(name))
        for _ in range(20):  # verify for up to ~5 s
            ctx.checkpoint()
            try:
                if await d.is_app_running(name):
                    return ToolResult(True, f"{name} is open.", {"app": name}, verified=True,
                                      verification="macOS reports the application is running")
            except DesktopUnavailable:
                break
            await asyncio.sleep(0.25)
        return ToolResult(True, f"Asked macOS to open {name}, but I couldn't confirm it is running.", {"app": name},
                          verified=False, verification="application not detected as running")


class OpenUrlTool(Tool):
    name = "apps.open_url"
    description = "Open a website (URL, domain or well-known site name like 'Gemini') in a browser, e.g. 'open Gemini in Safari'."
    risk = Risk.MEDIUM
    args = {"url": Arg("str", description="URL, domain or site name", max_len=2000),
            "browser": Arg("str", False, default="", description="browser app, e.g. Safari (default: system default)", max_len=80)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        dest = resolve_destination(args["url"])
        if dest is None:
            return ToolResult(False, "Which website should I open?", {"errorKind": "invalid_arguments"}, needs_clarification="Which website?")
        browser = None
        if args.get("browser"):
            app, problem = await resolve_app_arg(ctx, args["browser"], must_run=False)
            if problem:
                return problem
            browser = app.name if app else None
        await _desktop(d.open_url(dest.url, browser))
        where = f" in {browser}" if browser else ""
        what = f"a web search for “{dest.label}”" if dest.kind == "search" else dest.host
        data = {"url": dest.url, "kind": dest.kind, "browser": browser or "default"}
        if browser:  # verify: the browser's front tab actually shows the site
            actual = None
            for _ in range(16):  # up to ~4 s for the page to start loading
                ctx.checkpoint()
                actual = await d.browser_url(browser)
                if actual and same_site(actual, dest):
                    return ToolResult(True, f"Opened {what}{where}.", {**data, "actualUrl": actual}, verified=True,
                                      verification=f"{browser}'s front tab is at {actual}")
                await asyncio.sleep(0.25)
            if actual:  # the address bar was read and shows another page: the destination was not reached
                return ToolResult(False, f"I asked {browser} to open {what}, but it is showing {actual}.",
                                  {**data, "actualUrl": actual, "errorKind": "not_verified"}, verified=False,
                                  verification=f"{browser}'s front tab is at {actual}, not {dest.host}")
            note = f"I couldn't read {browser}'s address bar to confirm (allow BAYMAX under Automation for {browser})"
            return ToolResult(True, f"Asked {browser} to open {what}, but I couldn't confirm the page loaded: {note}.",
                              {**data, "actualUrl": None}, verified=False, verification=note)  # dispatched, unverified
        return ToolResult(True, f"Asked your default browser to open {what}. I can't see which page it shows, so this isn't "
                                "confirmed.", data, verified=False,
                          verification="macOS accepted the URL; the default browser's page was not inspected")


async def _check_on_display(ctx: ToolContext, x: float, y: float) -> ToolResult | None:
    """Pointer targets must be on a connected display (global points); otherwise nothing is posted."""
    try:
        displays = await ctx.services.desktop.displays()
    except DesktopUnavailable:
        return None  # display list unavailable: the event API itself will reject impossible points
    if displays and not any(dp.contains(x, y) for dp in displays):
        return ToolResult(False, f"({x:.0f}, {y:.0f}) isn't on any connected display, so I didn't move or click.",
                          {"errorKind": "invalid_arguments"})
    return None


CURSOR_POSITIONS = ("center", "top-left", "top-right", "bottom-left", "bottom-right")
# Corners are approached CORNER_INSET points inside the display: the exact corner pixel can fire a macOS Hot Corner
# (Mission Control, screen saver, lock screen...), which would be an action the user never asked for.
CORNER_INSET = 5.0


def _spot(dp: Any, spot: str) -> tuple[float, float]:
    """Global point (top-left origin, points - the space CGEvent uses on every display, Retina or not) of a named spot."""
    if spot == "center":
        return dp.x + dp.width / 2, dp.y + dp.height / 2
    v, h = spot.split("-")
    x = dp.x + CORNER_INSET if h == "left" else dp.x + dp.width - 1 - CORNER_INSET
    y = dp.y + CORNER_INSET if v == "top" else dp.y + dp.height - 1 - CORNER_INSET
    return x, y


class FocusWindowTool(Tool):
    name = "desktop.focus_window"
    description = ("Bring one specific window of an app to the front, chosen by its title (or accessibility identifier). "
                   "Asks when several windows match; never guesses.")
    risk = Risk.MEDIUM
    args = {"app": Arg("str", max_len=80), "title": Arg("str", False, default="", max_len=300),
            "identifier": Arg("str", False, default="", max_len=200)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        app, problem = await resolve_app_arg(ctx, args["app"], must_run=True)
        if problem or app is None:
            return problem or ToolResult(False, "Which app's window?", {"errorKind": "invalid_arguments"}, needs_clarification="Which app?")
        proc = app.process or app.name
        want_title, want_id = args.get("title", "").strip(), args.get("identifier", "").strip()
        if not want_title and not want_id:
            q = f"Which {app.name} window? Tell me its title."
            return ToolResult(False, q, {"errorKind": "invalid_arguments"}, needs_clarification=q)
        wins = await _desktop(d.list_windows(proc))
        if not wins:
            return ToolResult(False, f"{app.name} has no window I can see on this desktop.", {"errorKind": "not_found", "app": app.name})
        if want_id:
            matches = [w for w in wins if w["identifier"] and w["identifier"] == want_id]
        else:
            key = want_title.casefold()
            matches = [w for w in wins if w["title"].casefold() == key] or [w for w in wins if key in w["title"].casefold()]
        titles = [w["title"] or "(untitled)" for w in wins]
        if not matches:
            return ToolResult(False, f"{app.name} has no window matching “{want_title or want_id}”. Its windows: "
                                     + "; ".join(f"“{t}”" for t in titles[:8]) + ".",
                              {"errorKind": "not_found", "app": app.name, "windows": titles[:20]})
        if len(matches) > 1:
            listing = "; ".join(f"{n}. “{w['title'] or '(untitled)'}”" for n, w in enumerate(matches[:8], 1))
            q = f"Several {app.name} windows match “{want_title or want_id}”: {listing}. Which one? Say its full title."
            return ToolResult(False, q, {"errorKind": "ambiguous", "app": app.name, "windows": [w["title"] for w in matches[:8]]},
                              needs_clarification=q)
        w = matches[0]
        ctx.checkpoint()
        await _desktop(d.raise_window(proc, w["index"], w["title"]))  # refuses if that window's title changed meanwhile
        # Re-check: the app is in front and its front window is the one asked for.
        try:
            front = await d.frontmost_app()
            now = await d.list_windows(proc)
        except DesktopUnavailable:
            front, now = "", []
        main = next((x for x in now if x["main"]), now[0] if now else None)
        ok = front.lower() in (proc.lower(), app.name.lower()) and main is not None and main["title"] == w["title"]
        data = {"app": app.name, "title": w["title"], "identifier": w["identifier"]}
        if ok:
            return ToolResult(True, f"Switched to the {app.name} window “{w['title']}”.", data, verified=True,
                              verification="the app is frontmost and its main window has the requested title")
        return ToolResult(True, f"I asked {app.name} to bring “{w['title']}” to the front, but I couldn't confirm it is the front window.",
                          data, verified=False, verification="front window not confirmed after raising")


class NewDocumentTool(Tool):
    name = "desktop.new_document"
    description = "Create a new, empty document in a document app (TextEdit by default) and confirm its window appeared."
    risk = Risk.MEDIUM  # opens an unsaved, empty document; nothing is saved or overwritten
    args = {"app": Arg("str", False, default="TextEdit", max_len=80)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        app, problem = await resolve_app_arg(ctx, args.get("app") or "TextEdit", must_run=False)
        if problem or app is None:
            return problem or ToolResult(False, "Which app should I create the document in?", {"errorKind": "invalid_arguments"},
                                         needs_clarification="Which app should I create the document in?")
        ctx.checkpoint()
        name = await _desktop(d.new_document(app.name))
        # Verify: a window with that document's title must actually exist (the app's report alone isn't proof).
        titles: list[str] = []
        for _ in range(10):
            try:
                titles = await d.window_titles(app.process or app.name)
            except DesktopUnavailable:
                titles = []
            if any(t == name or t.startswith((name + " ", name + ".")) for t in titles):
                return ToolResult(True, f"Created a new {app.name} document (“{name}”). It's empty and not saved.",
                                  {"app": app.name, "document": name}, verified=True,
                                  verification="a window with the new document's title was found")
            await asyncio.sleep(0.3)
        return ToolResult(True, f"{app.name} reported a new document “{name}”, but I couldn't see its window to confirm.",
                          {"app": app.name, "document": name, "windows": titles[:8]}, verified=False,
                          verification="no window with that title was visible (it may be on another desktop)")


class MoveCursorTool(Tool):
    name = "desktop.move_cursor"
    description = ("Move the mouse cursor to screen coordinates (points), or to a named spot of the display the pointer is on: "
                   "position='center', 'top-left', 'top-right', 'bottom-left' or 'bottom-right'.")
    risk = Risk.MEDIUM
    args = {"x": Arg("float", False), "y": Arg("float", False), "position": Arg("str", False, choices=CURSOR_POSITIONS)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        spot = args.get("position")
        if spot:
            displays = await _desktop(d.displays())
            if not displays:
                return ToolResult(False, "macOS didn't report any display, so I didn't move the pointer.", {"errorKind": "not_found"})
            try:  # the display the pointer is on now (the one being looked at), else the main display
                px, py = await d.cursor_position()
                here = next((dp for dp in displays if dp.contains(px, py)), None)
            except DesktopUnavailable:
                here = None
            dp = here or next((dp for dp in displays if dp.is_primary), displays[0])
            x, y = _spot(dp, spot)
            args = {**args, "x": x, "y": y}
        if args.get("x") is None or args.get("y") is None:
            q = "Where should I move the pointer? You can say “the center of the screen” or name something on screen."
            return ToolResult(False, q, {"errorKind": "invalid_arguments"}, needs_clarification=q)
        if (bad := await _check_on_display(ctx, args["x"], args["y"])) is not None:
            return bad
        await _desktop(d.move_cursor(args["x"], args["y"]))
        x, y = await _desktop(d.cursor_position())
        ok = abs(x - args["x"]) < 2 and abs(y - args["y"]) < 2
        where = f"the {spot.replace('-', ' ')}" + (" corner" if spot and spot != "center" else "") + " of the screen" if spot else ""
        if not ok:
            return ToolResult(False, f"I tried to move the pointer to {where or f'({args['x']:.0f}, {args['y']:.0f})'}, but it is at "
                                     f"({x:.0f}, {y:.0f}).", {"x": x, "y": y, "target": [args["x"], args["y"]], "errorKind": "not_verified"})
        text = f"Moved the pointer to {where} ({x:.0f}, {y:.0f})." if where else f"Cursor at ({x:.0f}, {y:.0f})."
        return ToolResult(True, text, {"x": x, "y": y, "position": spot}, verified=True, verification="cursor position read back")


class ClickTool(Tool):
    name = "desktop.click"
    description = "Click at screen coordinates."
    risk = Risk.MEDIUM
    args = {"x": Arg("float"), "y": Arg("float"), "button": Arg("str", False, default="left", choices=("left", "right")),
            "count": Arg("int", False, default=1)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        if (bad := await _check_on_display(ctx, args["x"], args["y"])) is not None:
            return bad
        if not 1 <= args.get("count", 1) <= 3:
            return ToolResult(False, "I can click once, twice or three times.", {"errorKind": "invalid_arguments"})
        ctx.checkpoint()
        await _desktop(ctx.services.desktop.click(args["x"], args["y"], args.get("button", "left"), args.get("count", 1)))
        return ToolResult(True, f"Clicked at ({args['x']:.0f}, {args['y']:.0f}).", dict(args), verified=False,
                          verification="click events were posted; the effect on the application was not observed")


class ScrollTool(Tool):
    name = "desktop.scroll"
    description = "Scroll the frontmost window (lines; positive dy scrolls up)."
    risk = Risk.MEDIUM
    args = {"dy": Arg("int"), "dx": Arg("int", False, default=0)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        await _desktop(ctx.services.desktop.scroll(args.get("dx", 0), args["dy"]))
        return ToolResult(True, f"Scrolled {args['dy']} lines.", dict(args), verified=False, verification="scroll event posted; effect not observed")


class TypeTextTool(Tool):
    name = "desktop.type_text"
    description = "Type text into an explicitly named application (it is brought to the front first)."
    risk = Risk.MEDIUM
    args = {"app": Arg("str", max_len=80), "text": Arg("str", max_len=2000)}

    def describe(self, args: dict[str, Any]) -> str:
        return f"type {len(args['text'])} characters into {args['app']}"

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        app, problem = await resolve_app_arg(ctx, args["app"], must_run=True)
        if problem or app is None:
            return problem or ToolResult(False, "Which app should I type into?", {"errorKind": "invalid_arguments"},
                                         needs_clarification="Which app should I type into?")
        args = {**args, "app": app.process or app.name}
        target, text = args["app"], args["text"]
        before = await _focused_text(d, target)  # the field that will receive the keystrokes, as it is now
        await _desktop(d.type_text(target, text))
        # Proof of insertion is the typed text appearing in the focused field - an app being in front is not.
        after = None
        for _ in range(4):
            await asyncio.sleep(0.25)
            after = await _focused_text(d, target)
            if after is not None and _norm_typed(text) in _norm_typed(after[1]):
                break
        data = {"app": target}
        if before is None or after is None or after[0] not in _TEXT_ROLES:
            return ToolResult(True, f"Typed into {target}, but {target} doesn't let me read the field back, so I can't confirm the "
                                    "text arrived.", {**data, "readBack": None}, verified=False,
                              verification="focused field not readable: insertion not confirmed")
        want, was, now = _norm_typed(text), _norm_typed(before[1]), _norm_typed(after[1])
        if now.count(want) > was.count(want):
            return ToolResult(True, f"Typed into {target}; the text is in the field.", {**data, "readBack": now[-2000:]}, verified=True,
                              verification="typed text read back from the focused field")
        return ToolResult(False, f"I typed into {target}, but the focused field doesn't contain what I typed (it reads "
                                 f"“{now[-80:]}”).", {**data, "readBack": now[-2000:], "errorKind": "not_verified"})


_TEXT_ROLES = ("AXTextArea", "AXTextField", "AXComboBox", "AXSearchField")


def _norm_typed(t: str) -> str:
    """Line breaks as macOS text views may store them (U+2028 / CR) compared as \\n; everything else exactly."""
    return t.replace("\u2028", "\n").replace("\r\n", "\n").replace("\r", "\n")


async def _focused_text(d: Any, app: str) -> tuple[str, str] | None:
    try:
        return await d.focused_text(app)
    except DesktopPermissionError as exc:
        raise ToolUnavailable(str(exc)) from None  # same handling as _desktop(): the missing permission is named
    except DesktopUnavailable:
        return None


class ListElementsTool(Tool):
    name = "desktop.list_elements"
    description = "List accessible interface elements (role, name) of an app's front window."
    risk = Risk.LOW
    args = {"app": Arg("str", max_len=80), "limit": Arg("int", False, default=60)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        app, problem = await resolve_app_arg(ctx, args["app"], must_run=True)
        if problem or app is None:
            return problem or ToolResult(False, "Which app should I inspect?", {"errorKind": "invalid_arguments"},
                                         needs_clarification="Which app should I inspect?")
        args = {**args, "app": app.process or app.name}
        els = await _desktop(ctx.services.desktop.list_elements(args["app"], args.get("limit", 60)))
        if not els:
            return ToolResult(True, f"{args['app']} exposes no accessible elements in its front window (or has no window).",
                              {"elements": []}, verified=True, verification="accessibility tree queried")
        return ToolResult(True, f"Found {len(els)} interface elements in {args['app']}.", {"elements": [e.to_dict() for e in els]},
                          verified=True, verification="accessibility tree queried", untrusted=True)


class PressElementTool(Tool):
    name = "desktop.press_element"
    description = "Press a named interface element (e.g. a button) via Accessibility."
    risk = Risk.MEDIUM
    args = {"app": Arg("str", max_len=80), "role": Arg("str", False, default="AXButton", max_len=40), "name": Arg("str", max_len=120)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        app, problem = await resolve_app_arg(ctx, args["app"], must_run=True)
        if problem or app is None:
            return problem or ToolResult(False, "Which app is the button in?", {"errorKind": "invalid_arguments"},
                                         needs_clarification="Which app is the button in?")
        args = {**args, "app": app.process or app.name}
        await _desktop(ctx.services.desktop.press_element(args["app"], args.get("role", "AXButton"), args["name"]))
        return ToolResult(True, f"Pressed '{args['name']}' in {args['app']}.", dict(args), verified=True,
                          verification="Accessibility AXPress action returned without error")


class ReadWindowTool(Tool):
    name = "desktop.read_window"
    description = ("Read the visible text of an application's front window through Accessibility. "
                   "Leave 'app' empty to read the window the user is looking at.")
    risk = Risk.LOW
    args = {"app": Arg("str", False, default="", description="application name, e.g. Terminal; empty = the app in front", max_len=80)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        requested = (args.get("app") or "").strip()
        resolved, problem = await resolve_app_arg(ctx, requested, must_run=True)
        if problem:
            return problem
        if resolved is not None:
            app, label = resolved.process or resolved.name, resolved.name
        else:  # "read the current window": the app the user is looking at, never BAYMAX itself
            try:
                app = await d.target_app()
            except DesktopPermissionError as exc:
                raise ToolUnavailable(str(exc)) from None
            except DesktopUnavailable as exc:
                q = str(exc)
                return ToolResult(False, q, {"errorKind": "not_found"}, needs_clarification=q)
            label = app
        try:
            text = await d.read_window_text(app)
        except DesktopPermissionError as exc:
            raise ToolUnavailable(str(exc)) from None
        except DesktopUnavailable as exc:
            return ToolResult(False, f"I couldn't read {label}'s window: {exc}.", {"app": label, "errorKind": "not_found"})
        if text.strip():
            return ToolResult(True, f"Read {len(text)} characters from {label} (accessibility text):\n{text[:1500]}",
                              {"app": label, "text": text, "source": "accessibility"}, verified=True,
                              verification="accessibility values read", untrusted=True)
        # No accessibility text is not proof the window is empty: recognise the text shown in that window instead.
        return await _read_window_by_ocr(ctx, app, label)


async def _read_window_by_ocr(ctx: ToolContext, app: str, label: str) -> ToolResult:
    """Fallback for windows without accessibility text: on-device text recognition (Vision) of a fresh capture, kept
    to the frame of that app's front window. Needs Screen Recording; only used when that window is the one in front
    (its frame is known), so other apps' text is never mixed in."""
    d = ctx.services.desktop
    none = f"{label}'s window exposes no accessibility text"
    try:
        w = await d.active_window()
    except DesktopUnavailable as exc:
        return ToolResult(False, f"{none}, and I couldn't locate the window to recognise its text ({exc}).",
                          {"app": label, "errorKind": "not_found"})
    if w.app.lower() not in (app.lower(), label.lower()):
        return ToolResult(False, f"{none}. To read it from the screen instead, bring {label} to the front and ask again.",
                          {"app": label, "errorKind": "not_found", "front": w.app})
    try:
        items = await d.ocr_elements()
    except DesktopPermissionError as exc:
        return ToolResult(False, f"{none}, and reading it from the screen needs the Screen Recording permission. {exc}",
                          {"app": label, "errorKind": "permission"})
    except DesktopUnavailable as exc:
        return ToolResult(False, f"{none}, and on-screen text recognition is unavailable: {exc}.", {"app": label, "errorKind": "unsupported"})
    inside = [e for e in items if w.x <= e.center[0] <= w.x + w.width and w.y <= e.center[1] <= w.y + w.height]
    if not inside:
        return ToolResult(True, f"{none}, and no text was recognised on screen inside it — it looks empty.",
                          {"app": label, "text": "", "source": "ocr"}, verified=True,
                          verification="accessibility tree read and the window's screen area recognised")
    inside.sort(key=lambda e: (round(e.y / 8), e.x))  # reading order: rows, then left to right
    text = "\n".join(e.label for e in inside)
    conf = sum(e.confidence for e in inside) / len(inside)
    return ToolResult(True, f"{label} exposes no accessibility text, so I read it from the screen (text recognition, "
                            f"average confidence {conf:.0%}; it may contain recognition errors):\n{text[:1500]}",
                      {"app": label, "text": text, "source": "ocr", "confidence": round(conf, 3)}, verified=True,
                      verification="Vision OCR of a fresh capture of the window's area", untrusted=True)


class ScreenshotTool(Tool):
    name = "desktop.screenshot"
    description = "Capture the screen to the workspace (requires Screen Recording permission)."
    risk = Risk.MEDIUM
    args = {}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        out = ctx.services.settings.artifacts_dir / "screenshots"
        out.mkdir(parents=True, exist_ok=True)
        path = await _desktop(ctx.services.desktop.screenshot(out / f"screen-{time.strftime('%Y%m%d-%H%M%S')}.png"))
        art = ctx.services.artifacts.add("screenshot", path.name, path, task_id=ctx.task_id, validated=True)
        return ToolResult(True, f"Saved a screenshot to {path}.", {"path": str(path)}, [art.to_dict()], verified=True,
                          verification="file exists and is non-empty")


class _FileTool(Tool):
    def ops(self, ctx: ToolContext) -> FileOps:
        return FileOps(ctx.services.workspace, ctx.services.settings.data_dir / "backups", ctx.task_id)

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            return self._run(ctx, args)
        except WorkspaceViolation as exc:
            raise ToolError(f"{exc} (workspace: {ctx.services.workspace.root})") from None
        except (EditError, FileNotFoundError, IsADirectoryError) as exc:
            raise ToolError(str(exc)) from None

    def _run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:  # pragma: no cover
        raise NotImplementedError


class ListFilesTool(_FileTool):
    name = "files.list"
    description = "List files in the workspace (or a sub-folder)."
    risk = Risk.LOW
    args = {"path": Arg("str", False, default=".")}

    def _run(self, ctx, args):
        items = self.ops(ctx).list(args.get("path", "."))
        return ToolResult(True, f"{len(items)} entries.", {"entries": items}, verified=True, verification="directory listed")


class ReadFileTool(_FileTool):
    name = "files.read"
    description = "Read a text file from the workspace."
    risk = Risk.LOW
    args = {"path": Arg("str")}

    def _run(self, ctx, args):
        text = self.ops(ctx).read(args["path"])
        return ToolResult(True, f"Read {args['path']} ({len(text)} characters).", {"content": text}, verified=True,
                          verification="file read", untrusted=True)


class WriteFileTool(_FileTool):
    name = "files.write"
    description = "Create or overwrite a text file in the workspace (previous content is backed up)."
    risk = Risk.MEDIUM
    args = {"path": Arg("str"), "content": Arg("str", max_len=1_000_000), "overwrite": Arg("bool", False, default=False)}

    def describe(self, args):
        return f"write {len(args['content'])} characters to {args['path']}" + (" (overwrite)" if args.get("overwrite") else "")

    def _run(self, ctx, args):
        change = self.ops(ctx).write(args["path"], args["content"], overwrite=args.get("overwrite", False))
        return ToolResult(True, f"{change.action.title()} {change.path} (+{change.added}/-{change.removed}).", {"change": change.to_dict()},
                          verified=True, verification="file content re-read and hash matched")


class SearchFilesTool(_FileTool):
    name = "files.search"
    description = "Search workspace text files with a regular expression."
    risk = Risk.LOW
    args = {"pattern": Arg("str", max_len=200), "path": Arg("str", False, default=".")}

    def _run(self, ctx, args):
        hits = self.ops(ctx).search(args["pattern"], args.get("path", "."))
        return ToolResult(True, f"{len(hits)} matches.", {"matches": hits}, verified=True, verification="files searched", untrusted=True)


class DeleteFileTool(_FileTool):
    name = "files.delete"
    description = "Delete a file in the workspace (requires approval; a backup copy is kept)."
    risk = Risk.HIGH
    categories = frozenset({"delete_files"})
    args = {"path": Arg("str")}

    def _run(self, ctx, args):
        ops = self.ops(ctx)
        p = ctx.services.workspace.resolve(args["path"], must_exist=True)
        if not p.is_file():
            raise ToolError("only single files can be deleted")
        backup = ops._backup(p)
        p.unlink()
        return ToolResult(not p.exists(), f"Deleted {args['path']} (backup at {backup}).", {"backup": backup}, verified=not p.exists(),
                          verification="file no longer exists")


class RunCommandTool(Tool):
    name = "shell.run"
    description = "Run an allowlisted development command (no shell) in a workspace directory."
    risk = Risk.LOW
    args = {"command": Arg("str", max_len=500), "cwd": Arg("str", False, default=".")}

    def classify(self, args):
        try:
            v = validate_command(args["command"])
        except CommandRejected:
            return Risk.BLOCKED, frozenset({"unrestricted_shell"})
        return v.risk, v.categories

    def describe(self, args):
        return f"run `{args['command']}` in {args.get('cwd', '.')}"

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            verdict = validate_command(args["command"])
            cwd = ctx.services.workspace.resolve(args.get("cwd", "."), must_exist=True)
        except (CommandRejected, WorkspaceViolation, FileNotFoundError) as exc:
            raise ToolError(str(exc)) from None
        ctx.checkpoint()
        cfg = ctx.services.settings.agent
        ctx.progress(None, f"running {verdict.description}")
        res = await ctx.services.processes.run(list(verdict.argv), cwd=str(cwd), timeout=cfg.command_timeout_s, max_output=cfg.max_command_output_bytes)
        summary = f"`{args['command']}` exited with {res.returncode}" + (" (timed out)" if res.timed_out else "")
        return ToolResult(res.ok, summary, {"returncode": res.returncode, "stdout": res.stdout[-20000:], "stderr": res.stderr[-20000:],
                                            "timedOut": res.timed_out}, verified=res.ok,
                          verification=f"process exit status {res.returncode}", untrusted=True)


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAIL_AS = """on run argv
  set theTo to item 1 of argv
  set theSubject to item 2 of argv
  set theBody to item 3 of argv
  tell application "Mail"
    set m to make new outgoing message with properties {subject:theSubject, content:theBody, visible:false}
    tell m to make new to recipient at end of to recipients with properties {address:theTo}
    send m
  end tell
end run"""


class SendMailTool(Tool):
    name = "mail.send"
    description = "Send an email with Apple Mail (always requires approval)."
    risk = Risk.HIGH
    categories = frozenset({"send_message"})
    args = {"to": Arg("str", max_len=200), "subject": Arg("str", max_len=200), "body": Arg("str", max_len=20000)}

    def describe(self, args):
        return f"send email to {args['to']} with subject {args['subject']!r} ({len(args['body'])} chars)"

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        import sys

        if not _EMAIL.match(args["to"]):
            raise ToolError("invalid email address")
        if sys.platform != "darwin":
            raise ToolUnavailable("sending mail uses Apple Mail and is only available on macOS")
        res = await ctx.services.processes.run(["osascript", "-e", _MAIL_AS, args["to"], args["subject"], args["body"]], timeout=60)
        if not res.ok:
            raise ToolError(f"Mail refused to send: {res.stderr.strip()[-300:]}")
        return ToolResult(True, f"Handed the email to Mail for {args['to']}.", {"to": args["to"]}, verified=False,
                          verification="Mail accepted the message; delivery was not confirmed")


class HardwareStatusTool(Tool):
    name = "hardware.status"
    description = "Report hardware-control status (disabled unless configured)."
    risk = Risk.LOW
    args = {}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        enabled = ctx.services.settings.security.hardware_control_enabled
        return ToolResult(True, "Hardware control is " + ("enabled, but no device drivers are configured." if enabled else
                                                          "disabled. No physical devices are connected or controlled."),
                          {"enabled": enabled, "devices": []}, verified=True, verification="configuration read")


class FlashFirmwareTool(Tool):
    name = "hardware.flash_firmware"
    description = "Flash firmware to a configured device (not implemented; blocked by default)."
    risk = Risk.HIGH
    categories = frozenset({"flash_firmware"})
    args = {"device": Arg("str"), "image": Arg("str")}
    model_callable = False

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        raise ToolUnavailable("firmware flashing is not implemented: no device interface is configured. Nothing was flashed.")


class RunningAppsTool(Tool):
    name = "desktop.running_apps"
    description = "List the applications that are currently open (from macOS), marking the one in front."
    risk = Risk.LOW
    args: dict[str, Arg] = {}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        running, _ = await _desktop(d.list_apps())
        try:
            front = await d.frontmost_app()
        except DesktopUnavailable:
            front = None
        names = sorted({a.name for a in running if a.name}, key=str.lower)
        if not names:
            return ToolResult(True, "macOS reports no regular applications open.", {"apps": []}, verified=True,
                              verification="running applications listed by macOS")
        shown = ", ".join(f"{n} (in front)" if front and n.lower() == str(front).lower() else n for n in names)
        return ToolResult(True, f"Open apps: {shown}.", {"apps": names, "front": front}, verified=True,
                          verification="running applications listed by macOS")


# Safe, explicitly requested shortcuts only (nothing that closes, quits, deletes or sends).
SHORTCUTS = {
    "new_tab": ("t", ("command",), "open a new tab"), "new_window": ("n", ("command",), "open a new window"),
    "reload": ("r", ("command",), "reload the page"), "find": ("f", ("command",), "open Find"),
    "address_bar": ("l", ("command",), "focus the address bar"), "back": ("[", ("command",), "go back"),
    "forward": ("]", ("command",), "go forward"), "select_all": ("a", ("command",), "select all"),
    "copy": ("c", ("command",), "copy"), "paste": ("v", ("command",), "paste"), "undo": ("z", ("command",), "undo"),
    "zoom_in": ("=", ("command",), "zoom in"), "zoom_out": ("-", ("command",), "zoom out"),
}


class ShortcutTool(Tool):
    name = "desktop.shortcut"
    description = "Press a safe keyboard shortcut in an app: new tab, new window, reload, find, address bar, back, forward, select all, copy, paste, undo, zoom."
    risk = Risk.MEDIUM
    args = {"action": Arg("str", choices=tuple(SHORTCUTS)), "app": Arg("str", False, default="", max_len=80)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        key, mods, what = SHORTCUTS[args["action"]]
        if args.get("app"):
            app, problem = await resolve_app_arg(ctx, args["app"], must_run=False)
            if problem:
                return problem
            name = app.name if app else ""
            if app and not app.running:
                await _desktop(d.open_app(app.name))
                await asyncio.sleep(0.8)
        else:
            name = await _desktop(d.target_app())
        before = await d.browser_url(name) if name in BROWSERS else None
        ctx.checkpoint()
        await _desktop(d.press_keys(name, key, mods))
        if name in BROWSERS and args["action"] in ("new_tab", "reload", "back", "forward"):
            await asyncio.sleep(0.5)
            after = await d.browser_url(name)
            if args["action"] == "new_tab" and after is not None and after != before:
                return ToolResult(True, f"Opened a new tab in {name}.", {"app": name}, verified=True,
                                  verification="the front tab changed after the shortcut")
        return ToolResult(True, f"Pressed the shortcut to {what} in {name}.", {"app": name, "action": args["action"]}, verified=False,
                          verification="shortcut sent; its effect was not inspected")


class FindFileByNameTool(Tool):
    name = "files.find_by_name"
    description = "Find files in your home folder by name with Spotlight (read-only), e.g. 'find the file named report.pdf'."
    risk = Risk.LOW
    args = {"name": Arg("str", max_len=200)}
    outputs = {"path": "path"}  # only when exactly one file matches

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        name = args["name"].strip().strip("\"'“”")
        if not name or "/" in name or "\x00" in name:
            return ToolResult(False, "Which file name should I look for?", {"errorKind": "invalid_arguments"},
                              needs_clarification="Which file name should I look for?")
        root = user_files_root()
        found = await _desktop(ctx.services.desktop.find_files(name, root))
        allowed = []
        for p in found:  # existing regular files inside the permitted scope only (no ~/Library, no hidden folders)
            resolved, _, _ = check_user_file(p)
            if resolved is not None and str(resolved) not in allowed:
                allowed.append(str(resolved))
        if not allowed:
            return ToolResult(False, f"I found no file named “{name}” in your home folder.", {"errorKind": "not_found", "name": name})
        exact = [p for p in allowed if Path(p).name.lower() == name.lower()]
        best = (exact or allowed)[:20]
        data: dict[str, Any] = {"name": name, "paths": best}
        if len(best) == 1:
            data["path"] = best[0]
            return ToolResult(True, f"Found one file named “{name}”: {best[0]}", data, verified=True,
                              verification="path returned by Spotlight, resolved, and checked to exist in your home folder")
        listing = "\n".join(f"{i}. {p}" for i, p in enumerate(best[:8], 1))
        return ToolResult(True, f"Found {len(best)} files named “{name}”:\n{listing}", data, verified=True,
                          verification="paths returned by Spotlight, resolved, and checked to exist in your home folder")


class OpenFileTool(Tool):
    name = "files.open"
    description = "Open a file from your home folder (by full path) in its default app or a named app, and confirm it opened."
    risk = Risk.MEDIUM
    args = {"path": Arg("str", max_len=1024), "app": Arg("str", False, default="", max_len=80)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        path, kind, reason = check_user_file(args["path"])  # re-checked here: it may have moved since it was found
        if path is None:
            return ToolResult(False, f"I didn't open it: {reason}.", {"errorKind": {"stale": "not_found", "permission": "permission_denied"}
                                                                      .get(kind, "invalid_arguments"), "path": args["path"]})
        app_name = ""
        if args.get("app"):
            app, problem = await resolve_app_arg(ctx, args["app"], must_run=False)
            if problem or app is None:
                return problem or ToolResult(False, "Which app should open it?", {"errorKind": "invalid_arguments"})
            app_name = app.name
            await _desktop(d.open_path_in_app(app_name, path))
        else:
            await _desktop(d.open_path(path))
        # Verify: the front window's document is this file, or a window titled with its name exists.
        for _ in range(12):
            await asyncio.sleep(0.25)
            try:
                doc = await d.front_document()
            except DesktopUnavailable:
                doc = None
            if doc is not None and Path(doc).resolve() == path:
                return ToolResult(True, f"Opened {path.name}.", {"path": str(path), "app": app_name}, verified=True,
                                  verification="the front window's document is this file")
            if app_name:
                try:
                    titles = await d.window_titles(app_name)
                except DesktopUnavailable:
                    titles = []
                if any(t == path.name or t.startswith((path.name + " ", path.stem + " ")) for t in titles):
                    return ToolResult(True, f"Opened {path.name} in {app_name}.", {"path": str(path), "app": app_name}, verified=True,
                                      verification="a window titled with the file's name is open")
        return ToolResult(True, f"Asked macOS to open {path.name}, but I couldn't confirm which window shows it.",
                          {"path": str(path), "app": app_name}, verified=False, verification="no window confirmed showing the file")


def all_system_tools() -> list[Tool]:
    return [OpenAppTool(), OpenUrlTool(), NewDocumentTool(), FocusWindowTool(), MoveCursorTool(), ClickTool(), ScrollTool(), TypeTextTool(), ListElementsTool(), PressElementTool(),
            RunningAppsTool(), ShortcutTool(), FindFileByNameTool(), OpenFileTool(),
            ReadWindowTool(), ScreenshotTool(), ListFilesTool(), ReadFileTool(), WriteFileTool(), SearchFilesTool(), DeleteFileTool(),
            RunCommandTool(), SendMailTool(), HardwareStatusTool(), FlashFirmwareTool()]


__all__ = ["all_system_tools"]
