"""Conversation orchestrator.

Text (typed, or transcribed by local STT) enters :meth:`Assistant.handle_text`.
The router picks an intent; tools run as tasks through the TaskManager; the
final reply for a task is produced only from the task's real result.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import logging
from dataclasses import dataclass
from typing import Any

from .character.state_machine import CharacterEvent, CharacterState
from .capabilities import capability_report, capability_text
from .intent import trace as rtrace
from .hostos.sites import browser_named_in, ground_destination
from .buildinfo import build_status
from .tasks.refs import check_plan, is_ref, literal_path
from .intent.relevance import request_mode, tool_is_relevant
from .intent.router import LAST_DRAFT, Intent, IntentRouter, RouteContext, parse_model_proposal, split_compound
from .llm.ollama import LLMError, LLMUnavailable
from .llm.ollama import ToolsUnsupported, tool_spec
from .llm.prompts import NATIVE_TOOLS_HINT, claims_unperformed_action, honest_no_action, system_prompt, tool_proposal_prompt
from .memory.facts import Fact, fact_from_stored, find_fact, parse_remember
from .memory.store import MemoryRejected
from .services import Services
from .security.policy import HIGH_IMPACT_CATEGORIES
from .tasks.manager import TaskRejected
from .tasks.models import PlanStep
from .tools.base import ArgumentError
from .voice.stt import clean_transcript
from .voice.tts import TTSUnavailable

log = logging.getLogger(__name__)

DUPLICATE_WINDOW_S = 10.0  # the same action, with the same arguments, from the same source, is not started twice in this window


@dataclass
class Origin:
    source: str  # "local" | "voice" | "device:<id>"
    speak: bool


class Assistant:
    def __init__(self, services: Services) -> None:
        self.s = services
        self._native_tools_unsupported = False
        self._outdated_warned = ""  # build pair already announced as outdated
        self.router = IntentRouter(services.classifier)
        self.active_model: str | None = None
        self.sleeping = False
        self._origins: dict[str, Origin] = {}
        self._watch: asyncio.Task | None = None
        self._speech: asyncio.Task | None = None
        self._transcribing = False  # set while a client transcribes; cleared once text arrives
        self.traces = rtrace.TraceLog()
        self.last_draft: str | None = None  # the latest post the user asked me to draft (for "publish it")
        self.session_facts: list[Fact] = []  # "remember … for this conversation" (in memory only, never saved to disk)
        self._recent_starts: dict[str, float] = {}  # duplicate-execution guard: action fingerprint -> start time
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
            self._transcribing = True
            self.s.character.dispatch(CharacterEvent.REQUEST_RECEIVED, reason="transcribing")
        elif state in ("idle", "unavailable"):
            if self.s.character.state is CharacterState.LISTENING:
                self.s.character.dispatch(CharacterEvent.MIC_STOPPED, reason="microphone stopped")
            elif self._transcribing and self.s.character.state is CharacterState.THINKING and not self.s.tasks.active():
                # transcription ended without producing a request (e.g. nothing was said)
                self.s.character.dispatch(CharacterEvent.RESET, reason="nothing to process")
            self._transcribing = False

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
        self.s.cursor.sleeping = sleeping  # sleep suspends cursor relaying; wake restores it
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
        t = rtrace.current()
        if t is not None and not task_id:
            t.result = text
            t.stage("response", "ok")
        speak = origin.speak and role == "assistant"
        self.s.bus.publish("assistant.message", {"text": text, "messageId": msg["id"], "speak": speak, "taskId": task_id or "",
                                                 "role": role, "source": origin.source})
        if speak and not origin.source.startswith("device:"):
            self._speak(text)  # phones speak locally with their own TTS
        return msg

    # ---------------------------------------------------------- "which file?" follow-ups
    def _remember_file_choice(self, task: Any, origin: Origin) -> None:
        """A plan stopped because several files matched: keep the remaining steps and the candidates (5 minutes) so the
        user's answer ("number 2") resumes them with that file. Never guessed; the answer is re-checked."""
        res = (task.result or {}) if task else {}
        data = res.get("data") or {}
        if data.get("errorKind") != "ambiguous_reference" or not data.get("candidates"):
            return
        k = next((n for n, st in enumerate(task.plan) if st.status == "skipped"), None)
        j = data.get("fromStep")
        if k is None or not isinstance(j, int):
            return
        rest = [(st.title, st.tool, dict(st.args)) for st in task.plan[k:]]
        for _, _, args in rest:  # only references to the ambiguous step (or to later steps) can be carried over
            if any(is_ref(v) and v.get("$from") != j and v.get("$from", -1) < k for v in args.values()):
                return
        self._file_choice = {"candidates": list(data["candidates"]), "from": j, "start": k, "steps": rest,
                             "expires": time.monotonic() + 300, "source": origin.source}

    async def _answer_file_choice(self, text: str, origin: Origin) -> dict[str, Any] | None:
        pending = getattr(self, "_file_choice", None)
        if not pending or time.monotonic() > pending["expires"] or origin.source != pending["source"]:
            return None
        t = " ".join(text.lower().split()).strip(" .!?")
        if t in ("cancel", "none", "neither", "never mind", "nevermind", "stop"):
            self._file_choice = None
            return {"ok": True, "message": self._reply("OK, I won't use any of those files.", origin)}
        words = {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3, "four": 4, "fourth": 4, "five": 5,
                 "fifth": 5, "six": 6, "sixth": 6, "seven": 7, "seventh": 7, "eight": 8, "eighth": 8}
        cands = pending["candidates"]
        m = re.fullmatch(r"(?:(?:use|open|pick|choose)\s+)?(?:the\s+)?(?:number\s+|no\.?\s*|#)?(\d+|[a-z]+)(?:\s+(?:one|file))?", t)
        n = None
        if m:
            g = m.group(1)
            n = int(g) if g.isdigit() else words.get(g)
        chosen = cands[n - 1] if n and 1 <= n <= len(cands) else next((c for c in cands if c.lower() == text.strip().lower()), None)
        if chosen is None:
            return None  # not an answer to the question: handle as a new request
        self._file_choice = None
        path = literal_path(chosen)  # re-checked: it may have moved or changed since it was listed
        if path is None:
            return {"ok": False, "message": self._reply("That file is no longer available (moved, deleted or outside your home "
                                                        "folder), so I didn't use it.", origin)}
        j, k = pending["from"], pending["start"]
        steps = []
        for title, tool, args in pending["steps"]:
            new = {}
            for key, v in args.items():
                if is_ref(v) and v.get("$from") == j:
                    new[key] = path
                elif is_ref(v):
                    new[key] = {**v, "$from": v["$from"] - k}
                else:
                    new[key] = v
            steps.append(Intent("tool", tool, new, title, text=text))
        return await self._start_plan(steps, text, origin)

    def _warn_if_outdated(self) -> None:
        """Tell the user (Mac and iPhone) once per outdated build that this backend runs older code than what is on
        disk, so test results are not mistaken for the current version."""
        b = build_status()
        key = f"{b['build']}->{b['diskBuild']}"
        if not b["stale"] or key == self._outdated_warned:
            return
        self._outdated_warned = key
        log.warning("backend is running outdated code: running %s, on disk %s", b["build"], b["diskBuild"])
        self.s.bus.publish("assistant.message", {
            "text": (f"Heads-up: I'm running older code (build {b['build']}) than what's on disk (build {b['diskBuild']}). "
                     "Restart BAYMAX (menu bar → Restart Backend) so the update takes effect; until then results reflect the old version."),
            "messageId": f"outdated-{key}", "speak": False, "role": "system", "taskId": ""})

    async def handle_text(self, text: str, source: str = "local", speak: bool | None = None, voice: bool | None = None) -> dict[str, Any]:
        text = text.strip()[:4000]
        self._transcribing = False
        voice = source == "voice" if voice is None else voice
        trace, token = self.traces.begin(text, source, voice)
        try:
            return await self._handle(trace, text, source, speak, voice)
        finally:
            self.traces.end(token)

    async def _handle(self, trace: rtrace.RequestTrace, text: str, source: str, speak: bool | None, voice: bool) -> dict[str, Any]:
        self._warn_if_outdated()
        trace.stage("input", "ok" if text else "rejected", source)
        if voice:
            # Spoken input (Mac whisper or the iPhone's on-device recognizer): sound tags and silence hallucinations
            # are not speech and never become requests.
            cleaned = clean_transcript(text)
            if not cleaned:
                trace.stage("speech_validation", "rejected", "no valid speech in the transcript")
                self.s.character.dispatch(CharacterEvent.RESET, reason="no speech detected")
                self.s.bus.publish("voice.state", {"state": "idle", "source": source})
                return {"ok": False, "discarded": True, "error": "no valid speech"}
            text = cleaned
            trace.stage("speech_validation", "ok")
        else:
            trace.stage("speech_validation", "skipped", "typed text")
        if not text:
            return {"ok": False, "error": "empty"}
        origin = Origin(source, speak if speak is not None else source in ("voice",))
        choice = await self._answer_file_choice(text, origin)
        if choice is not None:
            return choice
        intent = self.router.route(text, RouteContext(active_model=self.active_model))
        plan = None
        if intent.kind == "chat" and not intent.reply or (intent.tool == "apps.open" and re.search(r"\b(and|then)\b", str(intent.args.get("app", "")).lower())):
            plan = split_compound(self.router, text, RouteContext(active_model=self.active_model))
        if plan:
            intent = Intent("plan", text=text)
        trace.intent = intent.kind if intent.kind != "tool" else f"tool:{intent.tool}"
        trace.stage("intent", "ok", trace.intent + (" (rule)" if intent.source != "model" else ""))
        if self.sleeping and intent.kind not in ("wake", "estop"):
            notice = "I'm asleep. Say “wake up” or tap me to wake me."
            self.s.bus.publish("assistant.message", {"text": notice, "messageId": "sleep-notice", "speak": False, "role": "system", "taskId": ""})
            return {"ok": False, "sleeping": True, "reply": notice}
        self.s.conversation.add("user", text, source)
        self.s.bus.publish("user.message", {"text": text, "source": source})
        self.stop_speaking()
        self.s.character.dispatch(CharacterEvent.REQUEST_RECEIVED, reason="processing request")
        try:
            if plan:
                return await self._start_plan(plan, text, origin)
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
        if k == "greeting":
            s.character.dispatch(CharacterEvent.RESET)
            asks_hearing = bool(re.search(r"\b(hear|listen|listening)\b", intent.text, re.I))
            if origin.source == "voice":
                text = f"Yes, I can hear you. I understood: “{intent.text}”. What would you like me to do?"
            elif asks_hearing:  # typed: report the real voice pipeline, not a pleasantry
                stt = s.stt.status()
                voice = ("Push-to-talk is ready (speech is transcribed locally with whisper.cpp)." if stt.get("available")
                         else f"Voice input isn't available right now: {stt.get('message', 'speech recognition is not set up')}.")
                text = f"I'm reading what you type. {voice}"
            else:
                text = "Hello, I'm here. What would you like me to do? (Ask “what can you do?” for ideas.)"
            return {"ok": True, "message": self._reply(text, origin)}
        if k == "help":
            s.character.dispatch(CharacterEvent.RESET)
            return {"ok": True, "message": self._reply(capability_text(await capability_report(s)), origin)}
        if k == "status":
            active = s.tasks.active()
            s.character.dispatch(CharacterEvent.RESET if not active else CharacterEvent.TASK_STARTED)
            hub = s.extras.get("hub")
            phones = hub.connected() if hub is not None else []
            phone = (f"Your iPhone is connected ({', '.join(d.get('name') or d.get('deviceId', 'device') for d in phones)})."
                     if phones else "No iPhone is connected right now." if hub is not None else "The iPhone link isn't running.")
            work = "Nothing is running." if not active else "Running: " + "; ".join(f"{t.title} ({t.status.value.lower()})" for t in active)
            return {"ok": True, "message": self._reply(f"{phone} {work}", origin)}
        if k == "memory_add":
            return self._remember(intent.args.get("raw") or intent.text, intent.args["content"], origin)
        if k == "memory_recall":
            fact = self._recall(intent.args["label"])
            if fact is None:  # nothing stored under that name: answer normally (the model may know from the conversation)
                return await self._chat(Intent("chat", text=intent.text), origin)
            s.character.dispatch(CharacterEvent.RESET)
            return {"ok": True, "message": self._reply(fact.sentence()[:1].upper() + fact.sentence()[1:] + ".", origin)}
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
        if k == "draft":
            self.last_draft = intent.args["text"]
            s.character.dispatch(CharacterEvent.RESET)
            return {"ok": True, "message": self._reply(
                f"Here's your draft post:\n“{self.last_draft}”\nNothing has been posted. Say “post it on X” and I'll put it in X's composer "
                "and ask you to confirm before publishing.", origin)}
        if k == "tool":
            if intent.args.get("prompt") == LAST_DRAFT:
                if not self.last_draft:
                    s.character.dispatch(CharacterEvent.CLARIFICATION_NEEDED, reason="no draft")
                    return {"ok": True, "message": self._reply("What should the post say? (Ask me to draft it first, or tell me the text.)", origin)}
                intent.args = {**intent.args, "prompt": self.last_draft}
            return await self._start_tool(intent, origin)
        return await self._chat(intent, origin)

    def _recall(self, label: str) -> Fact | None:
        """Conversation facts first (most recent wins), then saved memories written as 'label: value'."""
        fact = find_fact(label, self.session_facts)
        if fact is None:
            stored = [f for f in (fact_from_stored(c) for c in self.s.memory.contents()) if f is not None]
            fact = find_fact(label, stored)
        return fact

    def _remember(self, raw: str, content: str, origin: Origin) -> dict[str, Any]:
        s = self.s
        req = parse_remember(raw)
        saved: list[str] = []
        scope = req.scope
        note = ""
        items = [f.stored() for f in req.facts] + ([req.other] if req.other else [])
        if not items:
            s.character.dispatch(CharacterEvent.CLARIFICATION_NEEDED, reason="nothing to remember")
            return {"ok": True, "message": self._reply("What should I remember?", origin)}
        if scope == "persistent":
            try:
                for item in items:
                    mem = s.memory.add(item)
                    saved.append(mem.content)
                    s.audit.record(origin.source, "memory.add", "low", "ok", {"id": mem.id})
                s.bus.publish("memory.changed", {})
            except MemoryRejected as exc:
                s.character.dispatch(CharacterEvent.WARNING_RAISED, reason="memory rejected")
                return {"ok": False, "message": self._reply(str(exc), origin)}
            except Exception as exc:  # storage unavailable: keep it for this conversation and say so
                log.warning("memory store unavailable: %s", exc)
                scope, note = "conversation", " I couldn't save it permanently (memory storage is unavailable), so I'll keep it for this conversation only."
        if scope == "conversation":
            self.session_facts.extend(req.facts)
            if req.other:
                self.session_facts.append(Fact("note", req.other))
        s.character.dispatch(CharacterEvent.RESET)
        if req.facts:
            what = "; ".join(f.sentence() for f in req.facts)
            what = what[:1].upper() + what[1:] + "."
        else:
            what = f"I'll remember: “{req.other}”."
        where = (" I'll keep that for this conversation only — it isn't saved." if scope == "conversation" and not note
                 else note or " Saved — you can review or delete memories in Settings.")
        text = f"Got it. {what}{where}"
        if req.question:
            from .memory.facts import asked_label
            label = asked_label(req.question)
            fact = self._recall(label) if label else None
            if fact is not None:
                text += f"\n{fact.sentence()[:1].upper() + fact.sentence()[1:]}."
        return {"ok": True, "message": self._reply(text, origin)}

    async def _start_plan(self, steps: list[Intent], text: str, origin: Origin) -> dict[str, Any]:
        """Several actions in one request ('open Safari and then open Terminal'): one task, steps run in order, and the
        task stops at the first step that fails (later steps never run on a broken state)."""
        s = self.s
        rtrace.stage("tool_selection", "ok", "plan: " + " → ".join(i.tool or "" for i in steps))
        if any(s.tools.get(i.tool or "") is None for i in steps):
            return {"ok": False, "message": self._reply("I can't do one of those steps yet.", origin)}
        try:  # every step's arguments, including references to earlier steps' outputs (tasks/refs.py)
            cleaned = check_plan([(i.tool or "", i.args) for i in steps], s.tools)
        except ArgumentError as exc:
            rtrace.stage("argument_validation", "rejected", str(exc))
            bad = next((s.tools.get(i.tool or "") for i in steps), None)
            return {"ok": True, "message": self._reply(friendly_argument_error(bad, exc), origin)}
        plan = [PlanStep(i.title or i.tool or "", i.tool or "", clean, depends_on_previous=i.depends_on_previous)
                for i, clean in zip(steps, cleaned)]
        rtrace.stage("argument_validation", "ok", f"{len(plan)} steps")
        if s.estop.engaged:
            rtrace.stage("permissions", "rejected", "Emergency Stop engaged")
            s.character.dispatch(CharacterEvent.WARNING_RAISED, reason="estop engaged")
            return {"ok": False, "message": self._reply("Emergency Stop is engaged, so I can't run actions. Reset it on the Mac first.", origin)}
        try:
            task = s.tasks.submit("plan", " then ".join(p.title for p in plan)[:120], text, plan, source=origin.source)
        except TaskRejected as exc:
            rtrace.stage("permissions", "rejected", str(exc))
            return {"ok": False, "message": self._reply(str(exc), origin)}
        rtrace.stage("permissions", "ok", "per-step policy checks run before each step")
        rtrace.stage("execution", "ok", f"task {task.id} started ({len(plan)} steps)")
        t = rtrace.current()
        if t is not None:
            t.tool = "plan"
            self.traces.link_task(t, task.id)
        self._origins[task.id] = origin
        return {"ok": True, "taskId": task.id, "tool": "plan"}

    async def _start_tool(self, intent: Intent, origin: Origin) -> dict[str, Any]:
        s = self.s
        tool = s.tools.get(intent.tool or "")
        t = rtrace.current()
        if tool is None:
            rtrace.stage("tool_selection", "rejected", f"unknown tool {intent.tool}")
            return {"ok": False, "message": self._reply("I can't do that kind of action yet.", origin)}
        rtrace.stage("tool_selection", "ok", f"{tool.name} ({'rule' if intent.source != 'model' else 'model proposal'})")
        try:
            clean = tool.validate(intent.args)
        except ArgumentError as exc:
            rtrace.stage("argument_validation", "rejected", str(exc))
            s.character.dispatch(CharacterEvent.CLARIFICATION_NEEDED, reason="incomplete request")
            return {"ok": True, "message": self._reply(friendly_argument_error(tool, exc), origin)}
        if intent.source == "model" and tool.name == "apps.open_url":
            # A model-proposed address must come from the user's words (a 3B model invented "chatgpt.ai").
            grounded = ground_destination(intent.text, str(clean.get("url", "")))
            if grounded is None:
                rtrace.stage("argument_validation", "rejected", f"website not in the request: {str(clean.get('url'))[:80]}")
                s.character.dispatch(CharacterEvent.CLARIFICATION_NEEDED, reason="unclear website")
                return {"ok": True, "message": self._reply("Which website should I open? Say its name or address.", origin)}
            clean = {**clean, "url": grounded, "browser": clean.get("browser") or browser_named_in(intent.text) or ""}
        rtrace.stage("argument_validation", "ok")
        if t is not None:
            t.tool, t.args = tool.name, clean
            t.app = clean.get("app") or clean.get("browser") or None
            t.url = clean.get("url") or None
            t.permissions = list(getattr(tool, "permissions", ()))
            risk, cats = tool.classify(clean)
            t.confirmation_required = risk.value == "high" or bool(set(cats) & HIGH_IMPACT_CATEGORIES)
        if s.estop.engaged:
            rtrace.stage("permissions", "rejected", "Emergency Stop engaged")
            s.character.dispatch(CharacterEvent.WARNING_RAISED, reason="estop engaged")
            return {"ok": False, "message": self._reply("Emergency Stop is engaged, so I can't run actions. Reset it on the Mac first.", origin)}
        fp = json.dumps([tool.name, clean, origin.source], sort_keys=True, default=str)
        now = asyncio.get_running_loop().time()
        dup = next((tk for tk in s.tasks.active() if tk.plan and tk.plan[0].tool == tool.name and tk.plan[0].args == clean), None)
        if dup is not None and now - self._recent_starts.get(fp, -1e9) < DUPLICATE_WINDOW_S:
            rtrace.stage("execution", "skipped", f"identical action already running ({dup.id})")
            return {"ok": True, "taskId": dup.id, "tool": tool.name, "duplicate": True}
        self._recent_starts[fp] = now
        try:
            task = s.tasks.submit(intent.tool.split(".")[0], intent.title or tool.description[:60], intent.text or intent.title,
                                  [PlanStep(intent.title or tool.name, tool.name, clean)], source=origin.source)  # the checked arguments
        except TaskRejected as exc:
            rtrace.stage("permissions", "rejected", str(exc))
            return {"ok": False, "message": self._reply(str(exc), origin)}
        rtrace.stage("permissions", "ok", "approval required before running" if t and t.confirmation_required else "allowed by policy")
        rtrace.stage("execution", "ok", f"task {task.id} started")
        if t is not None:
            self.traces.link_task(t, task.id)
        self._origins[task.id] = origin
        return {"ok": True, "taskId": task.id, "tool": tool.name}

    async def _chat(self, intent: Intent, origin: Origin) -> dict[str, Any]:
        s = self.s
        if intent.reply:
            s.character.dispatch(CharacterEvent.RESET)
            return {"ok": True, "message": self._reply(intent.reply, origin)}
        history = s.conversation.model_messages(12)
        memories = s.memory.contents() + [f"(this conversation) {f.stored()}" for f in self.session_facts]
        # Constrained tool selection: the model only sees tools whose purpose matches this request. Ordinary
        # questions ("what is Newton's third law?") are answered with no tools offered at all.
        tools = {t.name: t for t in s.tools.model_tools() if tool_is_relevant(t.name, intent.text)}
        rtrace.stage("context", "ok", f"{len(history)} recent messages, {len(memories)} memories")
        if not tools:
            rtrace.stage("tool_selection", "skipped", "no tool is relevant: answering conversationally")
            return await self._plain_chat(memories, history, origin, intent.text)
        rtrace.stage("tool_selection", "ok", "offered to the model: " + ", ".join(sorted(tools)))
        if s.settings.llm.native_tools and not self._native_tools_unsupported:
            try:
                native = await s.llm.chat_tools([{"role": "system", "content": system_prompt(memories) + "\n" + NATIVE_TOOLS_HINT}] + history,
                                                [tool_spec(t.schema()) for t in tools.values()])
            except ToolsUnsupported:
                self._native_tools_unsupported = True  # e.g. a model without tool support: use JSON proposals instead
            except LLMUnavailable as exc:
                return self._llm_unavailable(exc, origin)
            except LLMError as exc:
                s.character.dispatch(CharacterEvent.TASK_FAILED, reason=str(exc))
                return {"ok": False, "message": self._reply(f"The local model had a problem: {exc}", origin)}
            else:
                if native.tool_calls and not tool_is_relevant(native.tool_calls[0].name, intent.text):
                    log.info("model picked %s for an unrelated request; answering without tools", native.tool_calls[0].name)
                    rtrace.stage("tool_selection", "rejected", f"model proposed unrelated {native.tool_calls[0].name}")
                    return await self._plain_chat(memories, history, origin, intent.text)
                if native.tool_calls:
                    call = native.tool_calls[0]
                    if call.name not in tools:
                        rtrace.stage("tool_selection", "rejected", f"model proposed unavailable {call.name[:60]}")
                        return await self._plain_chat(memories, history, origin, intent.text)
                    proposal = Intent("tool", call.name, call.arguments, call.name, source="model", text=intent.text)
                    return await self._start_tool(proposal, origin)
                return await self._model_text_reply(native.content, tools, intent, origin)
        messages = [{"role": "system", "content": system_prompt(memories) + "\n" + tool_proposal_prompt([t.schema() for t in tools.values()])}]
        messages += history
        try:
            reply = await s.llm.chat(messages, json_mode=True)
        except LLMUnavailable as exc:
            return self._llm_unavailable(exc, origin)
        except LLMError as exc:
            s.character.dispatch(CharacterEvent.TASK_FAILED, reason=str(exc))
            return {"ok": False, "message": self._reply(f"The local model had a problem: {exc}", origin)}
        return await self._proposal_reply(reply, tools, intent, origin)

    def _no_false_claims(self, text: str, request: str) -> str:
        """No tool ran for this reply. If it claims an action anyway: for an action request replace it with an honest
        statement (the claimed result was invented); for a question, keep the answer and add a note."""
        if not claims_unperformed_action(text):
            return text
        rtrace.stage("response", "corrected", "model reply claimed an action that no tool performed")
        if request and request_mode(request) == "action":
            return honest_no_action(request)
        return text + "\n\n(Note: no tool ran for this message, so no action was actually performed.)"

    async def _plain_chat(self, memories: list[str], history: list[dict[str, str]], origin: Origin, request: str = "") -> dict[str, Any]:
        """Conversational answer with no tools offered (the model cannot trigger actions here)."""
        try:
            text = await self.s.llm.chat([{"role": "system", "content": system_prompt(memories)}] + history, max_tokens=400)
        except LLMUnavailable as exc:
            return self._llm_unavailable(exc, origin)
        except LLMError as exc:
            self.s.character.dispatch(CharacterEvent.TASK_FAILED, reason=str(exc))
            return {"ok": False, "message": self._reply(f"The local model had a problem: {exc}", origin)}
        text = self._no_false_claims(text.strip(), request)
        self.s.character.dispatch(CharacterEvent.RESET)
        return {"ok": True, "message": self._reply(text or "Could you say that another way?", origin)}

    def _llm_unavailable(self, exc: Exception, origin: Origin) -> dict[str, Any]:
        self.s.character.dispatch(CharacterEvent.WARNING_RAISED, reason="local model unavailable")
        return {"ok": False, "message": self._reply(
            f"I can't reach my local language model right now ({exc}). Commands still work — for example 'open Safari', "
            "'make a red car', 'simulate a pendulum'. I won't use a cloud service instead.", origin)}

    async def _model_text_reply(self, content: str, tools: dict[str, Any], intent: Intent, origin: Origin) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("{"):  # some small models answer with a JSON proposal even in native mode
            return await self._proposal_reply(text, tools, intent, origin)
        text = self._no_false_claims(text, intent.text)
        self.s.character.dispatch(CharacterEvent.RESET)
        return {"ok": True, "message": self._reply(text or "I'm not sure how to help with that. Could you say it another way?", origin)}

    async def _proposal_reply(self, reply: str, tools: dict[str, Any], intent: Intent, origin: Origin) -> dict[str, Any]:
        s = self.s
        proposal = parse_model_proposal(reply, tools)
        if proposal.kind == "tool" and not tool_is_relevant(proposal.tool or "", intent.text):
            log.info("model proposed %s for an unrelated request; answering without tools", proposal.tool)
            rtrace.stage("tool_selection", "rejected", f"model proposed unrelated {proposal.tool}")
            return await self._plain_chat(s.memory.contents(), s.conversation.model_messages(12), origin)
        if proposal.kind == "tool":
            proposal.text = intent.text
            return await self._start_tool(proposal, origin)
        if proposal.kind == "clarify":
            s.character.dispatch(CharacterEvent.CLARIFICATION_NEEDED, reason="clarification needed")
        else:
            s.character.dispatch(CharacterEvent.RESET)
        text = proposal.reply
        if proposal.kind == "chat":
            text = self._no_false_claims(text, intent.text)
        return {"ok": True, "message": self._reply(text, origin)}

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
                self._remember_file_choice(task, origin)
                self._track_model(task)
                text = self._dedupe_permission_help(self._result_text(p, task))
                t = self.traces.for_task(p["taskId"])
                if t is not None:
                    t.verified = bool(p.get("verified")) if p["status"] == "COMPLETED" else False
                    t.stage("verification", "ok" if t.verified else ("failed" if p["status"] != "COMPLETED" else "unverified"),
                            (task.result or {}).get("verification", "") if task and task.result else p["status"].lower())
                    t.result = text
                    t.stage("response", "ok", p["status"].lower())
                self._reply(text, origin, task_id=p["taskId"])
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

    def _dedupe_permission_help(self, text: str) -> str:
        """The full 'open System Settings → …' instructions once per permission per 10 minutes; afterwards a short note."""
        import time as _t
        m = re.search(r"(Open System Settings → Privacy & Security → ([^,.]+?)(?: and| ,|,|\.))", text)
        if not m:
            return text
        key = m.group(2).strip()
        told = getattr(self, "_told_permission", {})
        now = _t.time()
        if now - told.get(key, 0) < 600:
            text = text.replace(text[text.find("Open System Settings"):], f"(The {key} permission is still missing — see my earlier message.)")
        else:
            told[key] = now
        self._told_permission = told
        return text

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
        return detail[:1].upper() + detail[1:] if detail else "That didn't work."

    def snapshot(self) -> dict[str, Any]:
        return {"sleeping": self.sleeping, "activeModel": self.active_model, "character": self.s.character.snapshot()}


def friendly_argument_error(tool, exc: Exception) -> str:
    """What's missing, in plain words (never the internal tool name)."""
    msg = str(exc)
    m = re.search(r"missing argument '(\w+)'", msg)
    field = {"app": "which app", "url": "which website", "query": "what to look for", "path": "which file",
             "prompt": "what to ask", "service": "which AI service", "text": "what to type"}
    if m:
        return f"I need a bit more detail first: {field.get(m.group(1), m.group(1))}?"
    return "I couldn't work out the details of that request. Could you say it another way?"


def to_json(obj: Any) -> str:
    return json.dumps(obj, default=str)
