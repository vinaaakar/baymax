"""WAV parsing and simple energy-based voice activity detection."""

from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass

import numpy as np


class AudioError(Exception):
    pass


@dataclass
class PCM:
    samples: np.ndarray  # float32 mono in [-1, 1]
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / float(self.sample_rate or 1)


def read_wav(data: bytes, max_seconds: int = 60) -> PCM:
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioError("audio must be a RIFF/WAVE file")
    try:
        with wave.open(io.BytesIO(data)) as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if width != 2:
                raise AudioError("only 16-bit PCM WAV is supported")
            if not (8000 <= rate <= 48000):
                raise AudioError("unsupported sample rate")
            if frames / rate > max_seconds:
                raise AudioError(f"audio longer than {max_seconds}s")
            raw = w.readframes(frames)
    except (wave.Error, EOFError, struct.error) as exc:
        raise AudioError(f"invalid WAV: {exc}") from None
    arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1)
    return PCM(arr, rate)


def resample(pcm: PCM, rate: int = 16000) -> PCM:
    if pcm.sample_rate == rate or len(pcm.samples) == 0:
        return PCM(pcm.samples, rate) if pcm.sample_rate == rate else PCM(pcm.samples, rate)
    n = int(round(len(pcm.samples) * rate / pcm.sample_rate))
    x_old = np.linspace(0.0, 1.0, num=len(pcm.samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return PCM(np.interp(x_new, x_old, pcm.samples).astype(np.float32), rate)


def write_wav(pcm: PCM) -> bytes:
    buf = io.BytesIO()
    clipped = np.clip(pcm.samples, -1.0, 1.0)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(pcm.sample_rate)
        w.writeframes((clipped * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def frame_energies(pcm: PCM, frame_ms: int = 30) -> np.ndarray:
    size = max(1, int(pcm.sample_rate * frame_ms / 1000))
    n = len(pcm.samples) // size
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    frames = pcm.samples[: n * size].reshape(n, size)
    return np.sqrt(np.mean(frames ** 2, axis=1))


def detect_speech(pcm: PCM, frame_ms: int = 30, threshold: float | None = None, min_speech_ms: int = 150) -> tuple[bool, float, float]:
    """Returns (has_speech, start_s, end_s) using an adaptive RMS threshold."""
    e = frame_energies(pcm, frame_ms)
    if e.size == 0:
        return False, 0.0, 0.0
    noise = float(np.percentile(e, 10))
    thr = threshold if threshold is not None else max(0.01, noise * 3.0)
    voiced = np.where(e > thr)[0]
    if voiced.size * frame_ms < min_speech_ms:
        return False, 0.0, 0.0
    return True, voiced[0] * frame_ms / 1000.0, (voiced[-1] + 1) * frame_ms / 1000.0


def trim(pcm: PCM, start: float, end: float, pad: float = 0.2) -> PCM:
    a = max(0, int((start - pad) * pcm.sample_rate))
    b = min(len(pcm.samples), int((end + pad) * pcm.sample_rate))
    return PCM(pcm.samples[a:b], pcm.sample_rate)
