"""In-process event bus with bounded per-subscriber queues.

Slow subscribers never block publishers: when a subscriber queue is full the
oldest event is dropped and a ``dropped`` counter is incremented.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    type: str
    payload: dict[str, Any]
    seq: int = 0
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "payload": self.payload, "seq": self.seq, "ts": self.ts}


class Subscription:
    def __init__(self, bus: "EventBus", maxsize: int) -> None:
        self._bus = bus
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def offer(self, event: Event) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:  # pragma: no cover - race guard
                pass
        self.queue.put_nowait(event)

    async def get(self) -> Event:
        return await self.queue.get()

    def close(self) -> None:
        self._bus.unsubscribe(self)


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self._subs: set[Subscription] = set()
        self._seq = itertools.count(1)
        self._history: list[Event] = []
        self._history_max = history

    def subscribe(self, maxsize: int = 256) -> Subscription:
        sub = Subscription(self, maxsize)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    def publish(self, type_: str, payload: dict[str, Any] | None = None) -> Event:
        event = Event(type=type_, payload=dict(payload or {}), seq=next(self._seq))
        self._history.append(event)
        if len(self._history) > self._history_max:
            del self._history[: len(self._history) - self._history_max]
        for sub in list(self._subs):
            sub.offer(event)
        return event

    def recent(self, limit: int = 50) -> list[Event]:
        return self._history[-limit:]

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
