"""Task / mission lifecycle model.

REQUESTED -> PLANNED -> AWAITING_APPROVAL -> RUNNING -> VERIFYING -> COMPLETED | FAILED | CANCELLED
(mirrors ``taskTransitions`` in the shared protocol file).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    REQUESTED = "REQUESTED"
    PLANNED = "PLANNED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


T = TaskStatus
TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    T.REQUESTED: frozenset({T.PLANNED, T.FAILED, T.CANCELLED}),
    T.PLANNED: frozenset({T.AWAITING_APPROVAL, T.RUNNING, T.FAILED, T.CANCELLED}),
    T.AWAITING_APPROVAL: frozenset({T.RUNNING, T.FAILED, T.CANCELLED}),
    T.RUNNING: frozenset({T.AWAITING_APPROVAL, T.VERIFYING, T.FAILED, T.CANCELLED}),
    T.VERIFYING: frozenset({T.COMPLETED, T.FAILED, T.CANCELLED}),
    T.COMPLETED: frozenset(),
    T.FAILED: frozenset(),
    T.CANCELLED: frozenset(),
}


class InvalidTaskTransition(Exception):
    pass


@dataclass
class PlanStep:
    title: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | awaiting_approval | running | done | failed | skipped | cancelled
    summary: str = ""


@dataclass
class Task:
    kind: str
    title: str
    request: str
    source: str = "local"
    plan: list[PlanStep] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: TaskStatus = TaskStatus.REQUESTED
    current_step: int = 0
    progress: float | None = None  # only set from real tool-reported progress
    approval: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    verified: bool = False
    cancel_requested: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def transition(self, new: TaskStatus) -> None:
        if new not in TASK_TRANSITIONS[self.status]:
            raise InvalidTaskTransition(f"{self.status.value} -> {new.value}")
        self.status = new
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_row(cls, row: Any) -> "Task":
        plan = [PlanStep(**s) for s in json.loads(row["plan"] or "[]")]
        return cls(
            id=row["id"], kind=row["kind"], title=row["title"], request=row["request"], source=row["source"],
            plan=plan, status=TaskStatus(row["status"]), current_step=row["current_step"], progress=row["progress"],
            approval=json.loads(row["approval"]) if row["approval"] else None,
            result=json.loads(row["result"]) if row["result"] else None, error=row["error"],
            verified=bool(row["verified"]), cancel_requested=bool(row["cancel_requested"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )
