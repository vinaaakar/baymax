"""Local speech recognition through whisper.cpp (``whisper-cli``).

Raw audio never leaves the Mac. If whisper.cpp or its model is missing the
recognizer reports itself unavailable with installation instructions.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import VoiceConfig
from ..hostos.process import ProcessManager
from ..hostos.tools import find_tool, find_whisper_model
from .audio import PCM, AudioError, detect_speech, read_wav, resample, trim, write_wav


class STTUnavailable(Exception):
    pass


@dataclass
class Transcript:
    text: str
    duration_s: float
    speech_detected: bool
    engine: str


# whisper.cpp annotates non-speech sounds: [BLANK_AUDIO], [GUNSHOT], [explosion], (music), *laughs*, ♪ ... ♪.
# None of these are words the user said, so they never reach the assistant.
_NOISE = re.compile(r"\[[^\]]{0,60}\]|\([^)]{0,40}\)|\*[^*]{0,40}\*|[♪♫]+[^♪♫]{0,80}[♪♫]*", re.I)


# Phrases whisper is known to produce from silence, breathing or background noise (not real requests).
_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thank you very much", "thank you for watching", "thanks for watching",
    "please subscribe", "subscribe", "bye", "bye bye", "so", "um", "uh", "hmm", "mm", "ah", "oh",
    "subtitles by the amara org community", "transcribed by", "the end", "music", "applause", "silence",
}


def clean_transcript(raw: str) -> str:
    text = " ".join(_NOISE.sub(" ", raw).split())
    if not re.search(r"[^\W\d_]", text):
        return ""  # nothing but punctuation/digits left -> no speech
    if " ".join(re.findall(r"[a-z']+", text.lower())).replace("'", "") in _HALLUCINATIONS:
        return ""  # a typical whisper hallucination on silence/noise: never treated as a command
    return text


class WhisperCppRecognizer:
    def __init__(self, config: VoiceConfig, processes: ProcessManager) -> None:
        self.config = config
        self.processes = processes

    def binary(self) -> str | None:
        # Homebrew's whisper-cpp installs `whisper-cli` (older formulae: `whisper-cpp`); /opt/homebrew/bin is searched
        # explicitly because GUI-launched processes do not have it on PATH.
        return find_tool(self.config.whisper_binary, "whisper-cli", "whisper-cpp")

    def model_path(self) -> Path:
        found, _ = find_whisper_model(self.config.whisper_model)
        return found or Path(self.config.whisper_model).expanduser()

    def status(self) -> dict:
        if self.config.stt_engine == "none":
            return {"available": False, "engine": "none", "message": "speech recognition disabled in settings"}
        binary = self.binary()
        model, notes = find_whisper_model(self.config.whisper_model)
        if not binary:
            return {"available": False, "engine": "whisper.cpp", "binary": None, "model": str(model) if model else None,
                    "message": "whisper-cli not found (searched PATH, /opt/homebrew/bin, /usr/local/bin). Install with: brew install whisper-cpp"}
        if model is None:
            return {"available": False, "engine": "whisper.cpp", "binary": binary, "model": None, "notes": notes,
                    "message": "whisper-cli is installed but no speech model was found — Homebrew does not download one. "
                               "Run: mkdir -p ~/.baymax/models && curl -L --fail -o ~/.baymax/models/ggml-base.en.bin "
                               "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
                               + (f" ({notes[0]})" if notes else "")}
        return {"available": True, "engine": "whisper.cpp", "binary": binary, "model": str(model), "notes": notes, "message": "ready"}

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
        text = clean_transcript(res.stdout)
        return Transcript(text, pcm.duration, bool(text), "whisper.cpp")


__all__ = ["WhisperCppRecognizer", "Transcript", "STTUnavailable", "AudioError"]
