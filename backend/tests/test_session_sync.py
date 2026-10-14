"""One live session shared by the Mac app (local WS) and a paired phone (TLS WS, protocol v2).

Covers sleep/wake from either side, emergency stop from the phone killing a real subprocess,
new work refused while stopped, and an abrupt phone disconnect + reconnect that resynchronises
state instead of corrupting it.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import aiohttp

from baymax.security.pairing import compute_mac
from baymax.security.policy import Risk
from baymax.tasks.models import PlanStep, TaskStatus
from baymax.tools.base import Tool, ToolContext, ToolResult
from test_server import H, connect, env, pair, recv_type, server  # noqa: F401


class RealSubprocessTool(Tool):
    """Runs `sleep 30` as a real child process (like a long build) and records its pid."""

    name = "test.long_process"
    description = "long-running subprocess"
    risk = Risk.LOW
    args = {}
    pid: int | None = None

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        proc_task = asyncio.create_task(ctx.services.processes.run(["sleep", "30"], timeout=60))
        for _ in range(50):
            await asyncio.sleep(0.02)
            if ctx.services.processes._procs:
                RealSubprocessTool.pid = next(iter(ctx.services.processes._procs)).pid
                break
        res = await proc_task
        return ToolResult(res.ok, "finished", {"rc": res.returncode})


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def mac_ws(server, s):
    ws = await s.ws_connect(server.base.replace("http", "ws") + "/api/ws")
    await ws.send_json({"type": "auth", "token": server.token})
    hello = await asyncio.wait_for(ws.receive_json(), 5)
    assert hello["type"] == "hello"
    return ws


async def mac_wait(ws, type_, pred=lambda p: True, timeout=5):
    async def _r():
        while True:
            ev = await ws.receive_json()
            if ev["type"] == type_ and pred(ev["payload"]):
                return ev["payload"]
    return await asyncio.wait_for(_r(), timeout)


async def phone_wait(ws, type_, pred=lambda p: True, timeout=5):
    async def _r():
        while True:
            m = json.loads((await ws.receive()).data)
            if m["type"] == type_ and pred(m["payload"]):
                return m["payload"]
    return await asyncio.wait_for(_r(), timeout)


async def test_shared_session_sleep_estop_disconnect_reconnect(server):  # noqa: F811
    server.s.tools.register(RealSubprocessTool())
    async with aiohttp.ClientSession() as s:
        mac = await mac_ws(server, s)
        phone, key = await pair(server, s, "iphone-sync")
        await mac_wait(mac, "devices.changed", lambda p: p.get("count") == 1)

        # --- sleep from the phone reaches the Mac; wake from the Mac reaches the phone
        await phone.send_str(env("sleep.set", {"sleeping": True}))
        assert (await mac_wait(mac, "sleep.state"))["sleeping"] is True
        await phone_wait(phone, "character.state", lambda p: p["state"] == "SLEEPING")
        assert server.s.cursor.sleeping  # cursor relay suspended
        await phone.send_str(env("voice.state", {"state": "listening"}))  # a stale mic event must not wake it
        await asyncio.sleep(0.1)
        assert server.s.character.state.value == "SLEEPING"
        async with s.post(server.base + "/api/sleep", json={"sleeping": False}, headers=H(server)):
            pass
        await phone_wait(phone, "sleep.state", lambda p: p["sleeping"] is False)
        assert not server.s.cursor.sleeping

        # --- emergency stop from the phone kills a real running process and blocks new work
        task = server.s.tasks.submit("test", "Long build", "x", [PlanStep("run", "test.long_process", {})])
        for _ in range(100):
            if RealSubprocessTool.pid:
                break
            await asyncio.sleep(0.02)
        pid = RealSubprocessTool.pid
        assert pid and alive(pid)
        await phone.send_str(env("estop.trigger", {"reason": "phone button"}))
        await mac_wait(mac, "estop.state", lambda p: p["engaged"] is True)
        done = await server.s.tasks.wait(task.id, 5)
        assert done.status is TaskStatus.CANCELLED
        await asyncio.sleep(0.2)
        assert not alive(pid), "the child process must be killed, not just the task marked cancelled"
        async with s.post(server.base + "/api/chat", json={"text": "open Safari"}, headers=H(server)) as r:
            body = await r.json()
        assert "taskId" not in body.get("result", body)
        assert ("open_app", "Safari") not in server.s.desktop.calls

        # --- the phone drops without a close frame, then reconnects: it gets the current state, not stale state
        phone._response.connection.transport.abort()
        await mac_wait(mac, "devices.changed", lambda p: p.get("count") == 0)
        ws2 = await connect(server, s)
        await ws2.send_str(env("hello", {"protocol": 2, "minProtocol": 1, "client": "test", "deviceId": "iphone-sync"}))
        assert (await phone_wait(ws2, "hello.ack"))["protocol"] == 2
        ch = await phone_wait(ws2, "auth.challenge")
        await ws2.send_str(env("auth.response", {"deviceId": "iphone-sync", "mac": compute_mac(key, ch["nonce"], "iphone-sync")}))
        await phone_wait(ws2, "auth.ok")
        assert (await phone_wait(ws2, "estop.state"))["engaged"] is True  # snapshot reflects the stop
        await mac_wait(mac, "devices.changed", lambda p: p.get("count") == 1)

        # --- only the Mac can reset the stop; the phone sees it
        async with s.post(server.base + "/api/estop", json={"engaged": False}, headers=H(server)):
            pass
        await phone_wait(ws2, "estop.state", lambda p: p["engaged"] is False)
        await ws2.send_str(env("user.text", {"text": "open Safari"}))
        res = await phone_wait(ws2, "task.result", timeout=10)
        assert res["status"] == "COMPLETED" and res["verified"]
        await ws2.close()
        await mac.close()
