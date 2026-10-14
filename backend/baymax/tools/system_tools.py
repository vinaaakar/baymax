"""Application, desktop, file, command, mail and hardware tools."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from ..coding.files import EditError, FileOps
from ..hostos.apps import AppInfo, resolve_app
from ..hostos.sites import resolve_destination, same_site
from ..hostos.desktop import DesktopPermissionError, DesktopUnavailable
from ..security.commands import CommandRejected, validate_command
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
            note = (f"{browser} is showing {actual}" if actual else
                    f"I couldn't read {browser}'s address bar to confirm (allow BAYMAX under Automation for {browser})")
            return ToolResult(True, f"Asked {browser} to open {what}, but I couldn't confirm the page loaded: {note}.",
                              {**data, "actualUrl": actual}, verified=False, verification=note)
        return ToolResult(True, f"Opened {what} in your default browser.", data, verified=False,
                          verification="macOS accepted the URL; the default browser's page was not inspected")


class MoveCursorTool(Tool):
    name = "desktop.move_cursor"
    description = "Move the mouse cursor to screen coordinates (points)."
    risk = Risk.MEDIUM
    args = {"x": Arg("float"), "y": Arg("float")}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        await _desktop(d.move_cursor(args["x"], args["y"]))
        x, y = await _desktop(d.cursor_position())
        ok = abs(x - args["x"]) < 2 and abs(y - args["y"]) < 2
        return ToolResult(True, f"Cursor at ({x:.0f}, {y:.0f}).", {"x": x, "y": y}, verified=ok,
                          verification="cursor position read back" if ok else "cursor position differs from target")


class ClickTool(Tool):
    name = "desktop.click"
    description = "Click at screen coordinates."
    risk = Risk.MEDIUM
    args = {"x": Arg("float"), "y": Arg("float"), "button": Arg("str", False, default="left", choices=("left", "right")),
            "count": Arg("int", False, default=1)}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
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
        await _desktop(d.type_text(args["app"], args["text"]))
        front = ""
        try:
            front = await d.frontmost_app()
        except DesktopUnavailable:
            pass
        ok = front.lower() in (args["app"].lower(), app.name.lower())
        return ToolResult(True, f"Typed into {args['app']}.", {"app": args["app"], "frontmost": front}, verified=ok,
                          verification=f"{front or 'unknown app'} was frontmost after typing" + ("" if ok else " (not the requested app)"))


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
        if not text.strip():
            return ToolResult(True, f"{label}'s front window has no readable text (it may not expose accessibility text).",
                              {"app": label, "text": ""}, verified=True, verification="accessibility tree queried")
        return ToolResult(True, f"Read {len(text)} characters from {label}:\n{text[:1500]}", {"app": label, "text": text}, verified=True,
                          verification="accessibility values read", untrusted=True)


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


def all_system_tools() -> list[Tool]:
    return [OpenAppTool(), OpenUrlTool(), MoveCursorTool(), ClickTool(), ScrollTool(), TypeTextTool(), ListElementsTool(), PressElementTool(),
            ReadWindowTool(), ScreenshotTool(), ListFilesTool(), ReadFileTool(), WriteFileTool(), SearchFilesTool(), DeleteFileTool(),
            RunCommandTool(), SendMailTool(), HardwareStatusTool(), FlashFirmwareTool()]


__all__ = ["all_system_tools"]
