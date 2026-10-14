"""Computer-use layer: app questions, shortcuts, pointer safety, typing, multi-step plans, retries, posting on X.

Mocked: the macOS desktop (FakeDesktop records every call and simulates the accessibility tree).
Real: router, assistant, task manager (plans, retries, Emergency Stop, approvals), tools and their verification.
Native macOS behaviour (CGEvent, AppleScript, Spotlight) is NOT exercised here — see docs/MANUAL_CHECKLIST.md.
"""

from __future__ import annotations

import asyncio

import pytest

from baymax.assistant import Assistant
from baymax.hostos.desktop import keystroke_plan
from baymax.tasks.models import PlanStep, TaskStatus
from baymax.tools.base import Tool, ToolResult

T = "\t"


def ax(role, name, x, y, w, h, desc="", value=""):
    return T.join([role, name, desc, value, str(x), str(y), str(w), str(h)])


@pytest.fixture
async def assistant(services):
    a = Assistant(services)
    a.start()
    yield a
    await a.stop()


def _text(r):
    return r["message"]["text"] if isinstance(r.get("message"), dict) else ""


async def _approve_next(services, approve=True):
    sub = services.bus.subscribe()
    while True:
        ev = await asyncio.wait_for(sub.get(), 5)
        if ev.type == "approval.request":
            services.tasks.respond_approval(ev.payload["approvalId"], approve, "local")
            return ev.payload


# ------------------------------------------------------------------ app questions
async def test_open_apps_question_lists_real_running_apps(services, assistant, fake_desktop):
    fake_desktop.running |= {"Safari", "Terminal"}
    fake_desktop.front = "Terminal"
    r = await assistant.handle_text("What applications are currently open?")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and t.verified
    assert "Safari" in t.result["summary"] and "Terminal (in front)" in t.result["summary"]


async def test_active_app_question_reads_the_front_window(services, assistant, fake_desktop):
    fake_desktop.front = "Code"
    r = await assistant.handle_text("Tell me which application is currently active.")
    t = await services.tasks.wait(r["taskId"], 5)
    assert "You're in Code" in t.result["summary"]


# ------------------------------------------------------------------ shortcuts
async def test_new_tab_in_safari_is_pressed_and_verified(services, assistant, fake_desktop):
    fake_desktop.running.add("Safari")
    fake_desktop.tab_url["Safari"] = "https://example.com/"
    r = await assistant.handle_text("Open a new tab in Safari")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    assert ("keys", "Safari", "t", ("command",)) in fake_desktop.calls


async def test_unsafe_shortcut_is_refused(services, assistant, fake_desktop):
    r = await assistant.handle_text("press command q")
    assert "taskId" not in r and "safe shortcuts" in _text(r) and fake_desktop.calls == []


# ------------------------------------------------------------------ pointer safety
async def test_click_outside_every_display_is_rejected(services, fake_desktop):
    task = services.tasks.submit("desktop", "click", "click", [PlanStep("click", "desktop.click", {"x": 99999.0, "y": 5.0})])
    t = await services.tasks.wait(task.id, 5)
    assert t.status is TaskStatus.FAILED and "isn't on any connected display" in t.result["summary"]
    assert not any(c[0] == "click" for c in fake_desktop.calls)


async def test_double_click_uses_pointer_events_at_the_verified_element_centre(services, assistant, fake_desktop):
    fake_desktop.front = "Finder"
    fake_desktop.ax_dump["Finder"] = ax("AXStaticText", "", 200, 300, 120, 20, value="README.md")
    r = await assistant.handle_text("double click README.md")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED, t.error
    assert ("click", 260.0, 310.0, "left", 2) in fake_desktop.calls  # centre of the real element, two clicks
    assert not t.verified  # nothing changed in the fake tree, so it is not claimed as verified


async def test_right_click_target_not_found_is_honest(services, assistant, fake_desktop):
    fake_desktop.front = "Finder"
    fake_desktop.ax_dump["Finder"] = ax("AXButton", "Share", 10, 10, 40, 20)
    r = await assistant.handle_text("right click the Projects folder")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and "couldn't find" in t.result["summary"]
    assert not any(c[0] == "click" for c in fake_desktop.calls)


# ------------------------------------------------------------------ typing
def test_keystroke_plan_keeps_unicode_and_turns_line_breaks_into_safe_newlines():
    plan = keystroke_plan("héllo 世界 👋\nsecond line\r\nend.")
    assert "".join(c for k, c in plan if k == "text") == "héllo 世界 👋second lineend."
    assert [k for k, _ in plan].count("newline") == 2  # Shift+Return, never a bare Return that would submit


async def test_multiline_unicode_prompt_reaches_the_focused_field_exactly(services, fake_desktop):
    fake_desktop.ax_dump["Safari"] = ax("AXTextArea", "", 400, 760, 640, 44, desc="Message ChatGPT")
    prompt = "Résumé für 東京:\nline two — ✓"
    task = services.tasks.submit("ai", "prepare", "prepare", [PlanStep("prepare", "ai.delegate", {"service": "chatgpt", "prompt": prompt})])
    t = await services.tasks.wait(task.id, 15)
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    assert ("type", "Safari", prompt) in fake_desktop.calls
    focus = fake_desktop.calls.index(("focus", "Safari", "AXTextArea", 400, 760))
    assert focus < fake_desktop.calls.index(("type", "Safari", prompt))  # focused before typing


# ------------------------------------------------------------------ multi-step plans
async def test_compound_request_runs_steps_in_order(services, assistant, fake_desktop):
    r = await assistant.handle_text("Open Safari and then open Terminal")
    t = await services.tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.COMPLETED and t.verified and len(t.plan) == 2
    opens = [c for c in fake_desktop.calls if c[0] == "open_app"]
    assert opens == [("open_app", "Safari"), ("open_app", "Terminal")]


async def test_plan_stops_at_the_first_failed_step(services, assistant, fake_desktop):
    r = await assistant.handle_text("Open Photoshop and then open Terminal")
    t = await services.tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.FAILED
    assert ("open_app", "Terminal") not in fake_desktop.calls and t.plan[1].status == "pending"


async def test_emergency_stop_interrupts_a_running_plan(services, assistant, fake_desktop):
    fake_desktop.after_open = lambda name: services.estop.engage("test") if name == "Safari" else None
    r = await assistant.handle_text("Open Safari and then open Terminal")
    t = await services.tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.CANCELLED and ("open_app", "Terminal") not in fake_desktop.calls


async def test_transient_failures_are_retried_once_and_only_once(services):
    calls = {"n": 0}

    class Flaky(Tool):
        name = "test.flaky"
        description = "fails transiently"
        intents = ("test",)
        timeout_s = 5
        verification = "n/a"
        recovery = "retry once"

        async def run(self, ctx, args):
            calls["n"] += 1
            if args.get("always") or calls["n"] == 1:
                return ToolResult(False, "still loading", {"errorKind": "not_found"}, retryable=True)
            return ToolResult(True, "ok", verified=True)

    from baymax.tools.base import Arg
    Flaky.args = {"always": Arg("bool", False, default=False)}
    services.tools.register(Flaky())
    t = await services.tasks.wait(services.tasks.submit("t", "f", "f", [PlanStep("f", "test.flaky", {})]).id, 10)
    assert t.status is TaskStatus.COMPLETED and calls["n"] == 2
    calls["n"] = 0
    t = await services.tasks.wait(services.tasks.submit("t", "f", "f", [PlanStep("f", "test.flaky", {"always": True})]).id, 10)
    assert t.status is TaskStatus.FAILED and calls["n"] == 2  # bounded: no endless repetition


# ------------------------------------------------------------------ posting on X
X_COMPOSER = ax("AXTextArea", "", 300, 200, 600, 120, desc="Post text")


async def test_draft_is_shown_and_nothing_is_posted(services, assistant, fake_desktop):
    r = await assistant.handle_text("Draft a post saying 'Building something cool.'")
    assert "Building something cool." in _text(r) and "Nothing has been posted" in _text(r) and fake_desktop.calls == []


async def test_publishing_the_draft_needs_confirmation_then_posts(services, assistant, fake_desktop):
    fake_desktop.ax_dump["Safari"] = X_COMPOSER
    fake_desktop.ax_after_submit["Safari"] = ax("AXStaticText", "", 300, 400, 300, 20, value="Building something cool.")
    await assistant.handle_text("Draft a post saying 'Building something cool.'")
    approve = asyncio.create_task(_approve_next(services))
    r = await assistant.handle_text("Open X, enter the post, and publish it.")
    payload = await approve
    assert payload["summary"].startswith("Publish this post on X") and "Building something cool." in payload["summary"]
    t = await services.tasks.wait(r["taskId"], 15)
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    assert ("open_url", "https://x.com/compose/post", "Safari") in fake_desktop.calls
    assert ("keys", "Safari", "return", ("command",)) in fake_desktop.calls


async def test_denied_publish_posts_nothing(services, assistant, fake_desktop):
    fake_desktop.ax_dump["Safari"] = X_COMPOSER
    approve = asyncio.create_task(_approve_next(services, approve=False))
    r = await assistant.handle_text("Post 'hello world' on X")
    await approve
    t = await services.tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.CANCELLED and not any(c[0] in ("open_url", "type", "keys") for c in fake_desktop.calls)


async def test_publish_it_without_a_draft_asks_for_the_text(services, assistant, fake_desktop):
    r = await assistant.handle_text("post it on X")
    assert "taskId" not in r and "What should the post say" in _text(r)


# ------------------------------------------------------------------ result contract
def test_result_outcomes_never_call_unverified_work_completed():
    assert ToolResult(True, "x", verified=True).outcome == "completed_verified"
    assert ToolResult(True, "x").outcome == "dispatched_unverified"
    assert ToolResult(False, "x", {"errorKind": "not_found"}).outcome == "target_not_found"
    assert ToolResult(False, "Which one?", needs_clarification="Which one?").outcome == "target_ambiguous"
    assert ToolResult(False, "x", {"errorKind": "permission_denied"}).outcome == "permission_denied"
    assert ToolResult(False, "x").to_dict()["outcome"] == "failed"


async def test_file_search_by_name_is_honest_without_spotlight(services, assistant, fake_desktop):
    import shutil

    r = await assistant.handle_text("Find the file named report.pdf.")
    t = await services.tasks.wait(r["taskId"], 10)
    if shutil.which("mdfind") is None:  # Linux CI: Spotlight doesn't exist, so the tool says so instead of inventing paths
        assert t.status is TaskStatus.FAILED and "Spotlight" in (t.error or t.result.get("summary", ""))


# ------------------------------------------------------------------ permission diagnosis (which API actually failed)
class _Res:
    def __init__(self, err, ok=False, stdout="", timed_out=False):
        self.ok, self.stdout, self.stderr, self.timed_out = ok, stdout, err, timed_out


class _Runner:
    def __init__(self, err, ok=False, stdout="", timed_out=False):
        self.res = _Res(err, ok, stdout, timed_out)
        self.argv = None

    async def run(self, argv, timeout=20):
        self.argv = argv
        return self.res


@pytest.mark.parametrize("err,expect", [
    ("execution error: Not authorized to send Apple events to Safari. (-1743)", "Automation"),
    ("System Events got an error: osascript is not allowed assistive access. (-1719)", "Accessibility"),
    ('System Events got an error: Can’t get process "Notes". (-1728)', "isn't running"),
    ("execution error: element not found (1404)", "element was not found"),
])
async def test_applescript_errors_name_the_permission_that_actually_failed(err, expect):
    from baymax.hostos.desktop import DesktopUnavailable, MacDesktop

    d = MacDesktop.__new__(MacDesktop)
    d.p = _Runner(err)
    with pytest.raises(DesktopUnavailable) as exc:
        await d._osascript("script", "Safari")
    assert expect in str(exc.value)
    if expect == "Automation":
        assert "Accessibility" not in str(exc.value) and "Safari" in str(exc.value)


def test_permission_report_lists_each_permission_with_its_fix(services):
    from baymax.capabilities import permission_report

    rep = {p["permission"]: p for p in permission_report(services)}
    assert {"Accessibility", "Screen Recording", "Automation", "Microphone"} <= set(rep)
    assert all(p["settings"].startswith(("System Settings", "iPhone Settings")) and p["fix"] for p in rep.values())


async def test_permission_instructions_are_not_repeated_every_message(services, assistant, fake_desktop):
    fake_desktop.accessibility = False
    texts = []
    for _ in range(2):
        r = await assistant.handle_text("look at the Run button")
        await services.tasks.wait(r["taskId"], 5)
        await asyncio.sleep(0.2)
        texts.append([m["text"] for m in services.conversation.recent(5) if m["role"] == "assistant"][-1])
    assert "Open System Settings" in texts[0]
    assert "Open System Settings" not in texts[1] and "still missing" in texts[1]


def test_applescripts_never_filter_entire_contents_with_whose():
    """Found on the real Mac: `every UI element of (entire contents …) whose …` fails with -1700. Scripts must iterate."""
    import re

    import baymax.hostos.desktop as dsk

    for name in dir(dsk):
        if name.startswith("_AS_"):
            src = getattr(dsk, name)
            code = "\n".join(line.split("--", 1)[0] for line in src.splitlines())  # ignore AppleScript comments
            assert not re.search(r"entire contents of [^\n]*\)\s*whose", code), name



# ------------------------------------------------------------------ MacDesktop focus / read-back / timeouts (osascript mocked)
def _mac(runner):
    from baymax.hostos.desktop import MacDesktop

    d = MacDesktop.__new__(MacDesktop)
    d.p = runner
    return d


async def test_focus_reports_missing_element_no_window_unfocused_and_timeout():
    from baymax.hostos.desktop import DesktopUnavailable

    cases = [(_Runner("execution error: element not found (1404)"), "not found"),
             (_Runner("execution error: no window (1406)"), "no window I can see"),
             (_Runner("", ok=True, stdout="unfocused\n"), "didn't let me put the cursor"),
             (_Runner("", timed_out=True), "didn't respond within")]
    for runner, expect in cases:
        with pytest.raises(DesktopUnavailable) as exc:
            await _mac(runner).focus_element("TextEdit", "AXTextArea", 10, 20)
        assert expect in str(exc.value), (expect, str(exc.value))
    ok = _Runner("", ok=True, stdout="ok\n")
    await _mac(ok).focus_element("TextEdit", "AXTextArea", 10.4, 19.6)
    assert ok.argv[-3:] == ["AXTextArea", "10", "20"]  # arguments passed as argv, never spliced into the script


async def test_element_value_returns_exact_text_with_line_breaks():
    runner = _Runner("", ok=True, stdout="Grüße 東京 👋\nsecond line\n")
    assert await _mac(runner).element_value("TextEdit", "AXTextArea", 1, 2) == "Grüße 東京 👋\nsecond line"


async def test_focus_refuses_non_text_roles():
    from baymax.hostos.desktop import DesktopUnavailable

    with pytest.raises(DesktopUnavailable):
        await _mac(_Runner("", ok=True, stdout="ok")).focus_element("TextEdit", "AXButton", 1, 2)
