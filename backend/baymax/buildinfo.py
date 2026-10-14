"""Fingerprint of the backend source actually running, so the desktop app can detect a stale backend process
(e.g. one started before the code was updated) instead of silently attaching to it."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

PKG = Path(__file__).resolve().parent


def source_hash(pkg: Path = PKG) -> str:
    """sha256 over (posix relative path, NUL, bytes, NUL) of every .py file, sorted by relative path. Mirrored in
    desktop/electron/backend.ts (sourceHash)."""
    h = hashlib.sha256()
    files = sorted((p.relative_to(pkg).as_posix(), p) for p in pkg.rglob("*.py") if "__pycache__" not in p.parts)
    for rel, p in files:
        h.update(rel.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:12]


@lru_cache(maxsize=1)
def running_build() -> str:
    return source_hash()  # computed once at startup: the code this process loaded
