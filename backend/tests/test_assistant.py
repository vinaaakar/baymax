import asyncio

import pytest

from baymax.assistant import Assistant
from baymax.character.state_machine import CharacterState
from baymax.tasks.models import TaskStatus


@pytest.fixture
async def assistant(services):
    a = Assistant(services)
    a.start()
    yield a
    await a.stop()


async def last_message(services, sub, timeout=5.0):
    async def _wait():
        while True:
            ev = await sub.get()
            if ev.type == "assistant.message":
                return ev.payload
    return await asyncio.wait_for(_wait(), timeout)


async def test_open_app_verified_success(services, assistant, fake_desktop):
    sub = services.bus.subscribe()
    r = await assistant.handle_text("open Safari")
    task = await services.tasks.wait(r["taskId"], 5)
    assert task.status is TaskStatus.COMPLETED and task.verified
    assert ("open_app", "Safari") in fake_desktop.calls
    assert services.character.state is CharacterState.SUCCESS
    msg = await last_message(services, sub)
    assert "Safari is open" in msg["text"]


async def test_missing_permission_is_explained(services, assistant, fake_desktop):
    fake_desktop.accessibility = False
    sub = services.bus.subscribe()
    r = await assistant.handle_text("click at 10, 20")
    task = await services.tasks.wait(r["taskId"], 5)
    assert task.status is TaskStatus.FAILED and "Accessibility" in task.error
    assert services.character.state is CharacterState.ERROR
    assert "Accessibility" in (await last_message(services, sub))["text"]
    assert not any(c[0] == "click" for c in fake_desktop.calls)


async def test_unknown_app_fails_honestly(services, assistant):
    r = await assistant.handle_text("open Nonexistent")
    task = await services.tasks.wait(r["taskId"], 5)
    assert task.status is TaskStatus.FAILED and "not found" in task.error


async def test_create_and_modify_3d_model(services, assistant):
    r = await assistant.handle_text("make a red sports car")
    task = await services.tasks.wait(r["taskId"], 10)
    assert task.status is TaskStatus.COMPLETED and task.verified
    await asyncio.sleep(0.05)
    assert assistant.active_model
    r2 = await assistant.handle_text("make the body blue")
    t2 = await services.tasks.wait(r2["taskId"], 10)
    assert t2.status is TaskStatus.COMPLETED, t2.error
    saved = services.models3d.load(assistant.active_model)
    assert saved.spec.params["body_color"] == "#1e5bd8" and saved.spec.version == 2
    r3 = await assistant.handle_text("export this as GLB")
    t3 = await services.tasks.wait(r3["taskId"], 10)
    assert t3.status is TaskStatus.COMPLETED and t3.result["data"]["export"]["validation"]["ok"]


async def test_unsupported_object_asks_instead_of_substituting(services, assistant):
    sub = services.bus.subscribe()
    r = await assistant.handle_text("create a 3D model of a giraffe")
    task = await services.tasks.wait(r["taskId"], 5)
    assert task.status is TaskStatus.FAILED
    assert services.character.state is CharacterState.CONFUSED
    msg = await last_message(services, sub)
    assert "giraffe" in msg["text"] and "won't substitute" in msg["text"]
    assert services.artifacts.list("model3d") == []


async def test_chat_without_local_model_is_honest(services, assistant):
    r = await assistant.handle_text("tell me a joke")
    assert not r["ok"] and "local language model" in r["message"]["text"] and "cloud" in r["message"]["text"]
    assert services.character.state is CharacterState.CONCERNED


async def test_memory_flow_and_secret_refusal(services, assistant):
    r = await assistant.handle_text("remember that I prefer metric units")
    assert r["ok"] and services.memory.contents() == ["I prefer metric units"]
    r = await assistant.handle_text("remember my password is hunter2")
    assert not r["ok"] and len(services.memory.list()) == 1
    r = await assistant.handle_text("what do you remember about me")
    assert "metric" in r["message"]["text"]
    r = await assistant.handle_text("forget that I prefer metric units")
    assert services.memory.list() == []


async def test_sleep_wake_and_estop_by_voice(services, assistant):
    await assistant.handle_text("go to sleep")
    assert services.character.state is CharacterState.SLEEPING
    r = await assistant.handle_text("open Safari")
    assert r.get("sleeping") and not services.tasks.active()
    await assistant.handle_text("wake up")
    assert services.character.state is CharacterState.IDLE
    await assistant.handle_text("emergency stop")
    assert services.estop.engaged
    r = await assistant.handle_text("open Safari")
    assert not r["ok"] and "Emergency Stop" in r["message"]["text"]


async def test_mic_state_drives_listening(services, assistant):
    assistant.mic_state("listening", "mac")
    assert services.character.state is CharacterState.LISTENING
    assistant.mic_state("idle", "mac")
    assert services.character.state is CharacterState.IDLE


async def test_simulation_and_diagram_produce_validated_artifacts(services, assistant):
    r = await assistant.handle_text("simulate a projectile at 25 m/s and 30 degrees")
    t = await services.tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.COMPLETED and t.verified
    art = services.artifacts.list("simulation")[0]
    assert art.meta["source"] == "simulated" and art.validated
    r = await assistant.handle_text("draw a flowchart: design -> print -> assemble -> test")
    t = await services.tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.COMPLETED


async def test_research_in_local_only_mode_does_not_claim_search(services, assistant):
    sub = services.bus.subscribe()
    r = await assistant.handle_text("research the history of Baymax")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED
    text = (await last_message(services, sub))["text"]
    assert "No online research was performed" in text


async def test_file_write_and_delete_needs_approval(services, assistant):
    services.tools.get("files.write")
    task = services.tasks.submit("files", "write", "w", [__import__("baymax.tasks.models", fromlist=["PlanStep"]).PlanStep(
        "w", "files.write", {"path": "notes/a.txt", "content": "hello"})])
    t = await services.tasks.wait(task.id, 5)
    assert t.status is TaskStatus.COMPLETED and t.verified
    assert (services.workspace.root / "notes" / "a.txt").read_text() == "hello"
    from baymax.tasks.models import PlanStep

    task = services.tasks.submit("files", "delete", "d", [PlanStep("d", "files.delete", {"path": "notes/a.txt"})])
    await asyncio.sleep(0.05)
    assert services.tasks.get(task.id).status is TaskStatus.AWAITING_APPROVAL
    (appr,) = services.tasks.pending_approvals()
    services.tasks.respond_approval(appr.id, True, "local")
    t = await services.tasks.wait(task.id, 5)
    assert t.status is TaskStatus.COMPLETED and not (services.workspace.root / "notes" / "a.txt").exists()


async def test_transcription_without_request_returns_to_idle(services, assistant):
    assistant.mic_state("listening", "device:x")
    assistant.mic_state("transcribing", "device:x")
    assert services.character.state is CharacterState.THINKING
    assistant.mic_state("idle", "device:x")
    assert services.character.state is CharacterState.IDLE
