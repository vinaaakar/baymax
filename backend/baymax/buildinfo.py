"""Fingerprint of the backend source actually running, so the desktop app can detect a stale backend process
(e.g. one started before the code was updated) instead of silently attaching to it."""

from __future__ import annotations

import hashlib
import time
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


_disk_cache: tuple[float, str] = (0.0, "")


def disk_build(max_age_s: float = 10.0) -> str:
    """Fingerprint of the backend source as it is on disk *now* (same function, same folder as running_build()).
    Cached briefly so health checks stay cheap."""
    global _disk_cache
    if time.monotonic() - _disk_cache[0] > max_age_s or not _disk_cache[1]:
        _disk_cache = (time.monotonic(), source_hash())
    return _disk_cache[1]


def build_status() -> dict[str, object]:
    """running: the code this process loaded at startup; disk: the code on disk now. They differ when the source was
    updated after this backend started - it is then running outdated code and must be restarted. Both values come from
    the same hash over the same files, so the comparison is valid (a git commit id is never compared with them)."""
    running, disk = running_build(), disk_build()
    return {"build": running, "diskBuild": disk, "stale": running != disk}
