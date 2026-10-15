"""Phase 1: runtime version guard, honest text-entry verification, dependent plan steps.

Simulated (Linux CI): the macOS desktop (FakeDesktop) and the build fingerprints (patched to model a backend whose
source changed on disk after it started). Real: buildinfo, the HTTP health endpoint, protocol validation, the
assistant, tools, task manager, policy and Emergency Stop.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
import pytest

import baymax.buildinfo as bi
from baymax.hostos.desktop import DesktopUnavailable
from baymax.protocol import messages as proto
from baymax.tasks.models import PlanStep, TaskStatus
from test_capability_gaps import env, model  # noqa: F401  (fixtures)
from test_server import server  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def outdated(monkeypatch):
    """The process loaded build A; the files on disk now hash to build B (code updated after start-up)."""
    bi.running_build.cache_clear()
    monkeypatch.setattr(bi, "source_hash", lambda pkg=bi.PKG: "aaaaaaaaaaaa")
    assert bi.running_build() == "aaaaaaaaaaaa"
    monkeypatch.setattr(bi, "source_hash", lambda pkg=bi.PKG: "bbbbbbbbbbbb")
    monkeypatch.setattr(bi, "_disk_cache", (0.0, ""))
    yield
    bi.running_build.cache_clear()
    monkeypatch.setattr(bi, "_disk_cache", (0.0, ""))


# ------------------------------------------------------------------ A. runtime version guard
def test_current_build_is_identified_and_not_stale(monkeypatch):
    bi.running_build.cache_clear()
    monkeypatch.setattr(bi, "_disk_cache", (0.0, ""))
    st = bi.build_status()
    assert st["build"] == st["diskBuild"] == bi.source_hash() and st["stale"] is False
    bi.running_build.cache_clear()


def test_stale_build_is_detected(outdated):
    assert bi.build_status() == {"build": "aaaaaaaaaaaa", "diskBuild": "bbbbbbbbbbbb", "stale": True}


async def test_health_endpoint_reports_running_and_disk_build(server, outdated):  # noqa: F811
    async with aiohttp.ClientSession() as s, s.get(server.base + "/api/health") as r:
        body = await r.json()
    assert (body["build"], body["diskBuild"], body["stale"]) == ("aaaaaaaaaaaa", "bbbbbbbbbbbb", True)


def test_phone_status_carries_the_build_and_the_outdated_flag(server, outdated):  # noqa: F811
    msg = server.hub._map("devices.changed", {"count": 1}, type("S", (), {"protocol": 2})())
    assert msg.type == "connection.status" and msg.payload["build"] == "aaaaaaaaaaaa" and msg.payload["buildStale"] is True
    spec = json.loads((ROOT / "protocol/baymax-protocol.json").read_text())
    def find(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "connection.status":
                    return v
                if (r := find(v)) is not None:
                    return r
        return None
    assert {"build": "str", "buildStale": "bool"}.items() <= find(spec)["optional"].items()
    assert {"build": "str", "buildStale": "bool"}.items() <= proto.SERVER_TO_CLIENT["connection.status"][1].items()
    assert '"build": .str, "buildStale": .bool' in (ROOT / "ios/BaymaxKit/Sources/BaymaxKit/ProtocolMessages.swift").read_text()


async def test_outdated_backend_warns_once_on_the_mac_and_phone(env, outdated):
    sub = env["s"].bus.subscribe()
    await env["a"].handle_text("hello")
    await env["a"].handle_text("hello again")
    notes = []
    while True:
        try:
            ev = await asyncio.wait_for(sub.get(), 0.3)
        except asyncio.TimeoutError:
            break
        if ev.type == "assistant.message" and ev.payload.get("role") == "system" and "older code" in ev.payload["text"]:
            notes.append(ev.payload["text"])
    assert len(notes) == 1 and "aaaaaaaaaaaa" in notes[0] and "bbbbbbbbbbbb" in notes[0]


# ------------------------------------------------------------------ B. honest text entry (desktop.type_text)
async def _type(env, text="hello", app="TextEdit"):
    env["desk"].running.add(app)
    task = env["s"].tasks.submit("t", "t", "t", [PlanStep("type", "desktop.type_text", {"app": app, "text": text})])
    return await env["s"].tasks.wait(task.id, 10)


async def test_app_in_front_alone_never_verifies_typing(env):
    # No readable focused field: the app is frontmost after typing, which used to be reported as "verified".
    t = await _type(env)
    assert env["desk"].front == "TextEdit"
    assert t.status is TaskStatus.COMPLETED and not t.verified and t.result["outcome"] == "dispatched_unverified"
    assert "can't confirm" in t.result["summary"]


async def test_exact_read_back_verifies_typing_unicode_multiline(env):
    env["desk"].focused["TextEdit"] = ("AXTextArea", 327, 368)
    text = "Grüße 東京 👋\nsecond line — ✓"
    t = await _type(env, text)
    assert t.status is TaskStatus.COMPLETED and t.verified and t.result["outcome"] == "completed_verified"
    assert env["desk"].field_raw[("TextEdit", "AXTextArea", 327, 368)] == text  # typed exactly


async def test_read_back_that_does_not_contain_the_text_is_a_failure(env):
    desk = env["desk"]
    desk.focused["TextEdit"] = ("AXTextArea", 327, 368)

    async def lossy(app, text):  # the app dropped the keystrokes
        desk.calls.append(("type", app, text))

    desk.type_text = lossy
    t = await _type(env, "hello")
    assert t.status is TaskStatus.FAILED and t.result["data"]["errorKind"] == "not_verified"


async def test_unreadable_field_is_reported_honestly(env):
    desk = env["desk"]
    desk.focused["TextEdit"] = ("AXTextArea", 327, 368)

    async def unreadable(app):
        raise DesktopUnavailable("macOS didn't respond within 10 seconds")

    desk.focused_text = unreadable
    t = await _type(env, "hello")
    assert t.status is TaskStatus.COMPLETED and not t.verified and "can't confirm" in t.result["summary"]


async def test_text_already_in_the_field_does_not_count_as_proof(env):
    desk = env["desk"]
    desk.focused["TextEdit"] = ("AXTextArea", 327, 368)
    desk.field_raw[("TextEdit", "AXTextArea", 327, 368)] = "hello"

    async def lossy(app, text):
        desk.calls.append(("type", app, text))

    desk.type_text = lossy
    t = await _type(env, "hello")
    assert t.status is TaskStatus.FAILED  # "hello" was there before; nothing new arrived


# ------------------------------------------------------------------ C. dependent plan steps
async def test_dependent_step_never_runs_after_an_unverified_prerequisite(env):
    desk = env["desk"]
    desk.ax_dump["TextEdit"] = "AXTextArea\t\ttext entry area\t\t327\t368\t586\t346"

    async def no_windows(app):  # TextEdit reports a document, but no window with that title is visible
        return []

    desk.window_titles = no_windows
    r = await env["a"].handle_text("create a new TextEdit document and type three lines into it", source="device:iphone")
    t = await env["s"].tasks.wait(r["taskId"], 10)
    assert [s.status for s in t.plan] == ["done", "skipped"] and t.plan[1].depends_on_previous
    assert t.status is TaskStatus.FAILED and t.error_kind == "unverified_prerequisite"
    d = t.result["data"]
    assert d["prerequisiteOutcome"] == "dispatched_unverified" and d["stoppedBefore"] == t.plan[1].title
    assert "couldn't be verified" in t.result["summary"]  # what the iPhone receives in task.result
    assert not [c for c in desk.calls if c[0] in ("focus", "type")]


async def test_dependent_step_runs_when_the_prerequisite_is_verified(env):
    env["desk"].ax_dump["TextEdit"] = "AXTextArea\t\ttext entry area\t\t327\t368\t586\t346"
    r = await env["a"].handle_text("create a new TextEdit document and type three lines into it")
    t = await env["s"].tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.COMPLETED and t.verified and [s.status for s in t.plan] == ["done", "done"]


async def test_independent_steps_are_not_stopped_by_an_unverified_step(env):
    # Opening a site in the default browser can't be verified; the next, independent step still runs.
    task = env["s"].tasks.submit("p", "p", "p", [PlanStep("site", "apps.open_url", {"url": "github.com"}),
                                                 PlanStep("app", "apps.open", {"app": "Safari"})])
    t = await env["s"].tasks.wait(task.id, 10)
    assert [s.status for s in t.plan] == ["done", "done"] and t.status is TaskStatus.COMPLETED and not t.verified


def test_saved_tasks_without_the_new_field_still_load():
    step = PlanStep(**{"title": "x", "tool": "apps.open", "args": {}, "status": "done", "summary": ""})
    assert step.depends_on_previous is False


# ------------------------------------------------------------------ safety: Emergency Stop and approvals unchanged
async def test_emergency_stop_still_cancels_a_dependent_plan(env):
    desk = env["desk"]
    started = asyncio.Event()

    async def slow_new_document(app):
        started.set()
        await asyncio.sleep(5)
        return "Untitled"

    desk.new_document = slow_new_document
    r = await env["a"].handle_text("create a new TextEdit document and type three lines into it")
    await asyncio.wait_for(started.wait(), 5)
    env["s"].estop.engage("test")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.CANCELLED and not [c for c in desk.calls if c[0] == "type"]
    env["s"].estop.reset()


async def test_phone_still_cannot_approve_high_risk_actions(env):
    assert env["s"].settings.security.remote_high_risk_approval is False
    sub = env["s"].bus.subscribe()
    task = env["s"].tasks.submit("m", "m", "m", [PlanStep("mail", "mail.send", {"to": "a@example.com", "subject": "s", "body": "b"})],
                                 source="device:iphone")
    req = None
    for _ in range(20):
        ev = await asyncio.wait_for(sub.get(), 2)
        if ev.type == "approval.request":
            req = ev
            break
    assert req is not None and req.payload["remoteAllowed"] is False
    ok, why = env["s"].tasks.respond_approval(req.payload["approvalId"], True, "device:iphone")
    assert not ok and "Mac" in why
    env["s"].tasks.respond_approval(req.payload["approvalId"], False, "local")
    assert (await env["s"].tasks.wait(task.id, 5)).status is TaskStatus.CANCELLED
