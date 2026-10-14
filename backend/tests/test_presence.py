"""Cursor presence: normalisation, relay policy, protocol negotiation and desktop -> phone end-to-end."""

import asyncio
import json

import aiohttp
import pytest

from baymax.presence.cursor import CursorError, CursorRelay, normalize
from baymax.security.pairing import compute_mac
from test_server import H, connect, env, pair, recv_type, server  # noqa: F401  (fixture + helpers)

PRIMARY = {"id": 1, "x": 0, "y": 0, "width": 1512, "height": 982, "scaleFactor": 2}
RIGHT = {"id": 2, "x": 1512, "y": -200, "width": 2560, "height": 1440, "scaleFactor": 1}


def cursor(x, y, display=PRIMARY, primary=PRIMARY):
    return {"type": "cursor", "x": x, "y": y, "display": display, "primary": primary}


def test_normalize_primary_and_secondary_display():
    s = normalize(cursor(756, 491), 1)
    assert (s.x, s.y, s.vx, s.vy) == (0.5, 0.5, 0.5, 0.5)
    assert s.payload()["displayWidth"] == 1512 and s.payload()["scale"] == 2
    # cursor on a monitor to the right: local coords within that display, vx beyond the primary's right edge
    s = normalize(cursor(1512 + 1280, 520, RIGHT), 2)
    assert s.x == 0.5 and s.y == 0.5 and s.vx == pytest.approx(1 + 1280 / 1512) and s.payload()["displayId"] == 2
    # clamped to the display that contains it
    assert normalize(cursor(-50, 2000), 3).x == 0.0 and normalize(cursor(-50, 2000), 3).y == 1.0


@pytest.mark.parametrize("bad", [
    {"x": "a", "y": 1, "display": PRIMARY}, {"x": 1, "y": 1}, {"x": float("nan"), "y": 1, "display": PRIMARY},
    {"x": 1, "y": 1, "display": {**PRIMARY, "width": 0}}, {"x": 1e9, "y": 1, "display": PRIMARY},
    {"x": 1, "y": 1, "display": {**PRIMARY, "scaleFactor": 100}},
])
def test_normalize_rejects_bad_samples(bad):
    with pytest.raises(CursorError):
        normalize(bad, 1)


def test_relay_rate_limit_dedupe_and_sleep():
    out = []
    r = CursorRelay(out.append, max_hz=30)
    assert r.submit(cursor(100, 100), now=0.0)
    assert not r.submit(cursor(400, 100), now=0.01)  # faster than 30 Hz
    assert r.submit(cursor(400, 100), now=0.05)
    assert not r.submit(cursor(400.5, 100), now=0.2)  # sub-pixel jitter
    r.sleeping = True
    assert not r.submit(cursor(900, 900), now=1.0)  # asleep: nothing relayed
    r.sleeping = False
    assert r.submit(cursor(900, 900), now=1.1)
    assert not r.submit({"x": "bad"}, now=2.0)
    assert [o["seq"] for o in out] == [1, 2, 3] and r.rejected == 1


async def test_cursor_reaches_v2_phone_but_not_v1(server):  # noqa: F811
    async with aiohttp.ClientSession() as s:
        phone_v2, key2 = await pair(server, s, "iphone-v2-0001")  # helper negotiates protocol 1 by default
        await phone_v2.close()
        # reconnect as a v2 client
        v2 = await connect(server, s)
        await v2.send_str(env("hello", {"protocol": 2, "minProtocol": 1, "client": "ios", "deviceId": "iphone-v2-0001"}, v=2))
        ack = await recv_type(v2, "hello.ack")
        assert ack["payload"]["protocol"] == 2 and ack["v"] == 2
        ch = await recv_type(v2, "auth.challenge")
        await v2.send_str(env("auth.response", {"deviceId": "iphone-v2-0001", "mac": compute_mac(key2, ch["payload"]["nonce"], "iphone-v2-0001")}, v=2))
        await recv_type(v2, "auth.ok")
        v1, _ = await pair(server, s, "iphone-v1-0001")  # a v1 client stays connected alongside
        await asyncio.sleep(0.05)
        server.s.cursor.submit(cursor(378, 245.5))
        msg = await recv_type(v2, "cursor.update")
        assert msg["v"] == 2 and msg["payload"]["vx"] == 0.25 and msg["payload"]["vy"] == 0.25
        # the v1 client must never receive v2-only messages
        with pytest.raises(asyncio.TimeoutError):
            await recv_type(v1, "cursor.update", timeout=0.4)
        await v1.close()
        await v2.close()


async def test_desktop_cursor_stream_end_to_end_with_sleep(server):  # noqa: F811
    """Desktop app (local WS, token auth) streams real-shaped samples -> backend -> paired phone (TLS + HMAC)."""
    async with aiohttp.ClientSession() as s:
        phone = await connect(server, s)
        code = server.s.devices.start_pairing().code
        await phone.send_str(env("hello", {"protocol": 2, "minProtocol": 1, "client": "ios"}, v=2))
        await recv_type(phone, "hello.ack")
        await phone.send_str(env("pair.request", {"code": code, "deviceId": "iphone-e2e-0001", "deviceName": "e2e"}, v=2))
        key = (await recv_type(phone, "pair.ok"))["payload"]["deviceKey"]
        ch = await recv_type(phone, "auth.challenge")
        await phone.send_str(env("auth.response", {"deviceId": "iphone-e2e-0001", "mac": compute_mac(key, ch["payload"]["nonce"], "iphone-e2e-0001")}, v=2))
        await recv_type(phone, "auth.ok")

        desktop = await s.ws_connect(server.base.replace("http", "ws") + "/api/ws")
        await desktop.send_json({"type": "auth", "token": server.token})
        hello = await desktop.receive_json()
        assert hello["payload"]["devices"] == 1  # the desktop learns a phone is listening

        path = [(0, 0), (1512, 0), (1512, 982), (0, 982), (756, 491)]
        for x, y in path:
            await desktop.send_json(cursor(x, y))
            m = await recv_type(phone, "cursor.update")
            assert (m["payload"]["vx"], m["payload"]["vy"]) == (x / 1512, y / 982)
            await asyncio.sleep(0.04)  # respect the 30 Hz relay

        # sleep suspends relaying; wake restores it
        async with s.post(server.base + "/api/sleep", json={"sleeping": True}, headers=H(server)):
            pass
        await recv_type(phone, "sleep.state")
        await desktop.send_json(cursor(100, 100))
        with pytest.raises(asyncio.TimeoutError):
            await recv_type(phone, "cursor.update", timeout=0.4)
        async with s.post(server.base + "/api/sleep", json={"sleeping": False}, headers=H(server)):
            pass
        await asyncio.sleep(0.05)
        await desktop.send_json(cursor(200, 300))
        m = await recv_type(phone, "cursor.update")
        assert m["payload"]["vx"] == pytest.approx(200 / 1512, abs=1e-4)
        # the desktop's own windows do not receive the high-rate cursor stream
        got_cursor = False
        try:
            while True:
                ev = json.loads((await desktop.receive(timeout=0.2)).data)
                got_cursor |= ev.get("type") == "cursor"
        except asyncio.TimeoutError:
            pass
        assert not got_cursor
        await desktop.close()
        await phone.close()
