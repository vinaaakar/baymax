"""Screen understanding tools: find (look/point at) and press UI elements described in words.

Order of evidence: the accessibility tree (exact roles, names and frames from macOS), then
on-device OCR of a fresh capture. A result is reported only when an element was actually
found; ambiguous matches produce a clarifying question; missing permissions are explained.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..hostos.desktop import DesktopPermissionError, DesktopUnavailable
from ..hostos.sites import BROWSERS
from ..screen.elements import (
    TEXT_INPUT_ROLES,
    Display,
    Resolution,
    ScreenElement,
    friendly_role,
    region,
    resolve,
    to_gaze,
)
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


class PointAtTool(Tool):
    name = "screen.point_at"
    description = ("Point at a UI element described in words: move the Mac's mouse pointer onto it and show the same target on "
                   "BAYMAX's face (Mac and iPhone). Never clicks.")
    risk = Risk.MEDIUM  # moves the real pointer (like desktop.move_cursor)
    args = {"query": Arg("str", description="what to point at, e.g. 'the Send button'", max_len=160),
            "app": Arg("str", False, description="application to search (default: the app in front)", max_len=80)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        query = args["query"].strip()
        res, displays, notes, _ = await _locate(ctx, query, args.get("app"))
        if res.status == "ambiguous":
            q = f"I see more than one “{query}”: {res.describe_candidates(displays)}. Which one should I point at?"
            return ToolResult(False, q, {**notes, "candidates": [e.to_dict() for _, e in res.candidates]}, needs_clarification=q)
        if res.status != "found" or res.element is None:
            return _not_found(query, notes)
        el = res.element
        cx, cy = el.center  # global points (top-left origin, all displays): the same space CGEvent uses
        try:
            gaze = to_gaze(el, displays)  # also rejects a centre that is on no connected display
        except ValueError as exc:
            return ToolResult(False, f"I found “{el.label}” but {exc}, so I didn't move the pointer.", {**notes, "errorKind": "not_found"})
        ctx.checkpoint()
        try:
            await d.move_cursor(cx, cy)
            px, py = await d.cursor_position()
        except DesktopPermissionError as exc:
            raise ToolUnavailable(str(exc)) from None
        except DesktopUnavailable as exc:
            ctx.services.bus.publish("gaze.target", gaze)  # the face can still look at it
            return ToolResult(False, f"I found “{el.label}” ({_where(el, displays)}) but couldn't move the pointer: {exc}. "
                                     "I'm looking at it instead.", {**notes, "element": el.to_dict(), "gaze": gaze})
        ctx.services.bus.publish("gaze.target", gaze)  # Mac companion + iPhone show the same target
        on_target = abs(px - cx) < 2 and abs(py - cy) < 2
        how = "the accessibility tree" if el.source == "ax" else f"on-screen text (confidence {el.confidence:.0%})"
        text = (f"Pointing at “{el.label}” ({_where(el, displays)}), found via {how}. I haven't clicked it."
                if on_target else f"I moved the pointer toward “{el.label}”, but it ended at ({px:.0f}, {py:.0f}) instead of "
                                  f"({cx:.0f}, {cy:.0f}). I haven't clicked anything.")
        return ToolResult(on_target, text, {**notes, "element": el.to_dict(), "gaze": gaze, "pointer": [round(px, 1), round(py, 1)],
                                            "clicked": False}, verified=on_target,
                          verification="pointer position read back at the element's centre" if on_target else "pointer read back off target")


class ClickElementTool(Tool):
    name = "screen.click_element"
    description = ("Press, double-click or right-click a UI element described in words (found in the accessibility tree, never "
                   "blind coordinates). Asks if ambiguous.")
    risk = Risk.MEDIUM
    args = {"query": Arg("str", max_len=160), "app": Arg("str", False, max_len=80),
            "button": Arg("str", False, default="left", choices=("left", "right")), "count": Arg("int", False, default=1)}

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
        if el.enabled is False:
            return ToolResult(False, f"“{el.label}” is greyed out (disabled) right now, so I didn't press it.",
                              {**notes, "element": el.to_dict(), "errorKind": "target_disabled"})
        if el.source != "ax":
            return ToolResult(False, f"I can see “{el.label}” on screen, but {notes.get('app', 'the app')} doesn't expose it to accessibility, "
                                     "so I won't click it blindly. I'm pointing at it instead.",
                              {**notes, "element": el.to_dict()}, needs_clarification="")
        ctx.services.bus.publish("gaze.target", to_gaze(el, displays, duration_ms=3000))
        app = notes["app"]
        button, count = args.get("button", "left"), args.get("count", 1)
        if not 1 <= count <= 2:
            return ToolResult(False, "I can click an element once or twice.", {**notes, "errorKind": "invalid_arguments"})
        cx, cy = el.center
        if not any(dp.contains(cx, cy) for dp in displays):
            return ToolResult(False, f"“{el.label}” is outside every connected display, so I didn't click.", {**notes, "errorKind": "not_found"})
        ctx.checkpoint()
        try:
            if button == "left" and count == 1:
                await d.press_element(app, el.role, el.label)  # accessibility action: works even if covered
            else:  # double/right click need real pointer events at the element's verified centre
                await d.click(cx, cy, button, count)
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
        verb = "Pressed" if button == "left" and count == 1 else ("Right-clicked" if button == "right" else "Double-clicked")
        return ToolResult(True, f"{verb} “{el.label}” in {app} ({detail}).", {**notes, "element": el.to_dict(), "changed": changed,
                          "frontAfter": front}, verified=changed, verification=detail)


def _norm_text(t: str) -> str:
    return t.replace("\u2028", "\n").replace("\r\n", "\n").replace("\r", "\n")


def _field_for(query: str, els: list[ScreenElement]) -> tuple[Resolution, ScreenElement | None]:
    """The text input the user means: a field whose own name/placeholder matches, else the input next to (right of or
    below) a matching on-screen label — real frames from the accessibility tree, never fixed coordinates."""
    inputs = [e for e in els if e.role in TEXT_INPUT_ROLES]
    res = resolve(query, inputs)
    if res.status != "not_found":
        return res, res.element
    label_res = resolve(query, [e for e in els if e.role == "AXStaticText"])
    if label_res.status != "found" or label_res.element is None:
        return res, None
    lab = label_res.element
    lx, ly = lab.center

    def gap(e: ScreenElement) -> float | None:
        ex, ey = e.center
        right = e.x >= lab.x + lab.width - 4 and abs(ey - ly) <= max(lab.height, e.height)
        below = e.y >= lab.y + lab.height - 4 and abs(ex - lx) <= max(lab.width, e.width)
        return (abs(ex - lx) + abs(ey - ly)) if (right or below) else None

    near = sorted((g, e) for e in inputs if (g := gap(e)) is not None and g <= 400)
    if not near:
        return res, None
    return Resolution("found", near[0][1], 1.0), near[0][1]


class TypeIntoFieldTool(Tool):
    name = "screen.type_into"
    description = ("Type text into a form field described in words (e.g. 'the Email field') in the front app or a named app. "
                   "Focuses that exact field, types, and reads the text back. Never presses Submit/Send.")
    risk = Risk.MEDIUM
    args = {"field": Arg("str", description="the field, e.g. 'Name field'", max_len=120), "text": Arg("str", max_len=2000),
            "app": Arg("str", False, max_len=80),
            "replace": Arg("bool", False, default=False, description="clear the field first (only when the user asked)")}

    def describe(self, args: dict[str, Any]) -> str:
        return f"type {len(args['text'])} characters into the {args['field']}"

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        query, text = args["field"].strip(), args["text"]
        try:
            app = args.get("app") or await d.target_app()
            displays = await d.displays()
            ctx.progress(0.2, f"finding the {query} in {app}")
            els = await d.accessibility_elements(app)
        except DesktopPermissionError as exc:
            raise ToolUnavailable(str(exc)) from None
        except DesktopUnavailable as exc:
            return ToolResult(False, f"I couldn't read {args.get('app') or 'the front app'}'s fields: {exc}.", {"errorKind": "not_found"})
        notes: dict[str, Any] = {"query": query, "app": app, "axElements": len(els), "problems": []}
        res, field = _field_for(query, els)
        if res.status == "ambiguous":
            q = f"Several fields match “{query}”: {res.describe_candidates(displays)}. Which one?"
            return ToolResult(False, q, notes, needs_clarification=q)
        if field is None:
            return ToolResult(False, f"I couldn't find a text field matching “{query}” in {app} (checked {len(els)} accessibility "
                                     "elements), so I didn't type anything.", {**notes, "errorKind": "not_found"})
        if field.enabled is False:
            return ToolResult(False, f"The “{field.label}” field is disabled, so I didn't type into it.", {**notes, "errorKind": "target_disabled"})
        if displays and not any(dp.contains(*field.center) for dp in displays):
            return ToolResult(False, f"The “{field.label}” field isn't on a connected display, so I didn't type.", {**notes, "errorKind": "not_found"})
        try:
            ctx.services.bus.publish("gaze.target", to_gaze(field, displays, duration_ms=3000))
        except ValueError:
            pass
        ctx.checkpoint()
        try:
            await d.focus_element(app, field.role, field.x, field.y)  # raises unless macOS confirms keyboard focus there
            before = ""
            try:
                before = _norm_text(await d.element_value(app, field.role, field.x, field.y))
            except DesktopUnavailable:
                pass
            if args.get("replace") and before:
                await d.press_keys(app, "a", ("command",))  # select the field's text; typing replaces it
            ctx.checkpoint()
            await d.type_text(app, text)
            got = None
            for _ in range(5):
                await asyncio.sleep(0.25)
                try:
                    got = _norm_text(await d.element_value(app, field.role, field.x, field.y))
                    break
                except DesktopUnavailable:
                    continue
        except DesktopPermissionError as exc:
            raise ToolUnavailable(str(exc)) from None
        except DesktopUnavailable as exc:
            return ToolResult(False, f"I found the “{field.label}” field but couldn't type into it: {exc}.", {**notes, "errorKind": "unsupported"})
        want = _norm_text(text)
        expected = want if args.get("replace") or not before else before + want
        data = {**notes, "element": field.to_dict(), "replaced": bool(args.get("replace") and before)}
        if got is None:
            return ToolResult(True, f"I typed into the “{field.label}” field, but couldn't read it back to confirm. Nothing was submitted.",
                              data, verified=False, verification="field value unreadable")
        if got == expected or (want and want in got):
            extra = "" if got == expected else " (the field also contains other text)"
            return ToolResult(True, f"Typed into the “{field.label}” field{extra}. I haven't submitted the form.", data, verified=True,
                              verification="field value read back and matched exactly" if got == expected else "typed text found in the field")
        if got.casefold() == expected.casefold():
            return ToolResult(False, f"I typed into the “{field.label}” field, but {app} changed the capitalization (macOS "
                                     f"auto-capitalization), so it reads “{got[:80]}”. Nothing was submitted.",
                              {**data, "errorKind": "not_verified", "readBack": got[:200], "alteredBy": "auto-capitalization"})
        return ToolResult(False, f"I typed into the “{field.label}” field, but it now reads “{got[:80]}”, not what you asked for. "
                                 "Nothing was submitted.", {**data, "errorKind": "not_verified", "readBack": got[:200]})


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


_INTERACTIVE = {"AXButton": "button", "AXTab": "tab", "AXRadioButton": "tab", "AXTextField": "field", "AXTextArea": "field",
                "AXSearchField": "search field", "AXComboBox": "field", "AXLink": "link", "AXPopUpButton": "menu", "AXMenuButton": "menu",
                "AXCheckBox": "checkbox"}


class InspectScreenTool(Tool):
    name = "screen.inspect"
    description = "Inspect the window the user is working in and say what BAYMAX can do with it (real controls, permissions)."
    risk = Risk.LOW
    args: dict[str, Arg] = {}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        try:
            w = await d.active_window()
        except DesktopPermissionError as exc:
            return ToolResult(False, f"I can't see which window you're using yet. {exc}", {"errorKind": "permission"})
        except DesktopUnavailable as exc:
            return ToolResult(False, f"I couldn't tell which window you're using: {exc}.", {"errorKind": "not_found"})
        where = f"{w.app} — “{w.title}”" if w.title else w.app
        data: dict[str, Any] = {"app": w.app, "title": w.title}
        ctx.progress(0.4, f"reading the controls in {w.app}")
        try:
            els = await d.accessibility_elements(w.app)
        except DesktopPermissionError as exc:
            return ToolResult(True, f"You're in {where}. I can't inspect its controls: {exc} Until then I can only open apps and "
                                    "websites and tell you which window is in front.", {**data, "errorKind": "permission"},
                              verified=True, verification="front window reported by macOS; controls not readable")
        except DesktopUnavailable as exc:
            els, data["note"] = [], str(exc)
        controls = [e for e in els if e.role in _INTERACTIVE]
        data["controls"] = len(controls)
        seen: list[str] = []
        for e in controls:
            item = f"the “{e.label}” {_INTERACTIVE[e.role]}"
            if item not in seen:
                seen.append(item)
            if len(seen) == 5:
                break
        page = ""
        if w.app in BROWSERS:
            url = await d.browser_url(w.app)
            if url:
                data["url"] = url
                page = f" (page {url})"
        if not controls:
            return ToolResult(True, f"You're in {where}{page}. It doesn't expose any controls I can identify, so I can read its text "
                                    "or recognise text on screen, but I can't press anything there.", data, verified=True,
                              verification="accessibility tree read; no interactive elements")
        can = ["read this window's text", "find or point at a control by name", "click a named control"]
        if any(_INTERACTIVE[e.role].endswith("field") for e in controls):
            can.append("type into a field")
        text = (f"You're in {where}{page}. I can see {len(controls)} controls, including {', '.join(seen)}. "
                f"Here I can {', '.join(can[:-1])} or {can[-1]} — for example “click {controls[0].label}”.")
        return ToolResult(True, text, data, verified=True, verification=f"{len(els)} accessibility elements read from {w.app}")


def all_screen_tools() -> list[Tool]:
    return [FindElementTool(), PointAtTool(), ClickElementTool(), TypeIntoFieldTool(), ActiveWindowTool(), PointWindowTool(), InspectScreenTool()]
