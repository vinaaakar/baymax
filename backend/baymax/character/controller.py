"""Authoritative character state, published to every client.

SUCCESS is shown briefly (a display hold, not a completion timer) and then
returns to IDLE — or to EXECUTING if other tasks are still running.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from ..events import EventBus
from .state_machine import CharacterEvent, CharacterState, CharacterStateMachine, TransitionResult


class CharacterController:
    SUCCESS_HOLD_S = 2.5

    def __init__(self, bus: EventBus, active_tasks: Callable[[], int] = lambda: 0) -> None:
        self.bus = bus
        self.sm = CharacterStateMachine()
        self.active_tasks = active_tasks
        self._hold: asyncio.TimerHandle | None = None
        self.last_reason = ""

    @property
    def state(self) -> CharacterState:
        return self.sm.state

    def dispatch(self, event: CharacterEvent | str, *, verified: bool = False, reason: str = "", task_id: str | None = None) -> TransitionResult:
        res = self.sm.dispatch(event, verified=verified, reason=reason)
        if res.accepted and res.previous != res.state:
            self.last_reason = reason
            payload = {"state": res.state.value, "previous": res.previous.value, "event": res.event.value, "reason": reason[:200]}
            if task_id:
                payload["taskId"] = task_id
            self.bus.publish("character.state", payload)
            self._schedule_hold(res.state)
        return res

    def _schedule_hold(self, state: CharacterState) -> None:
        if self._hold is not None:
            self._hold.cancel()
            self._hold = None
        if state is not CharacterState.SUCCESS:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._hold = loop.call_later(self.SUCCESS_HOLD_S, self._after_success)

    def _after_success(self) -> None:
        self._hold = None
        if self.sm.state is CharacterState.SUCCESS:
            self.dispatch(CharacterEvent.RESET, reason="success shown")
            if self.active_tasks() > 0:
                self.dispatch(CharacterEvent.TASK_STARTED, reason="other tasks still running")

    def snapshot(self) -> dict:
        return {"state": self.sm.state.value, "reason": self.last_reason}
