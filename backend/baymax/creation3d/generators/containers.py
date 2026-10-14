"""Bottles and other lathe-turned containers."""

from __future__ import annotations

import math
from typing import Any

from ..scene import Builder
from .base import Generator, Param, segs


def _smooth(points: list[tuple[float, float]], samples: int = 3) -> list[tuple[float, float]]:
    """Catmull-Rom style densification of a (r, y) profile for smoother silhouettes."""
    if len(points) < 3:
        return points
    out = [points[0]]
    for i in range(len(points) - 1):
        p0 = points[max(0, i - 1)]
        p1, p2 = points[i], points[i + 1]
        p3 = points[min(len(points) - 1, i + 2)]
        for s in range(1, samples + 1):
            t = s / samples
            t2, t3 = t * t, t * t * t
            r = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((max(0.0, r), y))
    return out


class BottleGenerator(Generator):
    id = "container.bottle"
    object_type = "bottle"
    category = "container"
    description = "Lathe-turned bottle: water, wine, soda, beer, milk or flask, with cap and label."
    aliases = {"bottle": {}, "water bottle": {"style": "water"}, "wine bottle": {"style": "wine"}, "soda bottle": {"style": "soda"},
               "beer bottle": {"style": "beer"}, "milk bottle": {"style": "milk"}, "flask": {"style": "flask"},
               "perfume bottle": {"style": "flask", "height": 0.12}, "potion bottle": {"style": "flask"}}
    params = {"style": Param("water", "choice", choices=("water", "wine", "soda", "beer", "milk", "flask")),
              "height": Param(0.25, min=0.05, max=2.0), "radius": Param(0.035, min=0.01, max=0.5),
              "neck_radius": Param(0.013, min=0.004, max=0.3), "glass_color": Param("#9fd6e8", "color"),
              "cap_color": Param("blue", "color"), "label_color": Param("white", "color"), "label": Param(True, "bool")}
    primary_color = "glass_color"
    color_params = {"bottle": "glass_color", "body": "glass_color", "glass": "glass_color", "cap": "cap_color", "lid": "cap_color",
                    "label": "label_color"}
    size_params = {"bottle": ["height", "radius", "neck_radius"], "body": ["radius"], "neck": ["neck_radius"], "cap": ["neck_radius"]}
    dimension_params = {"height": ["height"], "width": ["radius"]}
    required_roles = {"body": 1, "cap": 1}

    STYLE = {"water": {}, "wine": {"height": 0.31, "radius": 0.038, "neck_radius": 0.0125, "glass_color": "#1e4d2b", "cap_color": "#5a1020"},
             "soda": {"height": 0.3, "radius": 0.045, "neck_radius": 0.013, "glass_color": "#b9dcc6", "cap_color": "red"},
             "beer": {"height": 0.23, "radius": 0.031, "neck_radius": 0.0125, "glass_color": "#6b3a12", "cap_color": "gold"},
             "milk": {"height": 0.22, "radius": 0.045, "neck_radius": 0.025, "glass_color": "#f0f0ea", "cap_color": "silver"},
             "flask": {"height": 0.18, "radius": 0.06, "neck_radius": 0.012, "glass_color": "#c7b8e8", "cap_color": "#6d4c41", "label": False}}

    def resolve(self, overrides: dict[str, Any]) -> dict[str, Any]:
        style = overrides.get("style", "water")
        return super().resolve({**self.STYLE.get(style, {}), **overrides})

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        opaque = p["style"] == "milk"
        glass = b.material("glass", p["glass_color"], metallic=0.0, roughness=0.08, opacity=0.95 if opaque else 0.55)
        cap = b.material("cap", p["cap_color"], metallic=0.5 if p["style"] in ("beer", "milk") else 0.0, roughness=0.4)
        H, R, nr = p["height"], p["radius"], p["neck_radius"]
        style = p["style"]
        cap_h = H * 0.07
        if style == "flask":
            prof = [(0.0, 0.0), (R * 0.6, 0.0), (R * 0.95, H * 0.12), (R, H * 0.35), (R * 0.8, H * 0.6), (nr * 1.1, H * 0.78), (nr, H - cap_h), (0.0, H - cap_h)]
        elif style == "wine":
            prof = [(0.0, 0.0), (R * 0.95, 0.0), (R, H * 0.03), (R, H * 0.6), (R * 0.75, H * 0.72), (nr * 1.15, H * 0.82), (nr, H - cap_h), (0.0, H - cap_h)]
        else:
            shoulder = {"water": 0.62, "soda": 0.55, "beer": 0.5, "milk": 0.7}.get(style, 0.6)
            prof = [(0.0, 0.0), (R * 0.9, 0.0), (R, H * 0.04), (R, H * shoulder), (R * 0.7, H * (shoulder + 0.12)),
                    (nr * 1.2, H * 0.84), (nr, H - cap_h), (0.0, H - cap_h)]
        prof = [prof[0]] + _smooth(prof[1:-1], 2 + detail) + [prof[-1]]
        b.add("body", "lathe", glass, name="bottle_body", profile=prof, segments=segs(detail, 24))
        b.add("cap", "cylinder", cap, pos=(0, H - cap_h / 2, 0), radius_top=nr * 1.15, radius_bottom=nr * 1.2, height=cap_h, segments=segs(detail, 20))
        if p["label"]:
            label = b.material("label", p["label_color"], roughness=0.7)
            b.add("label", "cylinder", label, pos=(0, H * 0.32, 0), radius_top=R * 1.006, radius_bottom=R * 1.006, height=H * 0.22,
                  segments=segs(detail, 24), caps=False)
        if detail >= 2 and style in ("water", "soda"):
            for y in (H * 0.12, H * 0.5):
                b.add("ridge", "torus", glass, name="grip_ridge", pos=(0, y, 0), radius=R * 0.995, tube=R * 0.03, radial_segments=6, tubular_segments=segs(detail, 24))
        return b


class VesselGenerator(Generator):
    id = "container.vessel"
    object_type = "cup"
    category = "container"
    description = "Hollow turned vessels: cup, mug, drinking glass, vase, bowl, jar, can, bucket."
    aliases = {"cup": {"kind": "cup"}, "mug": {"kind": "mug"}, "coffee mug": {"kind": "mug"}, "tea cup": {"kind": "cup"},
               "teacup": {"kind": "cup"}, "glass": {"kind": "glass"}, "drinking glass": {"kind": "glass"}, "tumbler": {"kind": "glass"},
               "vase": {"kind": "vase"}, "bowl": {"kind": "bowl"}, "jar": {"kind": "jar"}, "can": {"kind": "can"},
               "soda can": {"kind": "can"}, "tin can": {"kind": "can"}, "bucket": {"kind": "bucket"}, "pot": {"kind": "bucket"},
               "flower pot": {"kind": "vase", "color": "#b5603a"}}
    params = {"kind": Param("mug", "choice", choices=("cup", "mug", "glass", "vase", "bowl", "jar", "can", "bucket")),
              "height": Param(0.1, min=0.02, max=2.0), "radius": Param(0.04, min=0.01, max=1.0), "wall": Param(0.004, min=0.001, max=0.05),
              "color": Param("white", "color"), "accent_color": Param("crimson", "color"), "handle": Param(True, "bool")}
    primary_color = "color"
    color_params = {"body": "color", "cup": "color", "mug": "color", "vase": "color", "bowl": "color", "jar": "color", "can": "color",
                    "handle": "color", "lid": "accent_color", "rim": "accent_color", "band": "accent_color", "stripe": "accent_color"}
    size_params = {"body": ["height", "radius"], "handle": ["radius"]}
    dimension_params = {"height": ["height"], "width": ["radius"]}
    required_roles = {"body": 1}

    KIND = {"cup": {"height": 0.075, "radius": 0.042}, "mug": {"height": 0.1, "radius": 0.042},
            "glass": {"height": 0.12, "radius": 0.035, "color": "#cfe8f0", "handle": False},
            "vase": {"height": 0.3, "radius": 0.08, "color": "#2b6c8f", "handle": False},
            "bowl": {"height": 0.07, "radius": 0.08, "handle": False}, "jar": {"height": 0.14, "radius": 0.05, "color": "#d8ecf2", "handle": False},
            "can": {"height": 0.122, "radius": 0.033, "color": "red", "handle": False},
            "bucket": {"height": 0.3, "radius": 0.15, "color": "#3c78c8", "handle": True}}

    def resolve(self, overrides: dict[str, Any]) -> dict[str, Any]:
        kind = overrides.get("kind", "mug")
        return super().resolve({**self.KIND.get(kind, {}), **overrides})

    def type_for(self, p: dict[str, Any]) -> str:
        return p["kind"]

    def required(self, p: dict[str, Any]) -> dict[str, int]:
        req = {"body": 1}
        if p["kind"] in ("cup", "mug", "bucket") and p["handle"]:
            req["handle"] = 1
        return req

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        kind, H, R, t = p["kind"], p["height"], p["radius"], max(p["wall"], p["radius"] * 0.06)
        glassy = kind in ("glass", "jar")
        body = b.material("body", p["color"], metallic=0.85 if kind == "can" else 0.0, roughness=0.1 if glassy else 0.35,
                          opacity=0.45 if glassy else 1.0)
        acc = b.material("accent", p["accent_color"], roughness=0.4)
        seg = segs(detail, 28)
        if kind == "vase":
            outer = _smooth([(R * 0.55, 0.0), (R, H * 0.3), (R * 0.85, H * 0.6), (R * 0.4, H * 0.82), (R * 0.55, H)], 2 + detail)
        elif kind == "bowl":
            outer = [(R * (0.45 + 0.55 * math.sin(math.pi / 2 * i / 8)), H * (1 - math.cos(math.pi / 2 * i / 8))) for i in range(9)]
        elif kind in ("cup", "bucket"):
            outer = [(R * 0.72, 0.0), (R, H)]
        else:
            outer = [(R, 0.0), (R, H)]
        # hollow profile: base centre -> outer wall up -> lip -> inner wall down -> inner floor
        inner = [(max(r - t, 0.001), y) for r, y in reversed(outer)]
        inner[-1] = (inner[-1][0], max(inner[-1][1], t * 1.5))
        prof = [(0.0, 0.0)] + outer + inner + [(0.0, max(t * 1.5, inner[-1][1]))]
        if kind == "can":
            prof = [(0.0, 0.0), (R * 0.85, 0.0), (R, H * 0.05), (R, H * 0.93), (R * 0.85, H), (0.0, H)]
        b.add("body", "lathe", body, name=f"{kind}_body", profile=prof, segments=seg)
        if p["handle"] and kind in ("cup", "mug"):
            hh = H * 0.55
            path = [(R * 0.95 + math.sin(math.pi * i / 12) * R * 0.55, H * 0.25 + hh * (0.5 + 0.5 * math.cos(math.pi * i / 12)), 0.0)
                    for i in range(13)]
            b.add("handle", "tube", body, path=path, radius=t * 1.6, segments=10)
        if p["handle"] and kind == "bucket":
            path = [(R * math.cos(math.pi * i / 16), H + R * 0.8 * math.sin(math.pi * i / 16), 0.0) for i in range(17)]
            b.add("handle", "tube", b.material("metal", "silver", metallic=0.9, roughness=0.3), path=path, radius=R * 0.02, segments=8)
        if kind == "jar":
            b.add("lid", "cylinder", acc, pos=(0, H + H * 0.05, 0), radius_top=R * 1.02, radius_bottom=R * 1.04, height=H * 0.1, segments=seg)
        if kind == "can":
            b.add("lid", "torus", b.material("metal", "silver", metallic=0.9, roughness=0.3), name="rim", pos=(0, H, 0), radius=R * 0.85, tube=R * 0.04)
        if detail >= 2 and kind in ("mug", "cup", "can"):
            r_band = R * 1.01
            b.add("band", "cylinder", acc, pos=(0, H * 0.75 if kind != "can" else H * 0.5, 0), radius_top=r_band, radius_bottom=r_band,
                  height=H * (0.08 if kind != "can" else 0.4), segments=seg, caps=False)
        return b


GENERATORS = [BottleGenerator(), VesselGenerator()]
