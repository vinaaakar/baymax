"""Local text-to-speech with real playback events.

* macOS: the built-in ``say`` command (offline, no network).
* Optional: Piper (``piper`` + a voice model) with ``afplay``/``aplay`` playback.

``on_state`` is called with ``"speaking"`` when the playback process has
actually started and ``"idle"`` when it exits or is interrupted, so the
SPEAKING animation tracks real audio playback.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Callable

from ..config import VoiceConfig

log = logging.getLogger(__name__)


class TTSUnavailable(Exception):
    pass


class Speaker:
    def __init__(self, config: VoiceConfig, on_state: Callable[[str], None] | None = None) -> None:
        self.config = config
        self.on_state = on_state or (lambda s: None)
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._interrupted = False

    def engine(self) -> str:
        e = self.config.tts_engine
        if e == "auto":
            if sys.platform == "darwin" and shutil.which("say"):
                return "say"
            if shutil.which(self.config.piper_binary) and self.config.piper_model:
                return "piper"
            return "none"
        return e

    def status(self) -> dict:
        e = self.engine()
        if e == "say":
            ok = bool(shutil.which("say"))
            return {"available": ok, "engine": "say", "message": "ready" if ok else "'say' not found (macOS only)"}
        if e == "piper":
            ok = bool(shutil.which(self.config.piper_binary)) and Path(self.config.piper_model).expanduser().exists()
            return {"available": ok, "engine": "piper", "message": "ready" if ok else "piper binary or voice model missing"}
        return {"available": False, "engine": "none",
                "message": "no local TTS engine (macOS 'say' is used automatically on a Mac)"}

    @property
    def speaking(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def speak(self, text: str) -> bool:
        """Speak text; returns True if playback completed, False if interrupted."""
        text = text.strip()
        if not text:
            return True
        st = self.status()
        if not st["available"]:
            raise TTSUnavailable(st["message"])
        async with self._lock:
            self._interrupted = False
            if st["engine"] == "say":
                argv = ["say", "-r", str(self.config.tts_rate_wpm)]
                if self.config.tts_voice:
                    argv += ["-v", self.config.tts_voice]
                argv += ["--", text[:4000]]
                return await self._play(argv)
            return await self._piper(text)

    async def _piper(self, text: str) -> bool:
        with tempfile.TemporaryDirectory(prefix="baymax-tts-") as tmp:
            out = Path(tmp) / "speech.wav"
            proc = await asyncio.create_subprocess_exec(
                self.config.piper_binary, "--model", str(Path(self.config.piper_model).expanduser()), "--output_file", str(out),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            await asyncio.wait_for(proc.communicate(text[:4000].encode()), timeout=60)
            if proc.returncode != 0 or not out.exists():
                raise TTSUnavailable("piper failed to synthesize speech")
            player = shutil.which("afplay") or shutil.which("aplay") or shutil.which("paplay")
            if not player:
                raise TTSUnavailable("no audio player (afplay/aplay) found")
            return await self._play([player, str(out)])

    async def _play(self, argv: list[str]) -> bool:
        self._proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.DEVNULL,
                                                          stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        self.on_state("speaking")
        try:
            await self._proc.wait()
        except asyncio.CancelledError:
            self.stop()
            raise
        finally:
            rc = self._proc.returncode if self._proc else None
            self._proc = None
            self.on_state("idle")
        return rc == 0 and not self._interrupted

    def stop(self) -> bool:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return False
        self._interrupted = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        return True
