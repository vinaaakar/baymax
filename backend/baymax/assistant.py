"""Conversation orchestrator.

Text (typed, or transcribed by local STT) enters :meth:`Assistant.handle_text`.
The router picks an intent; tools run as tasks through the TaskManager; the
final reply for a task is produced only from the task's real result.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from .character.state_machine import CharacterEvent, CharacterState
from .intent.router import Intent, IntentRouter, RouteContext, parse_model_proposal
from .llm.ollama import LLMError, LLMUnavailable
from .llm.prompts import system_prompt, tool_proposal_prompt
from .memory.store import MemoryRejected
from .services import Services
from .tasks.manager import TaskRejected
from .tasks.models import PlanStep
from .voice.tts import TTSUnavailable

log = logging.getLogger(__name__)

CAPABILITIES = ("open apps and websites, control the mouse and keyboard (with macOS permissions), create and edit 3D models "
                "(cars, houses, bottles, chairs, drones, robotic arms, gears and more), research the web with citations, "
                "run simulations, write documents and diagrams, run and fix code tests, and remember preferences you approve")


@dataclass
class Origin:
    source: str  # "local" | "voice" | "device:<id>"
    speak: bool


class Assistant:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.router = IntentRouter(services.classifier)
        self.active_model: str | None = None
        self.sleeping = False
        self._origins: dict[str, Origin] = {}
        self._watch: asyncio.Task | None = None
        self._speech: asyncio.Task | None = None
        self.s.tts.on_state = self._on_tts_state

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._watch is None:
            self._watch = asyncio.create_task(self._watch_results(), name="assistant-results")

    async def stop(self) -> None:
        if self._watch:
            self._watch.cancel()
        if self._speech:
            self._speech.cancel()

    # ------------------------------------------------------------ voice/tts
    def _on_tts_state(self, state: str) -> None:
        self.s.bus.publish("voice.state", {"state": "speaking" if state == "speaking" else "idle", "source": "mac"})
        if state == "speaking":
            self.s.character.dispatch(CharacterEvent.SPEECH_STARTED, reason="speaking")
        else:
            self.s.character.dispatch(CharacterEvent.SPEECH_ENDED, reason="speech finished")
            if self.s.tasks.active():
                self.s.character.dispatch(CharacterEvent.TASK_STARTED, reason="task still running")

    def mic_state(self, state: str, source: str) -> None:
        """Clients report real microphone capture state."""
        self.s.bus.publish("voice.state", {"state": state, "source": source})
        if self.sleeping:
            return
        if state == "listening":
            self.stop_speaking()
            self.s.character.dispatch(CharacterEvent.MIC_STARTED, reason=f"microphone active ({source})")
        elif state == "transcribing":
            self.s.character.dispatch(CharacterEvent.REQUEST_RECEIVED, reason="transcribing")
        elif state in ("idle", "unavailable") and self.s.character.state is CharacterState.LISTENING:
            self.s.character.dispatch(CharacterEvent.MIC_STOPPED, reason="microphone stopped")

    def stop_speaking(self) -> bool:
        stopped = self.s.tts.stop()
        if self._speech and not self._speech.done():
            self._speech.cancel()
        return stopped

    def _speak(self, text: str) -> None:
        if not self.s.tts.status()["available"]:
            return
        self.stop_speaking()

        async def run() -> None:
            try:
                await self.s.tts.speak(text)
            except TTSUnavailable as exc:
                log.info("tts unavailable: %s", exc)
            except asyncio.CancelledError:
                pass

        self._speech = asyncio.create_task(run(), name="tts")

    # ----------------------------------------------------------------- sleep
    def set_sleep(self, sleeping: bool, actor: str) -> None:
        if sleeping == self.sleeping:
            return
        self.sleeping = sleeping
        if sleeping:
            self.stop_speaking()
            self.s.character.dispatch(CharacterEvent.SLEEP, reason=f"put to sleep by {actor}")
        else:
            self.s.character.dispatch(CharacterEvent.WAKE, reason=f"woken by {actor}")
        self.s.bus.publish("sleep.state", {"sleeping": sleeping})
        self.s.audit.record(actor, "sleep.set", "low", "ok", {"sleeping": sleeping})

    # ------------------------------------------------------------- messages
    def _reply(self, text: str, origin: Origin, task_id: str | None = None, role: str = "assistant") -> dict[str, Any]:
        msg = self.s.conversation.add(role, text, origin.source, task_id)
        speak = origin.speak and role == "assistant"
        self.s.bus.publish("assistant.message", {"text": text, "messageId": msg["id"], "speak": speak, "taskId": task_id or "",
                                                 "role": role, "source": origin.source})
        if speak and not origin.source.startswith("device:"):
            self._speak(text)  # phones speak locally with their own TTS
        return msg

    async def handle_text(self, text: str, source: str = "local", speak: bool | None = None) -> dict[str, Any]:
        text = text.strip()[:4000]
        if not text:
            return {"ok": False, "error": "empty"}
        origin = Origin(source, speak if speak is not None else source in ("voice",))
        intent = self.router.route(text, RouteContext(active_model=self.active_model))
        if self.sleeping and intent.kind not in ("wake", "estop"):
            notice = "I'm asleep. Say “wake up” or tap me to wake me."
            self.s.bus.publish("assistant.message", {"text": notice, "messageId": "sleep-notice", "speak": False, "role": "system", "taskId": ""})
            return {"ok": False, "sleeping": True, "reply": notice}
        self.s.conversation.add("user", text, source)
        self.s.bus.publish("user.message", {"text": text, "source": source})
        self.stop_speaking()
        self.s.character.dispatch(CharacterEvent.REQUEST_RECEIVED, reason="processing request")
        try:
            return await self._dispatch(intent, origin)
        except Exception as exc:  # pragma: no cover - last-resort guard, still honest
            log.exception("handle_text failed")
            self.s.character.dispatch(CharacterEvent.TASK_FAILED, reason=str(exc))
            msg = self._reply(f"Something went wrong while handling that: {exc}", origin)
            return {"ok": False, "error": str(exc), "message": msg}

    async def _dispatch(self, intent: Intent, origin: Origin) -> dict[str, Any]:
        k = intent.kind
        s = self.s
        if k == "estop":
            s.estop.engage(f"voice/text command from {origin.source}")
            return {"ok": True, "message": self._reply("Emergency Stop engaged. All controlled actions are stopped and blocked until you reset it on the Mac.", origin)}
        if k == "cancel":
            stopped = self.stop_speaking()
            n = s.tasks.cancel_all("cancelled by user")
            s.character.dispatch(CharacterEvent.TASK_CANCELLED, reason="cancelled")
            text = f"Cancelled {n} task{'s' if n != 1 else ''}." if n else ("Okay, I stopped talking." if stopped else "There was nothing running to cancel.")
            return {"ok": True, "message": self._reply(text, Origin(origin.source, False))}
        if k == "sleep":
            self.set_sleep(True, origin.source)
            return {"ok": True, "sleeping": True}
        if k == "wake":
            self.set_sleep(False, origin.source)
            return {"ok": True, "message": self._reply("I'm awake. How can I help?", origin)}
        if k == "help":
            s.character.dispatch(CharacterEvent.RESET)
            return {"ok": True, "message": self._reply(f"I can {CAPABILITIES}. Risky actions always ask for your approval first.", origin)}
        if k == "status":
            active = s.tasks.active()
            s.character.dispatch(CharacterEvent.RESET if not active else CharacterEvent.TASK_STARTED)
            text = ("Nothing is running." if not active else "Running: " + "; ".join(f"{t.title} ({t.status.value.lower()})" for t in active))
            return {"ok": True, "message": self._reply(text, origin)}
        if k == "memory_add":
            try:
                mem = s.memory.add(intent.args["content"])
            except MemoryRejected as exc:
                s.character.dispatch(CharacterEvent.WARNING_RAISED, reason="memory rejected")
                return {"ok": False, "message": self._reply(str(exc), origin)}
            s.audit.record(origin.source, "memory.add", "low", "ok", {"id": mem.id})
            s.bus.publish("memory.changed", {})
            s.character.dispatch(CharacterEvent.RESET)
            return {"ok": True, "message": self._reply(f"I'll remember that: “{mem.content}”. You can review or delete memories in Settings.", origin)}
        if k == "memory_list":
            mems = s.memory.list()
            s.character.dispatch(CharacterEvent.RESET)
            text = "I don't have any saved memories." if not mems else "Here's what I remember:\n" + "\n".join(f"• {m.content}" for m in mems)
            return {"ok": True, "message": self._reply(text, origin)}
        if k == "memory_forget":
            n = s.memory.delete_matching(intent.args["phrase"])
            s.bus.publish("memory.changed", {})
            s.audit.record(origin.source, "memory.forget", "low", "ok", {"deleted": n})
            s.character.dispatch(CharacterEvent.RESET)
            return {"ok": True, "message": self._reply(f"Forgot {n} memor{'y' if n == 1 else 'ies'}." if n else "I couldn't find a memory matching that.", origin)}
        if k == "memory_clear":
            n = s.memory.clear()
            s.bus.publish("memory.changed", {})
            s.audit.record(origin.source, "memory.clear", "low", "ok", {"deleted": n})
            s.character.dispatch(CharacterEvent.RESET)
            return {"ok": True, "message": self._reply(f"Deleted all {n} memories.", origin)}
        if k == "tool":
            return await self._start_tool(intent, origin)
        return await self._chat(intent, origin)

    async def _start_tool(self, intent: Intent, origin: Origin) -> dict[str, Any]:
        s = self.s
        tool = s.tools.get(intent.tool or "")
        if tool is None:
            return {"ok": False, "message": self._reply(f"I don't have a tool called {intent.tool}.", origin)}
        if s.estop.engaged:
            s.character.dispatch(CharacterEvent.WARNING_RAISED, reason="estop engaged")
            return {"ok": False, "message": self._reply("Emergency Stop is engaged, so I can't run actions. Reset it on the Mac first.", origin)}
        try:
            task = s.tasks.submit(intent.tool.split(".")[0], intent.title or tool.description[:60], intent.text or intent.title,
                                  [PlanStep(intent.title or tool.name, tool.name, intent.args)], source=origin.source)
        except TaskRejected as exc:
            return {"ok": False, "message": self._reply(str(exc), origin)}
        self._origins[task.id] = origin
        return {"ok": True, "taskId": task.id, "tool": tool.name}

    async def _chat(self, intent: Intent, origin: Origin) -> dict[str, Any]:
        s = self.s
        if intent.reply:
            s.character.dispatch(CharacterEvent.RESET)
            return {"ok": True, "message": self._reply(intent.reply, origin)}
        history = s.conversation.model_messages(12)
        memories = s.memory.contents()
        tools = {t.name: t for t in s.tools.model_tools()}
        messages = [{"role": "system", "content": system_prompt(memories) + "\n" + tool_proposal_prompt([t.schema() for t in tools.values()])}]
        messages += history
        try:
            reply = await s.llm.chat(messages, json_mode=True)
        except LLMUnavailable as exc:
            s.character.dispatch(CharacterEvent.WARNING_RAISED, reason="local model unavailable")
            return {"ok": False, "message": self._reply(
                f"I can't reach my local language model right now ({exc}). Commands still work — for example 'open Safari', "
                "'make a red car', 'simulate a pendulum'. I won't use a cloud service instead.", origin)}
        except LLMError as exc:
            s.character.dispatch(CharacterEvent.TASK_FAILED, reason=str(exc))
            return {"ok": False, "message": self._reply(f"The local model had a problem: {exc}", origin)}
        proposal = parse_model_proposal(reply, tools)
        if proposal.kind == "tool":
            proposal.text = intent.text
            self._reply(f"I'll use {proposal.tool} for that.", Origin(origin.source, False), role="assistant")
            return await self._start_tool(proposal, origin)
        if proposal.kind == "clarify":
            s.character.dispatch(CharacterEvent.CLARIFICATION_NEEDED, reason="clarification needed")
        else:
            s.character.dispatch(CharacterEvent.RESET)
        return {"ok": True, "message": self._reply(proposal.reply, origin)}

    # ---------------------------------------------------------- task results
    async def _watch_results(self) -> None:
        sub = self.s.bus.subscribe(maxsize=512)
        try:
            while True:
                ev = await sub.get()
                if ev.type != "task.result":
                    continue
                p = ev.payload
                origin = self._origins.pop(p["taskId"], Origin("local", False))
                task = self.s.tasks.get(p["taskId"])
                self._track_model(task)
                self._reply(self._result_text(p, task), origin, task_id=p["taskId"])
        except asyncio.CancelledError:
            pass
        finally:
            sub.close()

    def _track_model(self, task) -> None:
        if task is None or not task.result:
            return
        for art in task.result.get("artifacts") or []:
            if art.get("kind") == "model3d":
                self.active_model = art["id"]

    @staticmethod
    def _result_text(p: dict[str, Any], task) -> str:
        status = p["status"]
        summary = p.get("summary") or ""
        if status == "COMPLETED":
            return summary if p.get("verified") else f"{summary} (I couldn't verify the outcome.)"
        if status == "CANCELLED":
            return f"Stopped: {summary}" if summary and summary != "cancelled" else "Cancelled."
        detail = summary or (task.error if task else "") or "unknown error"
        if task and task.result and task.result.get("needsClarification"):
            return detail
        return f"That didn't work: {detail}"

    def snapshot(self) -> dict[str, Any]:
        return {"sleeping": self.sleeping, "activeModel": self.active_model, "character": self.s.character.snapshot()}


def to_json(obj: Any) -> str:
    return json.dumps(obj, default=str)
