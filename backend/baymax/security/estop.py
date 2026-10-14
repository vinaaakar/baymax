"""Emergency Stop.

When engaged, every controlled action is refused (``ensure_clear`` raises),
running tasks are cancelled, child processes are killed and speech stops.
It stays engaged until explicitly reset from the Mac (local client).
"""

from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger(__name__)


class EmergencyStopEngaged(Exception):
    pass


class EmergencyStop:
    def __init__(self) -> None:
        self.engaged = False
        self.reason = ""
        self.engaged_at: float | None = None
        self._on_engage: list[Callable[[str], None]] = []
        self._on_change: list[Callable[[bool, str], None]] = []

    def on_engage(self, fn: Callable[[str], None]) -> None:
        self._on_engage.append(fn)

    def on_change(self, fn: Callable[[bool, str], None]) -> None:
        self._on_change.append(fn)

    def engage(self, reason: str = "user request") -> None:
        first = not self.engaged
        self.engaged = True
        self.reason = reason
        self.engaged_at = time.time()
        # Run every handler even if one fails: stopping must be best-effort complete.
        for fn in list(self._on_engage):
            try:
                fn(reason)
            except Exception:  # pragma: no cover - logged, never raised
                log.exception("emergency stop handler failed")
        if first:
            for fn in list(self._on_change):
                try:
                    fn(True, reason)
                except Exception:  # pragma: no cover
                    log.exception("emergency stop listener failed")

    def reset(self) -> None:
        if not self.engaged:
            return
        self.engaged = False
        self.reason = ""
        self.engaged_at = None
        for fn in list(self._on_change):
            try:
                fn(False, "")
            except Exception:  # pragma: no cover
                log.exception("emergency stop listener failed")

    def ensure_clear(self) -> None:
        if self.engaged:
            raise EmergencyStopEngaged(f"Emergency Stop is engaged ({self.reason}); reset it on the Mac to continue")
