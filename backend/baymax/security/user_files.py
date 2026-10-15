"""Which files of the user BAYMAX may hand to another step or app: regular files inside the user's home folder,
outside ~/Library and hidden folders (keys, credentials, app data). Paths are resolved (symlinks followed) before the
check, so a link cannot point a step outside the allowed scope."""

from __future__ import annotations

import os
from pathlib import Path

PROTECTED_TOP = {"Library"}  # plus every hidden top-level folder (.ssh, .gnupg, .aws, .config, .baymax, …)


def user_files_root() -> Path:
    return Path(os.environ.get("HOME") or Path.home()).resolve()


def check_user_file(path: object) -> tuple[Path | None, str, str]:
    """(resolved path, "", "") when allowed, else (None, kind, reason) with kind in invalid | stale | permission."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return None, "invalid", "that isn't a file path"
    p = Path(path)
    if not p.is_absolute():
        return None, "invalid", f"“{path}” isn't a full path"
    try:
        resolved = p.resolve(strict=True)
    except FileNotFoundError:
        return None, "stale", f"{p.name} no longer exists at {path}"
    except OSError as exc:
        return None, "stale", f"{p.name} can't be accessed ({exc.strerror or exc})"
    root = user_files_root()
    if resolved != root and root not in resolved.parents:
        return None, "permission", f"{resolved} is outside your home folder, which I don't open files from"
    top = resolved.relative_to(root).parts[0] if resolved != root else ""
    if top in PROTECTED_TOP or top.startswith("."):
        return None, "permission", f"{resolved.name} is in a protected folder (~/{top}), which I don't open files from"
    if not resolved.is_file():
        return None, "invalid", f"{resolved.name} isn't a file"
    return resolved, "", ""
