"""Structured record of one request as it moves through the pipeline.

Input → speech validation → intent → context → tool selection → argument validation → permissions →
execution → verification → response. Every stage records what it decided (ok / skipped / rejected / failed)
and why, so a wrong answer can be traced to the stage that caused it (GET /api/requests).
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

STAGES = ("input", "speech_validation", "intent", "context", "tool_selection", "argument_validation",
          "permissions", "execution", "verification", "response")


@dataclass
class RequestTrace:
    original: str
    source: str
    voice: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started: float = field(default_factory=time.time)
    intent: str = ""
    app: str | None = None
    url: str | None = None
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)
    confirmation_required: bool = False
    task_id: str | None = None
    result: str = ""
    verified: bool | None = None
    stages: list[dict[str, Any]] = field(default_factory=list)

    def stage(self, name: str, status: str, detail: str = "") -> None:
        assert name in STAGES, name
        self.stages.append({"stage": name, "status": status, "detail": detail[:300], "t": round(time.time() - self.started, 3)})

    def last(self, name: str) -> dict[str, Any] | None:
        return next((s for s in reversed(self.stages) if s["stage"] == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "original": self.original, "source": self.source, "voice": self.voice, "intent": self.intent,
                "app": self.app, "url": self.url, "tool": self.tool, "args": self.args, "permissions": self.permissions,
                "confirmationRequired": self.confirmation_required, "taskId": self.task_id, "result": self.result[:500],
                "verified": self.verified, "stages": self.stages, "started": self.started}


_current: ContextVar[RequestTrace | None] = ContextVar("baymax_request_trace", default=None)


def current() -> RequestTrace | None:
    return _current.get()


def stage(name: str, status: str, detail: str = "") -> None:
    """Record a stage on the request being handled (no-op outside a request)."""
    t = _current.get()
    if t is not None:
        t.stage(name, status, detail)


class TraceLog:
    """The last N request traces, newest last (in memory only)."""

    def __init__(self, limit: int = 100) -> None:
        self._items: deque[RequestTrace] = deque(maxlen=limit)
        self._by_task: dict[str, RequestTrace] = {}

    def begin(self, original: str, source: str, voice: bool):
        t = RequestTrace(original, source, voice)
        self._items.append(t)
        return t, _current.set(t)

    @staticmethod
    def end(token) -> None:
        _current.reset(token)

    def link_task(self, trace: RequestTrace, task_id: str) -> None:
        trace.task_id = task_id
        self._by_task[task_id] = trace
        if len(self._by_task) > 500:
            self._by_task.pop(next(iter(self._by_task)))

    def for_task(self, task_id: str) -> RequestTrace | None:
        return self._by_task.get(task_id)

    def recent(self, limit: int = 30) -> list[dict[str, Any]]:
        return [t.to_dict() for t in list(self._items)[-limit:]]
