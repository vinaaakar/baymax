"""Generator interface.

A generator turns semantic parameters ("wheel_radius", "floors", "body_color")
into parts built from reusable primitives. It also declares:

* ``aliases``      — request phrases it serves, with parameter presets
* ``color_params`` — which parameter holds the colour of a named role
* ``size_params``  — which parameters grow/shrink when a role is made bigger/smaller
* ``count_params`` — countable nouns ("legs", "floors") -> integer parameter
* ``required_roles`` — roles that must exist in the exported file for the
  output to count as this object type (used by verification)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..scene import Builder


@dataclass(frozen=True)
class Param:
    default: Any
    kind: str = "float"  # float | int | color | choice | bool
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()
    description: str = ""

    def coerce(self, value: Any) -> Any:
        if self.kind == "float":
            v = float(value)
            if self.min is not None:
                v = max(self.min, v)
            if self.max is not None:
                v = min(self.max, v)
            return round(v, 4)
        if self.kind == "int":
            v = int(round(float(value)))
            if self.min is not None:
                v = max(int(self.min), v)
            if self.max is not None:
                v = min(int(self.max), v)
            return v
        if self.kind == "bool":
            return bool(value)
        if self.kind == "choice":
            v = str(value)
            if v not in self.choices:
                raise ValueError(f"must be one of {', '.join(self.choices)}")
            return v
        if self.kind == "color":
            from ..scene import parse_color

            c = parse_color(str(value))
            if c is None:
                raise ValueError(f"unknown colour {value!r}")
            return c
        return value


class Generator:
    id: str = ""
    object_type: str = ""
    category: str = ""
    description: str = ""
    aliases: dict[str, dict[str, Any]] = {}
    params: dict[str, Param] = {}
    primary_color: str = ""
    color_params: dict[str, str] = {}
    size_params: dict[str, list[str]] = {}
    count_params: dict[str, str] = {}
    dimension_params: dict[str, list[str]] = {}  # "height" | "width" | "length" -> params
    required_roles: dict[str, int] = {}
    max_detail: int = 3

    def defaults(self) -> dict[str, Any]:
        return {k: (p.coerce(p.default) if p.kind == "color" else p.default) for k, p in self.params.items()}

    def resolve(self, overrides: dict[str, Any]) -> dict[str, Any]:
        values = self.defaults()
        for k, v in overrides.items():
            if k in self.params:
                values[k] = self.params[k].coerce(v)
        return values

    def build(self, p: dict[str, Any], detail: int) -> Builder:  # pragma: no cover - abstract
        raise NotImplementedError

    def required(self, p: dict[str, Any]) -> dict[str, int]:
        return dict(self.required_roles)

    def type_for(self, p: dict[str, Any]) -> str:
        """Concrete object type for these parameters (e.g. a vessel generator makes 'vase' or 'mug')."""
        return self.object_type

    def info(self) -> dict[str, Any]:
        return {"id": self.id, "objectType": self.object_type, "category": self.category, "description": self.description,
                "aliases": sorted(self.aliases), "params": {k: {"default": v.default, "kind": v.kind, "min": v.min, "max": v.max,
                                                                "choices": list(v.choices)} for k, v in self.params.items()}}


def segs(detail: int, base: int = 16) -> int:
    return int(base * (1, 1.5, 2.0)[max(0, min(detail, 3) - 1)])


def rot_yaw_pitch(yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0) -> tuple[float, float, float]:
    """Euler XYZ (degrees) equivalent to: roll about Z, then pitch about X, then yaw about Y (R = Ry·Rx·Rz)."""
    def rx(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])

    def ry(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])

    def rz(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])

    m = ry(math.radians(yaw_deg)) @ rx(math.radians(pitch_deg)) @ rz(math.radians(roll_deg))
    return euler_xyz_from_matrix(m)


def euler_xyz_from_matrix(m: np.ndarray) -> tuple[float, float, float]:
    m13 = max(-1.0, min(1.0, float(m[0, 2])))
    y = math.asin(m13)
    if abs(m13) < 0.9999999:
        x = math.atan2(-m[1, 2], m[2, 2])
        z = math.atan2(-m[0, 1], m[0, 0])
    else:
        x = math.atan2(m[2, 1], m[1, 1])
        z = 0.0
    return (math.degrees(x), math.degrees(y), math.degrees(z))


def direction_euler(d: tuple[float, float, float]) -> tuple[float, float, float]:
    """Euler XYZ rotating the +Y axis onto direction ``d``."""
    v = np.asarray(d, dtype=float)
    v = v / (np.linalg.norm(v) + 1e-12)
    yaxis = np.array([0.0, 1.0, 0.0])
    axis = np.cross(yaxis, v)
    s = np.linalg.norm(axis)
    c = float(np.dot(yaxis, v))
    if s < 1e-9:
        return (0.0, 0.0, 0.0) if c > 0 else (180.0, 0.0, 0.0)
    axis /= s
    angle = math.atan2(s, c)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    R = np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * (K @ K)
    return euler_xyz_from_matrix(R)


def strut(b: Builder, role: str, material: str, a: tuple[float, float, float], c: tuple[float, float, float],
          radius: float, segments: int = 12, name: str | None = None) -> None:
    """Cylinder spanning two points."""
    pa, pc = np.asarray(a, float), np.asarray(c, float)
    length = float(np.linalg.norm(pc - pa))
    mid = (pa + pc) / 2
    b.add(role, "cylinder", material, name=name, pos=tuple(mid), rot=direction_euler(tuple(pc - pa)),
          radius_top=radius, radius_bottom=radius, height=max(length, 1e-4), segments=segments)
