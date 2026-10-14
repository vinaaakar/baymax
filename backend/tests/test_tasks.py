import asyncio

import pytest

from baymax.character.state_machine import CharacterState
from baymax.security.estop import EmergencyStopEngaged
from baymax.security.policy import Risk
from baymax.tasks.models import InvalidTaskTransition, PlanStep, Task, TaskStatus
from baymax.tools.base import Arg, Tool, ToolResult


class SlowTool(Tool):
    name = "test.slow"
    risk = Risk.LOW
    args = {"steps": Arg("int", False, default=3)}

    def __init__(self):
        self.gate = asyncio.Event()

    async def run(self, ctx, args):
        for i in range(args.get("steps", 3)):
            ctx.checkpoint()
            ctx.progress((i + 1) / args["steps"], f"step {i + 1}")
            await asyncio.sleep(0.01)
        await self.gate.wait()
        return ToolResult(True, "slow done", verified=True, verification="checked")


class RiskyTool(Tool):
    name = "test.risky"
    risk = Risk.HIGH
    categories = frozenset({"send_message"})
    args = {"to": Arg("str")}
    runs = 0

    async def run(self, ctx, args):
        RiskyTool.runs += 1
        return ToolResult(True, f"sent to {args['to']}", verified=False, verification="not confirmable")


class FailingTool(Tool):
    name = "test.fail"
    args = {}

    async def run(self, ctx, args):
        return ToolResult(False, "it broke")


@pytest.fixture
def tools(services):
    slow, risky, fail = SlowTool(), RiskyTool(), FailingTool()
    for t in (slow, risky, fail):
        services.tools.register(t)
    RiskyTool.runs = 0
    return slow, risky, fail


def test_invalid_transitions_raise():
    t = Task("k", "t", "r")
    with pytest.raises(InvalidTaskTransition):
        t.transition(TaskStatus.COMPLETED)
    t.transition(TaskStatus.PLANNED)
    t.transition(TaskStatus.RUNNING)
    t.transition(TaskStatus.VERIFYING)
    t.transition(TaskStatus.COMPLETED)
    with pytest.raises(InvalidTaskTransition):
        t.transition(TaskStatus.RUNNING)


async def test_lifecycle_progress_and_verified_success(services, tools):
    slow, _, _ = tools
    sub = services.bus.subscribe()
    task = services.tasks.submit("test", "Slow", "do it", [PlanStep("slow", "test.slow", {"steps": 3})])
    await asyncio.sleep(0.15)
    assert services.tasks.get(task.id).status is TaskStatus.RUNNING
    assert services.character.state is CharacterState.EXECUTING
    slow.gate.set()
    done = await services.tasks.wait(task.id, 5)
    assert done.status is TaskStatus.COMPLETED and done.verified
    assert services.character.state is CharacterState.SUCCESS
    statuses, progresses = [], []
    while not sub.queue.empty():
        ev = sub.queue.get_nowait()
        if ev.type == "task.update" and ev.payload["taskId"] == task.id:
            statuses.append(ev.payload["status"])
            if "progress" in ev.payload:
                progresses.append(ev.payload["progress"])
    for s in ("REQUESTED", "PLANNED", "RUNNING", "VERIFYING", "COMPLETED"):
        assert s in statuses
    assert progresses and progresses[-1] == 1.0  # progress only came from the tool
    events = [e["kind"] for e in services.tasks.events(task.id)]
    assert events[0] == "created" and "finished" in events


async def test_failure_sets_error_state(services, tools):
    task = services.tasks.submit("test", "Fail", "x", [PlanStep("f", "test.fail", {})])
    done = await services.tasks.wait(task.id, 5)
    assert done.status is TaskStatus.FAILED and done.error == "it broke"
    assert services.character.state is CharacterState.ERROR


async def test_approval_approved_and_bound_to_action(services, tools):
    task = services.tasks.submit("test", "Risky", "x", [PlanStep("r", "test.risky", {"to": "bob"})], source="device:phone1")
    await asyncio.sleep(0.05)
    assert services.tasks.get(task.id).status is TaskStatus.AWAITING_APPROVAL
    assert services.character.state is CharacterState.CONCERNED
    (appr,) = services.tasks.pending_approvals()
    ok, msg = services.tasks.respond_approval(appr.id, True, "device:phone1")
    assert not ok and "Mac" in msg  # phones can't approve high risk by default
    ok, _ = services.tasks.respond_approval(appr.id, True, "local")
    assert ok
    done = await services.tasks.wait(task.id, 5)
    assert done.status is TaskStatus.COMPLETED and RiskyTool.runs == 1
    assert not done.verified  # unverifiable outcome is reported as such
    assert services.character.state is CharacterState.IDLE  # no success pose without verification


async def test_approval_denied(services, tools):
    task = services.tasks.submit("test", "Risky", "x", [PlanStep("r", "test.risky", {"to": "bob"})])
    await asyncio.sleep(0.05)
    (appr,) = services.tasks.pending_approvals()
    services.tasks.respond_approval(appr.id, False, "local")
    done = await services.tasks.wait(task.id, 5)
    assert done.status is TaskStatus.CANCELLED and RiskyTool.runs == 0


async def test_approval_timeout(services, tools):
    services.settings.security.approval_timeout_s = 0.1
    task = services.tasks.submit("test", "Risky", "x", [PlanStep("r", "test.risky", {"to": "bob"})])
    done = await services.tasks.wait(task.id, 5)
    assert done.status is TaskStatus.CANCELLED and RiskyTool.runs == 0


async def test_cancel_running_task(services, tools):
    task = services.tasks.submit("test", "Slow", "x", [PlanStep("s", "test.slow", {"steps": 1})])
    await asyncio.sleep(0.1)
    assert services.tasks.cancel(task.id)
    done = await services.tasks.wait(task.id, 5)
    assert done.status is TaskStatus.CANCELLED
    assert done.plan[0].status == "cancelled"


async def test_emergency_stop_cancels_and_blocks(services, tools):
    slow, _, _ = tools
    t1 = services.tasks.submit("test", "Slow", "x", [PlanStep("s", "test.slow", {"steps": 1})])
    t2 = services.tasks.submit("test", "Risky", "x", [PlanStep("r", "test.risky", {"to": "a"})])
    await asyncio.sleep(0.1)
    services.estop.engage("test")
    d1 = await services.tasks.wait(t1.id, 5)
    d2 = await services.tasks.wait(t2.id, 5)
    assert d1.status is TaskStatus.CANCELLED and d2.status is TaskStatus.CANCELLED
    assert RiskyTool.runs == 0
    with pytest.raises(EmergencyStopEngaged):
        services.tasks.submit("test", "Slow", "x", [PlanStep("s", "test.slow", {})])
    # approvals cannot be granted while engaged
    services.estop.reset()
    t3 = services.tasks.submit("test", "Risky", "x", [PlanStep("r", "test.risky", {"to": "a"})])
    await asyncio.sleep(0.05)
    (appr,) = services.tasks.pending_approvals()
    services.estop.engage("again")
    ok, _ = services.tasks.respond_approval(appr.id, True, "local")
    assert not ok
    assert (await services.tasks.wait(t3.id, 5)).status is TaskStatus.CANCELLED
    assert any(e["action"] == "estop.engage" for e in services.audit.recent())


async def test_unknown_tool_and_bad_args_fail(services, tools):
    t = services.tasks.submit("test", "x", "x", [PlanStep("bad", "no.such.tool", {})])
    assert (await services.tasks.wait(t.id, 5)).status is TaskStatus.FAILED
    t = services.tasks.submit("test", "x", "x", [PlanStep("bad", "test.slow", {"steps": "many"})])
    done = await services.tasks.wait(t.id, 5)
    assert done.status is TaskStatus.FAILED and "steps" in done.error


async def test_recovery_after_restart(settings, fake_desktop):
    from baymax.security.secrets import SecretStore
    from baymax.services import build_services

    s1 = build_services(settings, desktop=fake_desktop, secrets=SecretStore(settings.data_dir, use_keychain=False))
    slow = SlowTool()
    s1.tools.register(slow)
    task = s1.tasks.submit("test", "Slow", "x", [PlanStep("s", "test.slow", {"steps": 1})])
    await asyncio.sleep(0.1)
    # simulate a crash: drop the runner without finishing
    s1.tasks._runners[task.id].cancel()
    s1.tasks._finish = lambda *a, **k: None  # the "crash" prevents a clean finish
    await asyncio.sleep(0.05)
    s1.db.close()
    s2 = build_services(settings, desktop=fake_desktop, secrets=SecretStore(settings.data_dir, use_keychain=False))
    assert s2.tasks.recovered == 1
    t = s2.tasks.get(task.id)
    assert t.status is TaskStatus.FAILED and "restarted" in t.error
    await s2.close()
