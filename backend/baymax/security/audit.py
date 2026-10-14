"""Append-only, hash-chained audit log.

Each record stores ``hash = sha256(prev_hash + canonical(record))`` so any
edit or deletion of an earlier row is detectable with :meth:`AuditLog.verify_chain`.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Any

from ..db import Database

GENESIS = "0" * 64
_SECRETISH = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*\S+")


def redact(text: str) -> str:
    return _SECRETISH.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.Lock()

    def record(self, actor: str, action: str, risk: str, outcome: str, detail: dict[str, Any] | str | None = None) -> int:
        if isinstance(detail, dict):
            detail_s = json.dumps(detail, sort_keys=True, default=str)
        else:
            detail_s = str(detail or "")
        detail_s = redact(detail_s)[:4000]
        with self._lock:
            row = self.db.one("SELECT hash FROM audit ORDER BY id DESC LIMIT 1")
            prev = row["hash"] if row else GENESIS
            ts = time.time()
            digest = _digest(prev, ts, actor, action, risk, outcome, detail_s)
            cur = self.db.execute(
                "INSERT INTO audit(ts, actor, action, risk, outcome, detail, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?)",
                (ts, actor, action, risk, outcome, detail_s, prev, digest),
            )
            return int(cur.lastrowid)

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def verify_chain(self) -> bool:
        prev = GENESIS
        for r in self.db.query("SELECT * FROM audit ORDER BY id ASC"):
            if r["prev_hash"] != prev:
                return False
            if _digest(prev, r["ts"], r["actor"], r["action"], r["risk"], r["outcome"], r["detail"]) != r["hash"]:
                return False
            prev = r["hash"]
        return True


def _digest(prev: str, ts: float, actor: str, action: str, risk: str, outcome: str, detail: str) -> str:
    canonical = json.dumps([prev, repr(ts), actor, action, risk, outcome, detail], separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
