"""Model specification: parts, materials and transforms.

A :class:`ModelSpec` is the editable source of truth for a generated model. It
is JSON-serialisable, embedded in exported GLB files (``asset.extras``) so a
saved model can be reopened and modified later.
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass, field
from typing import Any

COLORS: dict[str, str] = {
    "red": "#c62828", "crimson": "#b5122a", "maroon": "#6d1022", "burgundy": "#7a1630", "pink": "#ec7fa9",
    "orange": "#ef6c00", "yellow": "#f9d71c", "gold": "#d4a72c", "green": "#2e7d32", "lime": "#9ccc3c",
    "olive": "#6b7b2b", "teal": "#00897b", "cyan": "#26c6da", "blue": "#1e5bd8", "navy": "#1a2a5c",
    "sky blue": "#7cc4f0", "light blue": "#90caf9", "purple": "#6a1b9a", "violet": "#8e44ad", "magenta": "#d81b60",
    "brown": "#6d4c41", "tan": "#c8a97e", "beige": "#e3d5b8", "cream": "#f3ead7", "white": "#f5f5f2",
    "black": "#151515", "grey": "#8a8d91", "gray": "#8a8d91", "dark grey": "#3c3f43", "dark gray": "#3c3f43",
    "light grey": "#c9ccd0", "light gray": "#c9ccd0", "silver": "#c0c4c8", "chrome": "#dfe3e6", "bronze": "#9c6b30",
    "copper": "#b87333", "wood": "#9a6b3f", "walnut": "#5d3a1a", "oak": "#b98b52", "glass": "#a8d8e8",
}


def parse_color(value: str) -> str | None:
    v = value.strip().lower()
    if v in COLORS:
        return COLORS[v]
    if len(v) in (4, 7) and v.startswith("#") and all(c in "0123456789abcdef" for c in v[1:]):
        if len(v) == 4:
            v = "#" + "".join(c * 2 for c in v[1:])
        return v
    return None


def hex_to_rgba(value: str, alpha: float = 1.0) -> list[float]:
    v = value.lstrip("#")
    r, g, b = (int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    # glTF base colours are linear; convert from sRGB.
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return [round(lin[0], 5), round(lin[1], 5), round(lin[2], 5), float(alpha)]


@dataclass
class MaterialSpec:
    name: str
    color: str
    metallic: float = 0.0
    roughness: float = 0.6
    opacity: float = 1.0
    emissive: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Transform:
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])  # Euler XYZ degrees
    scale: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])

    def quaternion(self) -> list[float]:
        return euler_to_quat(*self.rotation)


@dataclass
class Part:
    name: str
    role: str
    primitive: str
    params: dict[str, Any]
    material: str
    transform: Transform = field(default_factory=Transform)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "role": self.role, "primitive": self.primitive, "params": self.params,
                "material": self.material, "transform": asdict(self.transform)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Part":
        return cls(d["name"], d["role"], d["primitive"], d["params"], d["material"], Transform(**d.get("transform", {})))


@dataclass
class ModelSpec:
    object_type: str
    generator: str
    title: str
    params: dict[str, Any]
    detail: int = 1
    approximation: bool = False
    notes: list[str] = field(default_factory=list)
    material_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    part_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)  # role or part name -> transform deltas
    parts: list[Part] = field(default_factory=list)
    materials: dict[str, MaterialSpec] = field(default_factory=dict)
    request: str = ""
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_type": self.object_type, "generator": self.generator, "title": self.title, "params": self.params,
            "detail": self.detail, "approximation": self.approximation, "notes": self.notes,
            "material_overrides": self.material_overrides, "part_overrides": self.part_overrides,
            "parts": [p.to_dict() for p in self.parts], "materials": {k: m.to_dict() for k, m in self.materials.items()},
            "request": self.request, "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ModelSpec":
        return cls(
            object_type=d["object_type"], generator=d["generator"], title=d["title"], params=dict(d["params"]),
            detail=int(d.get("detail", 1)), approximation=bool(d.get("approximation", False)), notes=list(d.get("notes", [])),
            material_overrides=dict(d.get("material_overrides", {})), part_overrides=dict(d.get("part_overrides", {})),
            parts=[Part.from_dict(p) for p in d.get("parts", [])],
            materials={k: MaterialSpec(**m) for k, m in d.get("materials", {}).items()},
            request=d.get("request", ""), version=int(d.get("version", 1)),
        )

    def clone(self) -> "ModelSpec":
        return ModelSpec.from_dict(copy.deepcopy(self.to_dict()))

    def roles(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.parts:
            out[p.role] = out.get(p.role, 0) + 1
        return out


class Builder:
    """Small DSL used by generators to declare materials and parts."""

    def __init__(self) -> None:
        self.parts: list[Part] = []
        self.materials: dict[str, MaterialSpec] = {}
        self._names: dict[str, int] = {}

    def material(self, name: str, color: str, metallic: float = 0.0, roughness: float = 0.6, opacity: float = 1.0,
                 emissive: str | None = None) -> str:
        resolved = parse_color(color) or color
        self.materials[name] = MaterialSpec(name, resolved, metallic, roughness, opacity, emissive)
        return name

    def add(self, role: str, primitive: str, material: str, *, name: str | None = None, pos=(0, 0, 0), rot=(0, 0, 0),
            scale=(1, 1, 1), **params: Any) -> Part:
        base = name or role
        n = self._names.get(base, 0)
        self._names[base] = n + 1
        unique = base if n == 0 else f"{base}_{n + 1}"
        part = Part(unique, role, primitive, _jsonable(params), material,
                    Transform([float(v) for v in pos], [float(v) for v in rot], [float(v) for v in scale]))
        self.parts.append(part)
        return part


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, float):
        return round(v, 6)
    return v


def euler_to_quat(rx: float, ry: float, rz: float) -> list[float]:
    """XYZ intrinsic Euler (degrees) -> glTF quaternion [x, y, z, w]."""
    x, y, z = (math.radians(a) / 2 for a in (rx, ry, rz))
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    qx = sx * cy * cz + cx * sy * sz
    qy = cx * sy * cz - sx * cy * sz
    qz = cx * cy * sz + sx * sy * cz
    qw = cx * cy * cz - sx * sy * sz
    return [round(qx, 7), round(qy, 7), round(qz, 7), round(qw, 7)]
