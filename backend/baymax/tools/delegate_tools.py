"""Delegate a prompt to another AI application (ChatGPT, Claude, Gemini, Perplexity) the way a person would.

Resolve the service → native app if installed (unless the browser was asked for), else its website in a browser →
launch/focus → read the live accessibility tree → find the prompt field → focus it → type the prompt → read the field
back to verify → submit only when the request asked for it (which makes the whole task need approval, showing the exact
prompt) → verify the submission. Login screens, CAPTCHAs and changed interfaces stop the task with an explanation.

BAYMAX never claims the other AI answered unless its reply was actually read from the screen.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from ..hostos.apps import resolve_app
from ..hostos.desktop import DesktopPermissionError, DesktopUnavailable
from ..hostos.sites import BROWSERS, resolve_destination, same_site
from ..screen.elements import TEXT_INPUT_ROLES, ScreenElement
from ..security.policy import Risk
from .base import Arg, Tool, ToolContext, ToolResult, ToolUnavailable

FIELD_SEARCH_S = 30.0  # how long to look for the prompt box after opening the app/site


@dataclass(frozen=True)
class AIService:
    key: str
    label: str
    apps: tuple[str, ...]  # native macOS apps, if the vendor ships one
    site: str              # key in hostos.sites.KNOWN_SITES, or a full URL
    submit_keys: tuple[str, tuple[str, ...]] = ("return", ())  # how the page submits (X posts with ⌘Return)
    category: str = "send_message"  # policy category when submitting (publishing needs approval too)
    noun: str = "prompt"


SERVICES = {
    "chatgpt": AIService("chatgpt", "ChatGPT", ("ChatGPT",), "chatgpt"),
    "claude": AIService("claude", "Claude", ("Claude",), "claude"),
    "gemini": AIService("gemini", "Gemini", (), "gemini"),  # no native Mac app: always the website
    "perplexity": AIService("perplexity", "Perplexity", ("Perplexity",), "perplexity"),
    # Posting on X uses the same verified flow: composer found in the accessibility tree, text read back, publish only
    # after the user approved the exact post.
    "x": AIService("x", "X", (), "https://x.com/compose/post", ("return", ("command",)), "publish", "post"),
}
_ALIASES = {"chat gpt": "chatgpt", "openai": "chatgpt", "gpt": "chatgpt", "google gemini": "gemini", "bard": "gemini",
            "anthropic": "claude", "claude ai": "claude", "twitter": "x"}

_PROMPT_HINT = re.compile(r"\b(message|ask|prompt|chat|reply|type|enter|send|question|anything|help|post|happening|tweet|compose)\b", re.I)
_LOGIN_HINT = re.compile(r"\b(log ?in|sign ?in|sign up|create (an )?account|verify you are human|captcha|are you a robot|continue with (google|apple|email))\b", re.I)


def resolve_service(name: str) -> AIService | None:
    key = re.sub(r"[^a-z ]", "", (name or "").lower()).strip()
    key = _ALIASES.get(key, key.replace(" ", ""))
    return SERVICES.get(key)


def _norm(t: str) -> str:
    return " ".join(re.findall(r"\w+", (t or "").lower()))


def pick_prompt_field(els: list[ScreenElement]) -> ScreenElement | None:
    """The chat input: a text area/field labelled like a prompt box, else the lowest large text area on screen."""
    fields = [e for e in els if e.role in TEXT_INPUT_ROLES and e.width >= 120]
    if not fields:
        return None
    hinted = [e for e in fields if _PROMPT_HINT.search(f"{e.label} {e.description}")]
    pool = hinted or [e for e in fields if e.role == "AXTextArea"] or fields
    return max(pool, key=lambda e: (e.y + e.height, e.width))  # chat boxes sit at the bottom of the window


class DelegateToAITool(Tool):
    name = "ai.delegate"
    description = ("Enter a prompt into another AI app or website (ChatGPT, Claude, Gemini, Perplexity), e.g. 'ask ChatGPT to "
                   "explain recursion' or 'prepare this prompt in Claude'. Submits only when asked, after approval.")
    risk = Risk.MEDIUM
    args = {"service": Arg("str", max_len=40, description="chatgpt | claude | gemini | perplexity"),
            "prompt": Arg("str", max_len=4000, description="exact text to enter"),
            "submit": Arg("bool", False, default=False, description="press Send after entering (needs approval)"),
            "via": Arg("str", False, default="auto", choices=("auto", "app", "browser")),
            "new_chat": Arg("bool", False, default=False, description="start a new chat first (⌘N in the app)"),
            "browser": Arg("str", False, default="", max_len=60)}

    def classify(self, args: dict[str, Any]) -> tuple[Risk, frozenset[str]]:
        # Sending text to a third-party service (or publishing) is externally visible: the user approves the exact text first.
        svc = resolve_service(args.get("service", ""))
        return (Risk.HIGH, frozenset({svc.category if svc else "send_message"})) if args.get("submit") else (Risk.MEDIUM, frozenset())

    def describe(self, args: dict[str, Any]) -> str:
        svc = resolve_service(args.get("service", ""))
        name = svc.label if svc else args.get("service", "the AI service")
        if svc and svc.key == "x":
            verb = "Publish this post on X" if args.get("submit") else "Write this post in X's composer (not published)"
            return f"{verb}: “{args.get('prompt', '')[:400]}”"
        verb = "Send this prompt to" if args.get("submit") else "Type this prompt into"
        return f"{verb} {name}: “{args.get('prompt', '')[:400]}”"

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        d = ctx.services.desktop
        svc = resolve_service(args["service"])
        if svc is None:
            q = "Which AI should I use: ChatGPT, Claude, Gemini or Perplexity?"
            return ToolResult(False, q, {"errorKind": "invalid_arguments"}, needs_clarification=q)
        prompt = args["prompt"].strip()
        if not prompt:
            return ToolResult(False, f"What should I ask {svc.label}?", {"errorKind": "invalid_arguments"},
                              needs_clarification=f"What should I ask {svc.label}?")
        data: dict[str, Any] = {"service": svc.key, "submitted": False}
        try:
            # 1-3: resolve and open — native app if installed (unless the browser was requested), else the website.
            running, installed = await d.list_apps()
            native = None
            if args.get("via") != "browser":
                for app in svc.apps:
                    r = resolve_app(app, running, installed)
                    if r.status == "resolved" and r.app and r.app.name.lower() == app.lower():
                        native = r.app
                        break
            if args.get("via") == "app" and native is None:
                return ToolResult(False, f"The {svc.label} app isn't installed on this Mac. I can use {svc.label} in the browser instead.",
                                  {**data, "errorKind": "not_found"})
            if native is not None:
                ctx.progress(0.1, f"opening the {native.name} app")
                await d.open_app(native.name)
                target, data["via"] = native.process or native.name, "app"
                if args.get("new_chat"):  # "create a new chat": the app's own New Chat shortcut (the website root is a new chat)
                    await asyncio.sleep(0.8)
                    await d.press_keys(target, "n", ("command",))
                    data["newChat"] = True
                    await asyncio.sleep(0.6)
            else:
                browser = "Safari"
                if args.get("browser"):
                    r = resolve_app(args["browser"], running, installed)
                    if r.status != "resolved" or r.app is None or r.app.name not in BROWSERS:
                        return ToolResult(False, f"I couldn't find a browser called “{args['browser']}”.", {**data, "errorKind": "not_found"})
                    browser = r.app.name
                dest = resolve_destination(svc.site)
                if dest is None:
                    return ToolResult(False, f"I don't know {svc.label}'s web address.", {**data, "errorKind": "not_found"})
                ctx.progress(0.1, f"opening {dest.host} in {browser}")
                await d.open_url(dest.url, browser)
                target, data["via"], data["browser"] = browser, "browser", browser
                url = None
                for _ in range(16):
                    ctx.checkpoint()
                    url = await d.browser_url(browser)
                    if url and same_site(url, dest):
                        data["url"] = url
                        break
                    await asyncio.sleep(0.25)
                if "url" not in data:
                    # The page in front is not confirmed to be the service: never search it for a text box or type into
                    # it (a fallback field could belong to a different site).
                    seen = (f"{browser} is showing {url}" if url else
                            f"I couldn't read {browser}'s address bar (allow BAYMAX under Automation for {browser})")
                    return ToolResult(False, f"I asked {browser} to open {dest.host}, but I couldn't confirm it is showing "
                                             f"{svc.label}: {seen}. Nothing was typed or sent.",
                                      {**data, "actualUrl": url, "errorKind": "not_verified"})
            # 4-5: inspect the live interface and find the prompt field (pages and apps take a moment to load).
            data["stage"] = "opened"
            ctx.progress(0.35, f"looking for {svc.label}'s message box")
            field, els = None, []
            deadline = time.monotonic() + FIELD_SEARCH_S  # pages/apps take a moment to load, but never search forever
            attempts = 0
            while True:
                ctx.checkpoint()
                attempts += 1
                els = await d.accessibility_elements(target)
                win = getattr(d, "last_ax_window", None) or {}
                data["window"] = {k: win.get(k) for k in ("name", "windows", "truncated") if k in win}
                field = pick_prompt_field(els)
                if field is not None or time.monotonic() > deadline:
                    break
                if attempts >= 2 and _LOGIN_HINT.search(" ".join(f"{e.label} {e.value}" for e in els)):
                    break  # a sign-in page won't turn into a prompt box by waiting
                await asyncio.sleep(0.5)
            data["searchAttempts"], data["elementsRead"] = attempts, len(els)
        except DesktopPermissionError as exc:
            raise ToolUnavailable(str(exc)) from None
        except DesktopUnavailable as exc:
            where = (f"I opened {svc.label}, but reading its window failed" if data.get("stage") == "opened"
                     else f"I couldn't open {svc.label}")
            return ToolResult(False, f"{where}: {exc}. Nothing was typed or sent.", {**data, "errorKind": "unsupported"})
        if field is None:
            text = " ".join(f"{e.label} {e.value}" for e in els)
            if _LOGIN_HINT.search(text) or (data.get("url") and re.search(r"/(login|signin|i/flow/login)", str(data.get("url")))):
                msg = (f"{svc.label} is asking you to sign in or verify first. Please do that in its window, then ask me again — "
                       "I won't enter your credentials.")
                return ToolResult(False, msg, {**data, "errorKind": "login_required"}, needs_clarification=msg)
            win = data.get("window") or {}
            why = ("its window has more interface elements than I can read in time" if win.get("truncated")
                   else "no window of it is visible on this desktop" if win.get("windows") == 0
                   else "its interface may have changed or still be loading")
            shown = f" in its window “{win['name']}”" if win.get("name") else ""
            return ToolResult(False, f"I opened {svc.label} but couldn't find its message box within {FIELD_SEARCH_S:.0f} seconds "
                                     f"(checked {len(els)} interface elements{shown}; {why}). Nothing was typed or sent.",
                              {**data, "errorKind": "not_found"})
        # 6-7: type the prompt into that field and read it back.
        ctx.checkpoint()
        ctx.progress(0.55, "entering the prompt")
        try:
            await d.focus_element(target, field.role, field.x, field.y)
            await d.type_text(target, prompt)
            entered = False
            for _ in range(6):
                await asyncio.sleep(0.3)
                try:  # exact read-back of the field we typed into (line breaks kept)
                    raw = (await d.element_value(target, field.role, field.x, field.y)).replace("\u2028", "\n").replace("\r", "\n")
                    if prompt.replace("\r\n", "\n") in raw:
                        entered = True
                        break
                except DesktopUnavailable:
                    pass
                after = await d.accessibility_elements(target)  # fallback: the (flattened) accessibility dump
                now = pick_prompt_field(after)
                if now is not None and _norm(prompt)[:60] in _norm(now.value):
                    entered = True
                    break
        except DesktopPermissionError as exc:
            raise ToolUnavailable(str(exc)) from None
        except DesktopUnavailable as exc:
            return ToolResult(False, f"I found {svc.label}'s message box but couldn't type into it: {exc}.", {**data, "errorKind": "unsupported"})
        data["entered"] = entered
        if not args.get("submit"):
            if entered:
                return ToolResult(True, f"I typed your prompt into {svc.label}. It's not sent yet — press Return there, or ask me to send it.",
                                  data, verified=True, verification="prompt text read back from the message box")
            return ToolResult(True, f"I typed your prompt into {svc.label}, but I couldn't read the box back to confirm it.", data,
                              verified=False, verification="message box value unreadable")
        if not entered:
            return ToolResult(False, f"I didn't {'publish' if svc.key == 'x' else 'send'} anything: I couldn't confirm the text was in "
                                     f"{svc.label}'s {'composer' if svc.key == 'x' else 'message box'}.", {**data, "errorKind": "not_verified"})
        # 8-9: submit (already approved by the user for this exact prompt) and verify it left the box.
        ctx.checkpoint()
        ctx.progress(0.8, "sending")
        key, mods = svc.submit_keys
        if mods:
            await d.press_keys(target, key, mods)
        else:
            await d.press_return(target)
        for _ in range(10):
            await asyncio.sleep(0.4)
            after = await d.accessibility_elements(target)
            now = pick_prompt_field(after)
            if now is None or _norm(prompt)[:60] not in _norm(now.value):
                data["submitted"] = True
                shown = any(_norm(prompt)[:40] in _norm(f"{e.label} {e.value}") for e in after if e.role not in TEXT_INPUT_ROLES)
                if svc.key == "x":
                    return ToolResult(True, "Published your post on X" + ("." if shown else " (the composer cleared; I couldn't see the post "
                                      "in the timeline yet)."), data, verified=True, verification="composer cleared after posting")
                return ToolResult(True, f"Sent your prompt to {svc.label}. Its answer will appear in its window"
                                        + ("" if shown else " (I couldn't see the message in the conversation yet)")
                                        + ". I don't read its answer back, so I haven't checked or summarized it.",
                                  data, verified=True, verification="message box cleared after sending")
        return ToolResult(False, f"I pressed Send in {svc.label}, but the prompt is still in the box, so it may not have been sent.",
                          {**data, "errorKind": "not_verified"})


def all_delegate_tools() -> list[Tool]:
    return [DelegateToAITool()]
