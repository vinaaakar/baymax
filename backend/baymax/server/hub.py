"""Device hub: authenticated WebSocket sessions for paired phones (LAN, TLS).

Every inbound frame goes through :func:`protocol.parse` (size, JSON, envelope,
type whitelist, payload schema). Before authentication only ``hello``,
``pair.request``, ``auth.response`` and ``ping`` are accepted. Abuse limits:
handshake timeout, auth-failure limit, per-connection rate limiting and a
malformed-message strike counter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

from .. import __version__
from ..protocol import messages as proto
from ..security.pairing import PairingError

if TYPE_CHECKING:  # pragma: no cover
    from ..assistant import Assistant
    from ..services import Services

log = logging.getLogger(__name__)

PRE_AUTH = {"hello", "pair.request", "auth.response", "ping"}
HANDSHAKE_TIMEOUT_S = 15.0
MAX_AUTH_FAILURES = 3
MAX_STRIKES = 10
RATE = 20.0  # messages per second
BURST = 40.0


@dataclass(eq=False)  # identity semantics: sessions live in a set
class DeviceSession:
    ws: web.WebSocketResponse
    remote: str
    device_id: str | None = None
    authenticated: bool = False
    hello: bool = False
    protocol: int = 1  # negotiated in hello
    nonce: str | None = None
    auth_failures: int = 0
    strikes: int = 0
    tokens: float = BURST
    last: float = field(default_factory=time.monotonic)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(BURST, self.tokens + (now - self.last) * RATE)
        self.last = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class DeviceHub:
    def __init__(self, services: "Services", assistant: "Assistant") -> None:
        self.s = services
        self.assistant = assistant
        self.sessions: set[DeviceSession] = set()
        self._pump: asyncio.Task | None = None

    # ---------------------------------------------------------------- events
    def start(self) -> None:
        if self._pump is None:
            self._pump = asyncio.create_task(self._forward_events(), name="device-hub")

    async def stop(self) -> None:
        if self._pump:
            self._pump.cancel()
        for sess in list(self.sessions):
            await sess.ws.close(code=1001, message=b"server shutting down")

    @staticmethod
    def _llm_fields(st: Any) -> dict[str, Any]:
        """The Mac's local model status. Omitted when unknown, so a phone never shows a stale/false 'unavailable'."""
        if st is None:
            return {}
        return {"llm": "ready" if st.available else "unavailable", "model": st.model[:80]}

    def connected(self) -> list[dict[str, Any]]:
        return [{"deviceId": s.device_id, "remote": s.remote} for s in self.sessions if s.authenticated]

    async def _forward_events(self) -> None:
        sub = self.s.bus.subscribe(maxsize=512)
        try:
            while True:
                ev = await sub.get()
                for sess in [x for x in self.sessions if x.authenticated]:
                    msg = self._map(ev.type, ev.payload, sess)
                    if msg is not None:
                        await self._send(sess, msg)
        except asyncio.CancelledError:
            pass
        finally:
            sub.close()

    def _map(self, etype: str, p: dict[str, Any], sess: DeviceSession) -> proto.Message | None:
        try:
            if etype == "character.state":
                return proto.make("character.state", {"state": p["state"], "reason": p.get("reason", ""), **({"taskId": p["taskId"]} if p.get("taskId") else {})})
            if etype == "voice.state":
                return proto.make("voice.state", {"state": p["state"], "source": str(p.get("source", ""))})
            if etype == "assistant.message":
                speak = bool(p.get("speak")) and p.get("source") == f"device:{sess.device_id}"
                return proto.make("assistant.message", {"text": p["text"], "messageId": p["messageId"], "speak": speak,
                                                        "role": p.get("role", "assistant"), **({"taskId": p["taskId"]} if p.get("taskId") else {})})
            if etype == "task.update":
                body = {"taskId": p["taskId"], "status": p["status"], "title": p["title"]}
                if p.get("kind"):
                    body["kind"] = str(p["kind"])[:40]  # lets the phone show the researching expression
                if p.get("step"):
                    body["step"] = str(p["step"])
                if p.get("progress") is not None:
                    body["progress"] = float(p["progress"])
                if p.get("detail") or p.get("error"):
                    body["detail"] = str(p.get("detail") or p.get("error"))[:300]
                return proto.make("task.update", body)
            if etype == "task.result":
                return proto.make("task.result", {"taskId": p["taskId"], "status": p["status"], "summary": p["summary"][:2000],
                                                  "verified": bool(p.get("verified")),
                                                  "artifacts": [{"id": a.get("id"), "kind": a.get("kind"), "title": a.get("title")} for a in p.get("artifacts", [])]})
            if etype == "approval.request":
                return proto.make("approval.request", {"approvalId": p["approvalId"], "taskId": p["taskId"], "summary": p["summary"],
                                                       "risk": p["risk"], "remoteAllowed": bool(p.get("remoteAllowed")), "expiresAt": int(p["expiresAt"])})
            if etype == "estop.state":
                return proto.make("estop.state", {"engaged": p["engaged"], "reason": p.get("reason", "")})
            if etype == "sleep.state":
                return proto.make("sleep.state", {"sleeping": p["sleeping"]})
            if etype == "devices.changed":
                return proto.make("connection.status", {"status": "connected", "devices": int(p.get("count", 0)),
                                                        **self._llm_fields(self.s.llm.last_status)})
            if etype == "llm.status":  # Ollama came up or went away on the Mac
                return proto.make("connection.status", {"status": "connected", "devices": len(self.connected()),
                                                        "llm": "ready" if p.get("available") else "unavailable",
                                                        "model": str(p.get("model", ""))[:80]})
            if sess.protocol < 2:
                return None  # v2-only events below are never sent to v1 clients
            if etype == "cursor":
                return proto.make("cursor.update", p)
            if etype == "gaze.target":
                return proto.make("gaze.target", p)
            if etype == "research.result":
                return proto.make("research.result", p)
        except (KeyError, ValueError, TypeError) as exc:
            log.debug("could not map event %s: %s", etype, exc)
        return None

    async def _send(self, sess: DeviceSession, msg: proto.Message) -> None:
        if sess.ws.closed:
            return
        async with sess.send_lock:
            try:
                await asyncio.wait_for(sess.ws.send_str(msg.to_json(v=sess.protocol)), timeout=5)
            except (ConnectionResetError, asyncio.TimeoutError, RuntimeError):
                await sess.ws.close()

    # ------------------------------------------------------------- handshake
    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=proto.MAX_MESSAGE_BYTES)
        await ws.prepare(request)
        sess = DeviceSession(ws=ws, remote=request.remote or "?")
        self.sessions.add(sess)
        deadline = asyncio.get_running_loop().call_later(HANDSHAKE_TIMEOUT_S, lambda: (not sess.authenticated) and asyncio.ensure_future(
            self._close(sess, "unauthenticated", "handshake timeout")))
        try:
            async for raw in ws:
                if raw.type == WSMsgType.TEXT:
                    await self._on_frame(sess, raw.data)
                elif raw.type == WSMsgType.BINARY:
                    await self._error(sess, "bad_json", "binary frames are not supported")
                elif raw.type == WSMsgType.ERROR:
                    break
                if ws.closed:
                    break
        finally:
            deadline.cancel()
            was_auth = sess.authenticated
            self.sessions.discard(sess)
            if was_auth:
                self.s.audit.record(f"device:{sess.device_id}", "device.disconnect", "low", "ok", {"remote": sess.remote})
                self.s.bus.publish("devices.changed", {"count": len(self.connected()), "devices": self.connected()})
        return ws

    async def _close(self, sess: DeviceSession, code: str, message: str) -> None:
        await self._send(sess, proto.error(code, message, fatal=True))
        await sess.ws.close(code=4001, message=message.encode()[:120])

    async def _error(self, sess: DeviceSession, code: str, message: str, reply_to: str | None = None) -> None:
        sess.strikes += 1
        await self._send(sess, proto.error(code, message, reply_to))
        if sess.strikes > MAX_STRIKES:
            await self._close(sess, "rate_limited", "too many invalid messages")

    async def _on_frame(self, sess: DeviceSession, data: str) -> None:
        if not sess.allow():
            await self._error(sess, "rate_limited", "slow down")
            return
        try:
            msg = proto.parse(data, direction="c2s")
        except proto.ProtocolError as exc:
            if exc.fatal:
                await self._close(sess, exc.code, exc.message)
            else:
                await self._error(sess, exc.code, exc.message, exc.reply_to)
            return
        if not sess.authenticated and msg.type not in PRE_AUTH:
            await self._error(sess, "unauthenticated", "authenticate first", msg.id)
            return
        handler = getattr(self, "_m_" + msg.type.replace(".", "_"))
        await handler(sess, msg)

    async def _m_hello(self, sess: DeviceSession, msg: proto.Message) -> None:
        p = msg.payload
        if p["protocol"] < proto.MIN_PROTOCOL_VERSION or p["minProtocol"] > proto.PROTOCOL_VERSION:
            await self._close(sess, "version_unsupported",
                              f"client speaks v{p['minProtocol']}-{p['protocol']}, server v{proto.MIN_PROTOCOL_VERSION}-{proto.PROTOCOL_VERSION}")
            return
        sess.hello = True
        sess.protocol = min(p["protocol"], proto.PROTOCOL_VERSION)
        await self._send(sess, proto.make("hello.ack", {"protocol": sess.protocol, "server": "baymax-mac",
                                                        "serverVersion": __version__}))
        if p.get("deviceId"):
            await self._challenge(sess)

    async def _challenge(self, sess: DeviceSession) -> None:
        sess.nonce = self.s.devices.new_nonce()
        await self._send(sess, proto.make("auth.challenge", {"nonce": sess.nonce}))

    async def _m_pair_request(self, sess: DeviceSession, msg: proto.Message) -> None:
        if not sess.hello:
            await self._error(sess, "bad_envelope", "send hello first", msg.id)
            return
        p = msg.payload
        try:
            key = self.s.devices.complete_pairing(p["code"], p["deviceId"], p["deviceName"])
        except PairingError as exc:
            self.s.audit.record(f"remote:{sess.remote}", "device.pair", "high", "failed", {"reason": str(exc)})
            await self._send(sess, proto.error(exc.code, str(exc), msg.id, fatal=exc.code == "pair_locked"))
            if exc.code == "pair_locked":
                await sess.ws.close()
            return
        self.s.audit.record(f"device:{p['deviceId']}", "device.pair", "high", "paired", {"name": p["deviceName"], "remote": sess.remote})
        self.s.bus.publish("pairing.completed", {"deviceId": p["deviceId"], "deviceName": p["deviceName"]})
        await self._send(sess, proto.make("pair.ok", {"deviceId": p["deviceId"], "deviceKey": key}))
        await self._challenge(sess)

    async def _m_auth_response(self, sess: DeviceSession, msg: proto.Message) -> None:
        p = msg.payload
        nonce, sess.nonce = sess.nonce, None
        if nonce is None:
            await self._error(sess, "auth_failed", "no challenge outstanding", msg.id)
            return
        if self.s.devices.verify(p["deviceId"], nonce, p["mac"]):
            sess.authenticated = True
            sess.device_id = p["deviceId"]
            self.s.audit.record(f"device:{sess.device_id}", "device.connect", "low", "ok", {"remote": sess.remote})
            await self._send(sess, proto.make("auth.ok", {"deviceId": sess.device_id}))
            await self._snapshot(sess)
            self.s.bus.publish("devices.changed", {"count": len(self.connected()), "devices": self.connected()})
            return
        sess.auth_failures += 1
        self.s.audit.record(f"remote:{sess.remote}", "device.auth", "high", "failed", {"deviceId": p["deviceId"]})
        if sess.auth_failures >= MAX_AUTH_FAILURES:
            await self._close(sess, "auth_failed", "authentication failed")
            return
        await self._send(sess, proto.error("auth_failed", "authentication failed", msg.id))
        await self._challenge(sess)

    async def _snapshot(self, sess: DeviceSession) -> None:
        s = self.s
        await self._send(sess, proto.make("character.state", {"state": s.character.state.value, "reason": "sync"}))
        await self._send(sess, proto.make("sleep.state", {"sleeping": self.assistant.sleeping}))
        await self._send(sess, proto.make("estop.state", {"engaged": s.estop.engaged, "reason": s.estop.reason}))
        st = await s.llm.status()  # fresh check: the cached value may not exist yet right after start-up
        await self._send(sess, proto.make("connection.status", {"status": "connected", "devices": len(self.connected()) + 1,
                                                                **self._llm_fields(st)}))
        for t in s.tasks.active():
            await self._send(sess, proto.make("task.update", {"taskId": t.id, "status": t.status.value, "title": t.title}))
        for a in s.tasks.pending_approvals():
            from ..security.policy import Policy

            await self._send(sess, proto.make("approval.request", {**a.to_dict(), "remoteAllowed": Policy.may_approve(
                a.risk, f"device:{sess.device_id}", s.settings.security.remote_high_risk_approval)}))

    # ------------------------------------------------------- authenticated
    async def _m_ping(self, sess: DeviceSession, msg: proto.Message) -> None:
        await self._send(sess, proto.make("pong", {"t": msg.payload.get("t", 0)}))

    async def _m_user_text(self, sess: DeviceSession, msg: proto.Message) -> None:
        source = f"device:{sess.device_id}"
        speak = bool(msg.payload.get("speakReply", msg.payload.get("source") == "voice"))
        voice = msg.payload.get("source") == "voice"  # recognised on the iPhone: still validated as speech on the Mac
        asyncio.create_task(self.assistant.handle_text(msg.payload["text"], source=source, speak=speak, voice=voice))

    async def _m_voice_state(self, sess: DeviceSession, msg: proto.Message) -> None:
        self.assistant.mic_state(msg.payload["state"], f"device:{sess.device_id}")

    async def _m_approval_respond(self, sess: DeviceSession, msg: proto.Message) -> None:
        ok, text = self.s.tasks.respond_approval(msg.payload["approvalId"], msg.payload["approve"], f"device:{sess.device_id}")
        if not ok:
            await self._send(sess, proto.error("forbidden", text, msg.id))

    async def _m_task_cancel(self, sess: DeviceSession, msg: proto.Message) -> None:
        if not self.s.tasks.cancel(msg.payload["taskId"], f"cancelled from device {sess.device_id}"):
            await self._send(sess, proto.error("not_found", "no such active task", msg.id))
        else:
            self.s.audit.record(f"device:{sess.device_id}", "task.cancel", "low", "ok", {"task": msg.payload["taskId"]})

    async def _m_estop_trigger(self, sess: DeviceSession, msg: proto.Message) -> None:
        self.s.estop.engage(f"triggered from device {sess.device_id}: {msg.payload.get('reason', '')}".strip(": "))

    async def _m_sleep_set(self, sess: DeviceSession, msg: proto.Message) -> None:
        self.assistant.set_sleep(msg.payload["sleeping"], f"device:{sess.device_id}")

    async def _m_history_request(self, sess: DeviceSession, msg: proto.Message) -> None:
        limit = max(1, min(int(msg.payload.get("limit", 50)), 200))
        items = [{"id": m["id"], "role": m["role"], "text": m["text"][:4000], "ts": m["ts"]} for m in self.s.conversation.recent(limit)]
        out = proto.make("history", {"messages": items})
        if len(out.to_json().encode()) > proto.MAX_MESSAGE_BYTES:
            out = proto.make("history", {"messages": items[-20:]})
        await self._send(sess, out)
