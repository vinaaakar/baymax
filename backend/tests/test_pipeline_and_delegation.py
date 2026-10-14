"""Request pipeline, inference path, context handling, tool catalog and AI delegation.

Mocked: the macOS desktop (FakeDesktop) and Ollama (a local HTTP server speaking the real Ollama API).
Real: the router, assistant, task manager, policy/approvals, tool registry and HTTP/JSON paths.
These are not macOS integration tests; see docs/VERIFICATION_MATRIX.md for what still needs the real Mac.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web

from baymax.assistant import Assistant
from baymax.config import load_settings
from baymax.security.secrets import SecretStore
from baymax.tasks.models import TaskStatus
from baymax.tools.base import Tool, ToolResult
from baymax.tools.delegate_tools import pick_prompt_field, resolve_service
from baymax.screen.elements import parse_ax_dump
from conftest import FakeDesktop

T = "\t"


def ax(role, name, x, y, w, h, desc="", value=""):
    return T.join([role, name, desc, value, str(x), str(y), str(w), str(h)])


CHATGPT_PAGE = "\n".join([
    ax("AXButton", "New chat", 20, 120, 100, 30),
    ax("AXStaticText", "", 400, 300, 300, 20, value="What can I help with?"),
    ax("AXTextArea", "", 400, 760, 640, 44, desc="Message ChatGPT"),
    ax("AXButton", "Send prompt", 1050, 765, 32, 32),
])
LOGIN_PAGE = "\n".join([ax("AXStaticText", "", 500, 300, 200, 20, value="Log in to continue"), ax("AXButton", "Sign up", 500, 360, 80, 30)])


# ------------------------------------------------------------------ fake Ollama (real HTTP API shape)
@pytest.fixture
async def ollama():
    bodies: list[dict] = []

    async def tags(_r):
        return web.json_response({"models": [{"name": "llama3.2:3b"}]})

    async def chat(request):
        body = await request.json()
        bodies.append(body)
        last = next(m["content"] for m in reversed(body["messages"]) if m["role"] == "user")
        if "newton" in last.lower():
            text = "Newton's third law: for every action there is an equal and opposite reaction."
        elif "simpler" in last.lower() or "simply" in last.lower():
            prev = [m["content"] for m in body["messages"] if m["role"] == "user"]
            text = f"Put simply (about: {prev[-2] if len(prev) > 1 else '?'}): pushes come in pairs."
        else:
            text = "Sure."
        return web.json_response({"message": {"role": "assistant", "content": text}, "done": True})

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
async def env(tmp_path, ollama):
    url, bodies = ollama
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
    yield {"s": s, "a": a, "desk": desk, "bodies": bodies}
    await a.stop()
    await s.close()


def _text(r):
    return r["message"]["text"] if isinstance(r.get("message"), dict) else ""


# ------------------------------------------------------------------ inference path + context
async def test_knowledge_question_goes_to_the_local_model_with_a_proper_payload(env):
    r = await env["a"].handle_text("What is Newton's third law?")
    assert "equal and opposite reaction" in _text(r)
    body = env["bodies"][-1]
    assert body["model"] == "llama3.2:3b" and "tools" not in body
    assert body["messages"][0]["role"] == "system" and "BAYMAX" in body["messages"][0]["content"]
    assert body["messages"][-1] == {"role": "user", "content": "What is Newton's third law?"}
    assert env["desk"].calls == []


async def test_follow_up_uses_the_right_earlier_question(env):
    await env["a"].handle_text("What is Newton's third law?")
    r = await env["a"].handle_text("Explain that more simply")
    assert "What is Newton's third law?" in _text(r)  # the model received the earlier question as context


async def test_earlier_action_results_are_labelled_not_replayed_as_instructions(env):
    env["desk"].front = "Terminal"
    r = await env["a"].handle_text("read the current window")
    await env["s"].tasks.wait(r["taskId"], 5)
    await asyncio.sleep(0.2)
    await env["a"].handle_text("thanks, and what is Newton's third law?")
    msgs = env["bodies"][-1]["messages"]
    results = [m for m in msgs if m["role"] == "assistant" and "Result of an earlier action" in m["content"]]
    assert results and all(m["role"] == "assistant" for m in results)
    assert not any(m["role"] == "system" and "Terminal" in m["content"] for m in msgs[1:])


# ------------------------------------------------------------------ request trace
async def test_every_request_records_its_pipeline_stages(env):
    await env["a"].handle_text("Hello, BAYMAX.")
    await env["a"].handle_text("What is Newton's third law?")
    r = await env["a"].handle_text("Open Safari")
    await env["s"].tasks.wait(r["taskId"], 5)
    await asyncio.sleep(0.2)
    hello, newton, safari = env["a"].traces.recent(3)
    assert hello["intent"] == "greeting" and not any(s["stage"] == "execution" for s in hello["stages"])
    assert newton["intent"] == "chat" and newton["tool"] is None
    assert any(s["stage"] == "tool_selection" and s["status"] == "skipped" for s in newton["stages"])
    assert safari["tool"] == "apps.open" and safari["args"] == {"app": "Safari"} and safari["verified"] is True
    stages = [s["stage"] for s in safari["stages"]]
    for st in ("input", "intent", "tool_selection", "argument_validation", "permissions", "execution", "verification", "response"):
        assert st in stages, st


async def test_requests_api_exposes_traces(env):
    from baymax.server.app import BaymaxServer

    await env["a"].handle_text("Hello")
    server = BaymaxServer.__new__(BaymaxServer)
    server.assistant = env["a"]

    class Req:
        query = {"limit": "5"}
    resp = await server.requests(Req())
    data = json.loads(resp.body)
    assert data["requests"][-1]["original"] == "Hello"


# ------------------------------------------------------------------ speech validation for phone voice
async def test_phone_voice_sound_tags_are_discarded_before_routing(env):
    r = await env["a"].handle_text("[GUNSHOT]", source="device:abc", voice=True)
    assert r.get("discarded") and env["desk"].calls == [] and not env["bodies"]
    t = env["a"].traces.recent(1)[0]
    assert any(s["stage"] == "speech_validation" and s["status"] == "rejected" for s in t["stages"])
    r = await env["a"].handle_text("open Safari", source="device:abc", voice=True)
    assert "taskId" in r  # real speech still works


# ------------------------------------------------------------------ friendly validation errors, no tool names
async def test_invalid_model_arguments_get_a_plain_question(env):
    from baymax.intent.router import Intent

    r = await env["a"]._start_tool(Intent("tool", "apps.open", {}, "x", text="open it"), __import__("baymax.assistant").assistant.Origin("local", False))
    assert "which app" in _text(r) and "apps.open" not in _text(r)


# ------------------------------------------------------------------ typed tool catalog + timeouts
def test_every_tool_declares_intents_permissions_timeout_and_verification(env):
    for t in env["s"].tools.all():
        e = t.catalog_entry()
        assert e["intents"] and e["timeoutS"] > 0 and e["verification"] and e["recovery"], t.name


async def test_tool_timeouts_are_enforced(env):
    class Slow(Tool):
        name = "test.slow"
        description = "sleeps"
        timeout_s = 0.3
        intents = ("test",)
        verification = "n/a"
        recovery = "n/a"

        async def run(self, ctx, args):
            await asyncio.sleep(5)
            return ToolResult(True, "done")

    env["s"].tools.register(Slow())
    from baymax.tasks.models import PlanStep

    task = env["s"].tasks.submit("test", "slow", "slow", [PlanStep("slow", "test.slow", {})])
    t = await env["s"].tasks.wait(task.id, 5)
    assert t.status is TaskStatus.FAILED and "longer than" in (t.error or "") and t.error_kind == "timeout"


# ------------------------------------------------------------------ AI delegation
def test_service_resolution_and_field_picking():
    assert resolve_service("ChatGPT").key == "chatgpt" and resolve_service("chat gpt").key == "chatgpt"
    assert resolve_service("Google Gemini").key == "gemini" and resolve_service("notepad") is None
    f = pick_prompt_field(parse_ax_dump(CHATGPT_PAGE, "Safari"))
    assert f is not None and f.role == "AXTextArea" and f.description == "Message ChatGPT"


async def test_prepare_prompt_in_chatgpt_types_and_verifies_without_sending(env):
    desk = env["desk"]
    desk.ax_dump["Safari"] = CHATGPT_PAGE
    r = await env["a"].handle_text("Open ChatGPT and enter this prompt: Summarize the French Revolution")
    t = await env["s"].tasks.wait(r["taskId"], 15)
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    assert ("open_url", "https://chatgpt.com", "Safari") in desk.calls  # ChatGPT app not installed → website
    assert ("focus", "Safari", "AXTextArea", 400, 760) in desk.calls
    assert ("type", "Safari", "Summarize the French Revolution") in desk.calls
    assert not any(c[0] == "return" for c in desk.calls) and "not sent" in t.result["summary"]


async def test_asking_chatgpt_requires_approval_of_the_exact_prompt_then_sends(env):
    desk = env["desk"]
    desk.ax_dump["Safari"] = CHATGPT_PAGE
    desk.ax_after_submit["Safari"] = CHATGPT_PAGE.replace("What can I help with?", "Explain recursion")
    sub = env["s"].bus.subscribe()
    r = await env["a"].handle_text("Ask ChatGPT to explain recursion")
    appr = None
    while appr is None:
        ev = await asyncio.wait_for(sub.get(), 5)
        if ev.type == "approval.request":
            appr = ev.payload
    assert "Explain recursion" in appr["summary"] and appr["risk"] == "high"
    assert not any(c[0] in ("open_url", "type") for c in desk.calls)  # nothing happens before approval
    ok, _ = env["s"].tasks.respond_approval(appr["approvalId"], True, "local")
    assert ok
    t = await env["s"].tasks.wait(r["taskId"], 15)
    assert t.status is TaskStatus.COMPLETED and t.verified and t.result["data"]["submitted"], t.error
    assert ("return", "Safari") in desk.calls


async def test_denied_approval_sends_nothing(env):
    env["desk"].ax_dump["Safari"] = CHATGPT_PAGE
    sub = env["s"].bus.subscribe()
    r = await env["a"].handle_text("send 'hello there' to chatgpt")
    while True:
        ev = await asyncio.wait_for(sub.get(), 5)
        if ev.type == "approval.request":
            break
    env["s"].tasks.respond_approval(ev.payload["approvalId"], False, "local")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.CANCELLED and not any(c[0] in ("open_url", "type", "return") for c in env["desk"].calls)


async def test_login_wall_stops_and_explains(env):
    env["desk"].ax_dump["Safari"] = LOGIN_PAGE
    r = await env["a"].handle_text("Prepare this prompt in Claude: write a haiku")
    t = await env["s"].tasks.wait(r["taskId"], 20)
    assert t.status is TaskStatus.FAILED and "sign in" in t.result["summary"]
    assert not any(c[0] == "type" for c in env["desk"].calls)


async def test_native_app_is_used_when_installed(env):
    from baymax.hostos.apps import AppInfo

    desk = env["desk"]
    desk.installed.append(AppInfo("ChatGPT", "", "com.openai.chat"))
    desk.ax_dump["ChatGPT"] = CHATGPT_PAGE
    r = await env["a"].handle_text("Prepare this prompt in ChatGPT: hi")
    t = await env["s"].tasks.wait(r["taskId"], 15)
    assert t.status is TaskStatus.COMPLETED and ("open_app", "ChatGPT") in desk.calls
    assert not any(c[0] == "open_url" for c in desk.calls)


async def test_estop_blocks_delegation_but_not_questions(env):
    env["s"].estop.engage("test")
    r = await env["a"].handle_text("Prepare this prompt in Claude: hi")
    assert "taskId" not in r and "Emergency Stop" in _text(r)
    r = await env["a"].handle_text("What is Newton's third law?")
    assert "equal and opposite" in _text(r)


async def test_misspelled_app_resolves(env):
    r = await env["a"].handle_text("open safary")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and ("open_app", "Safari") in env["desk"].calls
