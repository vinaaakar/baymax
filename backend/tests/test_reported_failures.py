"""Regressions for the failures reported on the real Mac (Oct 15).

Simulated: Ollama (a local HTTP server with the real /api/chat shape that *always tries to call a tool* when offered
one — like the 3B model did) and the macOS desktop (FakeDesktop). Real: router, relevance gate, assistant, policy,
approvals, task manager, coding agent, memory store (SQLite in a temp dir).
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from aiohttp import web

from baymax.assistant import Assistant
from baymax.coding.agent import AgentReport, parse_test_counts, wants_code_change
from baymax.coding.agent import TestRun as AgentTestRun
from baymax.coding.project import ProjectInfo
from baymax.config import load_settings
from baymax.intent.relevance import request_mode, tool_is_relevant
from baymax.security.secrets import SecretStore
from baymax.tasks.models import PlanStep, TaskStatus
from baymax.tools.creation_tools import CodingAgentTool
from conftest import FakeDesktop

API_QUESTION = "Explain what an API is in simple terms, using a real-world example."
SPRING_SUGGEST = "Suggest three Spring Boot backend features for my project"


@pytest.fixture
async def eager_model():
    """Calls the first offered tool every time (worst case for a small model); plain answers otherwise."""
    bodies: list[dict] = []

    async def tags(_r):
        return web.json_response({"models": [{"name": "llama3.2:3b"}]})

    async def chat(request):
        body = await request.json()
        bodies.append(body)
        if body.get("tools"):
            fn = body["tools"][0]["function"]["name"]
            args = {"goal": "add caching and security"} if fn == "coding__agent" else {}
            return web.json_response({"message": {"role": "assistant", "content": "",
                                                  "tool_calls": [{"function": {"name": fn, "arguments": args}}]}, "done": True})
        return web.json_response({"message": {"role": "assistant", "content": "An API is a menu: you order, the kitchen cooks, the waiter "
                                                                              "brings it back."}, "done": True})

    app = web.Application()
    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}", bodies
    await runner.cleanup()


@pytest.fixture
async def env(tmp_path, eager_model):
    url, bodies = eager_model
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
    yield {"s": s, "a": a, "desk": desk, "bodies": bodies, "ws": tmp_path / "ws"}
    await a.stop()
    await s.close()


def _text(r):
    return r["message"]["text"] if isinstance(r.get("message"), dict) else ""


# ------------------------------------------------------------------ Phase 2: knowledge questions use no desktop tools
async def test_api_question_is_answered_without_any_tool(env):
    r = await env["a"].handle_text(API_QUESTION)
    assert "taskId" not in r and "menu" in _text(r)
    assert env["desk"].calls == [] and all("tools" not in b for b in env["bodies"])  # no tool was even offered
    t = env["a"].traces.recent(1)[0]
    assert any(st["stage"] == "tool_selection" and st["status"] == "skipped" for st in t["stages"])


@pytest.mark.parametrize("q", [API_QUESTION, "How do I use Git branches?", "Explain how a for loop works in Java",
                               "What is the active voice in English grammar?", "Give me ideas to improve my app",
                               "Why do apps use OAuth?", "What does the window function do in SQL?"])
def test_explanations_and_advice_offer_no_desktop_or_write_tools(q):
    for tool in ("desktop.active_window", "desktop.read_window", "desktop.running_apps", "apps.open", "screen.find_element",
                 "coding.agent", "coding.java_class", "files.write", "shell.run"):
        assert not tool_is_relevant(tool, q), (tool, q)


@pytest.mark.parametrize("q,tool", [("What's in my current window?", "desktop.read_window"),
                                    ("which app is in front right now", "desktop.active_window"),
                                    ("open the README file and summarise it", "files.read"),
                                    ("What apps are open?", "desktop.running_apps"),
                                    ("run the tests in my project", "coding.agent")])
def test_explicit_desktop_and_project_requests_keep_their_tools(q, tool):
    assert tool_is_relevant(tool, q), (tool, q)


async def test_explicit_window_request_still_runs_the_desktop_tool(env):
    env["desk"].front = "Terminal"
    r = await env["a"].handle_text("Read the title of the current window.")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and "You're in Terminal" in t.result["summary"]


async def test_the_same_action_is_not_started_twice(env):
    r1, r2 = await asyncio.gather(env["a"].handle_text("Open Safari"), env["a"].handle_text("Open Safari"))
    await env["s"].tasks.wait(r1["taskId"], 5)
    assert r2.get("duplicate") and r2["taskId"] == r1["taskId"]
    assert [c for c in env["desk"].calls if c[0] == "open_app"] == [("open_app", "Safari")]


# ------------------------------------------------------------------ Phase 3: suggestions never change files
def _spring_repo(ws):
    repo = ws / "demo"
    (repo / "src/main/resources").mkdir(parents=True)
    (repo / "pom.xml").write_text("<project><artifactId>demo</artifactId></project>\n")
    (repo / "src/main/resources/application.properties").write_text("server.port=8080\n")
    return repo


async def test_feature_suggestions_change_no_files(env):
    repo = _spring_repo(env["ws"])
    before = {p: p.read_text() for p in repo.rglob("*") if p.is_file()}
    assert request_mode(SPRING_SUGGEST) == "advice" and not tool_is_relevant("coding.agent", SPRING_SUGGEST)
    r = await env["a"].handle_text(SPRING_SUGGEST)
    assert "taskId" not in r and _text(r)
    assert {p: p.read_text() for p in repo.rglob("*") if p.is_file()} == before
    assert not any(b.get("tools") for b in env["bodies"])


def test_coding_agent_needs_approval_to_edit_but_not_to_run_tests():
    tool = CodingAgentTool()
    assert tool.classify({"goal": "add a caching layer"})[0].value == "high"
    assert tool.classify({"goal": "suggest three features"})[0].value == "medium"
    assert tool.classify({"goal": ""})[0].value == "medium"
    assert not wants_code_change("suggest three Spring Boot features") and not wants_code_change("review the security config")
    assert wants_code_change("implement the second suggestion") and wants_code_change("fix the failing test")


async def test_explicit_implementation_waits_for_approval_and_denial_changes_nothing(env):
    repo = _spring_repo(env["ws"])
    before = (repo / "pom.xml").read_text()
    sub = env["s"].bus.subscribe()
    task = env["s"].tasks.submit("coding", "edit", "add caching",
                                 [PlanStep("edit", "coding.agent", {"repo": "demo", "goal": "add a caching layer"})])
    while True:
        ev = await asyncio.wait_for(sub.get(), 5)
        if ev.type == "approval.request":
            break
    assert "EDIT files" in ev.payload["summary"] and ev.payload["risk"] == "high"
    env["s"].tasks.respond_approval(ev.payload["approvalId"], False, "local")
    t = await env["s"].tasks.wait(task.id, 5)
    assert t.status is TaskStatus.CANCELLED and (repo / "pom.xml").read_text() == before


async def test_deleting_a_file_requires_confirmation(env):
    (env["ws"] / "keep.txt").write_text("important")
    sub = env["s"].bus.subscribe()
    task = env["s"].tasks.submit("files", "delete", "delete keep.txt", [PlanStep("delete", "files.delete", {"path": "keep.txt"})])
    while True:
        ev = await asyncio.wait_for(sub.get(), 5)
        if ev.type == "approval.request":
            break
    env["s"].tasks.respond_approval(ev.payload["approvalId"], False, "local")
    await env["s"].tasks.wait(task.id, 5)
    assert (env["ws"] / "keep.txt").exists()


def test_test_reports_distinguish_passed_failed_skipped_and_not_run():
    assert parse_test_counts("=== 3 passed, 1 failed, 2 skipped in 0.4s ===") == {"passed": 3, "failed": 1, "skipped": 2}
    assert parse_test_counts("Tests run: 10, Failures: 1, Errors: 0, Skipped: 2\n[ERROR] BUILD FAILURE") == \
        {"passed": 7, "failed": 1, "error": 0, "skipped": 2}
    assert parse_test_counts("Tests:       1 failed, 4 passed, 5 total") == {"failed": 1, "passed": 4}
    assert parse_test_counts("bash: mvn: command not found") == {}
    info = ProjectInfo(root=__import__("pathlib").Path("."))
    untested = AgentReport(project=info, status="changed_untested", changes=[{"path": "pom.xml", "action": "modified", "added": 3, "removed": 0}])
    assert untested.summary().startswith("UNVERIFIED CHANGES:") and "Tests: not run" in untested.summary()
    failed = AgentReport(project=info, status="tests_failed",
                         baseline=AgentTestRun("mvn -q test", 1, False, False, "Tests run: 5, Failures: 2, Errors: 0, Skipped: 1"))
    assert "2 passed, 2 failed, 1 skipped" in failed.summary()


# ------------------------------------------------------------------ Phase 4: remember a fact, answer the question
PHOENIX = "Remember this for our current conversation: my test project is called Project Phoenix. What is my project called?"


async def test_fact_is_extracted_scoped_and_the_question_answered(env):
    r = await env["a"].handle_text(PHOENIX)
    text = _text(r)
    assert "Your test project is called Project Phoenix." in text
    assert "this conversation only" in text and "Remember this" not in text
    assert "{" not in text and "memory_add" not in text and "content" not in text  # no raw payloads
    assert env["s"].memory.list() == []  # conversation scope: nothing written to the persistent store
    r = await env["a"].handle_text("What is my project called?")
    assert _text(r) == "Your test project is called Project Phoenix."
    assert not env["bodies"]  # answered from memory, not guessed by the model


async def test_persistent_fact_is_stored_concisely_and_recalled(env):
    r = await env["a"].handle_text("Remember my favourite editor is VS Code")
    assert "Saved" in _text(r)
    assert [m.content for m in env["s"].memory.list()] == ["favourite editor: VS Code"]
    r = await env["a"].handle_text("what is my favourite editor?")
    assert _text(r) == "Your favourite editor is VS Code."


async def test_unavailable_persistence_falls_back_honestly(env, monkeypatch):
    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(env["s"].memory, "add", broken)
    r = await env["a"].handle_text("Remember my build tool is Maven")
    assert "couldn't save it permanently" in _text(r)
    r = await env["a"].handle_text("what's my build tool?")
    assert _text(r) == "Your build tool is Maven."


async def test_unknown_fact_is_not_invented(env):
    r = await env["a"].handle_text("What is my project called?")
    assert "Phoenix" not in _text(r)  # nothing stored: falls back to a normal answer, never a made-up value
