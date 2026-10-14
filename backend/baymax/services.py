"""Service container: builds and wires every subsystem once."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .artifacts import ArtifactRegistry
from .character.controller import CharacterController
from .character.state_machine import CharacterEvent
from .config import Settings
from .creation3d.classifier import RequestClassifier
from .creation3d.modify import Modifier
from .creation3d.planner import Planner
from .creation3d.registry import GeneratorRegistry
from .creation3d.store import ModelStore
from .db import Database
from .events import EventBus
from .hostos.desktop import DesktopBackend, make_desktop
from .hostos.process import ProcessManager
from .images.providers import ImageService
from .llm.ollama import OllamaClient
from .memory.conversation import ConversationLog
from .memory.store import MemoryStore
from .presence.cursor import CursorRelay
from .research.web import WebClient
from .security.audit import AuditLog
from .security.estop import EmergencyStop
from .security.pairing import DeviceRegistry
from .security.policy import Policy
from .security.secrets import SecretStore
from .security.workspace import Workspace
from .tasks.manager import TaskManager
from .tools.creation_tools import all_creation_tools
from .tools.registry import ToolRegistry
from .tools.dev_tools import all_dev_tools
from .tools.delegate_tools import all_delegate_tools
from .tools.screen_tools import all_screen_tools
from .tools.system_tools import all_system_tools
from .voice.stt import WhisperCppRecognizer
from .voice.tts import Speaker

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    db: Database
    bus: EventBus
    audit: AuditLog
    estop: EmergencyStop
    policy: Policy
    secrets: SecretStore
    devices: DeviceRegistry
    workspace: Workspace
    processes: ProcessManager
    desktop: DesktopBackend
    llm: OllamaClient
    stt: WhisperCppRecognizer
    tts: Speaker
    memory: MemoryStore
    conversation: ConversationLog
    artifacts: ArtifactRegistry
    generators: GeneratorRegistry
    classifier: RequestClassifier
    planner: Planner
    modifier: Modifier
    models3d: ModelStore
    web: WebClient
    images: ImageService
    tools: ToolRegistry
    character: CharacterController = field(init=False)
    cursor: CursorRelay = field(init=False)
    tasks: TaskManager = field(init=False)
    extras: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)  # session context, e.g. current_project

    async def close(self) -> None:
        self.processes.kill_all()
        self.tts.stop()
        await self.llm.close()
        await self.web.close()
        self.db.close()


def build_services(settings: Settings, *, desktop: DesktopBackend | None = None, secrets: SecretStore | None = None) -> Services:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    bus = EventBus()
    processes = ProcessManager()
    secret_store = secrets or SecretStore(settings.data_dir)
    generators = GeneratorRegistry()
    planner = Planner(generators)
    artifacts = ArtifactRegistry(db, settings.artifacts_dir)
    workspace = Workspace(settings.workspace_dir)
    tools = ToolRegistry()
    for tool in all_system_tools() + all_screen_tools() + all_dev_tools() + all_creation_tools(workspace.root) + all_delegate_tools():
        tools.register(tool)
    s = Services(
        settings=settings, db=db, bus=bus, audit=AuditLog(db), estop=EmergencyStop(),
        policy=Policy(allow_desktop_control=settings.security.allow_desktop_control, hardware_enabled=settings.security.hardware_control_enabled),
        secrets=secret_store,
        devices=DeviceRegistry(db, secret_store, ttl_s=settings.network.pairing_ttl_s, max_attempts=settings.network.pairing_max_attempts),
        workspace=workspace, processes=processes, desktop=desktop or make_desktop(processes), llm=OllamaClient(settings.llm),
        stt=WhisperCppRecognizer(settings.voice, processes), tts=Speaker(settings.voice), memory=MemoryStore(db),
        conversation=ConversationLog(db), artifacts=artifacts, generators=generators, classifier=RequestClassifier(generators),
        planner=planner, modifier=Modifier(planner), models3d=ModelStore(settings.artifacts_dir, artifacts, planner),
        web=WebClient(settings.research), images=ImageService(settings.images), tools=tools,
    )
    s.character = CharacterController(bus, active_tasks=lambda: len(s.tasks.active()))
    s.cursor = CursorRelay(lambda payload: bus.publish("cursor", payload, record=False))
    s.tasks = TaskManager(s, character=s.character.dispatch)

    s.desktop.typing_interval_s = max(0, min(int(settings.security.typing_interval_ms), 500)) / 1000

    def on_estop(reason: str) -> None:
        # Deterministic stop path (no model involved): halt typing/clicks at the next event, cancel queued and
        # running tasks (and their retries), kill child processes, and withdraw any pointing indication.
        s.desktop.cancel_input()
        cancelled = s.tasks.cancel_all(f"Emergency Stop: {reason}")
        killed = processes.kill_all()
        s.tts.stop()
        s.bus.publish("gaze.target", {"kind": "clear", "x": 0.0, "y": 0.0})
        s.audit.record("estop", "estop.engage", "high", "engaged", {"reason": reason, "cancelled": cancelled, "killed": killed})

    def on_estop_change(engaged: bool, reason: str) -> None:
        s.bus.publish("estop.state", {"engaged": engaged, "reason": reason})
        if engaged:
            s.character.dispatch(CharacterEvent.WARNING_RAISED, reason="Emergency Stop engaged")
        else:
            s.audit.record("local", "estop.reset", "high", "reset", {})
            s.character.dispatch(CharacterEvent.RESET, reason="Emergency Stop reset")

    s.estop.on_engage(on_estop)
    s.estop.on_change(on_estop_change)
    if s.tasks.recovered:
        log.warning("closed %d task(s) interrupted by a previous shutdown", s.tasks.recovered)
    return s
