"""Semantic construction planner and assembly.

``plan()`` turns a classification into a :class:`ModelSpec` (semantic params),
``assemble()`` runs the generator and applies user edits (material and part
overrides). For unsupported objects the planner refuses unless the user asked
for an approximation, in which case a composite of primitives may be planned
by the local language model — clearly labelled as an approximation.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from .classifier import Classification
from .generators.base import Generator, Param
from .mesh import build_primitive
from .registry import GeneratorRegistry
from .scene import Builder, MaterialSpec, ModelSpec, parse_color

COMPOSITE_ID = "approximation.composite"
COMPOSITE_PRIMS = {"box", "rounded_box", "cylinder", "cone", "sphere", "capsule", "torus"}
MAX_COMPOSITE_PARTS = 48


class PlanningError(Exception):
    pass


class CompositeGenerator(Generator):
    """Builds a model from an explicit primitive list (used for labelled approximations)."""

    id = COMPOSITE_ID
    object_type = "approximation"
    category = "approximation"
    params = {"parts": Param([], "list"), "color": Param("#c9ccd0", "color")}
    primary_color = "color"
    required_roles = {}

    def resolve(self, overrides: dict[str, Any]) -> dict[str, Any]:
        parts = overrides.get("parts") or []
        color = parse_color(str(overrides.get("color", "#c9ccd0"))) or "#c9ccd0"
        return {"parts": validate_composite_parts(parts), "color": color}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        for i, part in enumerate(p["parts"]):
            mat = b.material(f"m_{part['role']}_{i}", part.get("color") or p["color"], roughness=0.6)
            b.add(part["role"], part["primitive"], mat, name=part["name"], pos=part["position"], rot=part["rotation"], **part["params"])
        return b

    def required(self, p: dict[str, Any]) -> dict[str, int]:
        req: dict[str, int] = {}
        for part in p["parts"]:
            req[part["role"]] = req.get(part["role"], 0) + 1
        return req


def _num(v: Any, lo: float, hi: float, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise PlanningError(f"{name} must be a number")
    return max(lo, min(hi, float(v)))


def validate_composite_parts(parts: Any) -> list[dict[str, Any]]:
    if not isinstance(parts, list) or not parts:
        raise PlanningError("approximation needs at least one part")
    if len(parts) > MAX_COMPOSITE_PARTS:
        raise PlanningError(f"too many parts (max {MAX_COMPOSITE_PARTS})")
    out = []
    for i, raw in enumerate(parts):
        if not isinstance(raw, dict):
            raise PlanningError("each part must be an object")
        prim = str(raw.get("primitive", "box"))
        if prim not in COMPOSITE_PRIMS:
            raise PlanningError(f"primitive {prim!r} not allowed")
        size = raw.get("size", [0.2, 0.2, 0.2])
        if not isinstance(size, list) or len(size) != 3:
            raise PlanningError("size must be [x, y, z]")
        sx, sy, sz = (_num(s, 0.001, 100, "size") for s in size)
        params: dict[str, Any]
        if prim == "box":
            params = {"width": sx, "height": sy, "depth": sz}
        elif prim == "rounded_box":
            params = {"width": sx, "height": sy, "depth": sz, "radius": min(sx, sy, sz) * 0.2, "segments": 3}
        elif prim == "cylinder":
            params = {"radius_top": sx / 2, "radius_bottom": sx / 2, "height": sy, "segments": 24}
        elif prim == "cone":
            params = {"radius": sx / 2, "height": sy, "segments": 24}
        elif prim == "sphere":
            params = {"radius": sx / 2}
        elif prim == "capsule":
            params = {"radius": sx / 2, "length": max(0.001, sy - sx)}
        else:
            params = {"radius": sx / 2, "tube": max(0.001, sy / 2)}
        pos = raw.get("position", [0, 0, 0])
        rot = raw.get("rotation", [0, 0, 0])
        if not (isinstance(pos, list) and len(pos) == 3 and isinstance(rot, list) and len(rot) == 3):
            raise PlanningError("position/rotation must be [x, y, z]")
        name = re.sub(r"[^a-z0-9_]", "_", str(raw.get("name", f"part_{i}")).lower())[:40] or f"part_{i}"
        role = re.sub(r"[^a-z0-9_]", "_", str(raw.get("role", name)).lower())[:40] or name
        color = parse_color(str(raw.get("color", ""))) if raw.get("color") else None
        out.append({"name": name, "role": role, "primitive": prim, "params": params,
                    "position": [_num(v, -100, 100, "position") for v in pos],
                    "rotation": [_num(v, -360, 360, "rotation") for v in rot], "color": color, "size": [sx, sy, sz]})
    for part in out:  # make sure primitive params produce valid meshes
        build_primitive(part["primitive"], part["params"])
    return out


class Planner:
    def __init__(self, registry: GeneratorRegistry) -> None:
        self.registry = registry
        self.composite = CompositeGenerator()

    def generator_for(self, spec: ModelSpec) -> Generator:
        if spec.generator == COMPOSITE_ID:
            return self.composite
        gen = self.registry.get(spec.generator)
        if gen is None:
            raise PlanningError(f"unknown generator {spec.generator!r}")
        return gen

    def plan(self, cls: Classification) -> ModelSpec:
        if cls.status != "supported" or cls.generator is None:
            raise PlanningError(f"no generator supports '{cls.unknown_object}'")
        gen = cls.generator
        overrides: dict[str, Any] = dict(cls.preset)
        overrides.update(cls.choice_hints)
        primary_set = False
        for hint in cls.colors:
            if hint.role is None:
                if primary_set or not gen.primary_color:
                    continue
                overrides[gen.primary_color] = hint.color
                primary_set = True
            else:
                overrides[gen.color_params[hint.role]] = hint.color
        for noun, n in cls.counts.items():
            overrides[gen.count_params[noun]] = n
        params = gen.resolve(overrides)
        if cls.size_factor != 1.0:
            scaled = {p for ps in gen.dimension_params.values() for p in ps}
            for p in scaled:
                params[p] = gen.params[p].coerce(params[p] * cls.size_factor)
        for dim, f in cls.dimension_factors.items():
            for p in gen.dimension_params.get(dim, []):
                params[p] = gen.params[p].coerce(params[p] * f)
        for role, f in cls.role_size.items():
            for p in gen.size_params.get(role, []):
                params[p] = gen.params[p].coerce(params[p] * f)
        title = cls.alias.title()
        spec = ModelSpec(object_type=gen.type_for(params), generator=gen.id, title=title, params=params,
                         detail=max(1, min(cls.detail, gen.max_detail)), request=cls.request)
        return self.assemble(spec)

    def plan_composite(self, request: str, title: str, parts: list[dict[str, Any]], object_type: str) -> ModelSpec:
        params = self.composite.resolve({"parts": parts})
        spec = ModelSpec(object_type=object_type, generator=COMPOSITE_ID, title=f"{title} (approximation)", params=params,
                         detail=1, approximation=True, request=request,
                         notes=["Approximation composed of primitives by the local language model; no dedicated generator exists for this object."])
        return self.assemble(spec)

    def assemble(self, spec: ModelSpec) -> ModelSpec:
        gen = self.generator_for(spec)
        if gen is not self.composite:
            spec.object_type = gen.type_for(spec.params)
        builder = gen.build(spec.params, spec.detail)
        parts = builder.parts
        materials: dict[str, MaterialSpec] = dict(builder.materials)
        # material overrides: key is a role, material name, or "*"
        for key, props in spec.material_overrides.items():
            names = {p.material for p in parts if key in ("*", p.role, p.name)} | ({key} if key in materials else set())
            for name in names:
                m = materials[name]
                materials[name] = MaterialSpec(m.name, props.get("color", m.color), float(props.get("metallic", m.metallic)),
                                               float(props.get("roughness", m.roughness)), float(props.get("opacity", m.opacity)),
                                               props.get("emissive", m.emissive))
        for key, delta in spec.part_overrides.items():
            for part in parts:
                if key not in ("*", part.role, part.name):
                    continue
                tr = part.transform
                tr.position = [a + b for a, b in zip(tr.position, delta.get("position", [0, 0, 0]))]
                tr.rotation = [a + b for a, b in zip(tr.rotation, delta.get("rotation", [0, 0, 0]))]
                tr.scale = [a * b for a, b in zip(tr.scale, delta.get("scale", [1, 1, 1]))]
        spec.parts = parts
        spec.materials = materials
        roles = spec.roles()
        summary = ", ".join(f"{n}× {r}" for r, n in sorted(roles.items()))
        spec.notes = [n for n in spec.notes if not n.startswith("Construction:")] + [f"Construction: {summary}"]
        return spec


def composite_prompt(request: str, object_noun: str) -> list[dict[str, str]]:
    schema = ('{"title": str, "parts": [{"name": str, "role": str, "primitive": "box|rounded_box|cylinder|cone|sphere|capsule|torus", '
              '"size": [x, y, z], "position": [x, y, z], "rotation": [rx, ry, rz], "color": "#rrggbb"}]}')
    return [
        {"role": "system", "content": "You plan simple 3D blockouts from primitives. Units are metres, Y is up, the object stands on y=0, "
                                      "front faces +Z. Rotations are degrees. Reply with JSON only matching: " + schema},
        {"role": "user", "content": f"Plan a recognisable blockout of: {object_noun}. Original request: {request}. Use 4-30 parts."},
    ]


def parse_composite_reply(text: str) -> tuple[str, list[dict[str, Any]]]:
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise PlanningError("model reply was not JSON") from None
        try:
            data = json.loads(m.group(0))
        except ValueError:
            raise PlanningError("model reply was not valid JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("parts"), list):
        raise PlanningError("model reply missing 'parts'")
    return str(data.get("title", "Approximation"))[:60], data["parts"]
