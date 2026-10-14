"""Regressions for the failures seen in the BAYMAX chat (Oct 14):

* whisper sound tags ("[GUNSHOT]", "[explosion]") were sent as user messages;
* "Hi, BIMEX!" made the model call files.read with an invented path;
* "Hey Bimax, can you hear me?" / "what are the walks you can do" called desktop.read_window with app "None",
  producing AppleScript "Can't get process \\"None\\"" and "missing argument 'app'".

The fake Ollama server below misbehaves exactly like that on purpose.
"""

from __future__ import annotations

import pytest
from aiohttp import web

from baymax.assistant import Assistant
from baymax.config import load_settings
from baymax.hostos.desktop import AppNameError, validate_app_name
from baymax.intent.relevance import tool_is_relevant
from baymax.security.secrets import SecretStore
from baymax.tasks.models import TaskStatus
from baymax.voice.stt import clean_transcript
from conftest import FakeDesktop


@pytest.fixture
async def bad_ollama():
    """Always tries to call a tool, with the same bad arguments seen in the screenshots."""
    calls: list[dict] = []
    state = {"mode": "read_window_none"}

    async def tags(_r):
        return web.json_response({"models": [{"name": "llama3.2:3b"}]})

    async def chat(request):
        body = await request.json()
        calls.append(body)
        if "tools" not in body:  # plain conversation
            return web.json_response({"message": {"role": "assistant", "content": "I'm a local assistant. How can I help?"}, "done": True})
        mode = state["mode"]
        fn = {"read_window_none": ("desktop__read_window", {"app": "None"}),
              "read_window_missing": ("desktop__read_window", {}),
              "files_read": ("files__read", {"path": "/Users/username/Desktop/explosion.txt"})}[mode]
        return web.json_response({"message": {"role": "assistant", "content": "",
                                              "tool_calls": [{"function": {"name": fn[0], "arguments": fn[1]}}]}, "done": True})

    app = web.Application()
    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}", calls, state
    await runner.cleanup()


@pytest.fixture
async def env(tmp_path, bad_ollama):
    url, calls, state = bad_ollama
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
    yield {"s": s, "a": a, "desk": desk, "calls": calls, "state": state}
    await a.stop()
    await s.close()


def _text(r):
    return r["message"]["text"] if isinstance(r.get("message"), dict) else ""


@pytest.mark.parametrize("utterance", ["What can you do?", "What are the walks you can do tell me?",
                                       "Baymax, what are the walks you can do, tell me?", "What are the work you can do?",
                                       "Tell me what other walks you can do."])
async def test_capabilities_come_from_the_registry_without_tools(env, utterance):
    r = await env["a"].handle_text(utterance, source="voice")
    text = _text(r)
    assert "taskId" not in r and env["desk"].calls == []
    assert "Here's what I can do right now" in text and "Open apps and websites" in text
    assert "Not available at the moment" in text and "Generate images" in text  # honest: no image generator configured
    assert not env["calls"]  # the model was not asked


@pytest.mark.parametrize("utterance", ["Hey Bimax, can you hear me?", "Hi, BIMEX!", "Hey Baymax, can you hear me?", "Baymax!"])
async def test_greetings_never_touch_tools(env, utterance):
    r = await env["a"].handle_text(utterance, source="voice")
    assert "taskId" not in r and env["desk"].calls == []
    assert "can hear you" in _text(r)


async def test_model_picking_an_unrelated_tool_is_ignored(env):
    env["state"]["mode"] = "files_read"
    r = await env["a"].handle_text("tell me a joke about robots")
    assert "taskId" not in r
    assert "explosion.txt" not in _text(r) and "How can I help" in _text(r)
    assert any("tools" not in b for b in env["calls"])  # fell back to a plain, tool-less answer


async def test_read_the_current_window_resolves_the_front_app(env):
    env["desk"].front = "Terminal"
    r = await env["a"].handle_text("read the current window")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and t.verified
    assert ("read_window", "Terminal") in env["desk"].calls


async def test_read_window_with_no_identifiable_app_asks_instead_of_calling_applescript(env):
    env["desk"].front = None
    r = await env["a"].handle_text("read the current window")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and "Click the window first" in (t.error or "")
    assert not any(c[0] == "read_window" for c in env["desk"].calls)


async def test_model_passing_app_none_is_resolved_not_sent_to_applescript(env):
    env["state"]["mode"] = "read_window_none"
    env["desk"].front = "Notes"
    r = await env["a"].handle_text("could you check what my notes window says")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED, t.error
    assert ("read_window", "Notes") in env["desk"].calls and ("read_window", "None") not in env["desk"].calls


async def test_model_omitting_app_no_longer_fails_validation(env):
    env["state"]["mode"] = "read_window_missing"
    env["desk"].front = "Safari"
    r = await env["a"].handle_text("what does this window show")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED, t.error


async def test_invalid_and_unavailable_apps_get_helpful_answers(env):
    r = await env["a"].handle_text("read the Spotify window")  # not running
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and "isn't running" in t.result["summary"]
    assert t.error_kind == "not_found"


def test_app_name_validation_rejects_placeholders():
    for bad in (None, "", "None", "null", "undefined", "current", "  ", "rm -rf /;"):
        with pytest.raises(AppNameError):
            validate_app_name(bad)
    assert validate_app_name("Visual Studio Code") == "Visual Studio Code"


def test_whisper_sound_tags_are_not_speech():
    for raw in ("[GUNSHOT]", "[explosion]", " (music) ", "[BLANK_AUDIO]", "♪ la la ♪", "*laughs*"):
        assert clean_transcript(raw) == ""
    assert clean_transcript("*laughs* open Safari") == "open Safari"


def test_relevance_rules():
    assert not tool_is_relevant("files.read", "Hi, BIMEX!")
    assert not tool_is_relevant("desktop.read_window", "what are the walks you can do")
    assert tool_is_relevant("desktop.read_window", "read the current window")
    assert tool_is_relevant("apps.open", "open VS Code")
    assert not tool_is_relevant("made.up", "anything")


async def test_phone_status_question_answers_from_real_state(env):
    r = await env["a"].handle_text("Tell me whether my phone is connected")
    assert "taskId" not in r and ("iPhone" in _text(r))


# ---------------------------------------------------------------- application resolution (the "EB Max" failure)
from baymax.hostos.apps import AppInfo, resolve_app  # noqa: E402

RUNNING = [AppInfo("Code", "Code", "com.microsoft.VSCode", True), AppInfo("Terminal", "Terminal", "com.apple.Terminal", True)]
INSTALLED = [AppInfo("Visual Studio Code", "", "com.microsoft.VSCode"), AppInfo("Terminal", "", "com.apple.Terminal"),
             AppInfo("Safari", "", "com.apple.Safari"), AppInfo("Notes", "", "com.apple.Notes")]


@pytest.mark.parametrize("heard,status,name,running", [
    ("EB Max", "self", None, None), ("Bimax", "self", None, None), ("B Max", "self", None, None),
    ("None", "none", None, None), ("", "none", None, None), (None, "none", None, None),
    ("VS Code", "resolved", "Visual Studio Code", True), ("Visual Studio Code", "resolved", "Visual Studio Code", True),
    ("vscode", "resolved", "Visual Studio Code", True), ("terminal", "resolved", "Terminal", True),
    ("Safari", "resolved", "Safari", False), ("Photoshop", "unknown", None, None), ("Safary", "unknown", None, None),
])
def test_app_resolver(heard, status, name, running):
    r = resolve_app(heard, RUNNING, INSTALLED)
    assert r.status == status
    if name:
        assert r.app.name == name and r.app.running is running
    if heard == "VS Code":
        assert r.app.process == "Code"  # AppleScript gets the real process name
    if heard == "Safary":
        assert r.suggestions == ["Safari"]


@pytest.mark.parametrize("utterance,expect", [
    ("read the EB Max window", "sounds like my own name"),
    ("read the Photoshop window", "can't find an app called"),
    ("read the Notes window", "Notes isn't running"),  # installed but closed
])
async def test_read_window_never_sends_unverified_names_to_applescript(env, utterance, expect):
    env["desk"].front = "Terminal"
    r = await env["a"].handle_text(utterance)
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and expect in (t.error or t.result["summary"])
    assert not any(c[0] == "read_window" for c in env["desk"].calls)


async def test_read_window_with_an_alias_reads_the_real_process(env):
    env["desk"].running.add("Visual Studio Code")
    r = await env["a"].handle_text("read the VS Code window")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED, t.error
    assert ("read_window", "Code") in env["desk"].calls


async def test_open_eb_max_asks_instead_of_launching(env):
    r = await env["a"].handle_text("open EB Max")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and "my own name" in (t.error or "")
    assert not any(c[0] == "open_app" for c in env["desk"].calls)


async def test_open_visual_studio_code_by_alias(env):
    r = await env["a"].handle_text("Open Visual Studio Code")
    t = await env["s"].tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and t.verified
    assert ("open_app", "Visual Studio Code") in env["desk"].calls


# ---------------------------------------------------------------- the user's exact acceptance messages
async def test_users_exact_messages(env):
    a, s, desk = env["a"], env["s"], env["desk"]
    desk.front = "Terminal"
    desk.running.add("Terminal")
    s.state["current_project"] = str(__import__("pathlib").Path(__file__).resolve().parents[2])

    async def say(text):
        desk.calls.clear()
        return await a.handle_text(text)

    for greet in ("Hi, Baymax.", "Can you hear me?"):
        r = await say(greet)
        assert "taskId" not in r and desk.calls == []
    for cap in ("What can you do?", "What can you work on?"):
        r = await say(cap)
        assert "taskId" not in r and "Here's what I can do right now" in _text(r) and desk.calls == []
    r = await say("Read the current window.")
    assert (await s.tasks.wait(r["taskId"], 5)).verified and ("read_window", "Terminal") in desk.calls
    r = await say("Open Visual Studio Code.")
    assert (await s.tasks.wait(r["taskId"], 5)).verified and ("open_app", "Visual Studio Code") in desk.calls
    r = await say("Find the function startCursorTracking in my repository.")
    t = await s.tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.COMPLETED and t.result["data"]["hits"][0]["name"] == "startCursorTracking"
    r = await say("Explain the error currently displayed in the terminal.")
    t = await s.tasks.wait(r["taskId"], 5)
    assert ("read_window", "Terminal") in desk.calls  # looked at the Terminal window, not a guessed app
    assert t.status is TaskStatus.FAILED and "Copy the error text" in (t.error or "")  # fake Terminal shows no error
    r = await say("Is my iPhone connected?")
    assert "taskId" not in r and "iPhone" in _text(r)
    await say("Go to sleep.")
    assert a.sleeping
    r = await say("Open Visual Studio Code.")
    assert r.get("sleeping") and desk.calls == []  # asleep: no new work
    await say("Wake up.")
    assert not a.sleeping
