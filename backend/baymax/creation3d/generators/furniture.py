"""Furniture."""

from __future__ import annotations

import math
from typing import Any

from ..scene import Builder
from .base import Generator, Param, segs, strut


def _legs(b: Builder, mat: str, n: int, half_w: float, half_d: float, height: float, radius: float, style: str, seg: int,
          splay: float = 0.0) -> None:
    if n == 1:
        b.add("leg", "cylinder", mat, name="pedestal", pos=(0, height / 2, 0), radius_top=radius * 1.5, radius_bottom=radius * 1.5, height=height, segments=seg)
        b.add("leg", "cylinder", mat, name="pedestal_foot", pos=(0, 0.015, 0), radius_top=min(half_w, half_d) * 0.7,
              radius_bottom=min(half_w, half_d) * 0.75, height=0.03, segments=seg)
        return
    corners: list[tuple[float, float]]
    if n == 4:
        corners = [(-half_w, -half_d), (half_w, -half_d), (half_w, half_d), (-half_w, half_d)]
    else:
        corners = [(half_w * math.cos(2 * math.pi * k / n + math.pi / 2), half_d * math.sin(2 * math.pi * k / n + math.pi / 2)) for k in range(n)]
    for x, z in corners:
        if style == "square":
            b.add("leg", "box", mat, name="leg", pos=(x, height / 2, z), width=radius * 2, height=height, depth=radius * 2)
        else:
            strut(b, "leg", mat, (x, height, z), (x * (1 + splay), 0.0, z * (1 + splay)), radius, segments=seg, name="leg")


class ChairGenerator(Generator):
    id = "furniture.chair"
    object_type = "chair"
    category = "furniture"
    description = "Chair: dining, office (swivel base with casters) or armchair."
    aliases = {"chair": {}, "dining chair": {"style": "dining"}, "kitchen chair": {"style": "dining"}, "office chair": {"style": "office"},
               "desk chair": {"style": "office"}, "gaming chair": {"style": "office", "color": "black"}, "armchair": {"style": "armchair"},
               "lounge chair": {"style": "armchair"}}
    params = {"style": Param("dining", "choice", choices=("dining", "office", "armchair")), "seat_height": Param(0.46, min=0.2, max=1.0),
              "seat_width": Param(0.46, min=0.25, max=1.2), "seat_depth": Param(0.44, min=0.25, max=1.0), "back_height": Param(0.45, min=0.1, max=1.2),
              "legs": Param(4, "int", 3, 5), "leg_style": Param("round", "choice", choices=("round", "square")),
              "color": Param("wood", "color"), "seat_color": Param("wood", "color"), "leg_color": Param("wood", "color"),
              "armrests": Param(False, "bool")}
    primary_color = "color"
    color_params = {"chair": "color", "frame": "color", "back": "color", "backrest": "color", "seat": "seat_color", "cushion": "seat_color",
                    "leg": "leg_color", "base": "leg_color", "arm": "color", "armrest": "color"}
    size_params = {"seat": ["seat_width", "seat_depth"], "back": ["back_height"], "backrest": ["back_height"], "leg": ["seat_height"],
                   "chair": ["seat_width", "seat_depth", "seat_height", "back_height"]}
    count_params = {"leg": "legs"}
    dimension_params = {"height": ["seat_height", "back_height"], "width": ["seat_width"], "length": ["seat_depth"]}
    required_roles = {"seat": 1, "backrest": 1, "leg": 1}

    STYLE = {"office": {"color": "dark grey", "seat_color": "dark grey", "leg_color": "black", "armrests": True, "back_height": 0.55, "legs": 5},
             "armchair": {"color": "#7a1630", "seat_color": "#7a1630", "leg_color": "walnut", "armrests": True, "seat_height": 0.42,
                          "seat_width": 0.75, "seat_depth": 0.7, "back_height": 0.5}}

    def resolve(self, overrides: dict[str, Any]) -> dict[str, Any]:
        style = overrides.get("style", "dining")
        return super().resolve({**self.STYLE.get(style, {}), **overrides})

    def required(self, p: dict[str, Any]) -> dict[str, int]:
        req = {"seat": 1, "backrest": 1, "leg": p["legs"] if p["style"] == "dining" else 1}
        if p["armrests"]:
            req["armrest"] = 2
        return req

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        frame = b.material("frame", p["color"], roughness=0.6, metallic=0.1)
        seat_m = b.material("seat", p["seat_color"], roughness=0.8)
        leg_m = b.material("legs", p["leg_color"], roughness=0.5, metallic=0.6 if p["style"] == "office" else 0.0)
        sh, sw, sd, bh = p["seat_height"], p["seat_width"], p["seat_depth"], p["back_height"]
        seg = segs(detail, 12)
        style = p["style"]
        thick = 0.05 if style == "dining" else 0.1
        if style == "armchair":
            thick = 0.16
        b.add("seat", "rounded_box", seat_m, pos=(0, sh - thick / 2, 0), width=sw, height=thick, depth=sd, radius=thick * 0.3, segments=2 + detail)
        if style == "dining":
            _legs(b, leg_m, p["legs"], sw / 2 - 0.03, sd / 2 - 0.03, sh - thick, 0.018, p["leg_style"], seg, splay=0.04)
            b.add("backrest", "rounded_box", frame, pos=(0, sh + bh * 0.6, -sd / 2 + 0.02), rot=(-6, 0, 0),
                  width=sw * 0.95, height=bh * 0.55, depth=0.03, radius=0.012, segments=2)
            for side in (-1, 1):
                b.add("backrest", "box", frame, name="back_post", pos=(side * (sw / 2 - 0.03), sh + bh / 2, -sd / 2 + 0.02), rot=(-6, 0, 0),
                      width=0.035, height=bh, depth=0.035)
            if detail >= 2:
                for z in (-sd / 2 + 0.03, sd / 2 - 0.03):
                    b.add("stretcher", "box", leg_m, pos=(0, sh * 0.3, z), width=sw - 0.08, height=0.02, depth=0.02)
        elif style == "office":
            b.add("leg", "cylinder", leg_m, name="gas_cylinder", pos=(0, (sh - thick) / 2 + 0.05, 0), radius_top=0.025, radius_bottom=0.03,
                  height=sh - thick - 0.08, segments=seg)
            for k in range(p["legs"]):
                a = 2 * math.pi * k / p["legs"]
                b.add("leg", "box", leg_m, name="base_spoke", pos=(math.sin(a) * 0.16, 0.07, math.cos(a) * 0.16), rot=(0, math.degrees(a), 0),
                      width=0.04, height=0.035, depth=0.32)
                b.add("caster", "sphere", b.material("caster", "black", roughness=0.5), name="caster",
                      pos=(math.sin(a) * 0.31, 0.03, math.cos(a) * 0.31), radius=0.03, width_segments=10, height_segments=8)
            b.add("backrest", "rounded_box", frame, pos=(0, sh + bh / 2 + 0.05, -sd / 2 + 0.03), rot=(-10, 0, 0),
                  width=sw * 0.92, height=bh, depth=0.07, radius=0.03, segments=2 + detail)
            if detail >= 2:
                b.add("headrest", "rounded_box", frame, pos=(0, sh + bh + 0.14, -sd / 2 - 0.0), rot=(-10, 0, 0),
                      width=sw * 0.5, height=0.12, depth=0.07, radius=0.03, segments=2)
        else:  # armchair
            for x in (-1, 1):
                for z in (-1, 1):
                    b.add("leg", "cylinder", leg_m, name="leg", pos=(x * (sw / 2 - 0.04), 0.06, z * (sd / 2 - 0.04)),
                          radius_top=0.025, radius_bottom=0.018, height=0.12, segments=seg)
            b.add("seat", "rounded_box", frame, name="base", pos=(0, 0.12 + (sh - thick - 0.12) / 2, 0), width=sw, height=sh - thick - 0.12,
                  depth=sd, radius=0.03, segments=2)
            b.add("backrest", "rounded_box", frame, pos=(0, sh + bh / 2 - 0.05, -sd / 2 + 0.08), rot=(-8, 0, 0),
                  width=sw, height=bh + 0.1, depth=0.16, radius=0.05, segments=2 + detail)
        if p["armrests"]:
            ah = 0.22 if style != "armchair" else 0.18
            for side in (-1, 1):
                if style == "armchair":
                    b.add("armrest", "rounded_box", frame, name="armrest", pos=(side * (sw / 2 - 0.06), sh + ah / 2 - 0.08, 0.02),
                          width=0.14, height=ah + sh * 0.5, depth=sd * 0.95, radius=0.04, segments=2)
                else:
                    b.add("armrest", "rounded_box", frame, name="armrest", pos=(side * (sw / 2 + 0.03), sh + ah, 0.02),
                          width=0.06, height=0.04, depth=sd * 0.6, radius=0.015, segments=2)
                    b.add("armrest", "box", leg_m, name="armrest_post", pos=(side * (sw / 2 + 0.03), sh + ah / 2, 0.02),
                          width=0.03, height=ah, depth=0.04)
        return b


class TableGenerator(Generator):
    id = "furniture.table"
    object_type = "table"
    category = "furniture"
    description = "Table: dining, coffee, side or desk; rectangular or round; four legs or pedestal."
    aliases = {"table": {}, "dining table": {}, "kitchen table": {}, "coffee table": {"height": 0.42, "width": 1.1, "depth": 0.6},
               "side table": {"height": 0.55, "width": 0.5, "depth": 0.5}, "end table": {"height": 0.55, "width": 0.5, "depth": 0.5},
               "round table": {"shape": "round", "width": 1.1, "depth": 1.1}, "desk": {"desk": True, "width": 1.4, "depth": 0.7, "height": 0.75},
               "workbench": {"width": 1.8, "depth": 0.75, "height": 0.9, "color": "oak", "leg_style": "square"}, "picnic table": {"width": 1.8}}
    params = {"shape": Param("rect", "choice", choices=("rect", "round")), "width": Param(1.6, min=0.3, max=5), "depth": Param(0.9, min=0.3, max=3),
              "height": Param(0.75, min=0.25, max=1.3), "top_thickness": Param(0.04, min=0.01, max=0.2), "legs": Param(4, "int", 1, 6),
              "leg_style": Param("round", "choice", choices=("round", "square")), "color": Param("oak", "color"), "leg_color": Param("walnut", "color"),
              "desk": Param(False, "bool")}
    primary_color = "color"
    color_params = {"table": "color", "top": "color", "tabletop": "color", "leg": "leg_color", "drawer": "leg_color", "base": "leg_color"}
    size_params = {"top": ["width", "depth"], "tabletop": ["width", "depth"], "leg": ["height"], "table": ["width", "depth", "height"]}
    count_params = {"leg": "legs"}
    dimension_params = {"height": ["height"], "width": ["width"], "length": ["depth"]}
    required_roles = {"top": 1, "leg": 1}

    @staticmethod
    def leg_count(p: dict[str, Any]) -> int:
        n = p["legs"]
        if p["shape"] == "round":
            return n if n in (1, 3, 4) else 1
        if n in (1, 4):
            return n
        return n + (n % 2)  # rectangular tables need legs in pairs

    def required(self, p: dict[str, Any]) -> dict[str, int]:
        return {"top": 1, "leg": self.leg_count(p)}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        top_m = b.material("top", p["color"], roughness=0.55)
        leg_m = b.material("legs", p["leg_color"], roughness=0.6)
        W, D, H, t = p["width"], p["depth"], p["height"], p["top_thickness"]
        seg = segs(detail, 16)
        if p["shape"] == "round":
            b.add("top", "cylinder", top_m, pos=(0, H - t / 2, 0), radius_top=W / 2, radius_bottom=W / 2, height=t, segments=segs(detail, 40),
                  scale=(1, 1, D / W))
            _legs(b, leg_m, self.leg_count(p), W * 0.33, D * 0.33, H - t, 0.025, p["leg_style"], seg)
        else:
            b.add("top", "rounded_box", top_m, pos=(0, H - t / 2, 0), width=W, height=t, depth=D, radius=min(t * 0.45, 0.02), segments=2)
            n = self.leg_count(p)
            if n == 4:
                _legs(b, leg_m, 4, W / 2 - 0.06, D / 2 - 0.06, H - t, 0.028, p["leg_style"], seg)
            elif n == 1:
                _legs(b, leg_m, 1, W / 2, D / 2, H - t, 0.05, p["leg_style"], seg)
            else:
                xs = [(-W / 2 + 0.06 + (W - 0.12) * i / (n // 2 - 1)) if n // 2 > 1 else 0.0 for i in range(max(1, n // 2))]
                for x in xs:
                    for z in (-D / 2 + 0.06, D / 2 - 0.06):
                        b.add("leg", "box" if p["leg_style"] == "square" else "cylinder", leg_m, name="leg", pos=(x, (H - t) / 2, z),
                              **({"width": 0.056, "height": H - t, "depth": 0.056} if p["leg_style"] == "square" else
                                 {"radius_top": 0.028, "radius_bottom": 0.028, "height": H - t, "segments": seg}))
            if detail >= 2:
                for z in (-D / 2 + 0.06, D / 2 - 0.06):
                    b.add("apron", "box", top_m, pos=(0, H - t - 0.05, z), width=W - 0.16, height=0.08, depth=0.02)
        if p["desk"]:
            dw = W * 0.32
            b.add("drawer", "box", leg_m, name="drawer_unit", pos=(W / 2 - dw / 2 - 0.05, (H - t) / 2 + 0.05, 0), width=dw, height=H - t - 0.1, depth=D * 0.9)
            for i in range(3):
                y = 0.15 + i * (H - t - 0.2) / 3 + 0.08
                b.add("handle", "box", b.material("metal", "silver", metallic=0.9, roughness=0.3), name="drawer_handle",
                      pos=(W / 2 - dw / 2 - 0.05, y, D * 0.45 + 0.012), width=dw * 0.35, height=0.015, depth=0.02)
        return b


class StoolGenerator(Generator):
    id = "furniture.stool"
    object_type = "stool"
    category = "furniture"
    description = "Stool or bar stool with splayed legs and footrest ring."
    aliases = {"stool": {}, "bar stool": {"height": 0.75}, "barstool": {"height": 0.75}, "footstool": {"height": 0.3, "legs": 4}}
    params = {"height": Param(0.45, min=0.2, max=1.0), "seat_radius": Param(0.17, min=0.08, max=0.4), "legs": Param(3, "int", 3, 4),
              "color": Param("walnut", "color"), "seat_color": Param("walnut", "color")}
    primary_color = "color"
    color_params = {"stool": "color", "leg": "color", "seat": "seat_color", "cushion": "seat_color"}
    size_params = {"seat": ["seat_radius"], "leg": ["height"], "stool": ["height", "seat_radius"]}
    count_params = {"leg": "legs"}
    dimension_params = {"height": ["height"], "width": ["seat_radius"]}
    required_roles = {"seat": 1, "leg": 3}

    def required(self, p: dict[str, Any]) -> dict[str, int]:
        return {"seat": 1, "leg": p["legs"]}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        frame = b.material("frame", p["color"], roughness=0.6)
        seat = b.material("seat", p["seat_color"], roughness=0.7)
        H, R = p["height"], p["seat_radius"]
        seg = segs(detail, 24)
        b.add("seat", "cylinder", seat, pos=(0, H - 0.025, 0), radius_top=R, radius_bottom=R * 0.95, height=0.05, segments=seg)
        n = p["legs"]
        for k in range(n):
            a = 2 * math.pi * k / n
            top = (math.sin(a) * R * 0.6, H - 0.05, math.cos(a) * R * 0.6)
            bot = (math.sin(a) * R * 1.1, 0.0, math.cos(a) * R * 1.1)
            strut(b, "leg", frame, top, bot, 0.016, segments=12, name="leg")
        b.add("footrest", "torus", frame, pos=(0, H * 0.32, 0), radius=R * (0.6 + 0.5 * (1 - 0.32)) * 0.98, tube=0.01, radial_segments=6, tubular_segments=seg)
        return b


class SofaGenerator(Generator):
    id = "furniture.sofa"
    object_type = "sofa"
    category = "furniture"
    description = "Upholstered sofa with seat and back cushions, arms and legs."
    aliases = {"sofa": {}, "couch": {}, "loveseat": {"seats": 2, "width": 1.5}, "settee": {"seats": 2, "width": 1.5}}
    params = {"seats": Param(3, "int", 1, 6), "width": Param(2.1, min=0.8, max=5), "depth": Param(0.9, min=0.6, max=1.5),
              "seat_height": Param(0.43, min=0.25, max=0.6), "color": Param("#5a6b7d", "color"), "leg_color": Param("walnut", "color")}
    primary_color = "color"
    color_params = {"sofa": "color", "couch": "color", "cushion": "color", "arm": "color", "back": "color", "leg": "leg_color"}
    size_params = {"sofa": ["width", "depth"], "cushion": ["depth"], "leg": ["seat_height"]}
    count_params = {"seat": "seats", "cushion": "seats"}
    dimension_params = {"height": ["seat_height"], "width": ["width"], "length": ["depth"]}
    required_roles = {"base": 1, "cushion": 2, "armrest": 2}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        fab = b.material("fabric", p["color"], roughness=0.95)
        leg = b.material("legs", p["leg_color"], roughness=0.5)
        W, D, sh, n = p["width"], p["depth"], p["seat_height"], p["seats"]
        arm_w = 0.18
        inner = W - 2 * arm_w
        b.add("base", "rounded_box", fab, pos=(0, 0.1 + (sh - 0.22) / 2, 0), width=W, height=sh - 0.12, depth=D, radius=0.04, segments=2)
        cw = inner / n
        for i in range(n):
            x = -inner / 2 + cw * (i + 0.5)
            b.add("cushion", "rounded_box", fab, name="seat_cushion", pos=(x, sh - 0.04, 0.05), width=cw - 0.01, height=0.14, depth=D - 0.25,
                  radius=0.05, segments=2 + detail)
            b.add("cushion", "rounded_box", fab, name="back_cushion", pos=(x, sh + 0.22, -D / 2 + 0.16), rot=(-10, 0, 0), width=cw - 0.01,
                  height=0.42, depth=0.18, radius=0.07, segments=2 + detail)
        b.add("back", "rounded_box", fab, name="backrest_frame", pos=(0, sh + 0.12, -D / 2 + 0.06), width=W, height=0.55, depth=0.12, radius=0.05, segments=2)
        for side in (-1, 1):
            b.add("armrest", "rounded_box", fab, name="armrest", pos=(side * (W / 2 - arm_w / 2), sh * 0.75 + 0.05, 0), width=arm_w,
                  height=sh * 0.9 + 0.1, depth=D, radius=0.06, segments=2 + detail)
            for z in (-1, 1):
                b.add("leg", "cylinder", leg, name="leg", pos=(side * (W / 2 - 0.08), 0.05, z * (D / 2 - 0.08)), radius_top=0.025, radius_bottom=0.018,
                      height=0.1, segments=12)
        return b


class BedGenerator(Generator):
    id = "furniture.bed"
    object_type = "bed"
    category = "furniture"
    description = "Bed with frame, mattress, headboard, pillows and blanket."
    aliases = {"bed": {}, "double bed": {"width": 1.4}, "queen bed": {"width": 1.6}, "king bed": {"width": 1.9}, "single bed": {"width": 0.9},
               "twin bed": {"width": 0.97}, "bunk bed": {"width": 0.9}}
    params = {"width": Param(1.6, min=0.7, max=2.2), "length": Param(2.05, min=1.6, max=2.4), "frame_color": Param("walnut", "color"),
              "sheet_color": Param("white", "color"), "blanket_color": Param("#4a6fa5", "color"), "pillows": Param(2, "int", 1, 4)}
    primary_color = "frame_color"
    color_params = {"bed": "frame_color", "frame": "frame_color", "headboard": "frame_color", "sheet": "sheet_color", "mattress": "sheet_color",
                    "pillow": "sheet_color", "blanket": "blanket_color", "duvet": "blanket_color", "cover": "blanket_color"}
    size_params = {"bed": ["width", "length"], "mattress": ["width", "length"]}
    count_params = {"pillow": "pillows"}
    dimension_params = {"width": ["width"], "length": ["length"]}
    required_roles = {"frame": 1, "mattress": 1, "headboard": 1}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        frame = b.material("frame", p["frame_color"], roughness=0.6)
        sheet = b.material("sheet", p["sheet_color"], roughness=0.95)
        blanket = b.material("blanket", p["blanket_color"], roughness=0.95)
        W, L = p["width"], p["length"]
        b.add("frame", "box", frame, pos=(0, 0.2, 0), width=W + 0.08, height=0.22, depth=L + 0.08)
        b.add("mattress", "rounded_box", sheet, pos=(0, 0.42, 0), width=W, height=0.24, depth=L, radius=0.06, segments=2 + detail)
        b.add("blanket", "rounded_box", blanket, pos=(0, 0.555, L * 0.12), width=W + 0.04, height=0.04, depth=L * 0.72, radius=0.015, segments=2)
        b.add("headboard", "rounded_box", frame, pos=(0, 0.6, -L / 2 - 0.05), width=W + 0.12, height=1.0, depth=0.07, radius=0.03, segments=2)
        n = p["pillows"]
        pw = (W - 0.1) / n
        for i in range(n):
            b.add("pillow", "rounded_box", sheet, name="pillow", pos=(-W / 2 + 0.05 + pw * (i + 0.5), 0.6, -L / 2 + 0.3), rot=(-15, 0, 0),
                  width=pw * 0.92, height=0.13, depth=0.4, radius=0.06, segments=2 + detail)
        for x in (-1, 1):
            for z in (-1, 1):
                b.add("leg", "box", frame, name="leg", pos=(x * W / 2, 0.045, z * L / 2), width=0.08, height=0.09, depth=0.08)
        return b


class ShelfGenerator(Generator):
    id = "furniture.shelf"
    object_type = "bookshelf"
    category = "furniture"
    description = "Bookshelf / shelving unit with configurable shelves and optional books."
    aliases = {"bookshelf": {}, "bookcase": {}, "shelf": {"books": False}, "shelving unit": {"books": False}, "shelves": {"books": False}}
    params = {"width": Param(0.9, min=0.3, max=3), "height": Param(1.8, min=0.4, max=3), "depth": Param(0.32, min=0.15, max=0.8),
              "shelves": Param(5, "int", 1, 12), "color": Param("oak", "color"), "books": Param(True, "bool")}
    primary_color = "color"
    color_params = {"shelf": "color", "bookshelf": "color", "frame": "color", "side": "color"}
    size_params = {"shelf": ["width", "height"], "bookshelf": ["width", "height"]}
    count_params = {"shelf": "shelves", "shelve": "shelves"}
    dimension_params = {"height": ["height"], "width": ["width"], "length": ["depth"]}
    required_roles = {"side": 2, "shelf": 2}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        wood = b.material("wood", p["color"], roughness=0.6)
        W, H, D, n = p["width"], p["height"], p["depth"], p["shelves"]
        t = 0.025
        for side in (-1, 1):
            b.add("side", "box", wood, name="side_panel", pos=(side * (W / 2 - t / 2), H / 2, 0), width=t, height=H, depth=D)
        b.add("back", "box", wood, name="back_panel", pos=(0, H / 2, -D / 2 + 0.005), width=W, height=H, depth=0.01)
        levels = n + 1
        palette = ["#7a1630", "#1e5bd8", "#2e7d32", "#d4a72c", "#3c3f43", "#6a1b9a", "#ef6c00"]
        for i in range(levels):
            y = t / 2 + (H - t) * i / n
            b.add("shelf", "box", wood, name="shelf", pos=(0, y, 0), width=W - 2 * t, height=t, depth=D - 0.01)
            if p["books"] and i < n and (detail >= 2 or i % 2 == 0):
                x = -W / 2 + t + 0.01
                k = 0
                gap = (H - t) / n
                while x < W / 2 - t - 0.04:
                    bw = 0.025 + 0.012 * ((k * 7) % 3)
                    bh = gap * (0.6 + 0.08 * ((k * 5) % 4))
                    mat = b.material(f"book_{k % len(palette)}", palette[k % len(palette)], roughness=0.8)
                    b.add("book", "box", mat, name="book", pos=(x + bw / 2, y + t / 2 + bh / 2, 0.0), width=bw, height=bh, depth=D * 0.75)
                    x += bw + 0.003
                    k += 1
        return b


class CabinetGenerator(Generator):
    id = "furniture.cabinet"
    object_type = "cabinet"
    category = "furniture"
    description = "Cabinet, wardrobe or dresser with doors or drawers and handles."
    aliases = {"cabinet": {}, "cupboard": {}, "wardrobe": {"height": 2.0, "width": 1.2, "depth": 0.6, "doors": 2, "drawers": 0},
               "dresser": {"height": 0.85, "width": 1.2, "depth": 0.5, "doors": 0, "drawers": 4}, "chest of drawers": {"height": 1.0, "doors": 0, "drawers": 5},
               "nightstand": {"height": 0.55, "width": 0.45, "depth": 0.4, "doors": 0, "drawers": 2}}
    params = {"width": Param(0.8, min=0.3, max=3), "height": Param(0.9, min=0.3, max=2.6), "depth": Param(0.45, min=0.2, max=1),
              "doors": Param(2, "int", 0, 4), "drawers": Param(1, "int", 0, 8), "color": Param("white", "color"), "handle_color": Param("silver", "color")}
    primary_color = "color"
    color_params = {"cabinet": "color", "body": "color", "door": "color", "drawer": "color", "handle": "handle_color", "knob": "handle_color"}
    size_params = {"cabinet": ["width", "height", "depth"], "handle": []}
    count_params = {"door": "doors", "drawer": "drawers"}
    dimension_params = {"height": ["height"], "width": ["width"], "length": ["depth"]}
    required_roles = {"body": 1}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        body = b.material("body", p["color"], roughness=0.55)
        front = b.material("front", p["color"], roughness=0.45)
        handle = b.material("handle", p["handle_color"], metallic=0.9, roughness=0.3)
        W, H, D = p["width"], p["height"], p["depth"]
        b.add("body", "box", body, pos=(0, H / 2 + 0.05, 0), width=W, height=H - 0.1, depth=D)
        b.add("plinth", "box", body, pos=(0, 0.04, 0.02), width=W - 0.04, height=0.08, depth=D - 0.04)
        y = 0.05
        usable = H - 0.1
        nd, ndr = p["doors"], p["drawers"]
        drawer_h = usable * (0.18 if nd else 1.0 / max(ndr, 1))
        for i in range(ndr):
            cy = H - 0.05 - drawer_h * (i + 0.5)
            b.add("drawer", "box", front, name="drawer_front", pos=(0, cy, D / 2 + 0.01), width=W - 0.03, height=drawer_h - 0.01, depth=0.02)
            b.add("handle", "box", handle, name="drawer_handle", pos=(0, cy, D / 2 + 0.03), width=min(0.16, W * 0.3), height=0.015, depth=0.02)
        if nd:
            door_top = H - 0.05 - drawer_h * ndr
            dh = door_top - y
            dw = W / nd
            for i in range(nd):
                cx = -W / 2 + dw * (i + 0.5)
                b.add("door", "box", front, name="door", pos=(cx, y + dh / 2, D / 2 + 0.01), width=dw - 0.01, height=dh - 0.01, depth=0.02)
                hx = cx + (dw * 0.38 if i % 2 == 0 else -dw * 0.38)
                b.add("handle", "box", handle, name="door_handle", pos=(hx, y + dh * 0.65, D / 2 + 0.03), width=0.015, height=min(0.2, dh * 0.2), depth=0.02)
        return b


GENERATORS = [ChairGenerator(), TableGenerator(), StoolGenerator(), SofaGenerator(), BedGenerator(), ShelfGenerator(), CabinetGenerator()]
