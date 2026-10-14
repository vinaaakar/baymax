"""Typed character animation state machine.

The tables here mirror ``protocol/baymax-protocol.json`` (verified by
``tests/test_character_state_machine.py``). The machine is *event driven*:
callers report what actually happened (microphone started, task verified, ...)
and the machine decides whether the visible state may change.

Key rules
---------
* SUCCESS requires ``verified=True`` and may only follow EXECUTING. A confident
  model answer is never enough to show success.
* SLEEPING ignores everything except WAKE and CONNECTION_LOST.
* DISCONNECTED accepts only CONNECTION_RESTORED (or SLEEP).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class CharacterState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    EXECUTING = "EXECUTING"
    SUCCESS = "SUCCESS"
    CONFUSED = "CONFUSED"
    CONCERNED = "CONCERNED"
    SLEEPING = "SLEEPING"
    ERROR = "ERROR"
    DISCONNECTED = "DISCONNECTED"


class CharacterEvent(str, Enum):
    MIC_STARTED = "MIC_STARTED"
    MIC_STOPPED = "MIC_STOPPED"
    REQUEST_RECEIVED = "REQUEST_RECEIVED"
    SPEECH_STARTED = "SPEECH_STARTED"
    SPEECH_ENDED = "SPEECH_ENDED"
    TASK_STARTED = "TASK_STARTED"
    TASK_VERIFIED = "TASK_VERIFIED"
    TASK_FAILED = "TASK_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"
    WARNING_RAISED = "WARNING_RAISED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SLEEP = "SLEEP"
    WAKE = "WAKE"
    CONNECTION_LOST = "CONNECTION_LOST"
    CONNECTION_RESTORED = "CONNECTION_RESTORED"
    RESET = "RESET"


S = CharacterState
E = CharacterEvent

TRANSITIONS: dict[CharacterState, frozenset[CharacterState]] = {
    S.IDLE: frozenset({S.LISTENING, S.THINKING, S.SPEAKING, S.EXECUTING, S.CONFUSED, S.CONCERNED, S.SLEEPING, S.ERROR, S.DISCONNECTED}),
    S.LISTENING: frozenset({S.IDLE, S.THINKING, S.SLEEPING, S.ERROR, S.DISCONNECTED}),
    S.THINKING: frozenset({S.IDLE, S.LISTENING, S.SPEAKING, S.EXECUTING, S.CONFUSED, S.CONCERNED, S.SLEEPING, S.ERROR, S.DISCONNECTED}),
    S.SPEAKING: frozenset({S.IDLE, S.LISTENING, S.THINKING, S.EXECUTING, S.CONFUSED, S.CONCERNED, S.SLEEPING, S.ERROR, S.DISCONNECTED}),
    S.EXECUTING: frozenset({S.IDLE, S.THINKING, S.SPEAKING, S.SUCCESS, S.CONFUSED, S.CONCERNED, S.SLEEPING, S.ERROR, S.DISCONNECTED}),
    S.SUCCESS: frozenset({S.IDLE, S.LISTENING, S.THINKING, S.SPEAKING, S.EXECUTING, S.CONFUSED, S.CONCERNED, S.SLEEPING, S.ERROR, S.DISCONNECTED}),
    S.CONFUSED: frozenset({S.IDLE, S.LISTENING, S.THINKING, S.SPEAKING, S.EXECUTING, S.CONCERNED, S.SLEEPING, S.ERROR, S.DISCONNECTED}),
    S.CONCERNED: frozenset({S.IDLE, S.LISTENING, S.THINKING, S.SPEAKING, S.EXECUTING, S.CONFUSED, S.SLEEPING, S.ERROR, S.DISCONNECTED}),
    S.SLEEPING: frozenset({S.IDLE, S.DISCONNECTED}),
    S.ERROR: frozenset({S.IDLE, S.LISTENING, S.THINKING, S.SPEAKING, S.EXECUTING, S.CONFUSED, S.CONCERNED, S.SLEEPING, S.DISCONNECTED}),
    S.DISCONNECTED: frozenset({S.IDLE, S.SLEEPING}),
}

EVENT_TARGETS: dict[CharacterEvent, CharacterState] = {
    E.MIC_STARTED: S.LISTENING,
    E.MIC_STOPPED: S.IDLE,
    E.REQUEST_RECEIVED: S.THINKING,
    E.SPEECH_STARTED: S.SPEAKING,
    E.SPEECH_ENDED: S.IDLE,
    E.TASK_STARTED: S.EXECUTING,
    E.TASK_VERIFIED: S.SUCCESS,
    E.TASK_FAILED: S.ERROR,
    E.TASK_CANCELLED: S.IDLE,
    E.CLARIFICATION_NEEDED: S.CONFUSED,
    E.WARNING_RAISED: S.CONCERNED,
    E.APPROVAL_REQUIRED: S.CONCERNED,
    E.SLEEP: S.SLEEPING,
    E.WAKE: S.IDLE,
    E.CONNECTION_LOST: S.DISCONNECTED,
    E.CONNECTION_RESTORED: S.IDLE,
    E.RESET: S.IDLE,
}


SLEEP_EXIT_EVENTS = frozenset({E.WAKE, E.CONNECTION_LOST})
DISCONNECTED_EXIT_EVENTS = frozenset({E.CONNECTION_RESTORED, E.SLEEP})


@dataclass(frozen=True)
class TransitionResult:
    accepted: bool
    previous: CharacterState
    state: CharacterState
    event: CharacterEvent
    reason: str = ""


Listener = Callable[[TransitionResult], None]


class CharacterStateMachine:
    def __init__(self, initial: CharacterState = S.IDLE, clock: Callable[[], float] = time.monotonic) -> None:
        self._state = initial
        self._clock = clock
        self._entered_at = clock()
        self._listeners: list[Listener] = []
        self.history: list[TransitionResult] = []

    @property
    def state(self) -> CharacterState:
        return self._state

    @property
    def time_in_state(self) -> float:
        return self._clock() - self._entered_at

    def on_change(self, listener: Listener) -> None:
        self._listeners.append(listener)

    @staticmethod
    def can_transition(src: CharacterState, dst: CharacterState) -> bool:
        return src == dst or dst in TRANSITIONS[src]

    def dispatch(self, event: CharacterEvent | str, *, verified: bool = False, reason: str = "") -> TransitionResult:
        event = CharacterEvent(event)
        src = self._state
        dst = EVENT_TARGETS[event]
        rejection = self._rejection(src, dst, event, verified)
        if rejection:
            result = TransitionResult(False, src, src, event, rejection)
        else:
            result = TransitionResult(True, src, dst, event, reason)
            if dst != src:
                self._state = dst
                self._entered_at = self._clock()
        self.history.append(result)
        if len(self.history) > 100:
            del self.history[:-100]
        if result.accepted and result.previous != result.state:
            for listener in list(self._listeners):
                listener(result)
        return result

    @staticmethod
    def _rejection(src: CharacterState, dst: CharacterState, event: CharacterEvent, verified: bool) -> str:
        if src is S.SLEEPING and event not in SLEEP_EXIT_EVENTS:
            return "sleeping: only WAKE or CONNECTION_LOST apply"
        if src is S.DISCONNECTED and event not in DISCONNECTED_EXIT_EVENTS:
            return "disconnected: only CONNECTION_RESTORED or SLEEP apply"
        if event is E.TASK_VERIFIED and not verified:
            return "success requires verified outcome"
        if dst is S.SUCCESS and src is not S.EXECUTING:
            return "success only follows an executing task"
        if src == dst:
            return ""
        if dst not in TRANSITIONS[src]:
            return f"invalid transition {src.value} -> {dst.value}"
        return ""

    def force_sync(self, state: CharacterState) -> None:
        """Adopt the authoritative state (used by clients after a reconnect)."""
        self._state = state
        self._entered_at = self._clock()
