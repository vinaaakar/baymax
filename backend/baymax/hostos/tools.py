"""Locating local executables and model files reliably on macOS.

Apps launched from Finder/Electron inherit launchd's minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin),
so Homebrew tools such as ``whisper-cli`` or ``ollama`` are invisible to ``shutil.which`` even
when installed. We search the standard Homebrew prefixes explicitly.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

EXTRA_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin", str(Path.home() / ".local/bin"))


def search_path() -> str:
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    return os.pathsep.join(parts + [d for d in EXTRA_BIN_DIRS if d not in parts])


def find_tool(*names: str | None) -> str | None:
    """First match among names; absolute/relative paths are accepted if executable."""
    for name in names:
        if not name:
            continue
        p = Path(name).expanduser()
        if os.sep in name and p.is_file() and os.access(p, os.X_OK):
            return str(p)
        found = shutil.which(name, path=search_path())
        if found:
            return found
    return None


# whisper.cpp ggml model files start with the uint32 magic 0x67676d6c ("ggml") in little-endian order.
GGML_MAGIC = (0x67676D6C).to_bytes(4, "little")
MIN_MODEL_BYTES = 30 * 1024 * 1024  # the smallest real model (tiny) is ~75 MB; test stubs are tiny
WHISPER_PREFERENCE = ("base.en", "small.en", "base", "small", "tiny.en", "tiny", "large-v3-turbo", "medium.en", "medium")


def whisper_model_dirs() -> list[Path]:
    home = Path.home()
    return [home / ".baymax/models", Path("/opt/homebrew/share/whisper-cpp"), Path("/usr/local/share/whisper-cpp"),
            home / "whisper.cpp/models", home / "Developer/whisper.cpp/models", home / "models"]


def check_whisper_model(path: Path) -> str | None:
    """None if the file looks like a usable whisper.cpp model, else the reason it does not."""
    try:
        if not path.is_file():
            return f"{path} does not exist"
        if "for-tests" in path.name:
            return f"{path.name} is a whisper.cpp test stub, not a speech model"
        size = path.stat().st_size
        if size < MIN_MODEL_BYTES:
            return f"{path.name} is only {size // 1024} KB — incomplete download or not a model"
        with path.open("rb") as f:
            if f.read(4) != GGML_MAGIC:
                return f"{path.name} is not a ggml whisper model (bad header)"
    except OSError as exc:
        return f"cannot read {path}: {exc}"
    return None


def find_whisper_model(configured: str | None) -> tuple[Path | None, list[str]]:
    """(model, notes). The configured path wins when valid; otherwise the best valid model found locally."""
    notes: list[str] = []
    if configured:
        p = Path(configured).expanduser()
        problem = check_whisper_model(p)
        if problem is None:
            return p, notes
        notes.append(problem)
    candidates: list[Path] = []
    for d in whisper_model_dirs():
        try:
            candidates += sorted(d.glob("ggml-*.bin"))
        except OSError:
            continue
    valid = [c for c in candidates if check_whisper_model(c) is None]
    notes += [f"skipped {c}: {check_whisper_model(c)}" for c in candidates if c not in valid]

    def rank(p: Path) -> int:
        stem = p.name[len("ggml-"):-len(".bin")]
        return WHISPER_PREFERENCE.index(stem) if stem in WHISPER_PREFERENCE else len(WHISPER_PREFERENCE)

    if valid:
        best = min(valid, key=rank)
        notes.append(f"using {best} (configured model unavailable)" if configured else f"using {best}")
        return best, notes
    return None, notes
