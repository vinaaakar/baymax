"""Fixes from the Oct 15 real-Mac run (19/21): ChatGPT's message box found through keyboard focus when its window is too
large to search in time, and a send confirmed only by reading that same box. Simulated: the macOS desktop (FakeDesktop:
a ChatGPT app whose window search finds nothing, as on the Mac). Real: the delegate tool, task manager, approvals.
"""

from __future__ import annotations

import asyncio

from baymax.hostos.apps import AppInfo
from baymax.tasks.models import PlanStep, TaskStatus
from test_capability_gaps import env, model  # noqa: F401  (fixtures)

PROMPT = "BAYMAX test prompt - do not send"


def _chatgpt(env, role="AXTextArea", label=""):
    desk = env["desk"]
    desk.installed.append(AppInfo("ChatGPT", "", "com.openai.chat"))
    desk.ax_dump["ChatGPT"] = ""  # the window walk reaches nothing in time (61 elements, no message box)
    desk.focused["ChatGPT"] = (role, 300, 700)
    desk.focused_labels["ChatGPT"] = label
    return desk


async def _delegate(env, **extra):
    task = env["s"].tasks.submit("d", "d", "d", [PlanStep("d", "ai.delegate", {"service": "chatgpt", "prompt": PROMPT,
                                                                              "via": "app", **extra})])
    return task


async def test_focused_message_box_is_used_and_read_back(env):
    desk = _chatgpt(env)
    t = await env["s"].tasks.wait((await _delegate(env)).id, 10)
    assert t.status is TaskStatus.COMPLETED and t.verified, t.result
    d = t.result["data"]
    assert d["fieldFrom"] == "keyboard focus" and d["entered"] is True and d["submitted"] is False
    assert ("type", "ChatGPT", PROMPT) in desk.calls
    assert not [c for c in desk.calls if c[0] == "focus"]  # it already had focus; no click or focus change needed


async def test_a_focused_search_box_is_never_typed_into(env):
    desk = _chatgpt(env, role="AXTextField", label="Search chats")
    t = await env["s"].tasks.wait((await _delegate(env)).id, 60)
    assert t.status is TaskStatus.FAILED and "Nothing was typed or sent" in t.result["summary"]
    assert not [c for c in desk.calls if c[0] == "type"]


async def _approve_and_wait(env, task):
    sub = env["s"].bus.subscribe()
    for _ in range(50):
        try:
            ev = await asyncio.wait_for(sub.get(), 2)
        except asyncio.TimeoutError:
            break
        if ev.type == "approval.request":
            env["s"].tasks.respond_approval(ev.payload["approvalId"], True, "local")  # approved on the Mac
            break
    return await env["s"].tasks.wait(task.id, 20)


async def test_send_is_confirmed_only_when_the_focused_box_is_empty_again(env):
    desk = _chatgpt(env)

    async def press_return(app):  # ChatGPT sends and clears its box
        desk.calls.append(("return", app))
        role, fx, fy = desk.focused[app]
        desk.field_raw[(app, role, fx, fy)] = ""

    desk.press_return = press_return
    t = await _approve_and_wait(env, await _delegate(env, submit=True))
    assert t.status is TaskStatus.COMPLETED and t.verified and t.result["data"]["submitted"] is True


async def test_unreadable_box_after_send_is_not_reported_as_sent(env):
    desk = _chatgpt(env)
    real = desk.focused_text
    sent = {"done": False}

    async def press_return(app):
        desk.calls.append(("return", app))
        sent["done"] = True

    async def focused_text(app):
        if sent["done"]:
            from baymax.hostos.desktop import DesktopUnavailable

            raise DesktopUnavailable("macOS didn't respond within 10 seconds")
        return await real(app)

    desk.press_return, desk.focused_text = press_return, focused_text
    t = await _approve_and_wait(env, await _delegate(env, submit=True))
    assert t.status is TaskStatus.FAILED and t.result["data"]["submitted"] is False  # never "Sent" without proof
