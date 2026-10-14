"""Mechanical components."""

from __future__ import annotations

import math
from typing import Any

from ..mesh import regular_polygon
from ..scene import Builder
from .base import Generator, Param, segs


def _ring(inner: float, outer: float, height: float, y0: float = 0.0) -> list[tuple[float, float]]:
    return [(inner, y0), (outer, y0), (outer, y0 + height), (inner, y0 + height), (inner, y0 + 0.0001)]


class GearGenerator(Generator):
    id = "mechanical.gear"
    object_type = "gear"
    category = "mechanical"
    description = "Spur gear with configurable teeth, hub and bore."
    aliases = {"gear": {}, "cog": {}, "cogwheel": {}, "spur gear": {}, "sprocket": {"teeth": 32}, "gear wheel": {}}
    params = {"teeth": Param(24, "int", 6, 120), "radius": Param(0.05, min=0.005, max=2.0), "thickness": Param(0.012, min=0.001, max=0.5),
              "bore": Param(0.25, min=0.0, max=0.6, description="bore radius as fraction of root radius"), "color": Param("silver", "color")}
    primary_color = "color"
    color_params = {"gear": "color", "body": "color", "hub": "color", "teeth": "color"}
    size_params = {"gear": ["radius", "thickness"], "teeth": ["radius"], "bore": ["bore"], "hub": ["bore"]}
    count_params = {"teeth": "teeth", "tooth": "teeth"}
    dimension_params = {"height": ["thickness"], "width": ["radius"], "length": ["radius"]}
    required_roles = {"gear": 1, "hub": 1}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        m = b.material("steel", p["color"], metallic=0.9, roughness=0.3)
        dark = b.material("bore", "#202224", metallic=0.6, roughness=0.5)
        R, T, n = p["radius"], p["thickness"], p["teeth"]
        tooth = min(R * 0.18, 2.4 * math.pi * R / n)
        root = R - tooth
        b.add("gear", "gear", m, pos=(0, T / 2, 0), rot=(90, 0, 0), teeth=n, outer_radius=R, root_radius=root, thickness=T)
        hub_r = root * 0.45
        bore_r = root * p["bore"]
        b.add("hub", "lathe", m, pos=(0, -T * 0.25, 0), profile=_ring(max(bore_r, root * 0.05), hub_r, T * 1.5), segments=segs(detail, 32))
        if bore_r > 0:
            b.add("bore", "cylinder", dark, pos=(0, T / 2, 0), radius_top=bore_r * 0.98, radius_bottom=bore_r * 0.98, height=T * 2.05,
                  segments=segs(detail, 24), caps=True)
            b.add("keyway", "box", dark, pos=(bore_r, T / 2, 0), width=bore_r * 0.5, height=T * 2.06, depth=bore_r * 0.5)
        return b


class BoltGenerator(Generator):
    id = "mechanical.bolt"
    object_type = "bolt"
    category = "mechanical"
    description = "Hex bolt or screw with helical thread (and optional nut)."
    aliases = {"bolt": {}, "hex bolt": {}, "screw": {"head": "round"}, "machine screw": {"head": "round"}}
    params = {"diameter": Param(0.01, min=0.002, max=0.1), "length": Param(0.05, min=0.005, max=0.5), "head": Param("hex", "choice", choices=("hex", "round")),
              "with_nut": Param(False, "bool"), "color": Param("silver", "color")}
    primary_color = "color"
    color_params = {"bolt": "color", "screw": "color", "head": "color", "thread": "color", "nut": "color"}
    size_params = {"bolt": ["diameter", "length"], "head": ["diameter"], "thread": ["diameter"]}
    dimension_params = {"length": ["length"], "height": ["length"], "width": ["diameter"]}
    required_roles = {"head": 1, "shaft": 1, "thread": 1}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        m = b.material("steel", p["color"], metallic=0.95, roughness=0.25)
        d, L = p["diameter"], p["length"]
        r = d / 2
        head_h = d * 0.65
        if p["head"] == "hex":
            b.add("head", "extrude", m, pos=(0, L + head_h / 2, 0), rot=(90, 0, 0), polygon=regular_polygon(6, d * 0.9), depth=head_h)
        else:
            b.add("head", "lathe", m, pos=(0, L, 0), profile=[(0.0, 0.0), (d * 0.9, 0.0)] + [(d * 0.9 * math.cos(a / 8 * math.pi / 2), head_h * math.sin(a / 8 * math.pi / 2)) for a in range(1, 9)],
                  segments=segs(detail, 24))
            b.add("slot", "box", b.material("slot", "#303234"), pos=(0, L + head_h * 0.95, 0), width=d * 1.5, height=head_h * 0.4, depth=d * 0.18)
        b.add("shaft", "cylinder", m, pos=(0, L / 2, 0), radius_top=r * 0.86, radius_bottom=r * 0.86, height=L, segments=segs(detail, 20))
        pitch = d * 0.15
        turns = L * 0.8 / pitch
        b.add("thread", "helix", m, pos=(0, L * 0.02, 0), radius=r * 0.88, pitch=pitch, turns=min(turns, 200.0), wire=r * 0.12, segments_per_turn=12 + detail * 4)
        if p["with_nut"]:
            b.add("nut", "lathe", m, pos=(0, L * 0.25, 0), profile=_ring(r, d * 0.9, d * 0.8), segments=6)
        return b


class NutGenerator(Generator):
    id = "mechanical.nut"
    object_type = "nut"
    category = "mechanical"
    description = "Hex nut or washer."
    aliases = {"nut": {}, "hex nut": {}, "washer": {"kind": "washer"}}
    params = {"diameter": Param(0.01, min=0.002, max=0.1), "kind": Param("hex", "choice", choices=("hex", "washer")), "color": Param("silver", "color")}
    primary_color = "color"
    color_params = {"nut": "color", "washer": "color", "body": "color"}
    size_params = {"nut": ["diameter"], "washer": ["diameter"]}
    dimension_params = {"width": ["diameter"], "height": ["diameter"], "length": ["diameter"]}
    required_roles = {"body": 1}

    def type_for(self, p: dict[str, Any]) -> str:
        return "washer" if p["kind"] == "washer" else "nut"

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        m = b.material("steel", p["color"], metallic=0.95, roughness=0.25)
        d = p["diameter"]
        if p["kind"] == "hex":
            b.add("body", "lathe", m, profile=_ring(d / 2, d * 0.95, d * 0.8), segments=6)
            b.add("thread", "helix", m, pos=(0, d * 0.05, 0), radius=d / 2 * 1.02, pitch=d * 0.15, turns=4.5, wire=d * 0.04)
        else:
            b.add("body", "lathe", m, profile=_ring(d / 2 * 1.05, d * 1.1, d * 0.12), segments=segs(detail, 32))
        return b


class BearingGenerator(Generator):
    id = "mechanical.bearing"
    object_type = "bearing"
    category = "mechanical"
    description = "Radial ball bearing: inner/outer races, balls and cage."
    aliases = {"bearing": {}, "ball bearing": {}, "roller bearing": {}, "radial bearing": {}}
    params = {"outer_diameter": Param(0.05, min=0.005, max=1.0), "inner_diameter": Param(0.025, min=0.002, max=0.9), "width": Param(0.012, min=0.002, max=0.3),
              "balls": Param(9, "int", 5, 30), "color": Param("silver", "color"), "ball_color": Param("chrome", "color")}
    primary_color = "color"
    color_params = {"bearing": "color", "race": "color", "ring": "color", "ball": "ball_color", "cage": "color"}
    size_params = {"bearing": ["outer_diameter", "inner_diameter", "width"], "ball": ["width"]}
    count_params = {"ball": "balls"}
    dimension_params = {"width": ["outer_diameter"], "height": ["width"], "length": ["outer_diameter"]}
    required_roles = {"outer_race": 1, "inner_race": 1, "ball": 5}

    def required(self, p: dict[str, Any]) -> dict[str, int]:
        return {"outer_race": 1, "inner_race": 1, "ball": p["balls"]}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        m = b.material("steel", p["color"], metallic=0.95, roughness=0.2)
        bm = b.material("balls", p["ball_color"], metallic=1.0, roughness=0.08)
        ro, ri, w = p["outer_diameter"] / 2, min(p["inner_diameter"], p["outer_diameter"] * 0.8) / 2, p["width"]
        gap = ro - ri
        seg = segs(detail, 40)
        b.add("outer_race", "lathe", m, profile=_ring(ro - gap * 0.28, ro, w), segments=seg)
        b.add("inner_race", "lathe", m, profile=_ring(ri, ri + gap * 0.28, w), segments=seg)
        pitch_r = (ro + ri) / 2
        ball_r = min(gap * 0.24, math.pi * pitch_r / p["balls"] * 0.45)
        for k in range(p["balls"]):
            a = 2 * math.pi * k / p["balls"]
            b.add("ball", "sphere", bm, name="ball", pos=(pitch_r * math.cos(a), w / 2, pitch_r * math.sin(a)), radius=ball_r,
                  width_segments=12 + detail * 4, height_segments=8 + detail * 2)
        if detail >= 2:
            b.add("cage", "torus", b.material("cage", "bronze", metallic=0.7, roughness=0.4), pos=(0, w / 2, 0), radius=pitch_r, tube=ball_r * 0.25,
                  radial_segments=6, tubular_segments=seg)
        return b


class SpringGenerator(Generator):
    id = "mechanical.spring"
    object_type = "spring"
    category = "mechanical"
    description = "Helical compression spring."
    aliases = {"spring": {}, "coil spring": {}, "compression spring": {}, "helical spring": {}}
    params = {"radius": Param(0.02, min=0.002, max=1.0), "wire": Param(0.0025, min=0.0003, max=0.1), "coils": Param(8, "int", 2, 60),
              "pitch": Param(0.012, min=0.001, max=0.5), "color": Param("silver", "color")}
    primary_color = "color"
    color_params = {"spring": "color", "coil": "color", "wire": "color"}
    size_params = {"spring": ["radius", "wire", "pitch"], "coil": ["radius"], "wire": ["wire"]}
    count_params = {"coil": "coils", "turn": "coils"}
    dimension_params = {"height": ["pitch"], "length": ["pitch"], "width": ["radius"]}
    required_roles = {"coil": 1}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        m = b.material("steel", p["color"], metallic=0.95, roughness=0.25)
        b.add("coil", "helix", m, pos=(0, p["wire"], 0), radius=p["radius"], pitch=p["pitch"], turns=float(p["coils"]), wire=p["wire"],
              segments_per_turn=16 + detail * 8)
        return b


class PipeGenerator(Generator):
    id = "mechanical.pipe"
    object_type = "pipe"
    category = "mechanical"
    description = "Pipe section with optional flanges and bolt holes (visual)."
    aliases = {"pipe": {}, "pipe section": {}, "flanged pipe": {"flanges": True}, "tube section": {}}
    params = {"length": Param(0.6, min=0.02, max=20), "outer_radius": Param(0.05, min=0.003, max=2), "wall": Param(0.006, min=0.0005, max=0.5),
              "flanges": Param(True, "bool"), "color": Param("#6f7b85", "color")}
    primary_color = "color"
    color_params = {"pipe": "color", "flange": "color", "body": "color"}
    size_params = {"pipe": ["outer_radius", "length"], "flange": ["outer_radius"]}
    dimension_params = {"length": ["length"], "height": ["length"], "width": ["outer_radius"]}
    required_roles = {"pipe": 1}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        m = b.material("steel", p["color"], metallic=0.8, roughness=0.4)
        L, R, t = p["length"], p["outer_radius"], min(p["wall"], p["outer_radius"] * 0.5)
        seg = segs(detail, 32)
        b.add("pipe", "lathe", m, pos=(0, 0, 0), rot=(0, 0, 90), profile=_ring(R - t, R, L, -L / 2), segments=seg)
        if p["flanges"]:
            for x in (-L / 2, L / 2 - R * 0.25):
                b.add("flange", "lathe", m, name="flange", pos=(x, 0, 0), rot=(0, 0, -90), profile=_ring(R - t, R * 1.8, R * 0.25, -R * 0.25 if x > 0 else 0.0),
                      segments=seg)
                for k in range(6):
                    a = 2 * math.pi * k / 6
                    b.add("bolt_head", "cylinder", b.material("bolt", "#303234", metallic=0.7), name="flange_bolt",
                          pos=(x + (R * 0.27 if x > 0 else -0.02 * R), R * 1.45 * math.cos(a), R * 1.45 * math.sin(a)), rot=(0, 0, 90),
                          radius_top=R * 0.12, radius_bottom=R * 0.12, height=R * 0.1, segments=6)
        return b


class BracketGenerator(Generator):
    id = "mechanical.bracket"
    object_type = "bracket"
    category = "mechanical"
    description = "L-shaped mounting bracket with gusset and (visual) holes."
    aliases = {"bracket": {}, "l bracket": {}, "angle bracket": {}, "mounting bracket": {}}
    params = {"size": Param(0.08, min=0.01, max=2), "thickness": Param(0.005, min=0.0005, max=0.1), "width": Param(0.04, min=0.005, max=1),
              "gusset": Param(True, "bool"), "color": Param("#9aa3ab", "color")}
    primary_color = "color"
    color_params = {"bracket": "color", "body": "color"}
    size_params = {"bracket": ["size", "width"]}
    dimension_params = {"height": ["size"], "length": ["size"], "width": ["width"]}
    required_roles = {"bracket": 1}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        m = b.material("metal", p["color"], metallic=0.8, roughness=0.35)
        dark = b.material("hole", "#151515")
        S, t, W = p["size"], p["thickness"], p["width"]
        b.add("bracket", "extrude", m, pos=(0, 0, 0), polygon=[(0, 0), (S, 0), (S, t), (t, t), (t, S), (0, S)], depth=W)
        if p["gusset"]:
            b.add("gusset", "extrude", m, pos=(0, 0, 0), polygon=[(t, t), (S * 0.5, t), (t, S * 0.5)], depth=t)
        for z in (-W * 0.25, W * 0.25):
            b.add("hole", "cylinder", dark, name="hole", pos=(S * 0.72, t / 2, z), radius_top=W * 0.08, radius_bottom=W * 0.08, height=t * 1.1, segments=16)
            b.add("hole", "cylinder", dark, name="hole", pos=(t / 2, S * 0.72, z), rot=(0, 0, 90), radius_top=W * 0.08, radius_bottom=W * 0.08,
                  height=t * 1.1, segments=16)
        return b


GENERATORS = [GearGenerator(), BoltGenerator(), NutGenerator(), BearingGenerator(), SpringGenerator(), PipeGenerator(), BracketGenerator()]
