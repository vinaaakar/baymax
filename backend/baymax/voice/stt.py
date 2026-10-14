"""Local speech recognition through whisper.cpp (``whisper-cli``).

Raw audio never leaves the Mac. If whisper.cpp or its model is missing the
recognizer reports itself unavailable with installation instructions.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import VoiceConfig
from ..hostos.process import ProcessManager
from .audio import PCM, AudioError, detect_speech, read_wav, resample, trim, write_wav


class STTUnavailable(Exception):
    pass


@dataclass
class Transcript:
    text: str
    duration_s: float
    speech_detected: bool
    engine: str


_NOISE = re.compile(r"\[(?:BLANK_AUDIO|MUSIC|NOISE|SILENCE|inaudible)[^\]]*\]|\((?:silence|music|noise)\)", re.I)


class WhisperCppRecognizer:
    def __init__(self, config: VoiceConfig, processes: ProcessManager) -> None:
        self.config = config
        self.processes = processes

    def binary(self) -> str | None:
        for name in (self.config.whisper_binary, "whisper-cli", "whisper-cpp"):
            found = shutil.which(name) if name else None
            if found:
                return found
        return None

    def model_path(self) -> Path:
        return Path(self.config.whisper_model).expanduser()

    def status(self) -> dict:
        if self.config.stt_engine == "none":
            return {"available": False, "engine": "none", "message": "speech recognition disabled in settings"}
        binary = self.binary()
        model = self.model_path()
        if not binary:
            return {"available": False, "engine": "whisper.cpp",
                    "message": "whisper.cpp not found. Install with: brew install whisper-cpp"}
        if not model.exists():
            return {"available": False, "engine": "whisper.cpp",
                    "message": f"whisper model missing at {model}. See docs/SETUP.md (download ggml-base.en.bin)."}
        return {"available": True, "engine": "whisper.cpp", "binary": binary, "model": str(model), "message": "ready"}

    async def transcribe(self, wav_bytes: bytes) -> Transcript:
        st = self.status()
        pcm = read_wav(wav_bytes, self.config.max_audio_seconds)
        has_speech, start, end = detect_speech(pcm)
        if not has_speech:
            return Transcript("", pcm.duration, False, "vad")
        if not st["available"]:
            raise STTUnavailable(st["message"])
        clip: PCM = resample(trim(pcm, start, end), 16000)
        with tempfile.TemporaryDirectory(prefix="baymax-stt-") as tmp:
            wav_path = Path(tmp) / "in.wav"
            wav_path.write_bytes(write_wav(clip))
            argv = [st["binary"], "-m", st["model"], "-f", str(wav_path), "-nt", "-np", "-l", "auto" if not st["model"].endswith(".en.bin") else "en"]
            res = await self.processes.run(argv, timeout=self.config.stt_timeout_s)
        if not res.ok:
            raise STTUnavailable(f"whisper.cpp failed (exit {res.returncode}): {res.stderr[-300:]}")
        text = _NOISE.sub("", res.stdout).strip()
        text = " ".join(text.split())
        return Transcript(text, pcm.duration, bool(text), "whisper.cpp")


__all__ = ["WhisperCppRecognizer", "Transcript", "STTUnavailable", "AudioError"]
