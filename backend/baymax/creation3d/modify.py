"""Conversational and structured model modifications.

Instructions are parsed into explicit operations, applied to a copy of the
spec, and the model is regenerated. Instructions that cannot be mapped to a
known part/parameter return a clarification question instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .classifier import NUMBER_WORDS, _singular
from .planner import COMPOSITE_ID, Planner
from .scene import COLORS, ModelSpec, parse_color

VIEWS = {"front": "front", "back": "back", "rear": "back", "side": "side", "left": "left", "right": "right", "top": "top",
         "above": "top", "bird": "top", "bottom": "bottom", "below": "bottom", "iso": "iso", "isometric": "iso", "3/4": "iso", "angle": "iso"}
GROW = re.compile(r"\b(bigger|larger|increase|enlarge|grow|wider|taller|longer|thicker|more)\b")
SHRINK = re.compile(r"\b(smaller|decrease|reduce|shrink|narrower|shorter|thinner|less)\b")


class ModificationError(Exception):
    pass


@dataclass
class ModificationResult:
    spec: ModelSpec | None
    operations: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    view: str | None = None
    export: str | None = None
    undo: bool = False
    clarification: str = ""

    @property
    def changed(self) -> bool:
        return self.spec is not None and any(op["op"] not in ("camera", "export", "undo") for op in self.operations)


def _color_in(text: str) -> str | None:
    for name in sorted(COLORS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", text):
            return COLORS[name]
    m = re.search(r"#[0-9a-f]{6}\b", text)
    return m.group(0) if m else None


def _factor(text: str, grow: bool) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(%|percent)", text)
    if m:
        pct = float(m.group(1)) / 100.0
        return 1 + pct if grow else max(0.05, 1 - pct)
    m = re.search(r"\b(?:by|x|times)\s*(\d+(?:\.\d+)?)\b|\b(\d+(?:\.\d+)?)\s*(?:x|times)\b", text)
    if m:
        v = float(m.group(1) or m.group(2))
        if v > 0:
            return v if grow else 1 / v
    if re.search(r"\b(much|a lot|way)\b", text):
        return 1.6 if grow else 0.6
    if re.search(r"\b(slightly|a bit|a little)\b", text):
        return 1.1 if grow else 0.9
    return 1.25 if grow else 0.8


class Modifier:
    def __init__(self, planner: Planner) -> None:
        self.planner = planner

    def parse(self, spec: ModelSpec, instruction: str) -> ModificationResult:
        text = " ".join(re.sub(r"[^\w#%.\s/-]", " ", instruction.lower()).split())
        gen = self.planner.generator_for(spec)
        ops: list[dict[str, Any]] = []
        res = ModificationResult(None)
        if re.fullmatch(r"(please )?(undo|revert|go back)( that| the last change| last change)?( please)?", text):
            res.undo = True
            res.operations = [{"op": "undo"}]
            return res
        if re.search(r"\bexport\b|\bsave\b.*\b(glb|gltf)\b|\bdownload\b", text):
            fmt = "gltf" if "gltf" in text and "glb" not in text else "glb"
            ops.append({"op": "export", "format": fmt})
        if re.search(r"\b(camera|view|look at|angle|zoom|perspective)\b", text):
            view = next((VIEWS[w] for w in text.split() if w in VIEWS), "iso")
            ops.append({"op": "camera", "view": view})
        if re.search(r"\b(more|higher|extra|increase)\s+(detail|detailed|polygons|resolution)\b|\bmore detailed\b", text):
            ops.append({"op": "detail", "delta": 1})
        elif re.search(r"\b(less|lower|reduce)\s+(detail|detailed|polygons|resolution)\b|\bsimpler\b|\bless detailed\b", text):
            ops.append({"op": "detail", "delta": -1})
        color = _color_in(text)
        roles = sorted(set(gen.color_params) | {p.role for p in spec.parts}, key=len, reverse=True)
        target = self._target(text, roles)
        if color:
            ops.append({"op": "color", "target": target, "color": color})
        if re.search(r"\b(metallic|metal|chrome|shiny|glossy)\b", text):
            ops.append({"op": "material", "target": target or "*", "props": {"metallic": 0.9, "roughness": 0.2}})
        if re.search(r"\b(matte|matt|rough|dull)\b", text):
            ops.append({"op": "material", "target": target or "*", "props": {"metallic": 0.0, "roughness": 0.9}})
        if re.search(r"\b(transparent|see[- ]through|translucent)\b", text):
            ops.append({"op": "material", "target": target or "*", "props": {"opacity": 0.45}})
        if re.search(r"\bopaque\b", text):
            ops.append({"op": "material", "target": target or "*", "props": {"opacity": 1.0}})
        # counts: "6 arms", "add a floor", "three legs"
        m = re.search(r"\b(\d{1,3}|" + "|".join(NUMBER_WORDS) + r")\s+(\w+)", text)
        if m and _singular(m.group(2)) in gen.count_params:
            n = int(m.group(1)) if m.group(1).isdigit() else NUMBER_WORDS[m.group(1)]
            ops.append({"op": "set_param", "param": gen.count_params[_singular(m.group(2))], "value": n})
        else:
            m2 = re.search(r"\b(add|remove)\s+(?:a|an|one|another)?\s*(\w+)", text)
            if m2 and _singular(m2.group(2)) in gen.count_params:
                param = gen.count_params[_singular(m2.group(2))]
                ops.append({"op": "add_param", "param": param, "delta": 1 if m2.group(1) == "add" else -1})
        # style changes: "make it an suv", "make it round"
        for pname, prm in gen.params.items():
            if prm.kind == "choice":
                for choice in prm.choices:
                    if re.search(rf"\b(make it|turn it into|change it to|as|into|style)\b.*\b{choice}\b", text):
                        ops.append({"op": "set_param", "param": pname, "value": choice})
        for alias, preset in gen.aliases.items():
            if "style" in preset and re.search(rf"\b(make it|turn it into|change it to|into)\s+(a|an)?\s*{re.escape(alias)}\b", text):
                ops.append({"op": "set_param", "param": "style", "value": preset["style"]})
        # sizes
        grow, shrink = GROW.search(text), SHRINK.search(text)
        size_ctx = re.search(r"\b(size|bigger|larger|smaller|wider|narrower|taller|shorter|longer|thicker|thinner|scale|enlarge|shrink|grow)\b", text)
        if (grow or shrink) and size_ctx and not any(o["op"] in ("add_param", "detail") for o in ops):
            f = _factor(text, bool(grow) and not (shrink and shrink.start() < grow.start()))
            dim = None
            for word, d in (("taller", "height"), ("shorter", "height"), ("height", "height"), ("tall", "height"), ("wider", "width"),
                            ("narrower", "width"), ("width", "width"), ("wide", "width"), ("longer", "length"), ("length", "length"), ("long", "length")):
                if re.search(rf"\b{word}\b", text):
                    dim = d
                    break
            if target and target in gen.size_params and gen.size_params[target] and not dim:
                ops.append({"op": "scale_param", "params": list(gen.size_params[target]), "factor": f})
            elif dim and (not target or target in (spec.object_type, "it", "model")) and gen.dimension_params.get(dim):
                ops.append({"op": "scale_param", "params": list(gen.dimension_params[dim]), "factor": f})
            elif target and any(p.role == target for p in spec.parts):
                ops.append({"op": "transform", "target": target, "scale": [f, f, f]})
            elif not target:
                all_dims = sorted({p for ps in gen.dimension_params.values() for p in ps})
                if all_dims:
                    ops.append({"op": "scale_param", "params": all_dims, "factor": f})
                else:
                    ops.append({"op": "transform", "target": "*", "scale": [f, f, f]})
        # rotation: "rotate the arm 45 degrees"
        mr = re.search(r"\brotate\b.*?(-?\d+(?:\.\d+)?)\s*(?:deg|degrees|°)?", text)
        if mr:
            ops.append({"op": "transform", "target": target or "*", "rotation": [0.0, float(mr.group(1)), 0.0]})
        # robotic-arm style poses: "raise the arm", "lower the arm", "open the gripper"
        if "shoulder_angle" in gen.params:
            if re.search(r"\b(raise|lift)\b", text):
                ops.append({"op": "add_param", "param": "shoulder_angle", "delta": -20})
            if re.search(r"\blower\b", text):
                ops.append({"op": "add_param", "param": "shoulder_angle", "delta": 20})
            if re.search(r"\bopen\b.*\b(gripper|claw)\b", text):
                ops.append({"op": "set_param", "param": "gripper_open", "value": 1.0})
            if re.search(r"\bclose\b.*\b(gripper|claw)\b", text):
                ops.append({"op": "set_param", "param": "gripper_open", "value": 0.0})
        if not ops:
            res.clarification = ("I couldn't map that to a change I can make. You can change colours ('make the roof blue'), sizes "
                                 "('make the wheels bigger'), counts ('use 6 arms'), detail ('more detailed'), materials ('make it metallic'), "
                                 "the camera ('view from the top') or export ('export as GLB').")
            return res
        if any(o["op"] == "color" and o["target"] is None and not gen.primary_color for o in ops):
            res.clarification = "Which part should change colour?"
            return res
        res.operations = ops
        return res

    @staticmethod
    def _target(text: str, roles: list[str]) -> str | None:
        words = [_singular(w) for w in text.split()]
        for role in roles:
            if role in words or role.replace("_", " ") in text:
                return role
        return None

    def apply(self, spec: ModelSpec, operations: list[dict[str, Any]]) -> ModelSpec:
        new = spec.clone()
        gen = self.planner.generator_for(new)
        for op in operations:
            kind = op.get("op")
            if kind in ("camera", "export", "undo"):
                continue
            if kind == "color":
                color = parse_color(str(op["color"]))
                if not color:
                    raise ModificationError(f"unknown colour {op['color']!r}")
                target = op.get("target")
                param = gen.color_params.get(target) if target else gen.primary_color
                if target is None and new.generator == COMPOSITE_ID:
                    new.material_overrides["*"] = {**new.material_overrides.get("*", {}), "color": color}
                elif param and param in gen.params:
                    new.params[param] = gen.params[param].coerce(color)
                elif target and any(p.role == target for p in new.parts):
                    new.material_overrides[target] = {**new.material_overrides.get(target, {}), "color": color}
                else:
                    raise ModificationError(f"this model has no part called '{target}'")
            elif kind == "material":
                target = op.get("target") or "*"
                new.material_overrides[target] = {**new.material_overrides.get(target, {}), **op["props"]}
            elif kind == "detail":
                new.detail = max(1, min(gen.max_detail, new.detail + int(op["delta"])))
            elif kind == "set_param":
                pname = op["param"]
                if pname not in gen.params:
                    raise ModificationError(f"parameter {pname!r} does not exist for {new.object_type}")
                try:
                    new.params[pname] = gen.params[pname].coerce(op["value"])
                except ValueError as exc:
                    raise ModificationError(f"{pname}: {exc}") from None
            elif kind == "add_param":
                pname = op["param"]
                if pname not in gen.params:
                    raise ModificationError(f"parameter {pname!r} does not exist")
                new.params[pname] = gen.params[pname].coerce(new.params[pname] + op["delta"])
            elif kind == "scale_param":
                f = float(op["factor"])
                if not (0.05 <= f <= 20):
                    raise ModificationError("scale factor out of range (0.05-20)")
                for pname in op["params"]:
                    if pname in gen.params and gen.params[pname].kind in ("float", "int"):
                        new.params[pname] = gen.params[pname].coerce(new.params[pname] * f)
            elif kind == "transform":
                target = op.get("target") or "*"
                cur = new.part_overrides.get(target, {})
                if "scale" in op:
                    cur["scale"] = [a * b for a, b in zip(cur.get("scale", [1, 1, 1]), op["scale"])]
                if "position" in op:
                    cur["position"] = [a + b for a, b in zip(cur.get("position", [0, 0, 0]), op["position"])]
                if "rotation" in op:
                    cur["rotation"] = [a + b for a, b in zip(cur.get("rotation", [0, 0, 0]), op["rotation"])]
                new.part_overrides[target] = cur
            else:
                raise ModificationError(f"unknown operation {kind!r}")
        new.version = spec.version + 1
        return self.planner.assemble(new)
