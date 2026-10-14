"""Logging configuration. Logs go to stderr and a rotating file in the data dir."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_dir / "baymax.log", maxBytes=2_000_000, backupCount=3)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
