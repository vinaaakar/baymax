import asyncio
import json
import math

import numpy as np
import pytest
from aiohttp import web

from baymax.config import LLMConfig, VoiceConfig
from baymax.hostos.process import ProcessManager
from baymax.llm.ollama import LLMUnavailable, OllamaClient, _bound_messages
from baymax.voice.audio import PCM, AudioError, detect_speech, read_wav, resample, write_wav
from baymax.voice.stt import STTUnavailable, WhisperCppRecognizer
from baymax.voice.tts import Speaker, TTSUnavailable


def tone(seconds=1.0, rate=16000, amp=0.3, silence_pad=0.5):
    t = np.arange(int(seconds * rate)) / rate
    sig = amp * np.sin(2 * math.pi * 220 * t)
    pad = np.zeros(int(silence_pad * rate))
    return PCM(np.concatenate([pad, sig, pad]).astype(np.float32), rate)


def test_wav_roundtrip_and_vad():
    pcm = tone()
    back = read_wav(write_wav(pcm))
    assert back.sample_rate == 16000 and abs(back.duration - pcm.duration) < 0.01
    has, start, end = detect_speech(back)
    assert has and 0.4 < start < 0.6 and 1.4 < end < 1.6
    silent = PCM(np.zeros(16000, dtype=np.float32), 16000)
    assert not detect_speech(silent)[0]


def test_wav_validation():
    with pytest.raises(AudioError):
        read_wav(b"not a wav file at all, definitely not" * 3)
    with pytest.raises(AudioError):
        read_wav(write_wav(PCM(np.zeros(16000 * 70, dtype=np.float32), 16000)), max_seconds=60)
    r = resample(PCM(np.zeros(48000, dtype=np.float32), 48000), 16000)
    assert len(r.samples) == 16000


async def test_stt_unavailable_is_explicit():
    rec = WhisperCppRecognizer(VoiceConfig(whisper_binary="nope-not-here"), ProcessManager())
    assert not rec.status()["available"] and "whisper" in rec.status()["message"]
    # silence never reaches the engine
    tr = await rec.transcribe(write_wav(PCM(np.zeros(16000, dtype=np.float32), 16000)))
    assert tr.text == "" and not tr.speech_detected
    with pytest.raises(STTUnavailable):
        await rec.transcribe(write_wav(tone()))


async def test_tts_states_track_real_process_and_interrupt():
    states = []
    sp = Speaker(VoiceConfig(tts_engine="none"), on_state=states.append)
    assert not sp.status()["available"]
    with pytest.raises(TTSUnavailable):
        await sp.speak("hello")
    # drive the playback path with a stand-in process to check state events and interruption
    task = asyncio.create_task(sp._play(["sleep", "5"]))
    await asyncio.sleep(0.2)
    assert states == ["speaking"] and sp.speaking
    assert sp.stop()
    completed = await asyncio.wait_for(task, 3)
    assert completed is False and states == ["speaking", "idle"] and not sp.speaking


# --------------------------------------------------------------------- LLM
async def test_ollama_unreachable_is_reported_not_faked():
    c = OllamaClient(LLMConfig(base_url="http://127.0.0.1:9", connect_timeout_s=0.5, request_timeout_s=2))
    st = await c.status()
    assert not st.available and "not reachable" in st.message
    with pytest.raises(LLMUnavailable):
        await c.chat([{"role": "user", "content": "hi"}])
    assert c.failures == 1
    await c.close()


@pytest.fixture
async def fake_ollama(aiohttp_unused_port=None):
    seen = {}

    async def tags(request):
        return web.json_response({"models": [{"name": "llama3.2:3b"}]})

    async def chat(request):
        body = await request.json()
        seen["body"] = body
        if body["model"] != "llama3.2:3b":
            return web.Response(status=404, text="model not found")
        resp = web.StreamResponse()
        await resp.prepare(request)
        for piece in ("Hel", "lo", "!"):
            await resp.write((json.dumps({"message": {"content": piece}, "done": False}) + "\n").encode())
        await resp.write((json.dumps({"done": True}) + "\n").encode())
        return resp

    app = web.Application()
    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", seen
    await runner.cleanup()


async def test_ollama_streaming_and_bounds(fake_ollama):
    url, seen = fake_ollama
    c = OllamaClient(LLMConfig(base_url=url, num_ctx=2048, max_output_tokens=100))
    st = await c.status()
    assert st.available and st.model_installed
    assert await c.chat([{"role": "user", "content": "hi"}]) == "Hello!"
    opts = seen["body"]["options"]
    assert opts["num_ctx"] == 2048 and opts["num_predict"] == 100
    c.config.model = "missing:1b"
    assert not (await c.status()).available
    with pytest.raises(LLMUnavailable):
        await c.chat([{"role": "user", "content": "hi"}])
    await c.close()


def test_context_bounding_keeps_system_and_recent():
    msgs = [{"role": "system", "content": "S" * 100}] + [{"role": "user", "content": f"{i}" * 1000} for i in range(30)]
    out = _bound_messages(msgs, num_ctx=1024)
    assert out[0]["role"] == "system"
    assert sum(len(m["content"]) for m in out) <= 1024 * 4
    assert out[-1]["content"].startswith("29")
