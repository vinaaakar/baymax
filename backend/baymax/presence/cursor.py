"""Real cursor presence: the Mac app samples the actual cursor and display geometry
(Electron ``screen`` API, backed by native macOS APIs) and streams it here over the
authenticated local WebSocket. This module validates, normalises and rate-limits it
before it is relayed to paired phones.

Coordinate system
-----------------
* Input: global desktop points (macOS "points", origin top-left of the primary display,
  y grows downward), plus the display that contains the cursor and the primary display.
* ``x, y``: position normalised to the display under the cursor (0..1).
* ``vx, vy``: position normalised to the *primary* display (the one the phone sits by).
  Values outside 0..1 mean the cursor is on another monitor (left/right/above/below).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable


class CursorError(ValueError):
    pass


@dataclass(frozen=True)
class Display:
    id: int
    x: float
    y: float
    width: float
    height: float
    scale: float = 1.0

    @classmethod
    def parse(cls, raw: Any, name: str) -> "Display":
        if not isinstance(raw, dict):
            raise CursorError(f"{name} must be an object")
        try:
            d = cls(int(raw["id"]), float(raw["x"]), float(raw["y"]), float(raw["width"]), float(raw["height"]),
                    float(raw.get("scaleFactor", raw.get("scale", 1.0))))
        except (KeyError, TypeError, ValueError):
            raise CursorError(f"{name} needs id, x, y, width, height") from None
        if not all(math.isfinite(v) for v in (d.x, d.y, d.width, d.height, d.scale)):
            raise CursorError(f"{name} has non-finite values")
        if not (1 <= d.width <= 100_000 and 1 <= d.height <= 100_000 and 0.25 <= d.scale <= 8):
            raise CursorError(f"{name} has implausible dimensions")
        return d


@dataclass(frozen=True)
class CursorSample:
    x: float  # normalised to the current display
    y: float
    vx: float  # normalised to the primary display (may be outside 0..1)
    vy: float
    display: Display
    seq: int
    ts: float

    def payload(self) -> dict[str, Any]:
        return {"x": round(self.x, 4), "y": round(self.y, 4), "vx": round(self.vx, 4), "vy": round(self.vy, 4),
                "displayId": self.display.id, "displayWidth": int(self.display.width), "displayHeight": int(self.display.height),
                "scale": self.display.scale, "seq": self.seq}


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def normalize(raw: dict[str, Any], seq: int, now: float | None = None) -> CursorSample:
    """Validate a raw sample from the desktop app and normalise it."""
    try:
        px, py = float(raw["x"]), float(raw["y"])
    except (KeyError, TypeError, ValueError):
        raise CursorError("cursor sample needs numeric x and y") from None
    if not (math.isfinite(px) and math.isfinite(py)) or abs(px) > 1e6 or abs(py) > 1e6:
        raise CursorError("cursor position out of range")
    display = Display.parse(raw.get("display"), "display")
    primary = Display.parse(raw.get("primary", raw.get("display")), "primary")
    x = _clamp((px - display.x) / display.width, 0.0, 1.0)
    y = _clamp((py - display.y) / display.height, 0.0, 1.0)
    # Relative to the primary display; clamp to a few screens' worth either side.
    vx = _clamp((px - primary.x) / primary.width, -4.0, 5.0)
    vy = _clamp((py - primary.y) / primary.height, -4.0, 5.0)
    return CursorSample(x, y, vx, vy, display, seq, now if now is not None else time.time())


@dataclass
class CursorRelay:
    """Accepts samples, drops them while asleep, de-duplicates and rate-limits output."""

    publish: Callable[[dict[str, Any]], None]
    max_hz: float = 30.0
    min_delta: float = 0.0015  # ignore sub-pixel jitter (~2 px on a 1512 pt display)
    sleeping: bool = False
    last: CursorSample | None = None
    received: int = 0
    published: int = 0
    rejected: int = 0
    _last_sent_at: float = field(default=float("-inf"), repr=False)
    _seq: int = 0

    def submit(self, raw: dict[str, Any], now: float | None = None) -> bool:
        """Returns True if the sample was relayed."""
        self.received += 1
        now = time.monotonic() if now is None else now
        if self.sleeping:
            return False
        try:
            sample = normalize(raw, self._seq + 1)
        except CursorError:
            self.rejected += 1
            return False
        if self.last is not None:
            moved = abs(sample.vx - self.last.vx) + abs(sample.vy - self.last.vy)
            if moved < self.min_delta and sample.display.id == self.last.display.id:
                return False
        if now - self._last_sent_at < 1.0 / self.max_hz:
            return False
        self._seq += 1
        sample = CursorSample(sample.x, sample.y, sample.vx, sample.vy, sample.display, self._seq, sample.ts)
        self.last = sample
        self._last_sent_at = now
        self.published += 1
        self.publish(sample.payload())
        return True

    def stats(self) -> dict[str, Any]:
        age = None if self.last is None else round(time.time() - self.last.ts, 2)
        return {"received": self.received, "published": self.published, "rejected": self.rejected, "sleeping": self.sleeping,
                "lastAgeSeconds": age, "last": self.last.payload() if self.last else None}
