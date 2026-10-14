"""Bounded, cancellable subprocess execution (no shell).

Every child process is registered so Emergency Stop can kill it. Output is
capped, the environment is scrubbed of obvious secrets, and a timeout always
applies.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass

log = logging.getLogger(__name__)

_SECRET_ENV = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "APIKEY", "CREDENTIAL", "PRIVATE_KEY")


@dataclass
class ProcessResult:
    argv: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled

    def tail(self, n: int = 4000) -> str:
        text = (self.stdout + ("\n" if self.stdout and self.stderr else "") + self.stderr)
        return text[-n:]


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in _SECRET_ENV)}
    env.setdefault("CI", "1")
    if extra:
        env.update(extra)
    return env


class ProcessManager:
    def __init__(self) -> None:
        self._procs: set[asyncio.subprocess.Process] = set()

    @property
    def active(self) -> int:
        return len(self._procs)

    async def run(self, argv: list[str], *, cwd: str | None = None, timeout: float = 120.0,
                  max_output: int = 200_000, env: dict[str, str] | None = None, stdin: bytes | None = None) -> ProcessResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=scrubbed_env(env),
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError:
            return ProcessResult(argv, 127, "", f"program not found: {argv[0]}")
        except PermissionError as exc:
            return ProcessResult(argv, 126, "", f"permission denied: {exc}")
        self._procs.add(proc)
        timed_out = cancelled = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            self._kill(proc)
            out, err = await _drain(proc)
        except asyncio.CancelledError:
            cancelled = True
            self._kill(proc)
            await _drain(proc)
            self._procs.discard(proc)
            raise
        finally:
            self._procs.discard(proc)
        truncated = len(out) > max_output or len(err) > max_output
        return ProcessResult(
            argv, proc.returncode,
            out[-max_output:].decode("utf-8", errors="replace"),
            err[-max_output:].decode("utf-8", errors="replace"),
            timed_out=timed_out, cancelled=cancelled, truncated=truncated,
        )

    def kill_all(self) -> int:
        n = 0
        for proc in list(self._procs):
            self._kill(proc)
            n += 1
        return n

    @staticmethod
    def _kill(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass


async def _drain(proc: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=5)
    except Exception:
        return b"", b""
