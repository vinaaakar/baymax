"""User-controlled persistent memory.

Only explicitly approved facts/preferences are stored (e.g. "remember that I
prefer metric units"). Content that looks like credentials or other sensitive
data is refused. Users can list, edit and delete every entry.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..db import Database

MAX_MEMORY_CHARS = 500

_SENSITIVE = [
    (re.compile(r"(?i)\b(password|passcode|passwd|pin code|pin is|secret|api[ _-]?key|access[ _-]?token|auth[ _-]?token|private key|seed phrase|recovery phrase)\b"), "credentials"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "card or account number"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "government ID number"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})\b"), "API token"),
]


class MemoryRejected(Exception):
    pass


def sensitive_reason(text: str) -> str | None:
    for pattern, label in _SENSITIVE:
        if pattern.search(text):
            return label
    return None


@dataclass
class Memory:
    id: str
    content: str
    category: str
    source: str
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "content": self.content, "category": self.category, "source": self.source,
                "createdAt": self.created_at, "updatedAt": self.updated_at}


class MemoryStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _clean(self, content: str) -> str:
        content = " ".join(content.split())
        if not content:
            raise MemoryRejected("memory is empty")
        if len(content) > MAX_MEMORY_CHARS:
            raise MemoryRejected(f"memory is longer than {MAX_MEMORY_CHARS} characters")
        reason = sensitive_reason(content)
        if reason:
            raise MemoryRejected(f"I won't store {reason} in memory. Please keep secrets in a password manager.")
        return content

    def add(self, content: str, category: str = "preference", source: str = "user") -> Memory:
        content = self._clean(content)
        existing = self.db.one("SELECT * FROM memories WHERE lower(content)=lower(?)", (content,))
        if existing:
            return _row(existing)
        now = time.time()
        mem = Memory(uuid.uuid4().hex[:12], content, category[:32], source[:32], now, now)
        self.db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?)",
                        (mem.id, mem.content, mem.category, mem.source, mem.created_at, mem.updated_at))
        return mem

    def update(self, memory_id: str, content: str) -> Memory | None:
        content = self._clean(content)
        cur = self.db.execute("UPDATE memories SET content=?, updated_at=? WHERE id=?", (content, time.time(), memory_id))
        if cur.rowcount == 0:
            return None
        return self.get(memory_id)

    def get(self, memory_id: str) -> Memory | None:
        row = self.db.one("SELECT * FROM memories WHERE id=?", (memory_id,))
        return _row(row) if row else None

    def list(self) -> list[Memory]:
        return [_row(r) for r in self.db.query("SELECT * FROM memories ORDER BY created_at")]

    def delete(self, memory_id: str) -> bool:
        return self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount > 0

    def delete_matching(self, phrase: str) -> int:
        phrase = phrase.strip()
        if len(phrase) < 3:
            return 0
        return self.db.execute("DELETE FROM memories WHERE content LIKE ?", (f"%{phrase}%",)).rowcount

    def clear(self) -> int:
        return self.db.execute("DELETE FROM memories").rowcount

    def contents(self) -> list[str]:
        return [m.content for m in self.list()]


def _row(r) -> Memory:
    return Memory(r["id"], r["content"], r["category"], r["source"], r["created_at"], r["updated_at"])
