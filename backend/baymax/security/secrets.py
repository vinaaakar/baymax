"""Secret storage.

On macOS secrets go to the login Keychain via the ``security`` CLI (no
third-party dependency). Elsewhere, or if the Keychain is unavailable, they go
to ``<data_dir>/secrets/<name>`` with 0600 permissions inside a 0700 directory.
Secrets are never written to config files or logs.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)
_NAME = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
SERVICE = "com.baymax.companion"


class SecretStore:
    def __init__(self, data_dir: Path, use_keychain: bool | None = None) -> None:
        self.dir = data_dir / "secrets"
        if use_keychain is None:
            use_keychain = sys.platform == "darwin" and shutil.which("security") is not None
        self.use_keychain = use_keychain

    def _check(self, name: str) -> None:
        if not _NAME.match(name):
            raise ValueError("invalid secret name")

    def get(self, name: str) -> str | None:
        self._check(name)
        if self.use_keychain:
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                return proc.stdout.rstrip("\n")
            return None
        path = self.dir / name
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8").strip()

    def set(self, name: str, value: str) -> None:
        self._check(name)
        if self.use_keychain:
            # -U updates an existing item. The value is passed as an argument to
            # a local process only; it is not logged.
            proc = subprocess.run(
                ["security", "add-generic-password", "-U", "-s", SERVICE, "-a", name, "-w", value],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                return
            log.warning("keychain write failed (%s); falling back to file store", proc.returncode)
            self.use_keychain = False
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        path = self.dir / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
        os.chmod(path, 0o600)

    def delete(self, name: str) -> None:
        self._check(name)
        if self.use_keychain:
            subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", name], capture_output=True, timeout=10)
            return
        path = self.dir / name
        if path.exists():
            path.unlink()
