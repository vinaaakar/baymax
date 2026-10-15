"""Task manager: drives tasks through the lifecycle, enforces policy and approvals.

Progress shown in any UI comes exclusively from :meth:`_progress`, which is
only called by tools reporting real work. There are no timers that fabricate
progress or completion.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from ..character.state_machine import CharacterEvent
from ..security.estop import EmergencyStopEngaged
from ..security.policy import ActionRequest, Decision, Policy, Risk
from ..tools.base import ArgumentError, ToolContext, ToolError, ToolResult
from . import preflight
from .models import PlanStep, Task, TaskStatus
from .refs import RefFailure, refs_in, resolve

MAX_ATTEMPTS = 2  # one retry for transient failures; identical failures are never repeated more than this
RETRY_DELAY_S = 1.0

if TYPE_CHECKING:  # pragma: no cover
    from ..services import Services

log = logging.getLogger(__name__)

CharacterHook = Callable[..., None]


class TaskRejected(Exception):
    pass


@dataclass
class PendingApproval:
    id: str
    task_id: str
    fingerprint: str
    risk: Risk
    summary: str
    expires_at: float
    future: asyncio.Future

    def to_dict(self) -> dict[str, Any]:
        return {"approvalId": self.id, "taskId": self.task_id, "risk": self.risk.value, "summary": self.summary,
                "expiresAt": int(self.expires_at * 1000)}


class TaskManager:
    MAX_ACTIVE = 16
    MAX_CONCURRENT = 2

    def __init__(self, services: "Services", character: CharacterHook | None = None) -> None:
        self.s = services
        self.character = character or (lambda *a, **k: None)
        self._tasks: dict[str, Task] = {}
        self._runners: dict[str, asyncio.Task] = {}
        self._approvals: dict[str, PendingApproval] = {}
        self._sem = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._last_persist: dict[str, float] = {}
        self.recovered = self._recover()

    # ------------------------------------------------------------------ public
    def submit(self, kind: str, title: str, request: str, steps: list[PlanStep], source: str = "local") -> Task:
        self.s.estop.ensure_clear()
        active = [t for t in self._tasks.values() if not t.status.terminal]
        if len(active) >= self.MAX_ACTIVE:
            raise TaskRejected("too many active tasks; cancel some first")
        if not steps:
            raise TaskRejected("a task needs at least one step")
        task = Task(kind=kind, title=title[:200], request=request[:4000], source=source, plan=steps)
        self._tasks[task.id] = task
        self._persist(task, force=True)
        self._event(task, "created", {"request": task.request})
        self._emit(task)
        self._runners[task.id] = asyncio.create_task(self._drive(task), name=f"task-{task.id}")
        return task

    async def wait(self, task_id: str, timeout: float | None = None) -> Task:
        runner = self._runners.get(task_id)
        if runner is not None:
            try:
                await asyncio.wait_for(asyncio.shield(runner), timeout)
            except asyncio.CancelledError:
                # The task itself was cancelled (Emergency Stop / user cancel): report its final state. Only re-raise
                # when it is the *waiter* that is being cancelled.
                if not runner.done():
                    raise
        return self.get(task_id)  # type: ignore[return-value]

    def get(self, task_id: str) -> Task | None:
        if task_id in self._tasks:
            return self._tasks[task_id]
        row = self.s.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        return Task.from_row(row) if row else None

    def list(self, limit: int = 50) -> list[Task]:
        rows = self.s.db.query("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            out.append(self._tasks.get(r["id"]) or Task.from_row(r))
        return out

    def events(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.s.db.query("SELECT ts, kind, data FROM task_events WHERE task_id=? ORDER BY id", (task_id,))
        return [{"ts": r["ts"], "kind": r["kind"], "data": json.loads(r["data"])} for r in rows]

    def active(self) -> list[Task]:
        return [t for t in self._tasks.values() if not t.status.terminal]

    def cancel(self, task_id: str, reason: str = "cancelled by user") -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.status.terminal:
            return False
        task.cancel_requested = True
        task.error = reason
        for appr in list(self._approvals.values()):
            if appr.task_id == task_id and not appr.future.done():
                appr.future.set_result(False)
        runner = self._runners.get(task_id)
        if runner and not runner.done():
            runner.cancel()
        self._event(task, "cancel_requested", {"reason": reason})
        return True

    def cancel_all(self, reason: str) -> int:
        return sum(1 for t in list(self._tasks.values()) if self.cancel(t.id, reason))

    def pending_approvals(self) -> list[PendingApproval]:
        return [a for a in self._approvals.values() if not a.future.done()]

    def respond_approval(self, approval_id: str, approve: bool, approver: str) -> tuple[bool, str]:
        appr = self._approvals.get(approval_id)
        if appr is None or appr.future.done():
            return False, "approval not found or already resolved"
        if approve:
            if self.s.estop.engaged:
                return False, "Emergency Stop is engaged"
            if not Policy.may_approve(appr.risk, approver, self.s.settings.security.remote_high_risk_approval):
                self.s.audit.record(approver, "approval.denied_remote", appr.risk.value, "refused", {"task": appr.task_id})
                return False, "this action must be approved on the Mac"
        self.s.audit.record(approver, "approval.respond", appr.risk.value, "approved" if approve else "denied",
                            {"task": appr.task_id, "summary": appr.summary})
        appr.future.set_result(bool(approve))
        return True, "approved" if approve else "denied"

    # --------------------------------------------------------------- lifecycle
    async def _drive(self, task: Task) -> None:
        results: list[ToolResult] = []
        try:
            self._set(task, TaskStatus.PLANNED)
            async with self._sem:
                for index, step in enumerate(task.plan):
                    task.current_step = index
                    self.s.estop.ensure_clear()
                    if task.cancel_requested:
                        raise asyncio.CancelledError()
                    if step.depends_on_previous and results and not results[-1].verified:
                        # The previous step ran but its outcome is unconfirmed: never act on it (e.g. type into a
                        # document whose window was not seen). Distinct from that step having failed.
                        prev = task.plan[index - 1]
                        why = results[-1].verification or "its outcome could not be confirmed"
                        stop = ToolResult(False, f"I stopped before “{step.title}”: it depends on “{prev.title}”, which ran but "
                                                 f"couldn't be verified ({why}).",
                                          {"errorKind": "unverified_prerequisite", "stoppedBefore": step.title,
                                           "prerequisite": prev.title, "prerequisiteOutcome": results[-1].outcome})
                        step.status = "skipped"
                        task.error_kind = "unverified_prerequisite"
                        self._event(task, "stopped", {"step": step.title, "reason": stop.summary[:300]})
                        self._finish(task, TaskStatus.FAILED, stop, error=stop.summary)
                        return
                    args: dict[str, Any] | None = None
                    if refs_in(step.args):
                        got = resolve(step.args, results, [st.title for st in task.plan], self.s.tools, [st.tool for st in task.plan])
                        if isinstance(got, RefFailure):
                            step.status = "skipped"
                            task.error_kind = got.kind
                            stop = got.result()
                            self._event(task, "stopped", {"step": step.title, "reason": got.message[:300]})
                            self._finish(task, TaskStatus.FAILED, stop, error=stop.needs_clarification or stop.summary,
                                         char=CharacterEvent.CLARIFICATION_NEEDED if stop.needs_clarification else None)
                            return
                        args = got
                    result = await self._run_step(task, step, args)
                    attempts = 1
                    # Bounded recovery: only results the tool marked as transient, at most MAX_ATTEMPTS, never for
                    # actions that need approval (an approval covers one execution).
                    while not result.ok and result.retryable and attempts < MAX_ATTEMPTS and not task.approval:
                        await asyncio.sleep(RETRY_DELAY_S)
                        self.s.estop.ensure_clear()
                        if task.cancel_requested:
                            raise asyncio.CancelledError()
                        attempts += 1
                        self._event(task, "retry", {"step": step.title, "attempt": attempts, "reason": result.summary[:200]})
                        result = await self._run_step(task, step, args)
                    results.append(result)
                    if not result.ok:
                        step.status = "failed"
                        task.error_kind = str(result.data.get("errorKind") or ("invalid_arguments" if result.needs_clarification else "execution_failed"))
                        if result.needs_clarification:
                            self._finish(task, TaskStatus.FAILED, result, error=result.needs_clarification,
                                         char=CharacterEvent.CLARIFICATION_NEEDED)
                        else:
                            self._finish(task, TaskStatus.FAILED, result, error=result.summary)
                        return
                    step.status = "done"
                    step.summary = result.summary[:500]
                    self._persist(task, force=True)
                self._set(task, TaskStatus.VERIFYING)
                verified = all(r.verified for r in results)
                final = _merge(results)
                final.verified = verified
                self._finish(task, TaskStatus.COMPLETED, final, verified=verified)
        except asyncio.CancelledError:
            for st in task.plan:
                if st.status in ("pending", "running", "awaiting_approval"):
                    st.status = "cancelled"
            self._finish(task, TaskStatus.CANCELLED, None, error=task.error or "cancelled")
        except EmergencyStopEngaged as exc:
            self._finish(task, TaskStatus.CANCELLED, None, error=str(exc))
        except (ToolError, ArgumentError, PermissionError) as exc:
            task.error_kind = classify_error(exc)
            self._finish(task, TaskStatus.FAILED, None, error=str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("task %s crashed", task.id)
            self._finish(task, TaskStatus.FAILED, None, error=f"internal error: {exc.__class__.__name__}: {exc}")
        finally:
            self._runners.pop(task.id, None)

    async def _run_step(self, task: Task, step: PlanStep, resolved: dict[str, Any] | None = None) -> ToolResult:
        tool = self.s.tools.get(step.tool)
        if tool is None:
            raise ToolError(f"unknown tool {step.tool!r}")
        args = tool.validate(resolved if resolved is not None else step.args)  # references already substituted + re-checked
        risk, cats = tool.classify(args)
        req = ActionRequest(tool=tool.name, args=args, risk=risk, categories=cats, source=task.source)
        decision = self.s.policy.evaluate(req)
        summary = tool.describe(args)
        self._event(task, "policy", {"tool": tool.name, "decision": decision.decision.value, "risk": decision.risk.value,
                                     "reason": decision.reason})
        if decision.decision is Decision.DENY:
            self.s.audit.record(task.source, tool.name, decision.risk.value, "denied", {"task": task.id, "reason": decision.reason})
            self.character(CharacterEvent.WARNING_RAISED, reason=decision.reason, task_id=task.id)
            raise ToolError(f"not permitted: {decision.reason}")
        if decision.decision is Decision.REQUIRE_APPROVAL:
            step.status = "awaiting_approval"
            self._set(task, TaskStatus.AWAITING_APPROVAL)
            approved = await self._await_approval(task, req, decision.risk, summary)
            if not approved:
                self.s.audit.record(task.source, tool.name, decision.risk.value, "not_approved", {"task": task.id})
                task.error = "approval was denied or expired"
                raise asyncio.CancelledError()
            # Re-check the action is still exactly the approved one.
            if req.fingerprint() != (task.approval or {}).get("fingerprint"):
                raise ToolError("approved action changed; refusing to run")
        if task.status is not TaskStatus.RUNNING:
            self._set(task, TaskStatus.RUNNING)
        step.status = "running"
        self.character(CharacterEvent.TASK_STARTED, task_id=task.id)
        self._emit(task, step=step.title)
        self.s.audit.record(task.source, tool.name, decision.risk.value, "started", {"task": task.id, "args": _short(args)})
        ctx = ToolContext(services=self.s, task_id=task.id, source=task.source,
                          progress=lambda frac, text: self._progress(task, frac, text),
                          is_cancelled=lambda: task.cancel_requested)
        # Right before acting: is the Mac unlocked and awake, and is the target app free of blocking dialogs?
        blocked = await preflight.check(self.s, tool, args, step_title=step.title,
                                        progress=lambda text: self._progress(task, None, text), checkpoint=ctx.checkpoint)
        if blocked is not None:
            self._event(task, "preflight", {"step": step.title, "reason": blocked.summary[:300]})
            return blocked
        timeout = tool.timeout_s or 300.0
        try:
            result = await asyncio.wait_for(tool.run(ctx, args), timeout)
        except asyncio.TimeoutError:
            raise ToolError(f"that took longer than {timeout:.0f} seconds, so I stopped it") from None
        self.s.audit.record(task.source, tool.name, decision.risk.value, "ok" if result.ok else "failed",
                            {"task": task.id, "summary": result.summary[:300], "verified": result.verified})
        self._event(task, "step_result", {"step": step.title, **{k: v for k, v in result.to_dict().items() if k != "data"}})
        return result

    async def _await_approval(self, task: Task, req: ActionRequest, risk: Risk, summary: str) -> bool:
        loop = asyncio.get_running_loop()
        appr = PendingApproval(
            id=uuid.uuid4().hex[:12], task_id=task.id, fingerprint=req.fingerprint(), risk=risk, summary=summary,
            expires_at=time.time() + self.s.settings.security.approval_timeout_s, future=loop.create_future(),
        )
        self._approvals[appr.id] = appr
        task.approval = {"id": appr.id, "status": "pending", "risk": risk.value, "summary": summary, "fingerprint": appr.fingerprint}
        self._persist(task, force=True)
        payload = appr.to_dict()
        payload["remoteAllowed"] = Policy.may_approve(risk, "device:any", self.s.settings.security.remote_high_risk_approval)
        self.s.bus.publish("approval.request", payload)
        self.character(CharacterEvent.APPROVAL_REQUIRED, reason=summary, task_id=task.id)
        try:
            approved = await asyncio.wait_for(appr.future, timeout=self.s.settings.security.approval_timeout_s)
        except asyncio.TimeoutError:
            approved = False
        finally:
            self._approvals.pop(appr.id, None)
        task.approval["status"] = "approved" if approved else "denied"
        self.s.bus.publish("approval.resolved", {"approvalId": appr.id, "taskId": task.id, "approved": approved})
        return approved

    # ---------------------------------------------------------------- helpers
    def _progress(self, task: Task, fraction: float | None, text: str) -> None:
        if fraction is not None:
            task.progress = max(0.0, min(1.0, float(fraction)))
        if task.plan and task.current_step < len(task.plan) and text:
            task.plan[task.current_step].summary = text[:300]
        self._persist(task)
        self._emit(task, detail=text)

    def _set(self, task: Task, status: TaskStatus) -> None:
        task.transition(status)
        self._persist(task, force=True)
        self._event(task, "status", {"status": status.value})
        self._emit(task)

    def _finish(self, task: Task, status: TaskStatus, result: ToolResult | None, *, error: str | None = None,
                verified: bool = False, char: CharacterEvent | None = None) -> None:
        if task.status.terminal:
            return
        task.transition(status)
        task.verified = verified and status is TaskStatus.COMPLETED
        if result is not None:
            task.result = result.to_dict()
        if error and status is not TaskStatus.COMPLETED:
            task.error = error
        self._persist(task, force=True)
        self._event(task, "finished", {"status": status.value, "verified": task.verified, "error": task.error})
        summary = (result.summary if result else "") or (task.error or status.value.lower())
        artifacts = (result.artifacts if result else []) or []
        if status is TaskStatus.COMPLETED:
            task.error_kind = "success"
        elif status is TaskStatus.CANCELLED and not task.error_kind:
            task.error_kind = "cancelled"
        self.s.bus.publish("task.result", {"taskId": task.id, "status": status.value, "summary": summary,
                                           "verified": task.verified, "artifacts": artifacts, "title": task.title,
                                           "errorKind": task.error_kind})
        self._emit(task)
        if char is not None:
            self.character(char, reason=summary, task_id=task.id)
        elif status is TaskStatus.COMPLETED and task.verified:
            self.character(CharacterEvent.TASK_VERIFIED, verified=True, reason=summary, task_id=task.id)
        elif status is TaskStatus.COMPLETED:
            # Finished but the outcome could not be verified: no success pose, just return to rest.
            self.character(CharacterEvent.RESET, reason="completed (outcome not verified)", task_id=task.id)
        elif status is TaskStatus.FAILED:
            self.character(CharacterEvent.TASK_FAILED, reason=summary, task_id=task.id)
        else:
            self.character(CharacterEvent.TASK_CANCELLED, reason=summary, task_id=task.id)

    def _emit(self, task: Task, *, step: str | None = None, detail: str | None = None) -> None:
        payload: dict[str, Any] = {"taskId": task.id, "status": task.status.value, "title": task.title, "kind": task.kind,
                                   "currentStep": task.current_step, "steps": [s.__dict__ for s in task.plan]}
        if task.progress is not None:
            payload["progress"] = task.progress
        if step or (task.plan and task.current_step < len(task.plan)):
            payload["step"] = step or task.plan[task.current_step].title
        if detail:
            payload["detail"] = detail[:300]
        if task.approval:
            payload["approval"] = {k: v for k, v in task.approval.items() if k != "fingerprint"}
        if task.error:
            payload["error"] = task.error
        self.s.bus.publish("task.update", payload)

    def _event(self, task: Task, kind: str, data: dict[str, Any]) -> None:
        self.s.db.execute("INSERT INTO task_events(task_id, ts, kind, data) VALUES (?,?,?,?)",
                          (task.id, time.time(), kind, json.dumps(data, default=str)[:8000]))

    def _persist(self, task: Task, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_persist.get(task.id, 0) < 0.5:
            return
        self._last_persist[task.id] = now
        self.s.db.execute(
            """INSERT INTO tasks(id, kind, title, request, status, source, plan, current_step, progress, approval, result,
                                 error, verified, cancel_requested, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, plan=excluded.plan, current_step=excluded.current_step,
                 progress=excluded.progress, approval=excluded.approval, result=excluded.result, error=excluded.error,
                 verified=excluded.verified, cancel_requested=excluded.cancel_requested, updated_at=excluded.updated_at""",
            (task.id, task.kind, task.title, task.request, task.status.value, task.source,
             json.dumps([s.__dict__ for s in task.plan], default=str), task.current_step, task.progress,
             json.dumps(task.approval) if task.approval else None,
             json.dumps(task.result, default=str) if task.result else None, task.error, int(task.verified),
             int(task.cancel_requested), task.created_at, now),
        )

    def _recover(self) -> int:
        """Tasks left unfinished by a crash/restart are closed honestly, never resumed blindly."""
        rows = self.s.db.query("SELECT id, status FROM tasks WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED')")
        for r in rows:
            status = "FAILED" if r["status"] in ("RUNNING", "VERIFYING") else "CANCELLED"
            self.s.db.execute("UPDATE tasks SET status=?, error=?, updated_at=? WHERE id=?",
                              (status, "interrupted: the backend restarted before this task finished", time.time(), r["id"]))
            self.s.db.execute("INSERT INTO task_events(task_id, ts, kind, data) VALUES (?,?,?,?)",
                              (r["id"], time.time(), "recovered", json.dumps({"from": r["status"], "to": status})))
        return len(rows)


def _merge(results: list[ToolResult]) -> ToolResult:
    if len(results) == 1:
        r = results[0]
        return ToolResult(ok=r.ok, summary=r.summary, data=r.data, artifacts=list(r.artifacts), verified=r.verified,
                          verification=r.verification, untrusted=r.untrusted)
    return ToolResult(
        ok=all(r.ok for r in results),
        summary=" ".join(r.summary for r in results if r.summary)[:2000],
        data={"steps": [r.data for r in results]},
        artifacts=[a for r in results for a in r.artifacts],
        verification="; ".join(r.verification for r in results if r.verification),
        untrusted=any(r.untrusted for r in results),
    )


def _short(args: dict[str, Any]) -> dict[str, Any]:
    return {k: (v[:200] if isinstance(v, str) else v) for k, v in args.items()}


def classify_error(exc: BaseException) -> str:
    """Typed failure reason for a tool exception (shown to the user and to the model)."""
    from ..tools.base import ToolUnavailable

    msg = str(exc).lower()
    if isinstance(exc, ArgumentError):
        return "invalid_arguments"
    if isinstance(exc, asyncio.TimeoutError) or "timed out" in msg or "timeout" in msg or "took longer than" in msg:
        return "timeout"
    if isinstance(exc, PermissionError) or "permission" in msg or "not permitted" in msg:
        return "permission_denied"
    if isinstance(exc, ToolUnavailable):
        return "unsupported"
    if any(k in msg for k in ("not found", "isn't running", "no such", "does not exist", "couldn't find", "unknown tool")):
        return "not_found"
    return "execution_failed"
