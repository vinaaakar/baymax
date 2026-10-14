import asyncio
import json
import ssl

import aiohttp
import pytest
from aiohttp import web

from baymax.security.pairing import compute_mac
from baymax.server.app import BaymaxServer


@pytest.fixture
async def server(services):
    srv = BaymaxServer(services)
    srv.assistant.start()
    srv.hub.start()
    runner = web.AppRunner(srv.local_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    services.settings.network.lan_host = "127.0.0.1"
    services.settings.network.lan_port = 0
    await srv.start_lan()
    lan_port = next(iter(srv.lan_runner.sites))._server.sockets[0].getsockname()[1]
    srv.base = f"http://127.0.0.1:{port}"
    srv.lan_url = f"wss://127.0.0.1:{lan_port}/v1/ws"
    yield srv
    await srv.assistant.stop()
    await srv.hub.stop()
    await srv.lan_runner.cleanup()
    await runner.cleanup()


def H(srv):
    return {"Authorization": f"Bearer {srv.token}"}


async def test_local_api_requires_token_and_local_host(server):
    async with aiohttp.ClientSession() as s:
        async with s.get(server.base + "/api/health") as r:
            assert r.status == 200 and (await r.json())["protocol"] == 1
        async with s.get(server.base + "/api/status") as r:
            assert r.status == 401
        async with s.get(server.base + "/api/status", headers={"Authorization": "Bearer wrong"}) as r:
            assert r.status == 401
        async with s.get(server.base + "/api/status", headers={**H(server), "Host": "evil.example"}) as r:
            assert r.status == 403
        async with s.get(server.base + "/api/status", headers={**H(server), "Origin": "https://evil.example"}) as r:
            assert r.status == 403
        async with s.get(server.base + "/api/status", headers=H(server)) as r:
            body = await r.json()
            assert r.status == 200 and body["llm"]["available"] is False and body["lan"]["enabled"]


async def test_local_api_chat_tool_and_artifact_flow(server):
    async with aiohttp.ClientSession(headers=H(server)) as s:
        async with s.post(server.base + "/api/tools/creation3d.create", json={"args": {"prompt": "a bottle"}, "wait": True}) as r:
            task = (await r.json())["task"]
            assert task["status"] == "COMPLETED" and task["verified"]
        art_id = task["result"]["artifacts"][0]["id"]
        async with s.get(f"{server.base}/api/artifacts/{art_id}/file") as r:
            data = await r.read()
            assert r.status == 200 and data[:4] == b"glTF" and r.headers["Content-Type"] == "model/gltf-binary"
        async with s.post(server.base + "/api/tools/files.delete", json={"args": {"path": "../../etc/passwd"}}) as r:
            tid = (await r.json())["task"]["id"]
        await asyncio.sleep(0.05)
        async with s.get(server.base + "/api/approvals") as r:
            approvals = (await r.json())["approvals"]
        assert approvals and approvals[0]["taskId"] == tid
        async with s.post(f"{server.base}/api/approvals/{approvals[0]['approvalId']}", json={"approve": True}) as r:
            assert r.status == 200
        t = await server.s.tasks.wait(tid, 5)
        assert t.status.value == "FAILED" and "escapes" in t.error  # approval never bypasses confinement
        async with s.post(server.base + "/api/tools/shell.run", json={"args": {"command": "rm -rf /"}, "wait": True}) as r:
            t = (await r.json())["task"]
            assert t["status"] == "FAILED" and "blocked" in t["error"]
        async with s.post(server.base + "/api/memory", json={"content": "my api key is sk-aaaaaaaaaaaaaaaaaaaa"}) as r:
            assert r.status == 422
        async with s.post(server.base + "/api/estop", json={"engaged": True}) as r:
            assert (await r.json())["engaged"]
        async with s.post(server.base + "/api/tools/apps.open", json={"args": {"app": "Notes"}}) as r:
            assert r.status == 423
        async with s.post(server.base + "/api/estop", json={"engaged": False}) as r:
            assert not (await r.json())["engaged"]
        async with s.get(server.base + "/api/audit") as r:
            assert (await r.json())["chainValid"]


async def test_local_ws_requires_auth(server):
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(server.base.replace("http", "ws") + "/api/ws") as ws:
            await ws.send_json({"type": "auth", "token": "nope"})
            msg = await ws.receive()
            assert msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)
        async with s.ws_connect(server.base.replace("http", "ws") + "/api/ws") as ws:
            await ws.send_json({"type": "auth", "token": server.token})
            hello = await ws.receive_json()
            assert hello["type"] == "hello"
            await ws.send_json({"type": "mic.state", "state": "listening"})
            while True:
                ev = await asyncio.wait_for(ws.receive_json(), 5)
                if ev["type"] == "character.state":
                    assert ev["payload"]["state"] == "LISTENING"
                    break


# --------------------------------------------------------------- device hub
def env(type_, payload=None, v=1, mid="m1"):
    return json.dumps({"v": v, "type": type_, "id": mid, "ts": 1, "payload": payload or {}})


def pinned_ssl(server) -> ssl.SSLContext:
    """Like the iPhone: accept only the certificate whose SHA-256 fingerprint was in the QR code."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # verified manually below via fingerprint
    return ctx


async def recv_type(ws, type_, timeout=5):
    async def _r():
        while True:
            m = json.loads((await ws.receive()).data)
            if m["type"] == type_:
                return m
    return await asyncio.wait_for(_r(), timeout)


async def connect(server, s):
    ws = await s.ws_connect(server.lan_url, ssl=pinned_ssl(server))
    import hashlib

    der = ws._response.connection.transport.get_extra_info("ssl_object").getpeercert(binary_form=True)
    assert hashlib.sha256(der).hexdigest() == server.lan_fingerprint  # certificate pinning
    return ws


async def pair(server, s, device_id="iphone-0001"):
    code = server.s.devices.start_pairing().code
    ws = await connect(server, s)
    await ws.send_str(env("hello", {"protocol": 1, "minProtocol": 1, "client": "test"}))
    await recv_type(ws, "hello.ack")
    await ws.send_str(env("pair.request", {"code": code, "deviceId": device_id, "deviceName": "Test iPhone"}))
    ok = await recv_type(ws, "pair.ok")
    key = ok["payload"]["deviceKey"]
    ch = await recv_type(ws, "auth.challenge")
    await ws.send_str(env("auth.response", {"deviceId": device_id, "mac": compute_mac(key, ch["payload"]["nonce"], device_id)}))
    await recv_type(ws, "auth.ok")
    return ws, key


async def test_device_pairing_auth_commands_and_reconnect(server):
    async with aiohttp.ClientSession() as s:
        ws, key = await pair(server, s)
        state = await recv_type(ws, "character.state")
        assert state["payload"]["state"] == "IDLE"
        await ws.send_str(env("voice.state", {"state": "listening"}))
        st = await recv_type(ws, "character.state")
        assert st["payload"]["state"] == "LISTENING"
        await ws.send_str(env("voice.state", {"state": "idle"}))
        await ws.send_str(env("user.text", {"text": "open Safari", "speakReply": True}))
        res = await recv_type(ws, "task.result")
        assert res["payload"]["status"] == "COMPLETED" and res["payload"]["verified"]
        msg = await recv_type(ws, "assistant.message")
        assert msg["payload"]["speak"] is True  # the phone asked for a spoken reply
        await ws.send_str(env("ping", {"t": 7}))
        assert (await recv_type(ws, "pong"))["payload"]["t"] == 7
        await ws.close()
        await asyncio.sleep(0.05)
        assert server.hub.connected() == []
        # reconnect with the stored key: challenge-response only, no pairing needed
        ws2 = await connect(server, s)
        await ws2.send_str(env("hello", {"protocol": 1, "minProtocol": 1, "client": "test", "deviceId": "iphone-0001"}))
        ch = await recv_type(ws2, "auth.challenge")
        await ws2.send_str(env("auth.response", {"deviceId": "iphone-0001", "mac": compute_mac(key, ch["payload"]["nonce"], "iphone-0001")}))
        await recv_type(ws2, "auth.ok")
        assert len(server.hub.connected()) == 1
        # phone engages estop; cannot approve high-risk; estop state is pushed
        snap = await recv_type(ws2, "estop.state")  # part of the post-auth state snapshot
        assert snap["payload"]["engaged"] is False
        await ws2.send_str(env("estop.trigger", {"reason": "test"}))
        assert (await recv_type(ws2, "estop.state"))["payload"]["engaged"]
        assert server.s.estop.engaged
        await ws2.close()


async def test_unauthenticated_and_malformed_messages(server):
    async with aiohttp.ClientSession() as s:
        ws = await connect(server, s)
        await ws.send_str(env("user.text", {"text": "open Safari"}))
        err = await recv_type(ws, "error")
        assert err["payload"]["code"] == "unauthenticated"
        await ws.send_str("{garbage")
        assert (await recv_type(ws, "error"))["payload"]["code"] == "bad_json"
        await ws.send_str(env("shell.exec", {"cmd": "ls"}))
        assert (await recv_type(ws, "error"))["payload"]["code"] == "unknown_type"
        await ws.send_bytes(b"\x00\x01")
        assert (await recv_type(ws, "error"))["payload"]["code"] == "bad_json"
        assert not server.s.tasks.list()  # nothing executed
        await ws.close()


async def test_wrong_key_and_wrong_code_rejected(server):
    async with aiohttp.ClientSession() as s:
        ws, key = await pair(server, s, "iphone-0002")
        await ws.close()
        ws = await connect(server, s)
        await ws.send_str(env("hello", {"protocol": 1, "minProtocol": 1, "client": "t", "deviceId": "iphone-0002"}))
        for _ in range(3):
            ch = await recv_type(ws, "auth.challenge")
            await ws.send_str(env("auth.response", {"deviceId": "iphone-0002", "mac": compute_mac("bad" + key, ch["payload"]["nonce"], "iphone-0002")}))
            err = await recv_type(ws, "error")
            assert err["payload"]["code"] == "auth_failed"
        msg = await ws.receive()
        assert msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)
        # pairing with a wrong code
        server.s.devices.start_pairing()
        ws = await connect(server, s)
        await ws.send_str(env("hello", {"protocol": 1, "minProtocol": 1, "client": "t"}))
        await recv_type(ws, "hello.ack")
        await ws.send_str(env("pair.request", {"code": "000000" if server.s.devices._session.code != "000000" else "111111",
                                               "deviceId": "iphone-0003", "deviceName": "x"}))
        assert (await recv_type(ws, "error"))["payload"]["code"] == "pair_failed"
        await ws.close()


async def test_version_mismatch_closes(server):
    async with aiohttp.ClientSession() as s:
        ws = await connect(server, s)
        await ws.send_str(env("hello", {"protocol": 5, "minProtocol": 4, "client": "future"}))
        err = await recv_type(ws, "error")
        assert err["payload"]["code"] == "version_unsupported" and err["payload"]["fatal"]
        msg = await ws.receive()
        assert msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)


async def test_revoked_device_is_disconnected(server):
    async with aiohttp.ClientSession(headers=H(server)) as s:
        ws, key = await pair(server, s, "iphone-0009")
        async with s.delete(server.base + "/api/devices/iphone-0009") as r:
            assert (await r.json())["revoked"]
        msg = await ws.receive()
        while msg.type == aiohttp.WSMsgType.TEXT:
            msg = await ws.receive()
        assert msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)


async def test_cors_only_for_local_origins(server):
    async with aiohttp.ClientSession() as s:
        pre = {"Origin": "null", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
        async with s.options(server.base + "/api/chat", headers=pre) as r:
            assert r.status == 204 and r.headers["Access-Control-Allow-Origin"] == "null"
        async with s.options(server.base + "/api/chat", headers={**pre, "Origin": "https://evil.example"}) as r:
            assert r.status == 403 and "Access-Control-Allow-Origin" not in r.headers
        async with s.get(server.base + "/api/status", headers={**H(server), "Origin": "http://localhost:5173"}) as r:
            assert r.status == 200 and r.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
        async with s.get(server.base + "/api/tasks/nope", headers=H(server)) as r:
            assert r.status == 404
