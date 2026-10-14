"""The phone reports the Mac's model truthfully and gets real answers from it.

Root cause fixed: a device-count update (connection.status without an 'llm' field) used to be read by the
phone as "llm != ready", so a connected phone showed "No local model" even with Ollama running.
"""

from __future__ import annotations

import json

import aiohttp
import pytest
from aiohttp import web

from test_server import env, pair, recv_type, server  # noqa: F401


@pytest.fixture
async def ollama():
    async def tags(_r):
        return web.json_response({"models": [{"name": "llama3.2:3b"}]})

    async def chat(request):
        body = await request.json()
        user = [m for m in body["messages"] if m["role"] == "user"][-1]["content"]
        return web.json_response({"message": {"role": "assistant", "content": f"Local answer to: {user}"}, "done": True})

    app = web.Application()
    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    await runner.cleanup()


async def _statuses(ws, n=3, timeout=3.0):
    import asyncio

    out = []
    try:
        while len(out) < n:
            m = json.loads((await asyncio.wait_for(ws.receive(), timeout)).data)
            if m["type"] == "connection.status":
                out.append(m["payload"])
    except asyncio.TimeoutError:
        pass
    return out


async def test_phone_sees_mac_model_ready_and_gets_a_real_reply(server, ollama):  # noqa: F811
    server.s.settings.llm.base_url = ollama
    async with aiohttp.ClientSession() as s:
        ws, _ = await pair(server, s, "iphone-model")
        sts = await _statuses(ws, n=2)
        with_llm = [p for p in sts if "llm" in p]
        assert with_llm and all(p["llm"] == "ready" for p in with_llm), sts
        assert with_llm[0]["model"] == "llama3.2:3b"
        # every status that reaches the phone either carries the truth or says nothing about the model
        assert all(p.get("llm") in (None, "ready") for p in sts)
        await ws.send_str(env("user.text", {"text": "why is the sky blue"}))
        msg = await recv_type(ws, "assistant.message", timeout=10)
        while msg["payload"].get("role") != "assistant" or "Local answer" not in msg["payload"]["text"]:
            msg = await recv_type(ws, "assistant.message", timeout=10)
        assert "Local answer to: why is the sky blue" in msg["payload"]["text"]
        await ws.close()


async def test_phone_sees_mac_model_unavailable_and_an_honest_reply(server):  # noqa: F811
    async with aiohttp.ClientSession() as s:  # services fixture points Ollama at a closed port
        ws, _ = await pair(server, s, "iphone-nomodel")
        sts = [p for p in await _statuses(ws, n=2) if "llm" in p]
        assert sts and sts[0]["llm"] == "unavailable"
        await ws.send_str(env("user.text", {"text": "why is the sky blue"}))
        msg = await recv_type(ws, "assistant.message", timeout=10)
        assert "can't reach my local language model" in msg["payload"]["text"]
        await ws.close()


async def test_model_status_change_is_pushed_to_the_phone(server):  # noqa: F811
    async with aiohttp.ClientSession() as s:
        ws, _ = await pair(server, s, "iphone-push")
        await _statuses(ws, n=2, timeout=1.0)
        server.s.bus.publish("llm.status", {"available": True, "model": "llama3.2:3b"})
        sts = await _statuses(ws, n=1)
        assert sts and sts[0]["llm"] == "ready" and sts[0]["model"] == "llama3.2:3b"
        await ws.close()
