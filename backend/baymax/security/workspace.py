"""Workspace confinement.

All file tools resolve user/model supplied paths through :class:`Workspace`.
Resolution follows symlinks *before* checking containment, so a symlink that
points outside the workspace is rejected, and ``..`` traversal is rejected.
"""

from __future__ import annotations

import os
from pathlib import Path


class WorkspaceViolation(Exception):
    pass


class Workspace:
    def __init__(self, root: Path) -> None:
        root = Path(root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve(strict=True)

    def resolve(self, relative: str | os.PathLike[str], *, must_exist: bool = False) -> Path:
        raw = str(relative)
        if "\x00" in raw:
            raise WorkspaceViolation("path contains NUL byte")
        p = Path(raw).expanduser()
        candidate = p if p.is_absolute() else self.root / p
        # strict=False resolves existing components (including symlinks) and
        # leaves the non-existent tail as-is.
        resolved = candidate.resolve(strict=False)
        if not self.contains(resolved):
            raise WorkspaceViolation(f"path escapes the workspace: {raw}")
        # Re-check each existing ancestor: a symlinked parent must also stay inside.
        cursor = resolved
        while cursor != self.root and cursor != cursor.parent:
            if cursor.is_symlink():
                target = cursor.resolve(strict=False)
                if not self.contains(target):
                    raise WorkspaceViolation(f"symlink escapes the workspace: {raw}")
            cursor = cursor.parent
        if must_exist and not resolved.exists():
            raise FileNotFoundError(raw)
        return resolved

    def contains(self, path: Path) -> bool:
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False

    def relative(self, path: Path) -> str:
        return str(path.resolve(strict=False).relative_to(self.root))
