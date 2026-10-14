"""Lamps, trees, electronics and crates."""

from __future__ import annotations

import math
from typing import Any

from ..scene import Builder
from .base import Generator, Param, segs, strut


class LampGenerator(Generator):
    id = "lighting.lamp"
    object_type = "lamp"
    category = "lighting"
    description = "Desk, table or floor lamp with shade and emissive bulb."
    aliases = {"lamp": {}, "desk lamp": {"style": "desk"}, "table lamp": {"style": "table"}, "floor lamp": {"style": "floor"},
               "reading lamp": {"style": "desk"}, "standing lamp": {"style": "floor"}}
    params = {"style": Param("table", "choice", choices=("desk", "table", "floor")), "height": Param(0.55, min=0.2, max=2.2),
              "shade_color": Param("cream", "color"), "base_color": Param("bronze", "color"), "light_color": Param("#ffe7a8", "color")}
    primary_color = "shade_color"
    color_params = {"shade": "shade_color", "lampshade": "shade_color", "lamp": "base_color", "base": "base_color", "stand": "base_color",
                    "arm": "base_color", "light": "light_color", "bulb": "light_color"}
    size_params = {"shade": ["height"], "lamp": ["height"], "base": ["height"]}
    dimension_params = {"height": ["height"], "width": ["height"]}
    required_roles = {"base": 1, "shade": 1, "bulb": 1}

    def resolve(self, overrides: dict[str, Any]) -> dict[str, Any]:
        if overrides.get("style") == "floor":
            overrides = {"height": 1.6, **overrides}
        if overrides.get("style") == "desk":
            overrides = {"height": 0.45, "base_color": "dark grey", "shade_color": "dark grey", **overrides}
        return super().resolve(overrides)

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        base = b.material("base", p["base_color"], metallic=0.7, roughness=0.35)
        shade = b.material("shade", p["shade_color"], roughness=0.8, opacity=0.95)
        bulb = b.material("bulb", p["light_color"], emissive=p["light_color"])
        H = p["height"]
        seg = segs(detail, 28)
        if p["style"] == "desk":
            b.add("base", "cylinder", base, pos=(0, 0.012, 0), radius_top=H * 0.2, radius_bottom=H * 0.22, height=0.024, segments=seg)
            j1, j2, head = (0, 0.03, 0), (0, H * 0.55, -H * 0.12), (0, H * 0.75, H * 0.25)
            strut(b, "arm", base, j1, j2, H * 0.015, name="lower_arm")
            strut(b, "arm", base, j2, head, H * 0.015, name="upper_arm")
            b.add("joint", "sphere", base, pos=j2, radius=H * 0.03)
            b.add("shade", "lathe", shade, pos=head, rot=(150, 0, 0), profile=[(H * 0.04, 0.0), (H * 0.06, H * 0.05), (H * 0.15, H * 0.16), (H * 0.148, H * 0.165)],
                  segments=seg)
            b.add("bulb", "sphere", bulb, pos=(0, head[1] - H * 0.06, head[2] + H * 0.03), radius=H * 0.045)
        else:
            stem_h = H * (0.7 if p["style"] == "table" else 0.82)
            if p["style"] == "table":
                b.add("base", "lathe", base, profile=[(0.0, 0.0), (H * 0.2, 0.0), (H * 0.21, H * 0.03), (H * 0.15, H * 0.12), (H * 0.06, H * 0.3),
                                                      (H * 0.03, H * 0.34), (0.0, H * 0.34)], segments=seg)
            else:
                b.add("base", "cylinder", base, pos=(0, 0.015, 0), radius_top=0.16, radius_bottom=0.17, height=0.03, segments=seg)
            b.add("stem", "cylinder", base, pos=(0, stem_h / 2, 0), radius_top=H * 0.01 + 0.004, radius_bottom=H * 0.01 + 0.004, height=stem_h, segments=12)
            sh = H * (0.3 if p["style"] == "table" else 0.2)
            rt, rb = H * (0.16 if p["style"] == "table" else 0.1), H * (0.24 if p["style"] == "table" else 0.15)
            b.add("shade", "lathe", shade, pos=(0, stem_h - sh * 0.35, 0), profile=[(rb, 0.0), (rt, sh), (rt * 0.98, sh * 1.001)], segments=seg)
            b.add("bulb", "sphere", bulb, pos=(0, stem_h, 0), radius=H * 0.035)
        return b


class TreeGenerator(Generator):
    id = "nature.tree"
    object_type = "tree"
    category = "nature"
    description = "Deciduous, pine or palm tree."
    aliases = {"tree": {}, "oak tree": {"style": "deciduous"}, "pine tree": {"style": "pine"}, "pine": {"style": "pine"},
               "christmas tree": {"style": "pine"}, "fir tree": {"style": "pine"}, "palm tree": {"style": "palm"}, "palm": {"style": "palm"}}
    params = {"style": Param("deciduous", "choice", choices=("deciduous", "pine", "palm")), "height": Param(5.0, min=0.3, max=60),
              "foliage_color": Param("#3f7d3a", "color"), "trunk_color": Param("#5b3a24", "color")}
    primary_color = "foliage_color"
    color_params = {"tree": "foliage_color", "leaves": "foliage_color", "leave": "foliage_color", "foliage": "foliage_color", "canopy": "foliage_color",
                    "trunk": "trunk_color", "bark": "trunk_color"}
    size_params = {"tree": ["height"], "trunk": ["height"], "canopy": ["height"]}
    dimension_params = {"height": ["height"], "width": ["height"]}
    required_roles = {"trunk": 1, "foliage": 1}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        leaf = b.material("foliage", p["foliage_color"], roughness=0.9)
        bark = b.material("bark", p["trunk_color"], roughness=0.95)
        H = p["height"]
        seg = segs(detail, 14)
        st = p["style"]
        if st == "pine":
            b.add("trunk", "cylinder", bark, pos=(0, H * 0.15, 0), radius_top=H * 0.03, radius_bottom=H * 0.045, height=H * 0.3, segments=seg)
            tiers = 3 + detail
            for i in range(tiers):
                t = i / tiers
                b.add("foliage", "cone", leaf, name="tier", pos=(0, H * (0.25 + 0.6 * t) + H * 0.12, 0), radius=H * 0.28 * (1 - t * 0.7),
                      height=H * 0.32, segments=seg)
        elif st == "palm":
            pts = [(H * 0.05 * math.sin(math.pi * i / 10), H * 0.85 * i / 10, 0.0) for i in range(11)]
            b.add("trunk", "tube", bark, path=pts, radius=H * 0.03, segments=seg)
            top = pts[-1]
            for k in range(7 + detail):
                a = 2 * math.pi * k / (7 + detail)
                frond = [(top[0] + math.cos(a) * H * 0.35 * s, top[1] + H * 0.08 * math.sin(math.pi * s) - H * 0.12 * s * s, math.sin(a) * H * 0.35 * s)
                         for s in (0, 0.25, 0.5, 0.75, 1.0)]
                b.add("foliage", "tube", leaf, name="frond", path=frond, radius=H * 0.025, segments=6)
        else:
            b.add("trunk", "cylinder", bark, pos=(0, H * 0.25, 0), radius_top=H * 0.045, radius_bottom=H * 0.07, height=H * 0.5, segments=seg)
            blobs = [(0, 0.68, 0, 0.3), (0.18, 0.6, 0.05, 0.22), (-0.16, 0.62, -0.06, 0.22), (0.02, 0.6, 0.18, 0.2), (-0.04, 0.82, -0.02, 0.2)]
            for x, y, z, r in blobs[: 3 + detail]:
                b.add("foliage", "sphere", leaf, name="canopy", pos=(x * H, y * H, z * H), radius=r * H, width_segments=seg, height_segments=seg // 2 + 2)
        return b


class LaptopGenerator(Generator):
    id = "electronics.laptop"
    object_type = "laptop"
    category = "electronics"
    description = "Laptop with hinged screen, keyboard and trackpad."
    aliases = {"laptop": {}, "notebook computer": {}, "macbook": {"color": "silver"}, "computer": {}}
    params = {"width": Param(0.31, min=0.2, max=0.45), "depth": Param(0.215, min=0.14, max=0.32), "open_angle": Param(105.0, min=0, max=170),
              "color": Param("silver", "color"), "screen_color": Param("#0d1b2a", "color")}
    primary_color = "color"
    color_params = {"laptop": "color", "body": "color", "case": "color", "screen": "screen_color", "display": "screen_color"}
    size_params = {"laptop": ["width", "depth"], "screen": ["width"]}
    dimension_params = {"width": ["width"], "length": ["depth"]}
    required_roles = {"base": 1, "screen": 1, "keyboard": 1}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        body = b.material("body", p["color"], metallic=0.85, roughness=0.3)
        scr = b.material("screen", p["screen_color"], roughness=0.1, emissive="#0a1a2e")
        keys = b.material("keys", "#1c1c1e", roughness=0.7)
        W, D, ang = p["width"], p["depth"], p["open_angle"]
        t = 0.008
        b.add("base", "rounded_box", body, pos=(0, t, 0), width=W, height=t * 2, depth=D, radius=0.004, segments=2)
        b.add("keyboard", "box", keys, pos=(0, t * 2 + 0.0005, -D * 0.08), width=W * 0.86, height=0.001, depth=D * 0.42)
        b.add("trackpad", "box", b.material("trackpad", p["color"], metallic=0.6, roughness=0.2), pos=(0, t * 2 + 0.0005, D * 0.3),
              width=W * 0.38, height=0.001, depth=D * 0.24)
        if detail >= 2:
            rows, cols = 5, 13
            for r in range(rows):
                for c in range(cols):
                    b.add("keyboard", "box", keys, name="key", pos=(-W * 0.4 + c * W * 0.8 / (cols - 1), t * 2 + 0.0015, -D * 0.25 + r * D * 0.34 / (rows - 1)),
                          width=W * 0.05, height=0.0015, depth=D * 0.055)
        # lid hinged at the back edge; ang=0 is closed (lying on the base), 90 is upright
        ang = math.radians(p["open_angle"])
        lid_len = D * 0.98
        hinge = (0.0, t * 2, -D / 2)
        d = (0.0, math.sin(ang), math.cos(ang))
        n = (0.0, -math.cos(ang), math.sin(ang))  # inner face normal
        center = tuple(h + dc * lid_len / 2 for h, dc in zip(hinge, d))
        rot = (90 - p["open_angle"], 0, 0)
        b.add("lid", "rounded_box", body, pos=center, rot=rot, width=W, height=lid_len, depth=t, radius=0.003, segments=2)
        b.add("screen", "box", scr, pos=tuple(c + nc * (t / 2 + 0.0006) for c, nc in zip(center, n)), rot=rot,
              width=W * 0.92, height=lid_len * 0.88, depth=0.001)
        return b


class CrateGenerator(Generator):
    id = "container.crate"
    object_type = "crate"
    category = "container"
    description = "Wooden crate/box made of planks, or a plain cardboard box."
    aliases = {"crate": {}, "wooden crate": {}, "box": {"style": "cardboard", "color": "#b8925a"}, "cardboard box": {"style": "cardboard", "color": "#b8925a"}, "chest": {"style": "wood"}}
    params = {"size": Param(0.6, min=0.05, max=4), "style": Param("wood", "choice", choices=("wood", "cardboard")), "color": Param("oak", "color")}
    primary_color = "color"
    color_params = {"crate": "color", "box": "color", "plank": "color", "wood": "color"}
    size_params = {"crate": ["size"], "box": ["size"]}
    dimension_params = {"height": ["size"], "width": ["size"], "length": ["size"]}
    required_roles = {"body": 1}

    def type_for(self, p: dict[str, Any]) -> str:
        return "box" if p["style"] == "cardboard" else "crate"

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        S = p["size"]
        if p["style"] == "cardboard":
            m = b.material("cardboard", p["color"], roughness=0.95)
            b.add("body", "box", m, pos=(0, S * 0.35, 0), width=S, height=S * 0.7, depth=S * 0.8)
            b.add("tape", "box", b.material("tape", "#d9c9a3", roughness=0.4), pos=(0, S * 0.7 + 0.001, 0), width=S * 0.12, height=0.002, depth=S * 0.8)
            return b
        m = b.material("wood", p["color"], roughness=0.85)
        dark = b.material("frame", "walnut", roughness=0.85)
        b.add("body", "box", m, pos=(0, S / 2, 0), width=S * 0.96, height=S * 0.96, depth=S * 0.96)
        e = S * 0.08
        for x in (-1, 1):
            for z in (-1, 1):
                b.add("frame", "box", dark, name="edge", pos=(x * (S / 2 - e / 2), S / 2, z * (S / 2 - e / 2)), width=e, height=S, depth=e)
        for y in (e / 2, S - e / 2):
            for z in (-1, 1):
                b.add("frame", "box", dark, name="rail", pos=(0, y, z * (S / 2 - e / 2)), width=S, height=e, depth=e)
            for x in (-1, 1):
                b.add("frame", "box", dark, name="rail", pos=(x * (S / 2 - e / 2), y, 0), width=e, height=e, depth=S)
        if detail >= 2:
            for z in (-1, 1):
                b.add("frame", "box", dark, name="brace", pos=(0, S / 2, z * (S / 2 - e / 4)), rot=(0, 0, 45), width=S * 1.25, height=e * 0.8, depth=e / 2)
        return b


GENERATORS = [LampGenerator(), TreeGenerator(), LaptopGenerator(), CrateGenerator()]
