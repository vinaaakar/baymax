"""Architectural structures."""

from __future__ import annotations

import math
from typing import Any

from ..scene import Builder
from .base import Generator, Param, segs, strut

GLASS = "#2a3a4a"


def _window(b: Builder, glass: str, frame: str, pos, w: float, h: float, facing: str) -> None:
    x, y, z = pos
    t = 0.04
    if facing in ("front", "back"):
        sign = 1 if facing == "front" else -1
        b.add("window_frame", "box", frame, name="window_frame", pos=(x, y, z + sign * 0.01), width=w + 0.12, height=h + 0.12, depth=t)
        b.add("window", "box", glass, name="window", pos=(x, y, z + sign * 0.035), width=w, height=h, depth=0.02)
    else:
        sign = 1 if facing == "right" else -1
        b.add("window_frame", "box", frame, name="window_frame", pos=(x + sign * 0.01, y, z), width=t, height=h + 0.12, depth=w + 0.12)
        b.add("window", "box", glass, name="window", pos=(x + sign * 0.035, y, z), width=0.02, height=h, depth=w)


class HouseGenerator(Generator):
    id = "architecture.house"
    object_type = "house"
    category = "architecture"
    description = "Detached house with configurable floors, roof style, windows, door, chimney and garage."
    aliases = {"house": {}, "home": {}, "cottage": {"floors": 1, "width": 7.0}, "cabin": {"floors": 1, "wall_color": "wood"},
               "bungalow": {"floors": 1, "width": 10.0}, "villa": {"floors": 2, "width": 12.0, "roof_style": "hip"},
               "townhouse": {"floors": 3, "width": 6.0}, "barn": {"floors": 1, "wall_color": "red", "width": 10, "depth": 14},
               "farmhouse": {"floors": 2}, "shed": {"floors": 1, "width": 3.0, "depth": 2.5, "windows": 1, "chimney": False}}
    params = {
        "floors": Param(2, "int", 1, 4), "width": Param(9.0, min=2.5, max=30), "depth": Param(7.0, min=2.5, max=30),
        "floor_height": Param(2.8, min=2.2, max=5), "roof_style": Param("gable", "choice", choices=("gable", "flat", "hip")),
        "roof_pitch": Param(0.45, min=0.1, max=1.2), "windows": Param(3, "int", 0, 8),
        "wall_color": Param("cream", "color"), "roof_color": Param("#7a2e22", "color"), "door_color": Param("#5a3420", "color"),
        "trim_color": Param("white", "color"), "chimney": Param(True, "bool"), "garage": Param(False, "bool"),
    }
    primary_color = "wall_color"
    color_params = {"wall": "wall_color", "house": "wall_color", "facade": "wall_color", "roof": "roof_color", "door": "door_color",
                    "trim": "trim_color", "window": "trim_color", "frame": "trim_color", "chimney": "wall_color", "garage": "wall_color"}
    size_params = {"house": ["width", "depth"], "roof": ["roof_pitch"], "floor": ["floor_height"], "wall": ["floor_height"]}
    count_params = {"floor": "floors", "storey": "floors", "story": "floors", "storie": "floors", "level": "floors", "window": "windows"}
    dimension_params = {"height": ["floor_height"], "width": ["width"], "length": ["depth"]}
    required_roles = {"wall": 1, "roof": 1, "door": 1}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        wall = b.material("wall", p["wall_color"], roughness=0.85)
        roof = b.material("roof", p["roof_color"], roughness=0.8)
        door = b.material("door", p["door_color"], roughness=0.6)
        trim = b.material("trim", p["trim_color"], roughness=0.5)
        glass = b.material("glass", GLASS, metallic=0.2, roughness=0.05, opacity=0.75)
        found = b.material("foundation", "#8d8d88", roughness=0.9)
        W, D, F, fh = p["width"], p["depth"], p["floors"], p["floor_height"]
        H = F * fh
        b.add("foundation", "box", found, pos=(0, 0.1, 0), width=W + 0.3, height=0.2, depth=D + 0.3)
        b.add("wall", "box", wall, name="walls", pos=(0, 0.2 + H / 2, 0), width=W, height=H, depth=D)
        top = 0.2 + H
        over = 0.35
        rh = (W / 2) * p["roof_pitch"]
        if p["roof_style"] == "gable":
            b.add("roof", "extrude", roof, pos=(0, top, 0), polygon=[(-W / 2 - over, -0.05), (W / 2 + over, -0.05), (0, rh)], depth=D + over * 2)
            if detail >= 2:
                for sign in (-1, 1):
                    b.add("wall", "extrude", wall, name="gable_end", pos=(0, top, sign * (D / 2 - 0.01)),
                          polygon=[(-W / 2, 0), (W / 2, 0), (0, rh * W / (W + 2 * over))], depth=0.02)
        elif p["roof_style"] == "hip":
            hw, hd = W / 2 + over, D / 2 + over
            ridge = max(0.02, hw - hd)
            # loft along local Z, rotated -90° about X so the loft axis points up
            b.add("roof", "loft", roof, pos=(0, top - 0.05, 0), rot=(-90, 0, 0), sections=[
                (0.0, [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]),
                (rh, [(-ridge, -0.02), (ridge, -0.02), (ridge, 0.02), (-ridge, 0.02)]),
            ])
        else:
            b.add("roof", "box", roof, pos=(0, top + 0.1, 0), width=W + 0.2, height=0.2, depth=D + 0.2)
            b.add("roof", "box", trim, name="parapet", pos=(0, top + 0.3, D / 2 + 0.05), width=W + 0.2, height=0.25, depth=0.1)
        # door (front, +Z)
        door_x = -W * 0.18 if p["windows"] else 0.0
        b.add("door", "box", door, pos=(door_x, 0.2 + 1.05, D / 2 + 0.03), width=1.0, height=2.1, depth=0.06)
        b.add("door_frame", "box", trim, pos=(door_x, 0.2 + 1.1, D / 2 + 0.01), width=1.2, height=2.25, depth=0.04)
        if detail >= 2:
            b.add("handle", "sphere", b.material("brass", "gold", metallic=0.9, roughness=0.3), name="door_handle",
                  pos=(door_x + 0.38, 0.2 + 1.0, D / 2 + 0.08), radius=0.04, width_segments=10, height_segments=8)
            b.add("step", "box", found, name="front_step", pos=(door_x, 0.1, D / 2 + 0.45), width=1.6, height=0.2, depth=0.6)
        # windows on each floor
        n = p["windows"]
        for f in range(F):
            y = 0.2 + f * fh + fh * 0.55
            xs = [(-W / 2 + W * (i + 0.5) / n) for i in range(n)] if n else []
            for x in xs:
                if f == 0 and abs(x - door_x) < 1.0:
                    continue
                _window(b, glass, trim, (x, y, D / 2), 1.0, 1.2, "front")
                if detail >= 2:
                    _window(b, glass, trim, (x, y, -D / 2), 1.0, 1.2, "back")
            side_n = max(1, int(D / 3.5)) if n else 0
            for i in range(side_n):
                z = -D / 2 + D * (i + 0.5) / side_n
                _window(b, glass, trim, (W / 2, y, z), 1.0, 1.2, "right")
                _window(b, glass, trim, (-W / 2, y, z), 1.0, 1.2, "left")
        if p["chimney"] and p["roof_style"] != "flat":
            b.add("chimney", "box", b.material("brick", "#8a3b2a", roughness=0.9), pos=(W * 0.25, top + rh * 0.7, -D * 0.15),
                  width=0.6, height=rh * 1.1 + 0.6, depth=0.6)
        if p["garage"]:
            gw, gh = 3.6, 2.8
            b.add("garage", "box", wall, pos=(W / 2 + gw / 2, 0.2 + gh / 2, D * 0.1), width=gw, height=gh, depth=D * 0.8)
            b.add("roof", "box", roof, name="garage_roof", pos=(W / 2 + gw / 2, 0.2 + gh + 0.08, D * 0.1), width=gw + 0.3, height=0.16, depth=D * 0.8 + 0.3)
            b.add("door", "box", b.material("garage_door", "light grey", roughness=0.5), name="garage_door",
                  pos=(W / 2 + gw / 2, 0.2 + 1.1, D * 0.5 + 0.03), width=gw * 0.8, height=2.2, depth=0.05)
        if detail >= 3:
            b.add("path", "box", found, name="walkway", pos=(door_x, 0.01, D / 2 + 2.5), width=1.2, height=0.02, depth=3.6)
            b.add("gutter", "box", trim, name="gutter", pos=(0, top - 0.02, D / 2 + over * 0.8), width=W + over * 2, height=0.1, depth=0.1)
        return b


class TowerGenerator(Generator):
    id = "architecture.tower"
    object_type = "skyscraper"
    category = "architecture"
    description = "Multi-storey tower / office or apartment building with window bands and crown."
    aliases = {"skyscraper": {}, "tower": {"floors": 12}, "office building": {"floors": 10}, "building": {"floors": 8},
               "apartment building": {"floors": 8, "style": "concrete"}, "apartment block": {"floors": 8, "style": "concrete"},
               "high rise": {"floors": 30}, "highrise": {"floors": 30}}
    params = {"floors": Param(24, "int", 2, 120), "floor_height": Param(3.4, min=2.6, max=6), "width": Param(24.0, min=6, max=120),
              "depth": Param(24.0, min=6, max=120), "style": Param("glass", "choice", choices=("glass", "concrete")),
              "wall_color": Param("#9aa3ab", "color"), "glass_color": Param("#3d5d78", "color"), "spire": Param(True, "bool")}
    primary_color = "wall_color"
    color_params = {"wall": "wall_color", "building": "wall_color", "facade": "wall_color", "window": "glass_color", "glass": "glass_color"}
    size_params = {"building": ["width", "depth"], "tower": ["width", "depth"], "floor": ["floor_height"]}
    count_params = {"floor": "floors", "storey": "floors", "story": "floors", "storie": "floors", "level": "floors"}
    dimension_params = {"height": ["floor_height"], "width": ["width"], "length": ["depth"]}
    required_roles = {"core": 1, "window": 4}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        wall = b.material("wall", p["wall_color"], metallic=0.2, roughness=0.6)
        glass = b.material("glass", p["glass_color"], metallic=0.6, roughness=0.1, opacity=0.85)
        F, fh, W, D = p["floors"], p["floor_height"], p["width"], p["depth"]
        H = F * fh
        b.add("podium", "box", wall, pos=(0, fh * 0.75, 0), width=W * 1.3, height=fh * 1.5, depth=D * 1.3)
        b.add("core", "box", wall, pos=(0, H / 2, 0), width=W, height=H, depth=D)
        group = max(1, F // 40)
        band_h = fh * (0.62 if p["style"] == "glass" else 0.45) * group
        for f in range(2, F, group):
            y = f * fh + fh * group / 2
            for facing, (pos, w, d) in {
                "front": ((0, y, D / 2 + 0.03), W * 0.94, 0.04), "back": ((0, y, -D / 2 - 0.03), W * 0.94, 0.04),
                "right": ((W / 2 + 0.03, y, 0), 0.04, D * 0.94), "left": ((-W / 2 - 0.03, y, 0), 0.04, D * 0.94),
            }.items():
                b.add("window", "box", glass, name=f"window_band_{facing}", pos=pos, width=w, height=band_h, depth=d)
        b.add("roof", "box", wall, name="crown", pos=(0, H + fh * 0.5, 0), width=W * 0.7, height=fh, depth=D * 0.7)
        if p["spire"]:
            b.add("spire", "cylinder", b.material("steel", "silver", metallic=0.9, roughness=0.25), pos=(0, H + fh + H * 0.06, 0),
                  radius_top=0.05, radius_bottom=0.6, height=H * 0.12, segments=segs(detail, 12))
        b.add("entrance", "box", glass, pos=(0, fh * 0.6, D * 0.65 + 0.05), width=W * 0.3, height=fh * 1.1, depth=0.1)
        return b


class BridgeGenerator(Generator):
    id = "architecture.bridge"
    object_type = "bridge"
    category = "architecture"
    description = "Bridge: suspension, arch or beam style."
    aliases = {"bridge": {}, "suspension bridge": {"style": "suspension"}, "arch bridge": {"style": "arch"},
               "footbridge": {"style": "beam", "span": 20.0, "deck_width": 3.0}, "beam bridge": {"style": "beam"}}
    params = {"style": Param("suspension", "choice", choices=("suspension", "arch", "beam")), "span": Param(60.0, min=8, max=500),
              "deck_width": Param(10.0, min=2, max=50), "tower_height": Param(20.0, min=4, max=200),
              "deck_color": Param("dark grey", "color"), "structure_color": Param("#c0392b", "color")}
    primary_color = "structure_color"
    color_params = {"deck": "deck_color", "road": "deck_color", "tower": "structure_color", "cable": "structure_color",
                    "arch": "structure_color", "bridge": "structure_color", "structure": "structure_color"}
    size_params = {"tower": ["tower_height"], "deck": ["deck_width"], "bridge": ["span"]}
    dimension_params = {"length": ["span"], "width": ["deck_width"], "height": ["tower_height"]}
    required_roles = {"deck": 1, "support": 2}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        deck = b.material("deck", p["deck_color"], roughness=0.8)
        st = b.material("structure", p["structure_color"], metallic=0.5, roughness=0.4)
        L, Wd, Ht = p["span"], p["deck_width"], p["tower_height"]
        dy = Ht * 0.35
        b.add("deck", "box", deck, pos=(0, dy, 0), width=Wd, height=0.8, depth=L)
        for side in (-1, 1):
            b.add("railing", "box", st, name="railing", pos=(side * (Wd / 2 - 0.1), dy + 0.9, 0), width=0.15, height=1.0, depth=L)
        style = p["style"]
        if style == "suspension":
            tz = L * 0.3
            for z in (-tz, tz):
                for side in (-1, 1):
                    b.add("support", "box", st, name="tower", pos=(side * Wd / 2, Ht / 2, z), width=1.2, height=Ht, depth=1.6)
                b.add("support", "box", st, name="tower_beam", pos=(0, Ht * 0.92, z), width=Wd + 1.2, height=1.0, depth=1.2)
            n = 24 + detail * 8
            for side in (-1, 1):
                pts = []
                for i in range(n + 1):
                    z = -L / 2 + L * i / n
                    if abs(z) <= tz:
                        y = dy + 1.5 + (Ht - dy - 1.5) * (z / tz) ** 2
                    else:
                        y = Ht - (Ht - dy) * (abs(z) - tz) / (L / 2 - tz)
                    pts.append((side * Wd / 2, y, z))
                b.add("cable", "tube", st, name="main_cable", path=pts, radius=0.25)
                hangers = 10 + detail * 4
                for i in range(1, hangers):
                    z = -tz + 2 * tz * i / hangers
                    y = dy + 1.5 + (Ht - dy - 1.5) * (z / tz) ** 2
                    strut(b, "cable", st, (side * Wd / 2, dy + 0.4, z), (side * Wd / 2, y, z), 0.06, segments=6, name="hanger")
        elif style == "arch":
            n = 24 + detail * 8
            for side in (-1, 1):
                pts = [(side * Wd / 2, dy + (Ht - dy) * math.sin(math.pi * i / n), -L / 2 + L * i / n) for i in range(n + 1)]
                b.add("support", "tube", st, name="arch", path=pts, radius=0.6)
                for i in range(2, n - 1, 2):
                    z = -L / 2 + L * i / n
                    y = dy + (Ht - dy) * math.sin(math.pi * i / n)
                    strut(b, "cable", st, (side * Wd / 2, dy + 0.4, z), (side * Wd / 2, y, z), 0.08, segments=6, name="hanger")
        else:
            piers = max(2, int(L / 15))
            for i in range(piers + 1):
                z = -L / 2 + L * i / piers
                b.add("support", "box", st, name="pier", pos=(0, dy / 2, z), width=Wd * 0.6, height=dy, depth=1.5)
        return b


GENERATORS = [HouseGenerator(), TowerGenerator(), BridgeGenerator()]
