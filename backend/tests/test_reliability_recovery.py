"""Reliability recovery (Oct 15, Mac + iPhone): pointer positions, screen reading, ChatGPT/website resolution and
research parsing. Simulated: Ollama and the macOS desktop (FakeDesktop). Real: router, assistant, tools, task manager.
"""

from __future__ import annotations

import pytest

from baymax.creation3d.classifier import RequestClassifier
from baymax.creation3d.registry import GeneratorRegistry
from baymax.hostos.desktop import DesktopPermissionError, DesktopUnavailable, drop_inside
from baymax.intent.router import IntentRouter
from baymax.screen.elements import Display, ScreenElement
from baymax.tasks.models import PlanStep, TaskStatus
from test_capability_gaps import env, model  # noqa: F401  (fixtures)

R = IntentRouter(RequestClassifier(GeneratorRegistry()))


async def _run(env, tool, args, timeout=5):
    task = env["s"].tasks.submit("desktop", "t", "t", [PlanStep(tool, tool, args)])
    return await env["s"].tasks.wait(task.id, timeout)


# ------------------------------------------------------------------ pointer: coordinates, corners, targets, clicks
@pytest.mark.parametrize("q,pos", [("Move the pointer to the top left corner", "top-left"),
                                   ("Move the pointer to the top-left corner", "top-left"),
                                   ("move the mouse to the bottom right corner of the screen", "bottom-right"),
                                   ("put the cursor in the upper right corner", "top-right"),
                                   ("Move the pointer to the center of", "center"),  # speech cut off after "of"
                                   ("Move the pointer to the center of the screen", "center")])
def test_named_screen_positions_are_positions_not_text_searches(q, pos):
    i = R.route(q)
    assert (i.tool, i.args) == ("desktop.move_cursor", {"position": pos})


async def test_exact_coordinates_move_and_are_read_back(env):
    r = await env["a"].handle_text("move the cursor to 100, 200")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and t.verified and env["desk"].cursor == (100.0, 200.0)


@pytest.mark.parametrize("start,pos,expect", [
    ((10, 10), "top-left", (5.0, 5.0)),            # primary 1512x982 at (0,0); 5 pt inset (Hot Corners)
    ((10, 10), "bottom-right", (1506.0, 976.0)),
    ((-500, 100), "top-right", (-6.0, -93.0)),      # pointer on the external display (-1920,-98, 1920x1080): its corner
    ((-500, 100), "center", (-960.0, 442.0)),
])
async def test_corners_and_center_come_from_the_display_under_the_pointer(env, start, pos, expect):
    env["desk"].cursor = start
    t = await _run(env, "desktop.move_cursor", {"position": pos})
    assert t.status is TaskStatus.COMPLETED and t.verified and env["desk"].cursor == expect
    assert "Moved the pointer to the" in t.result["summary"]


async def test_corner_move_never_searches_the_screen_or_clicks(env):
    r = await env["a"].handle_text("Move the pointer to the top left corner")
    await env["s"].tasks.wait(r["taskId"], 5)
    assert not [c for c in env["desk"].calls if c[0] in ("ax", "ocr", "click", "press")]


async def test_a_pointer_that_does_not_arrive_is_reported_as_a_failure(env):
    async def stuck(x, y):
        env["desk"].calls.append(("move", x, y))  # macOS ignored the event

    env["desk"].move_cursor = stuck
    t = await _run(env, "desktop.move_cursor", {"position": "top-left"})
    assert t.status is TaskStatus.FAILED and "but it is at" in (t.error or t.result["summary"])


def test_text_recognised_inside_baymaxs_own_windows_is_never_a_target():
    # Seen: "Move the pointer to the top left corner" was "found" as text in BAYMAX's own conversation window.
    els = [ScreenElement("text", "Move the pointer to the top left corner", 100, 300, 300, 20, "ocr", 1.0),
           ScreenElement("text", "Send", 900, 700, 40, 20, "ocr", 0.99)]
    kept = drop_inside(els, [(50, 250, 640, 400)])  # BAYMAX's companion window
    assert [e.label for e in kept] == ["Send"]


async def test_pointing_at_a_target_moves_but_never_clicks(env):
    env["desk"].front = "Chat"
    env["desk"].ax_dump["Chat"] = "AXButton\tSend\t\t\t920\t705\t40\t30"
    r = await env["a"].handle_text("point to the Send button")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and ("move", 940.0, 720.0) in env["desk"].calls
    assert not [c for c in env["desk"].calls if c[0] in ("click", "press")]


# ------------------------------------------------------------------ reading the current window
async def test_accessible_window_text_is_read_with_its_source(env):
    env["desk"].front = "Safari"
    t = await _run(env, "desktop.read_window", {})
    assert t.status is TaskStatus.COMPLETED and t.result["data"]["source"] == "accessibility"


def _no_ax_text(env):
    async def empty(app, limit=4000):
        env["desk"].calls.append(("read_window", app))
        return ""

    env["desk"].read_window_text = empty


async def test_window_without_accessibility_text_is_read_from_the_screen(env):
    desk = env["desk"]
    desk.front, desk.window_title = "Safari", "Example"
    _no_ax_text(env)
    desk.ocr = [ScreenElement("text", "How AI works", 200, 150, 200, 24, "ocr", 0.98),   # inside Safari (100,80 900x600)
                ScreenElement("text", "Neural networks learn", 200, 200, 300, 20, "ocr", 0.9),
                ScreenElement("text", "Dock item", 1300, 950, 60, 20, "ocr", 0.99)]       # outside the window
    t = await _run(env, "desktop.read_window", {})
    d = t.result["data"]
    assert t.status is TaskStatus.COMPLETED and d["source"] == "ocr" and d["text"] == "How AI works\nNeural networks learn"
    assert "text recognition" in t.result["summary"] and "confidence" in t.result["summary"]


async def test_no_text_anywhere_is_explained_not_called_empty_when_reading_was_impossible(env):
    desk = env["desk"]
    desk.front = "Safari"
    _no_ax_text(env)

    async def denied():
        raise DesktopPermissionError("BAYMAX needs the Screen Recording permission for this.")

    desk.ocr_elements = denied
    t = await _run(env, "desktop.read_window", {})
    assert t.status is TaskStatus.FAILED and "Screen Recording" in t.result["summary"]
    assert "no readable text" not in t.result["summary"]


async def test_reading_a_window_that_is_not_in_front_asks_to_bring_it_forward(env):
    desk = env["desk"]
    desk.front = "Finder"
    desk.running.add("Safari")
    _no_ax_text(env)
    t = await _run(env, "desktop.read_window", {"app": "Safari"})
    assert t.status is TaskStatus.FAILED and "bring Safari to the front" in t.result["summary"]


async def test_reading_timeout_is_reported(env):
    async def slow(app, limit=4000):
        raise DesktopUnavailable("macOS didn't respond within 25 seconds (the app may be busy or showing a dialog)")

    env["desk"].front = "Safari"
    env["desk"].read_window_text = slow
    t = await _run(env, "desktop.read_window", {})
    assert t.status is TaskStatus.FAILED and "didn't respond within 25 seconds" in t.result["summary"]


# ------------------------------------------------------------------ ChatGPT: names, official site, tasks
async def test_open_chatgpt_without_the_app_opens_the_official_site(env):
    r = await env["a"].handle_text("Open ChatGPT")
    await env["s"].tasks.wait(r["taskId"], 5)
    opened = [c for c in env["desk"].calls if c[0] == "open_url"]
    assert opened and opened[0][1] == "https://chatgpt.com"


async def test_open_chatgpt_in_safari_opens_the_official_site_in_safari(env):
    r = await env["a"].handle_text("Open ChatGPT in Safari")
    await env["s"].tasks.wait(r["taskId"], 5)
    assert ("open_url", "https://chatgpt.com", "Safari") in env["desk"].calls


@pytest.mark.parametrize("q,steps", [
    ("open chatgpt undo some research on how i walk", [("apps.open", {"app": "ChatGPT"}), ("research.web", {"query": "how i walk"})]),
    ("Open ChatGPT on the Safari and do some research on how AI works",
     [("apps.open_url", {"url": "ChatGPT", "browser": "Safari"}), ("research.web", {"query": "how AI works"})]),
])
def test_open_and_research_keeps_destination_and_topic_separate_as_a_plan(q, steps):
    from baymax.intent.router import split_compound

    assert [(x.tool, x.args) for x in split_compound(R, q)] == steps


@pytest.mark.parametrize("q,expect", [
    ("Ask Gemini to research how AI works", {"service": "gemini", "submit": True, "prompt": "Research how AI works"}),
    ("create a new chat in ChatGPT and research how AI works",
     {"service": "chatgpt", "submit": True, "new_chat": True, "prompt": "Research this and summarize it with sources: how AI works"}),
])
def test_explicit_ai_requests_keep_the_delegate_workflow(q, expect):
    i = R.route(q)
    assert (i.tool, i.args) == ("ai.delegate", expect)


@pytest.mark.parametrize("q", ["Create a new chat and do some research on how AI works", "Create a new chat and research how AI works"])
def test_research_without_a_named_service_uses_web_research_with_real_sources(q):
    i = R.route(q)
    assert (i.tool, i.args) == ("research.web", {"query": "how AI works"})


@pytest.mark.parametrize("q", ["open chatgpt and do some research on how", "Open ChatGPT and do some research based on how",
                               "open chatgpt about the stuff we discussed", "open gpt"])
def test_sentences_never_become_app_names(q):
    i = R.route(q)
    assert i.tool != "apps.open" and i.kind == "chat" and i.reply  # a short question instead


def test_compound_open_commands_still_become_plans():
    from baymax.intent.router import split_compound

    i = R.route("Open Safari and then open Terminal")
    assert i.kind == "chat" and not i.reply
    assert [s.args["app"] for s in split_compound(R, "Open Safari and then open Terminal")] == ["Safari", "Terminal"]


async def test_new_chat_presses_the_apps_new_chat_shortcut_before_typing(env):
    from baymax.hostos.apps import AppInfo

    desk = env["desk"]
    desk.installed.append(AppInfo("ChatGPT", "", "com.openai.chat"))
    desk.ax_dump["ChatGPT"] = "AXTextArea\t\tMessage ChatGPT\t\t300\t700\t600\t40"
    t = await _run(env, "ai.delegate", {"service": "chatgpt", "prompt": "hi", "new_chat": True}, timeout=10)
    keys = [i for i, c in enumerate(desk.calls) if c[0] == "keys" and c[2] == "n"]
    typed = [i for i, c in enumerate(desk.calls) if c[0] == "type"]
    assert t.status is TaskStatus.COMPLETED and keys and typed and keys[0] < typed[0]


async def test_research_without_network_reports_that_no_research_was_done(env):
    r = await env["a"].handle_text("Create a new chat and research how AI works")  # env runs with research.local_only
    t = await env["s"].tasks.wait(r["taskId"], 10)
    text = (t.error or "") + t.result.get("summary", "") if t.result else (t.error or "")
    assert t.status is not TaskStatus.COMPLETED and "Read" not in text
