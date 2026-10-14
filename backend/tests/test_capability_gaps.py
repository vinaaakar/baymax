"""Regressions for the iPhone test session (Oct 15): commands that misrouted, false success claims, the ChatGPT
timeout, and the basic TextEdit workflow.

Simulated: Ollama (a local HTTP server with the real /api/chat shape that always calls the first offered tool, or
answers with a configurable claim) and the macOS desktop (FakeDesktop). Real: router, relevance gate, assistant,
tools, task manager, Emergency Stop.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from conftest import FakeDesktop

from baymax.assistant import Assistant
from baymax.config import load_settings
from baymax.creation3d.classifier import RequestClassifier
from baymax.creation3d.registry import GeneratorRegistry
from baymax.hostos.desktop import DesktopPermissionError, DesktopUnavailable
from baymax.intent.relevance import tool_is_relevant
from baymax.intent.router import IntentRouter, split_compound
from baymax.security.secrets import SecretStore
from baymax.tasks.models import PlanStep, TaskStatus

R = IntentRouter(RequestClassifier(GeneratorRegistry()))
T = "\t"


def ax(role, name, x, y, w, h, desc="", value=""):
    return T.join([role, name, desc, value, str(x), str(y), str(w), str(h)])


TEXTEDIT_DOC = ax("AXTextArea", "", 327, 368, 586, 346, desc="text entry area")


@pytest.fixture
async def model():
    """Calls the first offered tool (worst case for a 3B model); with no tools, answers with `claim`."""
    state = {"claim": "Sure.", "bodies": []}

    async def tags(_r):
        return web.json_response({"models": [{"name": "llama3.2:3b"}]})

    async def chat(request):
        body = await request.json()
        state["bodies"].append(body)
        if body.get("tools"):
            fn = body["tools"][0]["function"]["name"]
            args = {"repo": "project phoenix", "goal": "run the tests"} if fn.startswith("coding__") else {}
            return web.json_response({"message": {"role": "assistant", "content": "",
                                                  "tool_calls": [{"function": {"name": fn, "arguments": args}}]}, "done": True})
        return web.json_response({"message": {"role": "assistant", "content": state["claim"]}, "done": True})

    app = web.Application()
    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}", state
    await runner.cleanup()


@pytest.fixture
async def env(tmp_path, model):
    url, state = model
    settings = load_settings(tmp_path / "data", {
        "workspace_dir": str(tmp_path / "ws"), "network": {"local_port": 0},
        "llm": {"base_url": url, "connect_timeout_s": 1, "request_timeout_s": 5},
        "voice": {"tts_engine": "none", "whisper_binary": "definitely-not-installed"}, "research": {"local_only": True},
    })
    settings.ensure_dirs()
    from baymax.services import build_services

    desk = FakeDesktop()
    s = build_services(settings, desktop=desk, secrets=SecretStore(settings.data_dir, use_keychain=False))
    a = Assistant(s)
    a.start()
    yield {"s": s, "a": a, "desk": desk, "state": state}
    await a.stop()
    await s.close()


def _text(r):
    return r["message"]["text"] if isinstance(r.get("message"), dict) else ""


# ------------------------------------------------------------------ routing: the commands from the test session
@pytest.mark.parametrize("q,tool,args", [
    ("Open TextEdit", "apps.open", {"app": "TextEdit"}),
    ("Create a new TextEdit document", "desktop.new_document", {"app": "TextEdit"}),
    ("Create a new text document", "desktop.new_document", {"app": "TextEdit"}),
    ("make a new document", "desktop.new_document", {"app": "TextEdit"}),
    ("open a new blank document in TextEdit", "desktop.new_document", {"app": "TextEdit"}),
    ("Identify the active window", "desktop.active_window", {}),
    ("Identify the active window under CA", "desktop.active_window", {}),  # transcription noise after the command
    ("which is the front window", "desktop.active_window", {}),
    ("Move the pointer to the center of the screen", "desktop.move_cursor", {"position": "center"}),
    ("put the mouse in the middle", None, None),  # "in" is not "to": not this rule (falls through, no crash)
    ("Who the pointer to the center of the screen", "desktop.move_cursor", {"position": "center"}),  # misheard verb
    ("Type three separate lines into TextEdit", "screen.type_into",
     {"field": "text area", "text": "Line 1\nLine 2\nLine 3", "app": "TextEdit"}),
    ("type 2 lines into TextEdit", "screen.type_into", {"field": "text area", "text": "Line 1\nLine 2", "app": "TextEdit"}),
    ("Read the current window", "desktop.read_window", {}),
    ("Open ChatGPT", "apps.open", {"app": "Chatgpt"}),
    ("Open ChatGPT and do some research based on how AI is are working right now", "ai.delegate",
     {"service": "chatgpt", "prompt": "Research this and summarize it with sources: how AI is are working right now", "submit": True}),
])
def test_session_commands_route_to_the_right_tool(q, tool, args):
    i = R.route(q)
    if tool is None:
        assert i.tool != "desktop.move_cursor"
        return
    assert i.kind == "tool" and i.tool == tool and i.args == args, (i.kind, i.tool, i.args)


def test_create_document_then_type_lines_into_it_is_one_plan_in_the_same_app():
    steps = split_compound(R, "create a new TextEdit document and type three lines into it")
    assert [(s.tool, s.args.get("app")) for s in steps] == [("desktop.new_document", "TextEdit"), ("screen.type_into", "TextEdit")]


def test_writing_a_document_about_something_still_goes_to_the_document_writer():
    assert R.route("write a document about rockets").tool == "documents.write"


# ------------------------------------------------------------------ "Repository not found: project phoenix"
TEXT_ENTRY = ["Test multiple line text with three separate", "type three lines of text", "test typing a multiline text",
              "write three lines of text in TextEdit", "enter some text with line breaks", "test the text document"]


@pytest.mark.parametrize("q", TEXT_ENTRY)
def test_text_entry_requests_never_offer_repository_or_code_tools(q):
    for tool in ("coding.agent", "coding.inspect", "coding.find", "coding.java_class", "apps.open_project", "shell.run"):
        assert not tool_is_relevant(tool, q), (tool, q)


@pytest.mark.parametrize("q,tool", [("run the tests in my project", "coding.agent"), ("run the unit tests", "coding.agent"),
                                    ("fix the failing tests in the repo", "coding.agent"), ("open my repository in VS Code", "apps.open_project")])
def test_real_code_requests_keep_their_tools(q, tool):
    assert tool_is_relevant(tool, q)


async def test_multiline_text_test_asks_instead_of_touching_a_repository(env):
    env["a"].session_facts.clear()
    await env["a"].handle_text("Remember this for our current conversation: my test project is called Project Phoenix.")
    r = await env["a"].handle_text("Test multiple line text with three separate")
    text = _text(r)
    assert "taskId" not in r and "type three lines" in text and "Repository" not in text
    assert not any(c[0] in ("open_path", "type") for c in env["desk"].calls)
    assert not any(b.get("tools") for b in env["state"]["bodies"])  # answered by the rule, no model tool call


# ------------------------------------------------------------------ "Baymax display auto": no invented results
@pytest.mark.parametrize("claim", ["I've set your desktop background to Auto. The previous setting was Dynamic.",
                                   "Your desktop background has been set to Auto.", "Done! Display is now on Auto."])
async def test_a_reply_claiming_an_unperformed_setting_change_is_replaced(env, claim):
    env["state"]["claim"] = claim
    r = await env["a"].handle_text("Baymax display auto")
    text = _text(r)
    assert "taskId" not in r and env["desk"].calls == []
    assert "didn't do that" in text and "nothing on your Mac was changed" in text and "previous setting" not in text


async def test_a_question_keeps_its_answer_but_gets_a_note(env):
    env["state"]["claim"] = "The display was set to auto brightness in macOS 10.13."
    r = await env["a"].handle_text("What does auto brightness do?")
    assert "auto brightness" in _text(r) and "no action was actually performed" in _text(r)


# ------------------------------------------------------------------ verified TextEdit workflow (FakeDesktop)
async def test_new_document_is_confirmed_by_its_window(env):
    r = await env["a"].handle_text("Create a new text document")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and t.verified and "Untitled" in t.result["summary"]
    assert ("new_document", "TextEdit") in env["desk"].calls


async def test_new_document_without_a_visible_window_is_not_called_verified(env):
    async def no_windows(app):
        return []

    env["desk"].window_titles = no_windows
    task = env["s"].tasks.submit("desktop", "doc", "new doc", [PlanStep("doc", "desktop.new_document", {"app": "TextEdit"})])
    t = await env["s"].tasks.wait(task.id, 10)
    assert not t.verified and "couldn't see its window" in t.result["summary"]


async def test_new_document_permission_and_timeout_failures_are_reported(env):
    async def denied(app):
        raise DesktopPermissionError("BAYMAX needs the Automation permission to control TextEdit.")

    env["desk"].new_document = denied
    task = env["s"].tasks.submit("desktop", "doc", "new doc", [PlanStep("doc", "desktop.new_document", {"app": "TextEdit"})])
    t = await env["s"].tasks.wait(task.id, 5)
    assert t.status is TaskStatus.FAILED and "Automation" in (t.error or t.result.get("summary", ""))

    async def slow(app):
        raise DesktopUnavailable("macOS didn't respond within 20 seconds (the app may be busy or showing a dialog)")

    env["desk"].new_document = slow
    task = env["s"].tasks.submit("desktop", "doc", "new doc", [PlanStep("doc", "desktop.new_document", {"app": "TextEdit"})])
    t = await env["s"].tasks.wait(task.id, 5)
    assert t.status is TaskStatus.FAILED and "didn't respond within 20 seconds" in (t.error or t.result.get("summary", ""))


async def test_create_then_type_three_lines_reads_them_back(env):
    env["desk"].installed.append(__import__("baymax.hostos.apps", fromlist=["AppInfo"]).AppInfo("TextEdit", "", "com.apple.TextEdit"))
    env["desk"].ax_dump["TextEdit"] = TEXTEDIT_DOC
    r = await env["a"].handle_text("create a new TextEdit document and type three lines into it")
    t = await env["s"].tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    assert env["desk"].field_raw[("TextEdit", "AXTextArea", 327, 368)] == "Line 1\nLine 2\nLine 3"
    assert "haven't submitted" in t.result["summary"]


async def test_typing_lines_with_no_document_open_types_nothing(env):
    env["desk"].running.add("TextEdit")
    env["desk"].ax_dump["TextEdit"] = ""  # e.g. only the Open panel is showing
    r = await env["a"].handle_text("Type three separate lines into TextEdit")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and "didn't type anything" in t.result["summary"]
    assert not [c for c in env["desk"].calls if c[0] == "type"]


async def test_pointer_to_center_is_read_back(env):
    r = await env["a"].handle_text("Move the pointer to the center of the screen")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and t.verified
    assert env["desk"].cursor == (756.0, 491.0)  # centre of the primary 1512x982 display, not the external one


async def test_active_window_is_identified_from_macos(env):
    env["desk"].front, env["desk"].window_title = "TextEdit", "Untitled 3"
    r = await env["a"].handle_text("Identify the active window")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and "TextEdit" in t.result["summary"] and "Untitled 3" in t.result["summary"]


# ------------------------------------------------------------------ ChatGPT: bounded search with stage diagnostics
async def test_chatgpt_with_an_unreadable_huge_window_reports_the_stage_not_a_bare_timeout(env, monkeypatch):
    import baymax.tools.delegate_tools as dt
    from baymax.hostos.apps import AppInfo

    monkeypatch.setattr(dt, "FIELD_SEARCH_S", 0.6)
    desk = env["desk"]
    desk.installed.append(AppInfo("ChatGPT", "", "com.openai.chat"))
    desk.ax_dump["ChatGPT"] = ax("AXGroup", "conversation", 0, 0, 800, 600)  # no prompt box within the readable part
    desk.last_ax_window = {"name": "ChatGPT", "windows": 1, "truncated": True}
    task = env["s"].tasks.submit("delegate", "d", "chatgpt", [PlanStep("d", "ai.delegate", {"service": "chatgpt", "prompt": "hi"})])
    t = await env["s"].tasks.wait(task.id, 10)
    summary = t.result["summary"]
    assert t.status is TaskStatus.FAILED and "opened ChatGPT" in summary and "more interface elements than I can read" in summary
    assert "Nothing was typed or sent" in summary and t.result["data"]["searchAttempts"] >= 1
    assert not [c for c in desk.calls if c[0] in ("type", "keys", "return")]


async def test_reading_the_window_failing_after_opening_names_the_stage(env):
    from baymax.hostos.apps import AppInfo

    desk = env["desk"]
    desk.installed.append(AppInfo("ChatGPT", "", "com.openai.chat"))

    async def times_out(app, limit=400):
        raise DesktopUnavailable("macOS didn't respond within 25 seconds (the app may be busy or showing a dialog)")

    desk.accessibility_elements = times_out
    task = env["s"].tasks.submit("delegate", "d", "chatgpt", [PlanStep("d", "ai.delegate", {"service": "chatgpt", "prompt": "hi"})])
    t = await env["s"].tasks.wait(task.id, 10)
    assert t.status is TaskStatus.FAILED and "I opened ChatGPT, but reading its window failed" in t.result["summary"]


def test_locate_walk_is_bounded_and_never_builds_entire_contents():
    from baymax.hostos import desktop as dk

    assert "entire contents of" not in dk._AS_LOCATE  # built the whole tree first: > 25 s in the ChatGPT app
    assert "secondsSince(t0)) > budget" in dk._AS_LOCATE and "wasCut" in dk._AS_LOCATE


async def test_locate_passes_its_budget_inside_the_hard_timeout():
    from baymax.hostos.desktop import MacDesktop

    seen = {}

    class Res:
        ok, stdout, stderr, timed_out = True, "#window\t1\tmain\tX\tAXStandardWindow\t1\n", "", False

    class Runner:
        async def run(self, argv, timeout=20):
            seen["argv"], seen["timeout"] = argv, timeout
            return Res()

    d = MacDesktop.__new__(MacDesktop)
    d.p = Runner()
    await d.accessibility_elements("ChatGPT")
    assert seen["argv"][-1] == "12" and seen["timeout"] == 25 and d.last_ax_window["truncated"] is True


# ------------------------------------------------------------------ Emergency Stop during a harmless plan
async def test_emergency_stop_mid_plan_stops_typing_and_skips_later_steps(env):
    desk = env["desk"]
    desk.ax_dump["TextEdit"] = TEXTEDIT_DOC
    desk.running.add("TextEdit")
    started = asyncio.Event()
    real_type = desk.type_text

    async def slow_type(app, text):
        started.set()
        await asyncio.sleep(5)  # stands in for a long typing run
        await real_type(app, text)

    desk.type_text = slow_type
    gen = desk._input_gen
    task = env["s"].tasks.submit("desktop", "p", "type then read", [
        PlanStep("type", "screen.type_into", {"field": "text area", "text": "Line 1\nLine 2", "app": "TextEdit"}),
        PlanStep("read", "desktop.read_window", {"app": "TextEdit"})])
    await asyncio.wait_for(started.wait(), 5)
    env["s"].estop.engage("test")
    t = await env["s"].tasks.wait(task.id, 5)
    assert t.status is TaskStatus.CANCELLED and desk._input_gen == gen + 1
    assert not [c for c in desk.calls if c[0] in ("type", "read_window")]  # typing never finished; later step never ran
    env["s"].estop.reset()


# ------------------------------------------------------------------ active window = the app macOS says is in front
class _FakeCG:
    kCGWindowListOptionOnScreenOnly = 1
    kCGWindowListExcludeDesktopElements = 16
    kCGNullWindowID = 0

    def __init__(self, windows):
        self.windows = windows

    def CGWindowListCopyWindowInfo(self, *_a):
        return self.windows


def _cgw(owner, title, x, y, w, h):
    return {"kCGWindowOwnerName": owner, "kCGWindowName": title, "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}


def _mac_with(front, windows):
    from baymax.hostos.desktop import MacDesktop

    d = MacDesktop.__new__(MacDesktop)
    d._quartz = _FakeCG(windows)

    async def osa(script, *args, timeout=20.0):
        return front + "\n"

    d._osascript = osa
    return d


async def test_active_window_is_the_frontmost_apps_window_not_the_first_listed_strip():
    # Seen on the real Mac: "You're in Terminal; the window is titled “0”" (a 1710x68 strip) while TextEdit was in front.
    d = _mac_with("TextEdit", [_cgw("Terminal", "0", 0, 39, 1710, 68), _cgw("Terminal", "zsh", 0, 100, 1200, 800),
                               _cgw("TextEdit", "Untitled 3", 200, 120, 900, 700)])
    w = await d.active_window()
    assert (w.app, w.title) == ("TextEdit", "Untitled 3")
    assert await d.target_app() == "TextEdit"


async def test_when_baymax_is_in_front_the_window_behind_it_is_used_and_strips_are_skipped():
    d = _mac_with("Electron", [_cgw("Electron", "BAYMAX", 0, 0, 640, 400), _cgw("Terminal", "0", 0, 39, 1710, 68),
                               _cgw("Safari", "Apple", 100, 100, 1200, 800)])
    w = await d.active_window()
    assert (w.app, w.title) == ("Safari", "Apple") and await d.target_app() == "Safari"


# ------------------------------------------------------------------ "Open ChatGPT on the Safari and do some research…"
def test_research_in_a_named_browser_uses_that_browsers_website():
    i = R.route("Open ChatGPT on the Safari and do some research on how AI works")
    assert i.tool == "ai.delegate" and i.args == {"service": "chatgpt", "via": "browser", "browser": "Safari", "submit": True,
                                                  "prompt": "Research this and summarize it with sources: how AI works"}


@pytest.mark.parametrize("said,proposed,expect", [
    ("Open ChatGPT on the Safari and do some research", "chatgpt.ai", "chatgpt"),  # seen: invented domain -> real site
    ("open github.com", "github.com", "github.com"),
    ("open my bank", "mybank-login.com", None),  # never open an address the user didn't give
    ("open youtube", "https://youtube.example.net", "youtube"),
])
def test_model_proposed_websites_must_come_from_the_request(said, proposed, expect):
    from baymax.hostos.sites import ground_destination, resolve_destination

    got = ground_destination(said, proposed)
    assert got == expect
    if got == "chatgpt":
        assert resolve_destination(got).host == "chatgpt.com"


async def test_model_invented_url_is_corrected_and_the_named_browser_used(env):
    from baymax.intent.router import Intent

    proposal = Intent("tool", "apps.open_url", {"url": "chatgpt.ai"}, "open", source="model", text="open chatgpt on the safari please")
    r = await env["a"]._start_tool(proposal, __import__("baymax.assistant", fromlist=["Origin"]).Origin("local", False))
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert ("open_url", "https://chatgpt.com", "Safari") in env["desk"].calls, env["desk"].calls
    assert t.status is TaskStatus.COMPLETED


def test_locate_asks_electron_apps_to_expose_their_interface():
    from baymax.hostos import desktop as dk

    assert 'set value of attribute "AXManualAccessibility" to true' in dk._AS_LOCATE
