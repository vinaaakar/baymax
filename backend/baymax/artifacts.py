"""Artifact registry: every file BAYMAX produces is recorded with its kind,
path, originating task and whether it was validated after being written."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database

KINDS = {"model3d", "image", "document", "research", "simulation", "data", "diagram", "code", "screenshot"}


@dataclass
class Artifact:
    id: str
    kind: str
    title: str
    path: str
    task_id: str | None
    meta: dict[str, Any]
    validated: bool
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "title": self.title, "path": self.path, "taskId": self.task_id,
                "meta": self.meta, "validated": self.validated, "createdAt": self.created_at, "updatedAt": self.updated_at}


class ArtifactRegistry:
    def __init__(self, db: Database, root: Path) -> None:
        self.db = db
        self.root = root

    def add(self, kind: str, title: str, path: Path, *, task_id: str | None = None, meta: dict[str, Any] | None = None,
            validated: bool = False, artifact_id: str | None = None) -> Artifact:
        if kind not in KINDS:
            raise ValueError(f"unknown artifact kind {kind!r}")
        now = time.time()
        art = Artifact(artifact_id or uuid.uuid4().hex[:12], kind, title[:200], str(path), task_id, meta or {}, validated, now, now)
        self.db.execute(
            "INSERT INTO artifacts(id, kind, title, path, task_id, meta, validated, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, path=excluded.path, meta=excluded.meta, validated=excluded.validated, "
            "updated_at=excluded.updated_at",
            (art.id, kind, art.title, art.path, task_id, json.dumps(art.meta, default=str), int(validated), now, now),
        )
        return self.get(art.id)  # type: ignore[return-value]

    def update(self, artifact_id: str, *, meta: dict[str, Any] | None = None, validated: bool | None = None, path: Path | None = None,
               title: str | None = None) -> Artifact | None:
        art = self.get(artifact_id)
        if art is None:
            return None
        self.db.execute("UPDATE artifacts SET meta=?, validated=?, path=?, title=?, updated_at=? WHERE id=?",
                        (json.dumps(meta if meta is not None else art.meta, default=str), int(art.validated if validated is None else validated),
                         str(path or art.path), title or art.title, time.time(), artifact_id))
        return self.get(artifact_id)

    def get(self, artifact_id: str) -> Artifact | None:
        r = self.db.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
        return _row(r) if r else None

    def list(self, kind: str | None = None, limit: int = 200) -> list[Artifact]:
        if kind:
            rows = self.db.query("SELECT * FROM artifacts WHERE kind=? ORDER BY updated_at DESC LIMIT ?", (kind, limit))
        else:
            rows = self.db.query("SELECT * FROM artifacts ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [_row(r) for r in rows]


def _row(r) -> Artifact:
    return Artifact(r["id"], r["kind"], r["title"], r["path"], r["task_id"], json.loads(r["meta"] or "{}"), bool(r["validated"]),
                    r["created_at"], r["updated_at"])
