"""HTTP/WebSocket servers.

* Local API (127.0.0.1 only): used by the Mac desktop app. Every request needs
  the local bearer token stored in ``<data_dir>/local_token`` (0600). Host and
  Origin headers are checked to block DNS-rebinding / cross-site requests.
* LAN listener (optional, TLS): only the device protocol at ``/v1/ws``. It
  exposes no HTTP API and no command execution.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import socket
import ssl
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from .. import PROTOCOL_VERSION, __version__
from ..assistant import Assistant
from ..character.state_machine import CharacterEvent
from ..config import MUTABLE_FIELDS, save_settings, update_setting
from ..creation3d.validate import validate_glb
from ..llm.models import RECOMMENDED_MODELS
from ..memory.store import MemoryRejected
from ..security import tls
from ..services import Services
from ..tasks.manager import TaskRejected
from ..tasks.models import PlanStep
from ..tools.base import ArgumentError
from ..voice.audio import AudioError
from ..voice.stt import STTUnavailable
from .hub import DeviceHub

log = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 8 * 1024 * 1024
ALLOWED_ORIGINS = ("null", "file://", "app://", "http://localhost:", "http://127.0.0.1:")


def load_or_create_local_token(data_dir: Path) -> str:
    path = data_dir / "local_token"
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    return token


def lan_addresses() -> list[str]:
    addrs: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # TEST-NET address; no packets are sent for UDP connect
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addrs.add(ip)
    except OSError:
        pass
    return sorted(addrs)


class BaymaxServer:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.assistant = Assistant(services)
        self.hub = DeviceHub(services, self.assistant)
        self.token = load_or_create_local_token(services.settings.data_dir)
        self.local_runner: web.AppRunner | None = None
        self.lan_runner: web.AppRunner | None = None
        self.lan_fingerprint: str | None = None
        self._status_task: asyncio.Task | None = None

    # ------------------------------------------------------------- app setup
    def local_app(self) -> web.Application:
        app = web.Application(middlewares=[self._guard], client_max_size=MAX_AUDIO_BYTES)
        r = app.router
        r.add_get("/api/health", self.health)
        r.add_get("/api/status", self.status)
        r.add_get("/api/ws", self.local_ws)
        r.add_post("/api/chat", self.chat)
        r.add_post("/api/voice/transcribe", self.transcribe)
        r.add_post("/api/voice/state", self.voice_state)
        r.add_post("/api/tts/stop", self.tts_stop)
        r.add_get("/api/history", self.history)
        r.add_get("/api/tasks", self.tasks)
        r.add_get("/api/tasks/{id}", self.task)
        r.add_post("/api/tasks/{id}/cancel", self.task_cancel)
        r.add_get("/api/approvals", self.approvals)
        r.add_post("/api/approvals/{id}", self.approval)
        r.add_post("/api/estop", self.estop)
        r.add_post("/api/sleep", self.sleep)
        r.add_get("/api/memory", self.memory_list)
        r.add_post("/api/memory", self.memory_add)
        r.add_patch("/api/memory/{id}", self.memory_update)
        r.add_delete("/api/memory/{id}", self.memory_delete)
        r.add_delete("/api/memory", self.memory_clear)
        r.add_get("/api/artifacts", self.artifacts)
        r.add_get("/api/artifacts/{id}", self.artifact)
        r.add_get("/api/artifacts/{id}/file", self.artifact_file)
        r.add_get("/api/models3d/catalog", self.model_catalog)
        r.add_post("/api/models3d/{id}/undo", self.model_undo)
        r.add_get("/api/tools", self.tool_list)
        r.add_post("/api/tools/{name}", self.tool_run)
        r.add_get("/api/audit", self.audit)
        r.add_get("/api/devices", self.devices)
        r.add_delete("/api/devices/{id}", self.device_revoke)
        r.add_post("/api/pairing/start", self.pairing_start)
        r.add_post("/api/pairing/cancel", self.pairing_cancel)
        r.add_get("/api/config", self.config_get)
        r.add_patch("/api/config", self.config_patch)
        r.add_get("/api/models", self.llm_models)
        return app

    def lan_app(self) -> web.Application:
        app = web.Application(client_max_size=70_000)
        app.router.add_get("/v1/ws", self.hub.handle)
        return app

    # -------------------------------------------------------------- security
    @web.middleware
    async def _guard(self, request: web.Request, handler):
        host = (request.host or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return web.json_response({"error": "forbidden host"}, status=403)
        origin = request.headers.get("Origin")
        if origin and not origin.startswith(ALLOWED_ORIGINS):
            return web.json_response({"error": "forbidden origin"}, status=403)
        cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin else {}
        if request.method == "OPTIONS":  # CORS preflight from the desktop renderer (file:// or localhost dev server)
            return web.Response(status=204, headers={**cors, "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE",
                                                     "Access-Control-Allow-Headers": "Authorization, Content-Type",
                                                     "Access-Control-Max-Age": "600"})
        resp = await self._authorized(request, handler)
        if cors and not resp.prepared:
            resp.headers.update(cors)
        return resp

    async def _authorized(self, request: web.Request, handler):
        if request.path in ("/api/health", "/api/ws"):
            return await handler(request)
        auth = request.headers.get("Authorization", "")
        supplied = auth[7:] if auth.startswith("Bearer ") else request.query.get("token", "") if request.path.endswith("/file") else ""
        if not supplied or not hmac.compare_digest(supplied, self.token):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            return await handler(request)
        except web.HTTPException as exc:
            return web.json_response({"error": exc.reason}, status=exc.status)
        except (ArgumentError, ValueError, KeyError, json.JSONDecodeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

    # ------------------------------------------------------------- lifecycle
    async def start(self, *, lan: bool | None = None) -> None:
        net = self.s.settings.network
        self.assistant.start()
        self.hub.start()
        self.local_runner = web.AppRunner(self.local_app(), access_log=None)
        await self.local_runner.setup()
        await web.TCPSite(self.local_runner, net.local_host, net.local_port).start()
        log.info("local API listening on http://%s:%d", net.local_host, net.local_port)
        if lan if lan is not None else net.lan_enabled:
            await self.start_lan()
        self._status_task = asyncio.create_task(self._poll_status(), name="status-poll")

    async def start_lan(self) -> None:
        if self.lan_runner is not None:
            return
        net = self.s.settings.network
        cert, key = tls.ensure_certificate(self.s.settings.data_dir / "tls")
        self.lan_fingerprint = tls.fingerprint(cert)
        ctx: ssl.SSLContext = tls.server_context(cert, key)
        self.lan_runner = web.AppRunner(self.lan_app(), access_log=None)
        await self.lan_runner.setup()
        await web.TCPSite(self.lan_runner, net.lan_host, net.lan_port, ssl_context=ctx).start()
        log.info("LAN device listener on wss://%s:%d/v1/ws (TLS, fingerprint %s…)", net.lan_host, net.lan_port, self.lan_fingerprint[:16])
        self.s.bus.publish("lan.state", {"enabled": True, "port": net.lan_port})

    async def stop(self) -> None:
        if self._status_task:
            self._status_task.cancel()
        await self.assistant.stop()
        await self.hub.stop()
        for runner in (self.lan_runner, self.local_runner):
            if runner:
                await runner.cleanup()

    async def _poll_status(self) -> None:
        """Periodic, cheap health check of the local model (connection reused)."""
        last = None
        while True:
            st = await self.s.llm.status()
            if st.available != last:
                self.s.bus.publish("llm.status", st.to_dict())
                last = st.available
            await asyncio.sleep(30 if st.available else 10)

    # ------------------------------------------------------------- handlers
    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "name": "baymax", "version": __version__, "protocol": PROTOCOL_VERSION})

    async def status(self, request: web.Request) -> web.Response:
        s = self.s
        llm = await s.llm.status()
        return web.json_response({
            "version": __version__, "character": s.character.snapshot(), "sleeping": self.assistant.sleeping,
            "estop": {"engaged": s.estop.engaged, "reason": s.estop.reason}, "llm": llm.to_dict(),
            "stt": s.stt.status(), "tts": s.tts.status(), "images": await s.images.status(),
            "desktop": s.desktop.capabilities(), "research": {"localOnly": s.settings.research.local_only, "provider": s.settings.research.search_provider},
            "lan": {"enabled": self.lan_runner is not None, "port": s.settings.network.lan_port, "fingerprint": self.lan_fingerprint,
                    "addresses": lan_addresses()},
            "devices": {"connected": self.hub.connected(), "paired": [d.to_dict() for d in s.devices.list() if not d.revoked]},
            "activeTasks": [t.to_dict() for t in s.tasks.active()], "activeModel": self.assistant.active_model,
            "workspace": str(s.workspace.root), "hardware": {"enabled": s.settings.security.hardware_control_enabled},
            "cursor": s.cursor.stats(),
        })

    async def local_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=1_000_000)
        await ws.prepare(request)
        try:
            first = await ws.receive(timeout=10)
        except asyncio.TimeoutError:
            await ws.close(code=4001, message=b"auth timeout")
            return ws
        try:
            data = json.loads(first.data) if first.type == WSMsgType.TEXT else {}
        except ValueError:
            data = {}
        if data.get("type") != "auth" or not hmac.compare_digest(str(data.get("token", "")), self.token):
            await ws.close(code=4003, message=b"unauthorized")
            return ws
        sub = self.s.bus.subscribe(maxsize=1024)
        await ws.send_json({"type": "hello", "payload": {"version": __version__, "character": self.s.character.snapshot(),
                                                         "sleeping": self.assistant.sleeping, "estop": self.s.estop.engaged,
                                                         "devices": len(self.hub.connected()),
                                                         "recent": [e.to_dict() for e in self.s.bus.recent(30)]}})

        async def pump() -> None:
            while True:
                ev = await sub.get()
                if ev.type == "cursor":
                    continue  # desktop windows track the cursor locally; samples are relayed to phones only
                await ws.send_str(json.dumps(ev.to_dict(), default=str))

        sender = asyncio.create_task(pump())
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        cmd = json.loads(msg.data)
                    except ValueError:
                        continue
                    if cmd.get("type") == "mic.state" and cmd.get("state") in ("idle", "listening", "transcribing", "unavailable"):
                        self.assistant.mic_state(cmd["state"], "mac")
                    elif cmd.get("type") == "cursor":
                        self.s.cursor.submit(cmd)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            sender.cancel()
            sub.close()
        return ws

    async def chat(self, request: web.Request) -> web.Response:
        body = await request.json()
        text = str(body.get("text", ""))
        if not text.strip():
            raise ValueError("text is required")
        result = await self.assistant.handle_text(text, source="local", speak=bool(body.get("speak", False)))
        return web.json_response(result, dumps=lambda o: json.dumps(o, default=str))

    async def transcribe(self, request: web.Request) -> web.Response:
        data = await request.read()
        if len(data) > MAX_AUDIO_BYTES:
            raise ValueError("audio too large")
        self.assistant.mic_state("transcribing", "mac")
        try:
            tr = await self.s.stt.transcribe(data)
        except AudioError as exc:
            self.s.character.dispatch(CharacterEvent.RESET)
            self.assistant.mic_state("idle", "mac")
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        except STTUnavailable as exc:
            self.s.character.dispatch(CharacterEvent.WARNING_RAISED, reason="speech recognition unavailable")
            self.s.bus.publish("voice.state", {"state": "unavailable", "source": "mac"})
            return web.json_response({"ok": False, "error": str(exc), "unavailable": True}, status=503)
        self.s.bus.publish("voice.state", {"state": "idle", "source": "mac"})
        if not tr.speech_detected:
            self.s.character.dispatch(CharacterEvent.RESET, reason="no speech detected")
            return web.json_response({"ok": True, "text": "", "speech": False})
        submit = request.query.get("submit", "1") != "0"
        result = await self.assistant.handle_text(tr.text, source="voice", speak=True) if submit else None
        return web.json_response({"ok": True, "text": tr.text, "speech": True, "engine": tr.engine, "result": result},
                                 dumps=lambda o: json.dumps(o, default=str))

    async def voice_state(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.assistant.mic_state(str(body["state"]), "mac")
        return web.json_response({"ok": True})

    async def tts_stop(self, request: web.Request) -> web.Response:
        return web.json_response({"stopped": self.assistant.stop_speaking()})

    async def history(self, request: web.Request) -> web.Response:
        return web.json_response({"messages": self.s.conversation.recent(int(request.query.get("limit", 100)))})

    async def tasks(self, request: web.Request) -> web.Response:
        return web.json_response({"tasks": [t.to_dict() for t in self.s.tasks.list(int(request.query.get("limit", 50)))]})

    async def task(self, request: web.Request) -> web.Response:
        t = self.s.tasks.get(request.match_info["id"])
        if t is None:
            raise web.HTTPNotFound()
        return web.json_response({"task": t.to_dict(), "events": self.s.tasks.events(t.id)})

    async def task_cancel(self, request: web.Request) -> web.Response:
        ok = self.s.tasks.cancel(request.match_info["id"])
        return web.json_response({"cancelled": ok}, status=200 if ok else 404)

    async def approvals(self, request: web.Request) -> web.Response:
        return web.json_response({"approvals": [a.to_dict() for a in self.s.tasks.pending_approvals()]})

    async def approval(self, request: web.Request) -> web.Response:
        body = await request.json()
        ok, msg = self.s.tasks.respond_approval(request.match_info["id"], bool(body.get("approve")), "local")
        return web.json_response({"ok": ok, "message": msg}, status=200 if ok else 409)

    async def estop(self, request: web.Request) -> web.Response:
        body = await request.json()
        if body.get("engaged", True):
            self.s.estop.engage(str(body.get("reason", "Mac UI")))
        else:
            self.s.estop.reset()
        return web.json_response({"engaged": self.s.estop.engaged})

    async def sleep(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.assistant.set_sleep(bool(body.get("sleeping")), "local")
        return web.json_response({"sleeping": self.assistant.sleeping})

    async def memory_list(self, request: web.Request) -> web.Response:
        return web.json_response({"memories": [m.to_dict() for m in self.s.memory.list()]})

    async def memory_add(self, request: web.Request) -> web.Response:
        body = await request.json()
        try:
            m = self.s.memory.add(str(body.get("content", "")), str(body.get("category", "preference")))
        except MemoryRejected as exc:
            return web.json_response({"error": str(exc)}, status=422)
        self.s.audit.record("local", "memory.add", "low", "ok", {"id": m.id})
        self.s.bus.publish("memory.changed", {})
        return web.json_response({"memory": m.to_dict()})

    async def memory_update(self, request: web.Request) -> web.Response:
        body = await request.json()
        try:
            m = self.s.memory.update(request.match_info["id"], str(body.get("content", "")))
        except MemoryRejected as exc:
            return web.json_response({"error": str(exc)}, status=422)
        if m is None:
            raise web.HTTPNotFound()
        self.s.audit.record("local", "memory.update", "low", "ok", {"id": m.id})
        self.s.bus.publish("memory.changed", {})
        return web.json_response({"memory": m.to_dict()})

    async def memory_delete(self, request: web.Request) -> web.Response:
        ok = self.s.memory.delete(request.match_info["id"])
        self.s.audit.record("local", "memory.delete", "low", "ok" if ok else "missing", {"id": request.match_info["id"]})
        self.s.bus.publish("memory.changed", {})
        return web.json_response({"deleted": ok}, status=200 if ok else 404)

    async def memory_clear(self, request: web.Request) -> web.Response:
        n = self.s.memory.clear()
        self.s.audit.record("local", "memory.clear", "low", "ok", {"deleted": n})
        self.s.bus.publish("memory.changed", {})
        return web.json_response({"deleted": n})

    async def artifacts(self, request: web.Request) -> web.Response:
        kind = request.query.get("kind")
        return web.json_response({"artifacts": [a.to_dict() for a in self.s.artifacts.list(kind)]})

    async def artifact(self, request: web.Request) -> web.Response:
        art = self.s.artifacts.get(request.match_info["id"])
        if art is None:
            raise web.HTTPNotFound()
        body: dict[str, Any] = {"artifact": art.to_dict()}
        if art.kind == "model3d":
            saved = self.s.models3d.load(art.id)
            body["model"] = saved.to_dict()
            body["versions"] = self.s.models3d.versions(art.id)
        return web.json_response(body)

    async def artifact_file(self, request: web.Request) -> web.StreamResponse:
        art = self.s.artifacts.get(request.match_info["id"])
        if art is None:
            raise web.HTTPNotFound()
        path = Path(art.path)
        root = self.s.settings.artifacts_dir.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError:
            raise web.HTTPForbidden() from None
        if not path.is_file():
            raise web.HTTPNotFound()
        ctype = {".glb": "model/gltf-binary", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".md": "text/markdown",
                 ".json": "application/json", ".csv": "text/csv"}.get(path.suffix.lower(), "application/octet-stream")
        return web.FileResponse(path, headers={"Content-Type": ctype, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    async def model_catalog(self, request: web.Request) -> web.Response:
        return web.json_response({"generators": self.s.generators.catalog(), "types": self.s.generators.supported_types()})

    async def model_undo(self, request: web.Request) -> web.Response:
        try:
            saved = self.s.models3d.undo(request.match_info["id"])
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        self.s.bus.publish("model3d.updated", {"artifactId": saved.artifact.id, "version": saved.spec.version})
        return web.json_response({"model": saved.to_dict()})

    async def tool_list(self, request: web.Request) -> web.Response:
        return web.json_response({"tools": [t.schema() for t in self.s.tools.all()]})

    async def tool_run(self, request: web.Request) -> web.Response:
        """Direct tool invocation from the workspace UI — same task lifecycle, policy and approvals as chat."""
        name = request.match_info["name"]
        tool = self.s.tools.get(name)
        if tool is None:
            raise web.HTTPNotFound()
        body = await request.json()
        args = tool.validate(body.get("args", {}))
        try:
            task = self.s.tasks.submit(name.split(".")[0], str(body.get("title") or tool.description[:60]), str(body.get("request") or name),
                                       [PlanStep(str(body.get("title") or name), name, args)], source="local")
        except TaskRejected as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except Exception as exc:  # estop
            return web.json_response({"error": str(exc)}, status=423)
        if body.get("wait"):
            task = await self.s.tasks.wait(task.id, timeout=float(body.get("timeout", 120)))
        if name.startswith("creation3d.") and task.result:
            for art in task.result.get("artifacts", []):
                if art.get("kind") == "model3d":
                    self.assistant.active_model = art["id"]
        return web.json_response({"task": task.to_dict()}, dumps=lambda o: json.dumps(o, default=str))

    async def audit(self, request: web.Request) -> web.Response:
        return web.json_response({"entries": self.s.audit.recent(int(request.query.get("limit", 200))), "chainValid": self.s.audit.verify_chain()})

    async def devices(self, request: web.Request) -> web.Response:
        return web.json_response({"paired": [d.to_dict() for d in self.s.devices.list()], "connected": self.hub.connected(),
                                  "pairingActive": self.s.devices.pairing_active})

    async def device_revoke(self, request: web.Request) -> web.Response:
        did = request.match_info["id"]
        ok = self.s.devices.revoke(did)
        for sess in list(self.hub.sessions):
            if sess.device_id == did:
                await sess.ws.close(code=4003, message=b"device revoked")
        self.s.audit.record("local", "device.revoke", "high", "ok" if ok else "missing", {"deviceId": did})
        return web.json_response({"revoked": ok})

    async def pairing_start(self, request: web.Request) -> web.Response:
        await self.start_lan()
        session = self.s.devices.start_pairing()
        addrs = lan_addresses()
        payload = {"v": 1, "host": addrs[0] if addrs else "", "hosts": addrs, "port": self.s.settings.network.lan_port,
                   "fp": self.lan_fingerprint, "code": session.code}
        self.s.audit.record("local", "pairing.start", "high", "ok", {"expiresAt": session.expires_at})
        return web.json_response({"code": session.code, "expiresAt": session.expires_at, "qr": json.dumps(payload, separators=(",", ":")),
                                  "addresses": addrs, "port": self.s.settings.network.lan_port, "fingerprint": self.lan_fingerprint})

    async def pairing_cancel(self, request: web.Request) -> web.Response:
        self.s.devices.cancel_pairing()
        return web.json_response({"ok": True})

    async def config_get(self, request: web.Request) -> web.Response:
        return web.json_response({"config": self.s.settings.to_public_dict(), "mutable": sorted(MUTABLE_FIELDS)})

    async def config_patch(self, request: web.Request) -> web.Response:
        body = await request.json()
        for key, value in body.items():
            update_setting(self.s.settings, key, value)
        s = self.s
        s.policy.allow_desktop_control = s.settings.security.allow_desktop_control
        save_settings(s.settings)
        s.audit.record("local", "config.update", "medium", "ok", {"keys": sorted(body)})
        s.bus.publish("config.changed", {"keys": sorted(body)})
        return web.json_response({"config": s.settings.to_public_dict()})

    async def llm_models(self, request: web.Request) -> web.Response:
        st = await self.s.llm.status()
        return web.json_response({"status": st.to_dict(), "recommended": RECOMMENDED_MODELS})


async def run_server(services: Services, *, lan: bool | None = None) -> None:
    server = BaymaxServer(services)
    await server.start(lan=lan)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    import signal

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover
            pass
    await stop.wait()
    log.info("shutting down")
    await server.stop()
    await services.close()


def validate_file(path: Path) -> dict[str, Any]:
    return validate_glb(path.read_bytes()).to_dict()
