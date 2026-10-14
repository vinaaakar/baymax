"""Versioned Mac <-> iPhone message protocol (v1).

Envelope::

    {"v": 1, "type": "user.text", "id": "<uuid>", "ts": 1700000000000, "payload": {...}}

All inbound frames are validated before any handler sees them:
size limit, JSON parse, envelope fields, message type allowed for the
direction, and payload field types. Unknown optional fields are dropped.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MIN_PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 65536
MAX_STRING = 8000

_T = {"str": str, "int": int, "float": (int, float), "bool": bool, "list": list, "dict": dict}

# type -> (required fields, optional fields); field -> type name
CLIENT_TO_SERVER: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "hello": ({"protocol": "int", "minProtocol": "int", "client": "str"}, {"appVersion": "str", "deviceId": "str"}),
    "pair.request": ({"code": "str", "deviceId": "str", "deviceName": "str"}, {}),
    "auth.response": ({"deviceId": "str", "mac": "str"}, {}),
    "user.text": ({"text": "str"}, {"source": "str", "speakReply": "bool"}),
    "voice.state": ({"state": "str"}, {"level": "float"}),
    "approval.respond": ({"approvalId": "str", "approve": "bool"}, {}),
    "task.cancel": ({"taskId": "str"}, {}),
    "estop.trigger": ({}, {"reason": "str"}),
    "sleep.set": ({"sleeping": "bool"}, {}),
    "history.request": ({}, {"limit": "int"}),
    "ping": ({}, {"t": "int"}),
}

SERVER_TO_CLIENT: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "hello.ack": ({"protocol": "int", "server": "str"}, {"serverVersion": "str"}),
    "auth.challenge": ({"nonce": "str"}, {}),
    "auth.ok": ({"deviceId": "str"}, {}),
    "pair.ok": ({"deviceId": "str", "deviceKey": "str"}, {}),
    "error": ({"code": "str", "message": "str"}, {"replyTo": "str", "fatal": "bool"}),
    "character.state": ({"state": "str"}, {"reason": "str", "taskId": "str"}),
    "connection.status": ({"status": "str"}, {"devices": "int", "llm": "str"}),
    "voice.state": ({"state": "str"}, {"level": "float", "source": "str"}),
    "assistant.message": ({"text": "str", "messageId": "str"}, {"speak": "bool", "taskId": "str", "role": "str"}),
    "task.update": ({"taskId": "str", "status": "str", "title": "str"}, {"step": "str", "progress": "float", "detail": "str"}),
    "task.result": ({"taskId": "str", "status": "str", "summary": "str"}, {"verified": "bool", "artifacts": "list"}),
    "approval.request": ({"approvalId": "str", "taskId": "str", "summary": "str", "risk": "str"}, {"remoteAllowed": "bool", "expiresAt": "int"}),
    "estop.state": ({"engaged": "bool"}, {"reason": "str"}),
    "sleep.state": ({"sleeping": "bool"}, {}),
    "history": ({"messages": "list"}, {}),
    "pong": ({}, {"t": "int"}),
}

VOICE_STATES = {"idle", "listening", "transcribing", "speaking", "unavailable"}


class ProtocolError(Exception):
    def __init__(self, code: str, message: str, reply_to: str | None = None, fatal: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.reply_to = reply_to
        self.fatal = fatal


@dataclass(frozen=True)
class Message:
    type: str
    payload: dict[str, Any]
    id: str
    ts: int
    v: int = PROTOCOL_VERSION

    def to_json(self) -> str:
        return json.dumps({"v": self.v, "type": self.type, "id": self.id, "ts": self.ts, "payload": self.payload}, separators=(",", ":"))


def now_ms() -> int:
    return int(time.time() * 1000)


def make(type_: str, payload: dict[str, Any] | None = None, *, direction: str = "s2c") -> Message:
    """Build an outbound message, validating it against the schema."""
    table = SERVER_TO_CLIENT if direction == "s2c" else CLIENT_TO_SERVER
    payload = dict(payload or {})
    if type_ not in table:
        raise ValueError(f"unknown message type {type_!r} for direction {direction}")
    clean = _validate_payload(type_, payload, table[type_])
    return Message(type=type_, payload=clean, id=uuid.uuid4().hex, ts=now_ms())


def parse(raw: str | bytes, *, direction: str = "c2s") -> Message:
    """Parse and validate an inbound frame. Raises :class:`ProtocolError`."""
    if isinstance(raw, str):
        size = len(raw.encode("utf-8", errors="replace"))
    else:
        size = len(raw)
    if size > MAX_MESSAGE_BYTES:
        raise ProtocolError("too_large", f"message exceeds {MAX_MESSAGE_BYTES} bytes")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("bad_json", f"invalid JSON: {exc.__class__.__name__}") from None
    if not isinstance(data, dict):
        raise ProtocolError("bad_envelope", "envelope must be an object")
    msg_id = data.get("id")
    reply_to = msg_id if isinstance(msg_id, str) and len(msg_id) <= 64 else None
    v = data.get("v")
    if not isinstance(v, int) or isinstance(v, bool):
        raise ProtocolError("bad_envelope", "missing protocol version 'v'", reply_to)
    if v < MIN_PROTOCOL_VERSION or v > PROTOCOL_VERSION:
        raise ProtocolError("version_unsupported", f"protocol v{v} unsupported (server supports {MIN_PROTOCOL_VERSION}..{PROTOCOL_VERSION})", reply_to, fatal=True)
    type_ = data.get("type")
    if not isinstance(type_, str) or not type_:
        raise ProtocolError("bad_envelope", "missing 'type'", reply_to)
    if reply_to is None:
        raise ProtocolError("bad_envelope", "missing or invalid 'id'")
    ts = data.get("ts", 0)
    if not isinstance(ts, int) or isinstance(ts, bool):
        raise ProtocolError("bad_envelope", "'ts' must be an integer", reply_to)
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtocolError("bad_envelope", "'payload' must be an object", reply_to)
    table = CLIENT_TO_SERVER if direction == "c2s" else SERVER_TO_CLIENT
    if type_ not in table:
        raise ProtocolError("unknown_type", f"message type {type_!r} not allowed", reply_to)
    try:
        clean = _validate_payload(type_, payload, table[type_])
    except ValueError as exc:
        raise ProtocolError("bad_payload", str(exc), reply_to) from None
    return Message(type=type_, payload=clean, id=reply_to, ts=ts, v=v)


def _check(name: str, value: Any, tname: str) -> Any:
    expected = _T[tname]
    if isinstance(value, bool) and tname in ("int", "float"):
        raise ValueError(f"field {name!r} must be {tname}")
    if not isinstance(value, expected):
        raise ValueError(f"field {name!r} must be {tname}")
    if tname == "str" and len(value) > MAX_STRING:
        raise ValueError(f"field {name!r} too long")
    if tname == "float" and value != value:  # NaN
        raise ValueError(f"field {name!r} is NaN")
    return value


def _validate_payload(type_: str, payload: dict[str, Any], schema: tuple[dict[str, str], dict[str, str]]) -> dict[str, Any]:
    required, optional = schema
    clean: dict[str, Any] = {}
    for name, tname in required.items():
        if name not in payload:
            raise ValueError(f"{type_}: missing field {name!r}")
        clean[name] = _check(name, payload[name], tname)
    for name, tname in optional.items():
        if name in payload and payload[name] is not None:
            clean[name] = _check(name, payload[name], tname)
    if type_ == "voice.state" and clean["state"] not in VOICE_STATES:
        raise ValueError(f"voice.state: unknown state {clean['state']!r}")
    if type_ == "user.text" and not clean["text"].strip():
        raise ValueError("user.text: empty text")
    return clean


def error(code: str, message: str, reply_to: str | None = None, fatal: bool = False) -> Message:
    payload: dict[str, Any] = {"code": code, "message": message, "fatal": fatal}
    if reply_to:
        payload["replyTo"] = reply_to
    return make("error", payload)
