"""Voice -> local model -> registered tool, through real processes and sockets.

* whisper-cli is a real executable on disk (a script standing in for whisper.cpp) found via the
  Homebrew search path, fed a real WAV file by the recognizer.
* Ollama is a real HTTP server speaking the /api/chat tool-calling protocol.
* The tool runs through the task manager and its outcome is verified (FakeDesktop as the Mac).
"""

from __future__ import annotations

import json
import math
import os
import stat

import numpy as np
import pytest
from aiohttp import web

from baymax.assistant import Assistant
from baymax.config import load_settings
from baymax.hostos import tools as hostools
from baymax.hostos.tools import GGML_MAGIC, check_whisper_model, find_tool, find_whisper_model
from baymax.llm.ollama import parse_tool_reply, registry_name, tool_spec, wire_name
from baymax.llm.prompts import claims_unperformed_action
from baymax.security.secrets import SecretStore
from baymax.tasks.models import TaskStatus
from baymax.voice.audio import PCM, write_wav

from conftest import FakeDesktop


def _exe(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _model(path, size=31 * 1024 * 1024, magic=GGML_MAGIC):
    with path.open("wb") as f:
        f.write(magic)
        f.truncate(size)  # sparse: cheap on disk
    return path


def test_finds_homebrew_tools_with_a_gui_minimal_path(tmp_path, monkeypatch):
    brew = tmp_path / "opt-homebrew-bin"
    brew.mkdir()
    _exe(brew / "whisper-cli", "#!/bin/sh\n")
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")  # what Finder-launched apps get
    monkeypatch.setattr(hostools, "EXTRA_BIN_DIRS", (str(brew),))
    assert find_tool("whisper-cli") == str(brew / "whisper-cli")
    assert find_tool(str(brew / "whisper-cli")) == str(brew / "whisper-cli")
    assert find_tool("definitely-not-a-tool") is None


def test_whisper_model_validation_and_preference(tmp_path, monkeypatch):
    d = tmp_path / "models"
    d.mkdir()
    assert "test stub" in check_whisper_model(_model(d / "for-tests-ggml-tiny.bin"))
    assert "KB" in check_whisper_model(_model(d / "ggml-small.bin", size=4096))
    assert "bad header" in check_whisper_model(_model(d / "ggml-medium.bin", magic=b"<htm"))  # e.g. an HTML error page
    _model(d / "ggml-tiny.en.bin")
    _model(d / "ggml-base.en.bin")
    monkeypatch.setattr(hostools, "whisper_model_dirs", lambda: [d])
    found, notes = find_whisper_model(str(tmp_path / "missing.bin"))
    assert found == d / "ggml-base.en.bin"  # preferred over tiny; stubs/broken files skipped
    assert any("does not exist" in n for n in notes) and any("bad header" in n for n in notes)
    assert find_whisper_model(str(d / "ggml-tiny.en.bin"))[0] == d / "ggml-tiny.en.bin"  # explicit config wins


def test_tool_specs_and_reply_parsing():
    spec = tool_spec({"name": "apps.open", "description": "Open an app", "risk": "medium",
                      "args": {"app": {"type": "str", "required": True, "description": "name"}}})
    assert spec["function"]["name"] == "apps__open" and spec["function"]["parameters"]["required"] == ["app"]
    assert registry_name(wire_name("documents.diagram")) == "documents.diagram"
    r = parse_tool_reply({"message": {"content": "", "tool_calls": [{"function": {"name": "apps__open", "arguments": '{"app": "Safari"}'}}]}})
    assert r.tool_calls[0].name == "apps.open" and r.tool_calls[0].arguments == {"app": "Safari"}
    assert claims_unperformed_action("Sure! I've opened Safari for you.")
    assert not claims_unperformed_action("Safari is a web browser made by Apple.")


@pytest.fixture
async def fake_ollama_tools():
    seen: list[dict] = []
    plan: dict = {}

    async def tags(_r):
        return web.json_response({"models": [{"name": "llama3.2:3b"}]})

    async def chat(request):
        body = await request.json()
        seen.append(body)
        user = [m for m in body["messages"] if m["role"] == "user"][-1]["content"].lower()
        if "tools" in body and "browser" in user:
            return web.json_response({"message": {"role": "assistant", "content": "",
                                                  "tool_calls": [{"function": {"name": "apps__open", "arguments": {"app": "Safari"}}}]},
                                      "done": True})
        if "pretend" in user:  # claims an action with or without tools offered
            return web.json_response({"message": {"role": "assistant", "content": plan.get("text", "I've opened Safari for you!")}, "done": True})
        return web.json_response({"message": {"role": "assistant", "content": "Hello."}, "done": True})

    app = web.Application()
    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}", seen, plan
    await runner.cleanup()


@pytest.fixture
async def voice_env(tmp_path, fake_ollama_tools):
    url, seen, plan = fake_ollama_tools
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    transcript = tmp_path / "transcript.txt"
    log = tmp_path / "whisper-args.txt"
    # Behaves like whisper-cli: reads -m MODEL -f WAV, prints the transcript on stdout.
    _exe(bin_dir / "whisper-cli", f"""#!/bin/sh
echo "$@" > "{log}"
while [ $# -gt 0 ]; do case "$1" in -f) WAV="$2"; shift;; esac; shift; done
[ -s "$WAV" ] || {{ echo "no audio" >&2; exit 2; }}
cat "{transcript}"
""")
    model = _model(tmp_path / "ggml-base.en.bin")
    settings = load_settings(tmp_path / "data", {
        "workspace_dir": str(tmp_path / "ws"),
        "llm": {"base_url": url, "connect_timeout_s": 1, "request_timeout_s": 5},
        "network": {"local_port": 0},
        "voice": {"tts_engine": "none", "stt_engine": "whisper.cpp", "whisper_binary": str(bin_dir / "whisper-cli"),
                  "whisper_model": str(model)},
        "research": {"local_only": True},
    })
    settings.ensure_dirs()
    from baymax.services import build_services

    desktop = FakeDesktop()
    s = build_services(settings, desktop=desktop, secrets=SecretStore(settings.data_dir, use_keychain=False))
    a = Assistant(s)
    a.start()
    yield {"s": s, "a": a, "desktop": desktop, "transcript": transcript, "log": log, "seen": seen, "plan": plan}
    await a.stop()
    await s.close()


def _speech_wav() -> bytes:
    t = np.arange(16000) / 16000
    sig = 0.3 * np.sin(2 * math.pi * 220 * t)
    pad = np.zeros(8000)
    return write_wav(PCM(np.concatenate([pad, sig, pad]).astype(np.float32), 16000))


async def test_voice_command_reaches_model_and_runs_a_verified_tool(voice_env):
    s, a, desktop = voice_env["s"], voice_env["a"], voice_env["desktop"]
    voice_env["transcript"].write_text("I want to look at a web browser please\n")  # no rule matches this; the model decides
    st = s.stt.status()
    assert st["available"], st
    tr = await s.stt.transcribe(_speech_wav())
    assert tr.engine == "whisper.cpp" and tr.text == "I want to look at a web browser please"
    args = voice_env["log"].read_text()
    assert "-m" in args and "ggml-base.en.bin" in args and "-l en" in args  # model passed explicitly
    r = await a.handle_text(tr.text, source="voice")
    assert r.get("tool") == "apps.open", r
    task = await s.tasks.wait(r["taskId"], 5)
    assert task.status is TaskStatus.COMPLETED and task.verified
    assert ("open_app", "Safari") in desktop.calls
    native = [b for b in voice_env["seen"] if "tools" in b]
    assert native and any(t["function"]["name"] == "apps__open" for t in native[0]["tools"])


async def test_model_text_claiming_an_action_does_nothing_and_is_corrected(voice_env):
    s, a, desktop = voice_env["s"], voice_env["a"], voice_env["desktop"]
    sub = s.bus.subscribe()
    r = await a.handle_text("pretend you did something")
    assert "taskId" not in r and desktop.calls == []
    text = r["message"]["text"] if isinstance(r.get("message"), dict) else str(r)
    # An action request: the invented claim is replaced, never repeated (a question keeps its answer plus a note).
    assert "I've opened Safari" not in text and "nothing on your Mac was changed" in text
    sub.close()
