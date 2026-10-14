"""Conversation history (persisted, bounded when sent to the model)."""

from __future__ import annotations

import time
import uuid
from typing import Any

from ..db import Database


class ConversationLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, role: str, text: str, source: str = "local", task_id: str | None = None) -> dict[str, Any]:
        msg = {"id": uuid.uuid4().hex[:12], "role": role, "text": text[:20000], "source": source, "taskId": task_id, "ts": time.time()}
        self.db.execute("INSERT INTO messages(id, role, text, source, task_id, ts) VALUES (?,?,?,?,?,?)",
                        (msg["id"], role, msg["text"], source, task_id, msg["ts"]))
        return msg

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM messages ORDER BY ts DESC LIMIT ?", (limit,))
        return [{"id": r["id"], "role": r["role"], "text": r["text"], "source": r["source"], "taskId": r["task_id"], "ts": r["ts"]}
                for r in reversed(rows)]

    def model_messages(self, limit: int = 12) -> list[dict[str, str]]:
        out = []
        for m in self.recent(limit):
            if m["role"] in ("user", "assistant"):
                out.append({"role": m["role"], "content": m["text"]})
        return out

    def clear(self) -> int:
        return self.db.execute("DELETE FROM messages").rowcount
