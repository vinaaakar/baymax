"""Phase 2: step results passed between plan steps, Mac session/display state, window selection, blocking dialogs,
and what the iPhone is told. Simulated (Linux): the macOS desktop (FakeDesktop with injected session/dialog/window
state) and Spotlight results. Real: router, assistant, planner, references, preflight, task manager, policy, hub mapping.
None of these tests is evidence of real lock-screen or dialog behaviour on a Mac (see tests_macos for that).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

import baymax.tasks.preflight as preflight
from baymax.hostos.desktop import DesktopPermissionError, parse_modal_line, parse_window_lines
from baymax.security.user_files import check_user_file
from baymax.tasks.models import PlanStep, TaskStatus
from baymax.tasks.refs import RefFailure, check_plan, resolve
from baymax.tools.base import ArgumentError, ToolResult
from test_capability_gaps import env, model  # noqa: F401  (fixtures)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "Documents").mkdir(parents=True)
    (h / "Desktop").mkdir()
    (h / "Library" / "Keychains").mkdir(parents=True)
    (h / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(preflight, "POLL_S", 0.05)


def _file(home: Path, rel: str, text: str = "x") -> str:
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return str(p)


async def _submit(env, steps, timeout=10):
    task = env["s"].tasks.submit("t", "t", "t", [PlanStep(t, t, a) for t, a in steps])
    return await env["s"].tasks.wait(task.id, timeout)


async def _events(sub, wait=0.3):
    out = []
    while True:
        try:
            out.append(await asyncio.wait_for(sub.get(), wait))
        except asyncio.TimeoutError:
            return out


# ================================================================== A. results passed between steps
async def test_unique_file_is_found_then_opened_through_a_verified_reference(env, home):
    path = _file(home, "Documents/report.pdf")
    env["desk"].files = [path]
    r = await env["a"].handle_text("Find the file named report.pdf and open it", source="device:iphone")
    t = await env["s"].tasks.wait(r["taskId"], 10)
    assert t.plan[1].args["path"] == {"$from": 0, "output": "path"} and t.plan[1].depends_on_previous
    assert t.status is TaskStatus.COMPLETED and t.verified, t.result
    assert ("open_path", "default", str(Path(path).resolve())) in env["desk"].calls


async def test_several_matches_ask_which_one_then_the_answer_resumes_with_that_file(env, home):
    a = _file(home, "Documents/report.pdf")
    b = _file(home, "Desktop/report.pdf")
    env["desk"].files = [a, b]
    r = await env["a"].handle_text("Find the file named report.pdf and open it", source="device:iphone")
    t = await env["s"].tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.FAILED and t.error_kind == "ambiguous_reference"
    assert "Which one" in t.result["summary"] and a in t.result["summary"] and b in t.result["summary"]
    assert not [c for c in env["desk"].calls if c[0] == "open_path"]  # never guessed
    await asyncio.sleep(0.1)  # let the assistant record the pending choice
    r2 = await env["a"].handle_text("number 2", source="device:iphone")
    t2 = await env["s"].tasks.wait(r2["taskId"], 10)
    assert t2.status is TaskStatus.COMPLETED and t2.verified
    assert env["desk"].calls[-1][:2] == ("open_path", "default") and env["desk"].calls[-1][2] == str(Path(b).resolve())


async def test_a_choice_answer_from_another_device_is_not_taken(env, home):
    env["desk"].files = [_file(home, "Documents/a.txt"), _file(home, "Desktop/a.txt")]
    r = await env["a"].handle_text("find the file named a.txt and open it", source="device:iphone")
    await env["s"].tasks.wait(r["taskId"], 10)
    await asyncio.sleep(0.1)
    r2 = await env["a"].handle_text("number 1", source="local")
    assert "taskId" not in r2 or env["s"].tasks.get(r2["taskId"]).plan[0].tool != "files.open"


async def test_no_match_stops_and_the_open_step_never_runs(env, home):
    env["desk"].files = []
    r = await env["a"].handle_text("Find the file named nothing.pdf and open it")
    t = await env["s"].tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.FAILED and "no file named" in t.result["summary"]
    assert t.plan[1].status == "pending" and not [c for c in env["desk"].calls if c[0] == "open_path"]


async def test_a_file_that_disappears_before_use_is_not_opened(env, home):
    path = _file(home, "Documents/gone.txt")
    desk = env["desk"]
    desk.files = [path]
    real_state = desk.session_state

    async def state_and_delete():  # runs before step 2 (a desktop step): the file vanishes after step 1 found it
        if os.path.exists(path):
            os.remove(path)
        return await real_state()

    desk.session_state = state_and_delete
    t = await _submit(env, [("files.find_by_name", {"name": "gone.txt"}), ("apps.open", {"app": "Safari"}),
                            ("files.open", {"path": {"$from": 0, "output": "path"}})])
    assert [st.status for st in t.plan] == ["done", "done", "skipped"]
    assert t.status is TaskStatus.FAILED and t.error_kind == "stale_output" and "no longer exists" in t.result["summary"]
    assert not [c for c in desk.calls if c[0] == "open_path"]


@pytest.mark.parametrize("rel", ["Library/Keychains/login.keychain-db", ".ssh/id_rsa"])
async def test_protected_files_are_never_opened(env, home, rel):
    path = _file(home, rel)
    t = await _submit(env, [("files.open", {"path": path})])
    assert t.status is TaskStatus.FAILED and "protected folder" in t.result["summary"]
    assert not [c for c in env["desk"].calls if c[0] == "open_path"]


def test_file_scope_rules(home, tmp_path):
    ok = _file(home, "Documents/a.txt")
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    link = home / "Documents" / "link.txt"
    link.symlink_to(outside)
    assert check_user_file(ok)[0] == Path(ok).resolve()
    assert check_user_file(str(outside))[1] == "permission"
    assert check_user_file(str(link))[1] == "permission"  # a link can't point a step outside the home folder
    assert check_user_file(str(home / "Documents" / "missing.txt"))[1] == "stale"
    assert check_user_file("Documents/a.txt")[1] == "invalid"
    assert check_user_file(str(home / "Documents"))[1] == "invalid"
    assert check_user_file(str(home / ".ssh" / "x"))[1] in ("stale", "permission")


async def test_find_reports_only_files_in_scope(env, home, tmp_path):
    outside = tmp_path / "report.pdf"
    outside.write_text("x")
    inside = _file(home, "Documents/report.pdf")
    env["desk"].files = [str(outside), inside, _file(home, "Library/report.pdf")]
    t = await _submit(env, [("files.find_by_name", {"name": "report.pdf"})])
    assert t.result["data"]["paths"] == [str(Path(inside).resolve())] and t.result["data"]["path"] == str(Path(inside).resolve())


def test_invalid_references_are_rejected_when_the_plan_is_built(env):
    tools = env["s"].tools
    find = ("files.find_by_name", {"name": "a.txt"})
    bad = [
        [("files.open", {"path": {"$from": 0, "output": "path"}})],                       # refers to itself
        [find, ("files.open", {"path": {"$from": 2, "output": "path"}})],                  # a later step
        [find, ("files.open", {"path": {"$from": 0, "output": "contents"}})],              # undeclared output
        [("apps.open", {"app": "Safari"}), ("files.open", {"path": {"$from": 0, "output": "path"}})],  # no outputs
        [find, ("files.open", {"path": {"$from": "0", "output": "path"}})],                # malformed
        [find, ("files.open", {"path": {"$from": 0, "output": "path", "x": 1}})],          # extra keys
        [find, ("desktop.move_cursor", {"x": {"$from": 0, "output": "path"}})],            # type mismatch
        [find, ("files.open", {"path": {"$from": 0, "output": "path"}, "bogus": 1})],      # other args still checked
    ]
    for steps in bad:
        with pytest.raises(ArgumentError):
            check_plan(steps, tools)
    ok = check_plan([find, ("files.open", {"path": {"$from": 0, "output": "path"}})], tools)
    assert ok[1]["path"] == {"$from": 0, "output": "path"} and ok[1]["app"] == ""


def test_resolution_refuses_missing_unverified_and_failed_outputs(env, home):
    tools, path = env["s"].tools, _file(home, "Documents/a.txt")
    args = {"path": {"$from": 0, "output": "path"}}
    plan_tools, titles = ["files.find_by_name", "files.open"], ["find", "open"]
    cases = {
        "unverified_prerequisite": ToolResult(True, "x", {"path": path}, verified=False),
        "missing_output": ToolResult(True, "x", {}, verified=True),
        "ambiguous_reference": ToolResult(True, "x", {"paths": [path, path + "2"]}, verified=True),
        "invalid_reference": ToolResult(True, "x", {"path": 5}, verified=True),
    }
    for kind, res in cases.items():
        got = resolve(args, [res], titles, tools, plan_tools)
        assert isinstance(got, RefFailure) and got.kind == kind, (kind, got)
    failed = resolve(args, [ToolResult(False, "x")], titles, tools, plan_tools)
    assert isinstance(failed, RefFailure) and failed.kind == "unverified_prerequisite"
    assert resolve(args, [], titles, tools, plan_tools).kind == "invalid_reference"
    good = resolve(args, [ToolResult(True, "x", {"path": path}, verified=True)], titles, tools, plan_tools)
    assert good == {"path": str(Path(path).resolve())}


async def test_independent_plans_are_unchanged(env):
    t = await _submit(env, [("apps.open_url", {"url": "github.com"}), ("apps.open", {"app": "Safari"})])
    assert t.status is TaskStatus.COMPLETED and [s.status for s in t.plan] == ["done", "done"]


# ================================================================== B. session and display state
async def test_locked_mac_stops_with_a_request_to_unlock_and_nothing_runs(env):
    env["s"].settings.security.desktop_wait_s = 0
    env["desk"].session = {"state": "locked", "detail": "the lock screen is showing"}
    t = await _submit(env, [("apps.open", {"app": "Safari"})])
    assert t.status is TaskStatus.FAILED and t.result["data"]["errorKind"] == "session_locked"
    assert "Unlock it yourself" in t.result["summary"] and "never unlock" in t.result["summary"]
    assert ("open_app", "Safari") not in env["desk"].calls and ("wake_display",) not in env["desk"].calls


async def test_waits_while_locked_and_continues_once_unlocked(env):
    desk, sub = env["desk"], env["s"].bus.subscribe()
    env["s"].settings.security.desktop_wait_s = 5
    desk.session = {"state": "locked", "detail": "the lock screen is showing"}
    task = env["s"].tasks.submit("t", "t", "t", [PlanStep("open", "apps.open", {"app": "Safari"})], source="device:iphone")
    await asyncio.sleep(0.2)
    desk.session = {"state": "available", "detail": "unlocked"}
    t = await env["s"].tasks.wait(task.id, 10)
    assert t.status is TaskStatus.COMPLETED and ("open_app", "Safari") in desk.calls
    details = [e.payload.get("detail", "") for e in await _events(sub) if e.type == "task.update"]
    assert any("Waiting for you to unlock the Mac" in d for d in details)


async def test_sleeping_display_is_woken_with_a_normal_signal_then_rechecked(env):
    desk = env["desk"]
    desk.session = {"state": "display_asleep", "detail": "the display is asleep"}
    t = await _submit(env, [("apps.open", {"app": "Safari"})])
    assert ("wake_display",) in desk.calls and t.status is TaskStatus.COMPLETED


async def test_wake_shows_the_lock_screen_then_the_task_stops(env):
    desk = env["desk"]
    env["s"].settings.security.desktop_wait_s = 0
    desk.session = {"state": "display_asleep", "detail": "the display is asleep"}
    desk.after_wake = {"state": "locked", "detail": "the lock screen is showing"}
    t = await _submit(env, [("apps.open", {"app": "Safari"})])
    assert t.result["data"]["errorKind"] == "session_locked" and ("open_app", "Safari") not in desk.calls


async def test_waking_can_be_turned_off(env):
    env["s"].settings.security.wake_display_for_tasks = False
    env["s"].settings.security.desktop_wait_s = 0
    env["desk"].session = {"state": "display_asleep", "detail": "the display is asleep"}
    t = await _submit(env, [("apps.open", {"app": "Safari"})])
    assert t.result["data"]["errorKind"] == "display_asleep" and ("wake_display",) not in env["desk"].calls


async def test_unknown_state_is_reported_not_guessed(env):
    sub = env["s"].bus.subscribe()
    env["desk"].session = {"state": "unknown", "detail": "no login session is visible to BAYMAX"}
    t = await _submit(env, [("apps.open", {"app": "Safari"})])
    assert t.status is TaskStatus.COMPLETED
    details = [e.payload.get("detail", "") for e in await _events(sub) if e.type == "task.update"]
    assert any("couldn't check whether the Mac is locked" in d for d in details)


async def test_non_desktop_steps_skip_the_session_check(env):
    env["desk"].session = {"state": "locked", "detail": "x"}
    t = await _submit(env, [("files.list", {"path": "."})])
    assert t.status is TaskStatus.COMPLETED and ("session_state",) not in env["desk"].calls


async def test_emergency_stop_while_waiting_for_unlock(env):
    env["s"].settings.security.desktop_wait_s = 30
    env["desk"].session = {"state": "locked", "detail": "x"}
    task = env["s"].tasks.submit("t", "t", "t", [PlanStep("open", "apps.open", {"app": "Safari"})])
    await asyncio.sleep(0.2)
    env["s"].estop.engage("test")
    t = await env["s"].tasks.wait(task.id, 5)
    assert t.status is TaskStatus.CANCELLED and ("open_app", "Safari") not in env["desk"].calls
    env["s"].estop.reset()


# ================================================================== C. window selection
WINS = [{"index": 1, "title": "Budget 2026", "subrole": "AXStandardWindow", "identifier": "", "main": True, "minimized": False},
        {"index": 2, "title": "Notes — draft", "subrole": "AXStandardWindow", "identifier": "w2", "main": False, "minimized": False},
        {"index": 3, "title": "Notes — draft", "subrole": "AXStandardWindow", "identifier": "w3", "main": False, "minimized": True}]


def _numbers(env, wins=WINS):
    from baymax.hostos.apps import AppInfo

    desk = env["desk"]
    desk.installed.append(AppInfo("Numbers", "", "com.apple.iWork.Numbers"))
    desk.running.add("Numbers")
    desk.windows["Numbers"] = [dict(w) for w in wins]
    real = desk.raise_window

    async def raise_and_mark(app, index, title):
        await real(app, index, title)
        for w in desk.windows[app]:
            w["main"] = w["index"] == index

    desk.raise_window = raise_and_mark


async def test_window_selected_by_unique_title_and_verified(env):
    _numbers(env)
    t = await _submit(env, [("desktop.focus_window", {"app": "Numbers", "title": "budget 2026"})])
    assert t.status is TaskStatus.COMPLETED and t.verified and ("raise_window", "Numbers", 1, "Budget 2026") in env["desk"].calls


async def test_duplicate_titles_ask_instead_of_guessing(env):
    _numbers(env)
    t = await _submit(env, [("desktop.focus_window", {"app": "Numbers", "title": "Notes — draft"})])
    assert t.status is TaskStatus.FAILED and "Which one" in t.result["summary"]
    assert not [c for c in env["desk"].calls if c[0] == "raise_window"]


async def test_identifier_disambiguates(env):
    _numbers(env)
    t = await _submit(env, [("desktop.focus_window", {"app": "Numbers", "identifier": "w3"})])
    assert t.status is TaskStatus.COMPLETED and ("raise_window", "Numbers", 3, "Notes — draft") in env["desk"].calls


async def test_missing_window_lists_what_exists(env):
    _numbers(env)
    t = await _submit(env, [("desktop.focus_window", {"app": "Numbers", "title": "Taxes"})])
    assert t.status is TaskStatus.FAILED and "“Budget 2026”" in t.result["summary"]


async def test_window_that_changed_title_is_not_raised(env):
    _numbers(env)
    desk = env["desk"]
    real = desk.list_windows

    async def list_then_rename(app):
        out = await real(app)
        desk.windows[app][0]["title"] = "Budget 2027"  # renamed between listing and raising
        return out

    desk.list_windows = list_then_rename
    t = await _submit(env, [("desktop.focus_window", {"app": "Numbers", "title": "Budget 2026"})])
    assert t.status is TaskStatus.FAILED and "changed" in (t.error or t.result.get("summary", ""))


async def test_window_listing_without_accessibility_is_a_permission_failure(env):
    _numbers(env)
    env["desk"].accessibility = False
    t = await _submit(env, [("desktop.focus_window", {"app": "Numbers", "title": "Budget 2026"})])
    assert t.status is TaskStatus.FAILED and "Accessibility" in (t.error or "")


def _textedit_tabs(env):
    desk = env["desk"]
    desk.running.add("TextEdit")
    desk.windows["TextEdit"] = [{"index": 1, "title": "Untitled 2", "subrole": "AXStandardWindow", "identifier": "",
                                 "main": True, "minimized": False}]
    desk.tabs["TextEdit"] = ["Untitled"]  # a background tab of the same window
    return desk


async def test_background_tab_is_chosen_through_the_window_menu_and_verified(env):
    desk = _textedit_tabs(env)
    t = await _submit(env, [("desktop.focus_window", {"app": "TextEdit", "title": "Untitled"})])
    assert t.status is TaskStatus.COMPLETED and t.verified, t.result
    assert ("select_window_menu_item", "TextEdit", "Untitled") in desk.calls  # exact tab, not the partial "Untitled 2"
    assert not [c for c in desk.calls if c[0] == "raise_window"]


async def test_visible_window_still_wins_over_the_window_menu(env):
    desk = _textedit_tabs(env)
    t = await _submit(env, [("desktop.focus_window", {"app": "TextEdit", "title": "Untitled 2"})])
    assert t.status is TaskStatus.COMPLETED and ("raise_window", "TextEdit", 1, "Untitled 2") in desk.calls
    assert not [c for c in desk.calls if c[0] in ("window_menu_titles", "select_window_menu_item")]


async def test_partial_title_matching_a_window_and_a_tab_asks(env):
    desk = _textedit_tabs(env)
    desk.tabs["TextEdit"] = ["Untitled 3"]
    t = await _submit(env, [("desktop.focus_window", {"app": "TextEdit", "title": "untitled"})])
    assert t.status is TaskStatus.FAILED and "Which one" in t.result["summary"]
    assert not [c for c in desk.calls if c[0] in ("raise_window", "select_window_menu_item")]


async def test_tab_switch_not_confirmed_is_reported_unverified(env):
    desk = _textedit_tabs(env)

    async def select_but_nothing_changes(app, title):
        desk.calls.append(("select_window_menu_item", app, title))

    desk.select_window_menu_item = select_but_nothing_changes
    t = await _submit(env, [("desktop.focus_window", {"app": "TextEdit", "title": "Untitled"})])
    assert not t.verified and "couldn't confirm" in t.result["summary"]


def test_window_menu_entries_count_only_if_they_are_real_windows():
    from baymax.hostos import desktop as dk

    class Q:
        kCGWindowListOptionAll, kCGNullWindowID = 0, 0

        @staticmethod
        def CGWindowListCopyWindowInfo(_o, _w):
            return [{"kCGWindowOwnerName": "TextEdit", "kCGWindowLayer": 0, "kCGWindowName": n}
                    for n in ("Untitled", "Untitled 2")] + [{"kCGWindowOwnerName": "Notes", "kCGWindowLayer": 0, "kCGWindowName": "Zoom"}]

    d = dk.MacDesktop.__new__(dk.MacDesktop)
    d._quartz = Q()

    async def osa(script, *a, **k):
        assert script is dk._AS_WINDOW_MENU
        return "Minimize\nZoom\nBring All to Front\nUntitled\nUntitled 2\n"

    d._osascript = osa
    assert asyncio.run(d.window_menu_titles("TextEdit")) == ["Untitled", "Untitled 2"]  # commands never count


def test_window_routing_and_parsing():
    from baymax.creation3d.classifier import RequestClassifier
    from baymax.creation3d.registry import GeneratorRegistry
    from baymax.intent.router import IntentRouter

    i = IntentRouter(RequestClassifier(GeneratorRegistry())).route("Switch to the Budget 2026 window in Numbers")
    assert (i.tool, i.args) == ("desktop.focus_window", {"app": "Numbers", "title": "Budget 2026"})
    wins = parse_window_lines("1\tBudget\tAXStandardWindow\tmissing value\ttrue\tfalse\n2\tX\tAXDialog\tid9\tfalse\ttrue\nbad\n")
    assert wins == [{"index": 1, "title": "Budget", "subrole": "AXStandardWindow", "identifier": "", "main": True, "minimized": False},
                    {"index": 2, "title": "X", "subrole": "AXDialog", "identifier": "id9", "main": False, "minimized": True}]


# ================================================================== D. blocking dialogs
SAVE = {"kind": "save_dialog", "app": "TextEdit", "window": "Untitled", "title": "Do you want to keep this new document?",
        "buttons": "Delete, Cancel, Save"}


def _textedit(env):
    env["desk"].running.add("TextEdit")
    env["desk"].ax_dump["TextEdit"] = "AXTextArea\t\ttext entry area\t\t327\t368\t586\t346"


async def test_dialog_blocks_ui_steps_and_is_never_clicked(env):
    _textedit(env)
    env["s"].settings.security.desktop_wait_s = 0
    env["desk"].modals = [SAVE]
    t = await _submit(env, [("screen.type_into", {"field": "text area", "text": "hi", "app": "TextEdit"})])
    s = t.result["summary"]
    assert t.status is TaskStatus.FAILED and t.result["data"]["errorKind"] == "blocking_dialog"
    assert "Save dialog" in s and "Do you want to keep" in s and "I don't click dialog buttons" in s
    assert not [c for c in env["desk"].calls if c[0] in ("type", "focus", "press", "click", "keys", "return")]


async def test_task_resumes_after_the_user_handles_the_dialog(env):
    _textedit(env)
    env["s"].settings.security.desktop_wait_s = 5
    env["desk"].modals = [SAVE]
    task = env["s"].tasks.submit("t", "t", "t", [PlanStep("type", "screen.type_into", {"field": "text area", "text": "hi", "app": "TextEdit"})])
    await asyncio.sleep(0.2)
    env["desk"].modals = []  # the user dismissed it
    t = await env["s"].tasks.wait(task.id, 10)
    assert t.status is TaskStatus.COMPLETED and ("type", "TextEdit", "hi") in env["desk"].calls


async def test_several_dialogs_are_all_described(env):
    _textedit(env)
    env["s"].settings.security.desktop_wait_s = 0
    env["desk"].modals = [SAVE, {"kind": "authentication", "app": "SecurityAgent", "window": "", "title": "Enter your password",
                                 "buttons": "Cancel, OK"}]
    t = await _submit(env, [("screen.type_into", {"field": "text area", "text": "hi", "app": "TextEdit"})])
    s = t.result["summary"]
    assert "Save dialog" in s and "password / authentication prompt" in s and "macOS is showing" in s


async def test_unreadable_dialog_state_is_reported_and_the_step_proceeds(env):
    _textedit(env)
    sub = env["s"].bus.subscribe()
    env["desk"].modals = DesktopPermissionError("BAYMAX needs the Accessibility permission for this.")
    t = await _submit(env, [("screen.type_into", {"field": "text area", "text": "hi", "app": "TextEdit"})])
    details = [e.payload.get("detail", "") for e in await _events(sub) if e.type == "task.update"]
    assert any("couldn't check TextEdit for open dialogs" in d for d in details)
    assert t.status is TaskStatus.COMPLETED


async def test_read_only_steps_are_not_blocked_by_dialogs(env):
    env["desk"].front = "TextEdit"
    env["desk"].modals = [SAVE]
    t = await _submit(env, [("desktop.active_window", {})])
    assert t.status is TaskStatus.COMPLETED and not [c for c in env["desk"].calls if c[0] == "modal_dialogs"]


def test_dialog_classification():
    assert parse_modal_line("sheet\tTextEdit\tUntitled\tSave\tSave|Don't Save|Cancel|")["kind"] == "save_dialog"
    assert parse_modal_line("dialog\tPreview\tOpen\t\tOpen|Cancel|")["kind"] == "open_dialog"
    assert parse_modal_line("system\tSecurityAgent\t\t\tOK|Cancel|")["kind"] == "authentication"
    assert parse_modal_line("system\tUserNotificationCenter\tx\tx\tAllow|Don't Allow|")["kind"] == "system_prompt"
    assert parse_modal_line("dialog\tSafari\tAlert\tAlert\tOK|")["kind"] == "dialog"
    assert parse_modal_line("garbage") is None


# ================================================================== E. what the iPhone receives
async def test_phone_gets_the_stop_reason_in_the_same_result_the_mac_sees(env, home):
    from types import SimpleNamespace

    from baymax.server.hub import DeviceHub

    env["s"].settings.security.desktop_wait_s = 0
    env["desk"].session = {"state": "locked", "detail": "the lock screen is showing"}
    sub = env["s"].bus.subscribe()
    t = await _submit(env, [("apps.open", {"app": "Safari"})])
    evs = await _events(sub)
    result = next(e for e in evs if e.type == "task.result" and e.payload["taskId"] == t.id)
    hub = DeviceHub.__new__(DeviceHub)
    hub.s = env["s"]
    msg = hub._map("task.result", result.payload, SimpleNamespace(protocol=2, device_id="iphone"))
    assert msg.payload["taskId"] == t.id and msg.payload["status"] == "FAILED" and "Unlock it yourself" in msg.payload["summary"]
    assert msg.payload["summary"] == t.result["summary"][:2000]


# ================================================================== the Mac backend itself (no Mac needed for these)
def test_mac_backend_implements_every_desktop_operation():
    """Every operation declared on DesktopBackend is implemented by MacDesktop itself (an implementation placed in the
    base class by mistake would be shadowed by the base 'not on this platform' stub)."""
    import inspect

    from baymax.hostos.desktop import DesktopBackend, MacDesktop

    ops = [n for n, v in DesktopBackend.__dict__.items() if inspect.iscoroutinefunction(v)]
    assert {"session_state", "wake_display", "modal_dialogs", "list_windows", "raise_window", "find_files", "open_path"} <= set(ops)
    assert [n for n in ops if n not in MacDesktop.__dict__] == []


class _Quartz:
    def __init__(self, session, asleep=False):
        self.session, self.asleep = session, asleep

    def CGSessionCopyCurrentDictionary(self):
        return self.session

    def CGMainDisplayID(self):
        return 1

    def CGDisplayIsAsleep(self, _d):
        return self.asleep


@pytest.mark.parametrize("session,asleep,state", [
    ({"kCGSSessionOnConsoleKey": True}, False, "available"),
    ({"kCGSSessionOnConsoleKey": True, "CGSSessionScreenIsLocked": True}, False, "locked"),
    ({"kCGSSessionOnConsoleKey": False}, False, "locked"),        # another user's session is on screen
    ({"kCGSSessionOnConsoleKey": True}, True, "display_asleep"),
    (None, False, "unknown"),                                     # no GUI session visible
])
async def test_mac_session_state_reads_the_window_server_session(session, asleep, state):
    from baymax.hostos.desktop import MacDesktop

    d = MacDesktop.__new__(MacDesktop)
    d._quartz = _Quartz(session, asleep)
    assert (await d.session_state())["state"] == state


async def test_mac_session_state_without_quartz_is_unknown():
    from baymax.hostos.desktop import MacDesktop

    d = MacDesktop.__new__(MacDesktop)
    d._quartz = None
    assert (await d.session_state())["state"] == "unknown"
