"""Footwear."""

from __future__ import annotations

import math
from typing import Any

from ..scene import Builder
from .base import Generator, Param


def _foot_outline(length: float, width: float, n: int = 28) -> list[tuple[float, float]]:
    """Asymmetric foot outline in (x, y): y runs heel (-L/2) to toe (+L/2)."""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        y = math.sin(a) * length / 2
        t = (y / (length / 2) + 1) / 2  # 0 heel .. 1 toe
        w = width / 2 * (0.62 + 0.38 * math.sin(math.pi * min(1.0, t * 1.1)) ** 0.6)
        w *= 1.0 + (0.06 if math.cos(a) < 0 and t > 0.55 else 0.0)  # medial bulge
        pts.append((math.cos(a) * w, y))
    return pts


def _upper_section(width: float, height: float, n: int) -> list[tuple[float, float]]:
    """Closed arch cross-section: flat bottom, rounded top."""
    pts = []
    for i in range(n):
        a = math.pi * i / (n - 1)
        pts.append((math.cos(a) * width / 2, max(0.0, math.sin(a)) ** 0.8 * height))
    return pts  # the polygon closes along the flat bottom


class ShoeGenerator(Generator):
    id = "wearable.shoe"
    object_type = "shoe"
    category = "wearable"
    description = "Shoe: sneaker, boot or high heel, with lofted upper, sole, laces and heel counter."
    aliases = {"shoe": {}, "sneaker": {"style": "sneaker"}, "trainer": {"style": "sneaker"}, "running shoe": {"style": "sneaker"},
               "boot": {"style": "boot"}, "hiking boot": {"style": "boot"}, "high heel": {"style": "heel"}, "heel": {"style": "heel"},
               "pump": {"style": "heel"}, "stiletto": {"style": "heel"}}
    params = {"style": Param("sneaker", "choice", choices=("sneaker", "boot", "heel")), "length": Param(0.28, min=0.12, max=0.4),
              "width": Param(0.1, min=0.05, max=0.16), "upper_color": Param("white", "color"), "sole_color": Param("#e8e2d6", "color"),
              "accent_color": Param("crimson", "color"), "lace_color": Param("white", "color"), "heel_height": Param(0.09, min=0.02, max=0.14)}
    primary_color = "upper_color"
    color_params = {"shoe": "upper_color", "upper": "upper_color", "body": "upper_color", "sole": "sole_color", "midsole": "sole_color",
                    "accent": "accent_color", "swoosh": "accent_color", "stripe": "accent_color", "heel": "accent_color",
                    "lace": "lace_color", "laces": "lace_color", "toe": "accent_color"}
    size_params = {"shoe": ["length", "width"], "sole": ["width"], "heel": ["heel_height"]}
    dimension_params = {"length": ["length"], "width": ["width"], "height": ["heel_height"]}
    required_roles = {"sole": 1, "upper": 1}

    def type_for(self, p: dict[str, Any]) -> str:
        return "boot" if p["style"] == "boot" else "shoe"

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        upper = b.material("upper", p["upper_color"], roughness=0.75)
        sole = b.material("sole", p["sole_color"], roughness=0.9)
        acc = b.material("accent", p["accent_color"], roughness=0.6)
        lace = b.material("laces", p["lace_color"], roughness=0.9)
        L, W, style = p["length"], p["width"], p["style"]
        n = 10 + detail * 4
        sole_t = 0.028 if style != "heel" else 0.008
        outline = _foot_outline(L, W, 24 + detail * 8)
        tilt = 0.0
        s_t = 0.0
        if style == "heel":
            tilt = math.degrees(math.atan2(p["heel_height"], L * 0.75))
            s_t = math.sin(math.radians(tilt))
        # Rotating about X by +tilt raises the heel (z<0) and lowers the toe; lift so the toe touches the ground.
        sole_y = sole_t / 2 + (L / 2) * s_t
        # outline is (x, y=length); rotating +90° about X lays it flat with +y -> +z (toe forward)
        b.add("sole", "extrude", sole, pos=(0, sole_y, 0), rot=(90 + tilt, 0, 0), polygon=outline, depth=sole_t)
        sections = []
        steps = 6 + detail * 2
        for i in range(steps + 1):
            t = i / steps  # heel -> toe
            z = -L / 2 + L * 0.03 + t * L * 0.94
            w = W * (0.82 + 0.18 * math.sin(math.pi * min(1.0, t * 1.15)))
            if style == "heel":
                h = 0.02 + 0.045 * (1 - t) ** 1.2 + 0.01
            else:
                h = (0.055 if t < 0.3 else 0.055 - (t - 0.3) * 0.06) + (0.02 if style == "boot" else 0)
            h = max(h, 0.018)
            sections.append((z, _upper_section(w, h, n)))
        base_y = sole_y + sole_t / 2 - 0.002
        b.add("upper", "loft", upper, pos=(0, base_y, 0), rot=(tilt, 0, 0), sections=sections)
        if style == "boot":
            b.add("upper", "rounded_box", upper, name="shaft", pos=(0, base_y + 0.1, -L * 0.3), width=W * 0.85, height=0.16, depth=L * 0.36,
                  radius=0.03, segments=2 + detail)
            b.add("collar", "torus", acc, pos=(0, base_y + 0.18, -L * 0.3), scale=(W * 0.45 / 0.1, 1, L * 0.19 / 0.1), radius=0.1, tube=0.012,
                  radial_segments=6, tubular_segments=24)
        else:
            b.add("collar", "torus", acc, pos=(0, base_y + (0.065 if style == "sneaker" else 0.03 + 0.28 * L * s_t), -L * 0.28), rot=(tilt - 8, 0, 0),
                  scale=(W * 0.36 / 0.1, 1, L * 0.16 / 0.1), radius=0.1, tube=0.008, radial_segments=6, tubular_segments=24)
        if style == "heel":
            heel_top = sole_y + 0.42 * L * s_t - sole_t / 2
            b.add("heel", "cylinder", acc, name="heel_post", pos=(0, heel_top / 2, -L * 0.42), radius_top=0.012, radius_bottom=0.006,
                  height=heel_top, segments=12)
        else:
            b.add("heel", "rounded_box", acc, name="heel_counter", pos=(0, base_y + 0.03, -L / 2 + 0.02), width=W * 0.8, height=0.05, depth=0.03,
                  radius=0.012, segments=2)
            b.add("toe", "sphere", acc if style == "boot" else sole, name="toe_cap", pos=(0, base_y + 0.008, L * 0.4),
                  scale=(W * 0.5 / 0.05, 0.35, 1.0), radius=0.05, width_segments=16, height_segments=10)
            b.add("tongue", "rounded_box", upper, pos=(0, base_y + 0.07 + (0.02 if style == "boot" else 0), -L * 0.08), rot=(-25, 0, 0),
                  width=W * 0.45, height=0.012, depth=L * 0.3, radius=0.005, segments=2)
            rows = 4 + detail
            for k in range(rows):
                z = -L * 0.2 + k * L * 0.32 / rows
                y = base_y + 0.06 - k * 0.004 + (0.02 if style == "boot" else 0)
                b.add("lace", "tube", lace, name="lace", path=[(-W * 0.22, y, z), (0, y + 0.006, z + 0.006), (W * 0.22, y, z)], radius=0.0025, segments=6)
            if detail >= 2 and style == "sneaker":
                for side in (-1, 1):
                    b.add("stripe", "tube", acc, name="side_stripe",
                          path=[(side * W * 0.47, base_y + 0.012, -L * 0.25), (side * W * 0.5, base_y + 0.03, 0.0), (side * W * 0.46, base_y + 0.045, L * 0.15)],
                          radius=0.004, segments=6)
        return b


GENERATORS = [ShoeGenerator()]
