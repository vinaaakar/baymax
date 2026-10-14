"""Environment report: what works on this machine right now, and why not."""

from __future__ import annotations

import platform
import sys
from typing import Any

from . import __version__
from .config import Settings
from .hostos.desktop import make_desktop
from .hostos.process import ProcessManager
from .hostos.tools import find_tool
from .images.providers import ImageService
from .llm.ollama import OllamaClient
from .voice.stt import WhisperCppRecognizer
from .voice.tts import Speaker


async def run_doctor(settings: Settings) -> dict[str, Any]:
    llm = OllamaClient(settings.llm)
    try:
        llm_status = (await llm.status()).to_dict()
    finally:
        await llm.close()
    pm = ProcessManager()
    desktop = make_desktop(pm)
    perms: dict[str, Any] = {}
    if hasattr(desktop, "accessibility_trusted"):
        perms["accessibility"] = desktop.accessibility_trusted()  # None = cannot tell (PyObjC missing)
    if hasattr(desktop, "screen_recording_allowed"):
        perms["screenRecording"] = desktop.screen_recording_allowed()
    try:
        perms["displays"] = [d.__dict__ for d in await desktop.displays()]
    except Exception as exc:  # not a Mac, or PyObjC missing
        perms["displays"] = f"unavailable: {exc}"
    from .research.tls import client_context

    return {
        "baymax": __version__,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "llm": llm_status,
        "stt": WhisperCppRecognizer(settings.voice, pm).status(),
        "tts": Speaker(settings.voice).status(),
        "images": await ImageService(settings.images).status(),
        "desktop": desktop.capabilities(),
        "macos": perms,
        "tools": {name: bool(find_tool(name)) for name in ("ollama", "whisper-cli", "say", "osascript", "ffmpeg", "git", "node", "npm", "xcodebuild", "xcodegen")},
        "research": {"localOnly": settings.research.local_only, "provider": settings.research.search_provider,
                     "tlsTrust": client_context()[1]},
        "dataDir": str(settings.data_dir),
        "workspace": str(settings.workspace_dir),
    }
