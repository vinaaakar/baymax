"""Ollama client (local only).

* One shared aiohttp session (connection reuse).
* Bounded context (``num_ctx``) and output (``num_predict``).
* Connect and total timeouts; cancellation propagates to the HTTP request.
* There is deliberately **no** fallback to any cloud provider: if Ollama is not
  running or the model is missing, callers get :class:`LLMUnavailable` with an
  actionable message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import aiohttp

from ..config import LLMConfig

log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    pass


class LLMError(Exception):
    pass


@dataclass
class LLMStatus:
    available: bool
    model: str
    model_installed: bool
    installed_models: list[str] = field(default_factory=list)
    message: str = ""
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"available": self.available, "model": self.model, "modelInstalled": self.model_installed,
                "installedModels": self.installed_models, "message": self.message, "checkedAt": self.checked_at}


class OllamaClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()  # one generation at a time: avoids loading concurrent model contexts
        self.failures = 0
        self.last_status: LLMStatus | None = None

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=4))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _timeout(self, total: float | None = None) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=total or self.config.request_timeout_s, sock_connect=self.config.connect_timeout_s)

    async def status(self) -> LLMStatus:
        model = self.config.model
        try:
            http = await self._http()
            async with http.get(f"{self.config.base_url}/api/tags", timeout=self._timeout(5)) as resp:
                if resp.status != 200:
                    st = LLMStatus(False, model, False, message=f"Ollama responded HTTP {resp.status}")
                else:
                    data = await resp.json()
                    names = [m.get("name", "") for m in data.get("models", [])]
                    installed = _model_installed(model, names)
                    msg = "ready" if installed else f"model '{model}' is not installed; run: ollama pull {model}"
                    st = LLMStatus(installed, model, installed, names, msg)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            st = LLMStatus(False, model, False, message=f"Ollama is not reachable at {self.config.base_url} "
                                                          f"({exc.__class__.__name__}). Install from https://ollama.com and run 'ollama serve'.")
        self.last_status = st
        return st

    async def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, max_tokens: int | None = None,
                   temperature: float | None = None) -> str:
        chunks: list[str] = []
        async for piece in self.stream_chat(messages, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature):
            chunks.append(piece)
        return "".join(chunks)

    async def stream_chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, max_tokens: int | None = None,
                          temperature: float | None = None) -> AsyncIterator[str]:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": _bound_messages(messages, self.config.num_ctx),
            "stream": True,
            "keep_alive": self.config.keep_alive,
            "options": {
                "num_ctx": self.config.num_ctx,
                "num_predict": max_tokens or self.config.max_output_tokens,
                "temperature": self.config.temperature if temperature is None else temperature,
            },
        }
        if json_mode:
            body["format"] = "json"
        async with self._lock:
            try:
                http = await self._http()
                async with http.post(f"{self.config.base_url}/api/chat", json=body, timeout=self._timeout()) as resp:
                    if resp.status == 404:
                        raise LLMUnavailable(f"model '{self.config.model}' not found in Ollama; run: ollama pull {self.config.model}")
                    if resp.status != 200:
                        text = (await resp.text())[:300]
                        raise LLMError(f"Ollama HTTP {resp.status}: {text}")
                    async for raw in resp.content:
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except ValueError:
                            continue
                        if "error" in data:
                            raise LLMError(str(data["error"])[:300])
                        piece = (data.get("message") or {}).get("content", "")
                        if piece:
                            yield piece
                        if data.get("done"):
                            break
                self.failures = 0
            except (aiohttp.ClientConnectorError, ConnectionRefusedError, OSError) as exc:
                self.failures += 1
                raise LLMUnavailable(f"Ollama is not reachable at {self.config.base_url}: {exc.__class__.__name__}") from None
            except asyncio.TimeoutError:
                self.failures += 1
                raise LLMError(f"the local model did not answer within {self.config.request_timeout_s:.0f}s") from None


def _model_installed(model: str, names: list[str]) -> bool:
    if model in names:
        return True
    if ":" not in model:
        return f"{model}:latest" in names
    return False


def _bound_messages(messages: list[dict[str, str]], num_ctx: int) -> list[dict[str, str]]:
    """Keep the system prompt and the most recent turns within a rough token budget (~4 chars/token)."""
    budget_chars = int(num_ctx * 4 * 0.75)
    system = [m for m in messages if m.get("role") == "system"][:1]
    rest = [m for m in messages if m.get("role") != "system"]
    used = sum(len(m["content"]) for m in system)
    kept: list[dict[str, str]] = []
    for m in reversed(rest):
        size = len(m.get("content", ""))
        if kept and used + size > budget_chars:
            break
        if not kept and used + size > budget_chars:
            m = {**m, "content": m["content"][-(budget_chars - used):]}
            size = len(m["content"])
        kept.append(m)
        used += size
    return system + list(reversed(kept))
