"""Screen understanding tools: find (look/point at) and press UI elements described in words.

Order of evidence: the accessibility tree (exact roles, names and frames from macOS), then
on-device OCR of a fresh capture. A result is reported only when an element was actually
found; ambiguous matches produce a clarifying question; missing permissions are explained.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..hostos.desktop import DesktopPermissionError, DesktopUnavailable
from ..screen.elements import Display, Resolution, ScreenElement, friendly_role, region, resolve, to_gaze
from ..security.policy import Risk
from .base import Arg, Tool, ToolContext, ToolResult, ToolUnavailable


async def _locate(ctx: ToolContext, query: str, app: str | None
                  ) -> tuple[Resolution, list[Display], dict[str, Any], list[ScreenElement]]:
    d = ctx.services.desktop
    notes: dict[str, Any] = {"query": query}
    try:
        displays = await d.displays()
    except DesktopUnavailable as exc:
        raise ToolUnavailable(str(exc)) from None
    problems: list[str] = []
    res = Resolution("not_found")
    ax: list[ScreenElement] = []
    try:
        target = app or await d.target_app()
        notes["app"] = target
        ctx.progress(0.2, f"reading the accessibility tree of {target}")
        ax = await d.accessibility_elements(target)
        notes["axElements"] = len(ax)
        res = resolve(query, ax)
        notes["source"] = "accessibility"
    except DesktopPermissionError as exc:
        problems.append(str(exc))
    except DesktopUnavailable as exc:
        problems.append(f"accessibility tree unavailable: {exc}")
    if res.status == "not_found":
        ctx.checkpoint()
        ctx.progress(0.6, "recognising text on screen")
        try:
            ocr = await d.ocr_elements()
            notes["ocrItems"] = len(ocr)
            res_ocr = resolve(query, ocr)
            if res_ocr.status != "not_found":
                res, notes["source"] = res_ocr, "ocr"
        except DesktopPermissionError as exc:
            problems.append(str(exc))
        except DesktopUnavailable as exc:
            problems.append(f"text recognition unavailable: {exc}")
    notes["problems"] = problems
    return res, displays, notes, ax


def _where(el: ScreenElement, displays: list[Display]) -> str:
    return f"{friendly_role(el.role)}, {region(el, displays)}"


def _not_found(query: str, notes: dict[str, Any]) -> ToolResult:
    looked = []
    if "axElements" in notes:
        looked.append(f"{notes['axElements']} accessibility elements in {notes.get('app', 'the front app')}")
    if "ocrItems" in notes:
        looked.append(f"{notes['ocrItems']} pieces of on-screen text")
    msg = f"I couldn't find “{query}” on screen" + (f" (checked {' and '.join(looked)})" if looked else "") + ". I'm not pointing at anything."
    if notes["problems"]:
        msg += " " + " ".join(dict.fromkeys(notes["problems"]))
    return ToolResult(False, msg, {k: v for k, v in notes.items()})


class FindElementTool(Tool):
    name = "screen.find_element"
    description = ("Find a UI element on the Mac screen from a description (e.g. 'the Run button') and make BAYMAX look and point at it. "
                   "Uses the accessibility tree, then on-screen text recognition.")
    risk = Risk.LOW
    args = {"query": Arg("str", description="what to find, e.g. 'Run button'", max_len=160),
            "app": Arg("str", False, description="application to search (default: the app in front)", max_len=80)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        query = args["query"].strip()
        res, displays, notes, before = await _locate(ctx, query, args.get("app"))
        if res.status == "ambiguous":
            q = (f"I see more than one match for “{query}”: {res.describe_candidates(displays)}. "
                 "Which one? You can say, for example, “the first one” or name the area.")
            return ToolResult(False, q, {**notes, "candidates": [e.to_dict() for _, e in res.candidates]}, needs_clarification=q)
        if res.status != "found" or res.element is None:
            return _not_found(query, notes)
        el = res.element
        try:
            gaze = to_gaze(el, displays)
        except ValueError as exc:
            return ToolResult(False, f"I found “{el.label}” but {exc}; please try again.", notes)
        ctx.services.bus.publish("gaze.target", gaze)  # Mac companion + iPhone look and point at it
        how = "the accessibility tree" if el.source == "ax" else f"on-screen text recognition (confidence {el.confidence:.0%})"
        return ToolResult(True, f"Found “{el.label}” ({_where(el, displays)}) via {how}. Looking at it now.",
                          {**notes, "element": el.to_dict(), "gaze": gaze, "matchScore": round(res.score, 3)}, verified=True,
                          verification=f"element reported by {'macOS accessibility' if el.source == 'ax' else 'Vision OCR'} in a capture taken for this request")


class ClickElementTool(Tool):
    name = "screen.click_element"
    description = "Press a UI element described in words (accessibility tree only, never blind coordinates). Asks if ambiguous."
    risk = Risk.MEDIUM
    args = {"query": Arg("str", max_len=160), "app": Arg("str", False, max_len=80)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        query = args["query"].strip()
        res, displays, notes, before = await _locate(ctx, query, args.get("app"))
        if res.status == "ambiguous":
            q = f"Several elements match “{query}”: {res.describe_candidates(displays)}. Which one should I press?"
            return ToolResult(False, q, notes, needs_clarification=q)
        if res.status != "found" or res.element is None:
            return _not_found(query, notes)
        el = res.element
        if el.source != "ax":
            return ToolResult(False, f"I can see “{el.label}” on screen, but {notes.get('app', 'the app')} doesn't expose it to accessibility, "
                                     "so I won't click it blindly. I'm pointing at it instead.",
                              {**notes, "element": el.to_dict()}, needs_clarification="")
        ctx.services.bus.publish("gaze.target", to_gaze(el, displays, duration_ms=3000))
        app = notes["app"]
        try:
            await d.press_element(app, el.role, el.label)
        except DesktopPermissionError as exc:
            raise ToolUnavailable(str(exc)) from None
        except DesktopUnavailable as exc:
            return ToolResult(False, f"Pressing “{el.label}” failed: {exc}", notes)
        # Verify: re-read the tree. A pressed button usually changes something (window, title, enabled state).
        await asyncio.sleep(0.4)
        try:
            after = await d.accessibility_elements(app)
            front = await d.target_app()
        except DesktopUnavailable:
            after, front = [], ""
        def signature(els: list[ScreenElement]) -> set[tuple[str, str, int, int]]:
            return {(e.role, e.label, round(e.x), round(e.y)) for e in els}

        changed = bool(after) and (front != app or signature(after) != signature(before))
        detail = "the interface changed afterwards" if changed else "macOS accepted the press; no visible change was detected"
        return ToolResult(True, f"Pressed “{el.label}” in {app} ({detail}).", {**notes, "element": el.to_dict(), "changed": changed,
                          "frontAfter": front}, verified=changed, verification=detail)


class ActiveWindowTool(Tool):
    name = "desktop.active_window"
    description = "Say which app and window the user is working in (title and position)."
    risk = Risk.LOW
    args: dict[str, Arg] = {}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            w = await ctx.services.desktop.active_window()
        except DesktopPermissionError as exc:
            raise ToolUnavailable(str(exc)) from None
        except DesktopUnavailable as exc:
            return ToolResult(False, f"I couldn't tell which window you're using: {exc}.", {"errorKind": "not_found"})
        data = {"app": w.app, "title": w.title, "frame": [w.x, w.y, w.width, w.height]}
        if w.title:
            text = f"You're in {w.app}; the window is titled “{w.title}”."
        elif w.title == "":
            text = f"You're in {w.app}; its window has no title."
        else:
            text = f"You're in {w.app}. I can't read the window title: {w.title_note}."
        return ToolResult(True, text, data, verified=True, verification="front-most window reported by macOS")


class PointWindowTool(Tool):
    name = "screen.point_window"
    description = "Look and point toward the window the user is working in."
    risk = Risk.LOW
    args: dict[str, Arg] = {}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        try:
            w = await d.active_window()
            displays = await d.displays()
        except DesktopPermissionError as exc:
            raise ToolUnavailable(str(exc)) from None
        except DesktopUnavailable as exc:
            return ToolResult(False, f"I couldn't find the window you're using: {exc}.", {"errorKind": "not_found"})
        if w.width <= 1 or w.height <= 1:
            return ToolResult(False, f"{w.app}'s window has no visible frame.", {"errorKind": "not_found"})
        el = ScreenElement("AXWindow", w.title or w.app, w.x, w.y, w.width, w.height, "ax", 1.0, w.app)
        try:
            gaze = to_gaze(el, displays)
        except ValueError as exc:
            return ToolResult(False, f"I found {w.app}'s window but {exc}.", {"errorKind": "not_found"})
        ctx.services.bus.publish("gaze.target", gaze)
        return ToolResult(True, f"Looking at your {w.app} window ({_where(el, displays)}).", {"app": w.app, "gaze": gaze}, verified=True,
                          verification="window frame reported by macOS")


def all_screen_tools() -> list[Tool]:
    return [FindElementTool(), ClickElementTool(), ActiveWindowTool(), PointWindowTool()]
