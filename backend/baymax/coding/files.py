"""Workspace-confined file operations with backups.

Every overwrite first copies the previous content into a per-task backup
directory, so changes can be inspected and reverted. Edits use exact
search/replace that must match exactly once (no fuzzy patching).
"""

from __future__ import annotations

import difflib
import hashlib
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..security.workspace import Workspace, WorkspaceViolation

MAX_READ = 400_000
MAX_WRITE = 1_000_000


class EditError(Exception):
    pass


@dataclass
class FileChange:
    path: str
    action: str  # created | modified
    added: int
    removed: int
    backup: str | None = None

    def to_dict(self) -> dict:
        return {"path": self.path, "action": self.action, "added": self.added, "removed": self.removed, "backup": self.backup}


@dataclass
class ChangeSet:
    changes: list[FileChange] = field(default_factory=list)

    def merge(self, change: FileChange) -> None:
        for c in self.changes:
            if c.path == change.path:
                c.added += change.added
                c.removed += change.removed
                return
        self.changes.append(change)


def _is_text(data: bytes) -> bool:
    return b"\x00" not in data[:8192]


class FileOps:
    def __init__(self, workspace: Workspace, backup_root: Path, task_id: str) -> None:
        self.ws = workspace
        self.backup_dir = backup_root / f"{time.strftime('%Y%m%d-%H%M%S')}-{task_id}"
        self.changes = ChangeSet()

    def read(self, rel: str, limit: int = MAX_READ) -> str:
        p = self.ws.resolve(rel, must_exist=True)
        if not p.is_file():
            raise EditError(f"{rel} is not a file")
        data = p.read_bytes()[: limit + 1]
        if not _is_text(data):
            raise EditError(f"{rel} looks like a binary file")
        text = data[:limit].decode("utf-8", errors="replace")
        return text + ("\n…[truncated]" if len(data) > limit else "")

    def list(self, rel: str = ".", limit: int = 400) -> list[str]:
        base = self.ws.resolve(rel, must_exist=True)
        out = []
        for p in sorted(base.rglob("*")):
            if any(part in (".git", "node_modules", ".venv", "__pycache__", "dist", "build", "target") for part in p.parts):
                continue
            if p.is_symlink():
                continue
            out.append(self.ws.relative(p) + ("/" if p.is_dir() else ""))
            if len(out) >= limit:
                out.append("…")
                break
        return out

    def search(self, pattern: str, rel: str = ".", limit: int = 100) -> list[str]:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise EditError(f"invalid pattern: {exc}") from None
        hits = []
        base = self.ws.resolve(rel, must_exist=True)
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.is_symlink() or any(part in (".git", "node_modules", ".venv", "dist", "build") for part in p.parts):
                continue
            try:
                data = p.read_bytes()[:MAX_READ]
            except OSError:
                continue
            if not _is_text(data):
                continue
            for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{self.ws.relative(p)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= limit:
                        return hits
        return hits

    def _backup(self, path: Path) -> str | None:
        if not path.exists():
            return None
        dest = self.backup_dir / self.ws.relative(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        return str(dest)

    def write(self, rel: str, content: str, *, overwrite: bool = True) -> FileChange:
        if len(content.encode("utf-8")) > MAX_WRITE:
            raise EditError("file content too large")
        p = self.ws.resolve(rel)
        if p.exists() and p.is_dir():
            raise EditError(f"{rel} is a directory")
        existed = p.exists()
        if existed and not overwrite:
            raise EditError(f"{rel} already exists")
        old = p.read_text(encoding="utf-8", errors="replace") if existed else ""
        backup = self._backup(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".baymax-tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(p)
        if hashlib.sha256(p.read_bytes()).hexdigest() != hashlib.sha256(content.encode("utf-8")).hexdigest():
            raise EditError(f"verification failed: {rel} does not contain the written content")
        added, removed = _diffstat(old, content)
        change = FileChange(self.ws.relative(p), "modified" if existed else "created", added, removed, backup)
        self.changes.merge(change)
        return change

    def edit(self, rel: str, search: str, replace: str) -> FileChange:
        if not search:
            raise EditError("edit needs a non-empty search string")
        text = self.read(rel)
        count = text.count(search)
        if count == 0:
            raise EditError(f"search text not found in {rel}")
        if count > 1:
            raise EditError(f"search text matches {count} places in {rel}; make it more specific")
        return self.write(rel, text.replace(search, replace, 1))

    def restore_all(self) -> int:
        """Revert every change made through this FileOps instance."""
        n = 0
        for c in self.changes.changes:
            target = self.ws.resolve(c.path)
            if c.backup:
                shutil.copy2(c.backup, target)
            elif target.exists():
                target.unlink()
            n += 1
        return n


def _diffstat(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


__all__ = ["FileOps", "FileChange", "EditError", "WorkspaceViolation"]
