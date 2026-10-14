"""Road vehicles, aircraft, watercraft, rockets and drones."""

from __future__ import annotations

import math
from typing import Any

from ..scene import Builder
from .base import Generator, Param, segs, strut

GLASS = "#1d2733"


class CarGenerator(Generator):
    id = "vehicle.car"
    object_type = "car"
    category = "vehicle"
    description = "Parametric road vehicle: sedan, SUV, sports car, pickup, van, bus."
    aliases = {
        "car": {}, "automobile": {}, "sedan": {"style": "sedan"}, "saloon": {"style": "sedan"}, "taxi": {"style": "sedan", "body_color": "yellow"},
        "suv": {"style": "suv"}, "jeep": {"style": "suv"}, "crossover": {"style": "suv"},
        "sports car": {"style": "sports"}, "race car": {"style": "sports"}, "racecar": {"style": "sports"}, "supercar": {"style": "sports"},
        "coupe": {"style": "sports"}, "hatchback": {"style": "hatchback"},
        "pickup": {"style": "pickup"}, "pickup truck": {"style": "pickup"}, "truck": {"style": "pickup"},
        "van": {"style": "van"}, "minivan": {"style": "van"}, "bus": {"style": "bus"}, "school bus": {"style": "bus", "body_color": "yellow"},
        "police car": {"style": "sedan", "body_color": "white"}, "vehicle": {},
    }
    params = {
        "style": Param("sedan", "choice", choices=("sedan", "suv", "sports", "hatchback", "pickup", "van", "bus")),
        "length": Param(4.5, min=2.5, max=14.0), "width": Param(1.85, min=1.2, max=3.0), "height": Param(1.45, min=0.9, max=3.6),
        "wheel_radius": Param(0.34, min=0.2, max=0.8), "wheel_width": Param(0.24, min=0.1, max=0.6),
        "body_color": Param("red", "color"), "wheel_color": Param("black", "color"), "rim_color": Param("silver", "color"),
        "window_color": Param(GLASS, "color"), "light_color": Param("#fff4d6", "color"),
    }
    primary_color = "body_color"
    color_params = {"body": "body_color", "car": "body_color", "paint": "body_color", "wheel": "wheel_color", "tire": "wheel_color",
                    "tyre": "wheel_color", "rim": "rim_color", "window": "window_color", "glass": "window_color", "light": "light_color",
                    "headlight": "light_color", "cabin": "body_color", "roof": "body_color"}
    size_params = {"wheel": ["wheel_radius", "wheel_width"], "tire": ["wheel_radius", "wheel_width"], "rim": ["wheel_radius"],
                   "body": ["length", "width", "height"], "car": ["length", "width", "height"]}
    dimension_params = {"length": ["length"], "width": ["width"], "height": ["height"]}
    required_roles = {"body": 1, "wheel": 4, "window": 2}

    def type_for(self, p: dict[str, Any]) -> str:
        return {"bus": "bus", "pickup": "pickup truck", "van": "van"}.get(p["style"], "car")

    STYLE_DEFAULTS = {
        "sedan": {}, "hatchback": {"length": 4.0}, "sports": {"height": 1.2, "length": 4.4, "width": 1.95, "wheel_radius": 0.35},
        "suv": {"height": 1.75, "length": 4.7, "wheel_radius": 0.4, "wheel_width": 0.27},
        "pickup": {"height": 1.85, "length": 5.4, "width": 2.0, "wheel_radius": 0.42, "wheel_width": 0.28},
        "van": {"height": 2.0, "length": 5.0, "width": 1.95}, "bus": {"height": 3.1, "length": 11.5, "width": 2.5, "wheel_radius": 0.5, "wheel_width": 0.3},
    }

    def resolve(self, overrides: dict[str, Any]) -> dict[str, Any]:
        style = overrides.get("style", self.params["style"].default)
        merged = {**self.STYLE_DEFAULTS.get(style, {}), **overrides}
        return super().resolve(merged)

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        body = b.material("body", p["body_color"], metallic=0.35, roughness=0.35)
        tire = b.material("tire", p["wheel_color"], roughness=0.9)
        rim = b.material("rim", p["rim_color"], metallic=0.9, roughness=0.25)
        glass = b.material("glass", p["window_color"], metallic=0.1, roughness=0.05, opacity=0.6)
        light = b.material("headlight", p["light_color"], roughness=0.2, emissive=p["light_color"])
        tail = b.material("taillight", "#d0101a", roughness=0.3, emissive="#7a0008")
        trim = b.material("trim", "#1a1a1a", roughness=0.7)
        style = p["style"]
        L, W, H, wr, ww = p["length"], p["width"], p["height"], p["wheel_radius"], p["wheel_width"]
        seg = segs(detail, 20)
        clearance = wr * 0.55
        lower_h = H * (0.42 if style != "bus" else 0.3)
        cabin_frac = {"sedan": 0.5, "hatchback": 0.55, "sports": 0.42, "suv": 0.62, "pickup": 0.32, "van": 0.82, "bus": 0.96}[style]
        cabin_h = H - clearance - lower_h
        cabin_offset = {"sedan": -0.04, "hatchback": -0.12, "sports": -0.06, "suv": -0.06, "pickup": 0.1, "van": -0.06, "bus": 0.0}[style]
        lower_y = clearance + lower_h / 2
        b.add("body", "rounded_box", body, name="lower_body", pos=(0, lower_y, 0), width=W, height=lower_h, depth=L,
              radius=min(0.14, lower_h * 0.4), segments=2 + detail)
        cab_len, cab_w = L * cabin_frac, W * (0.9 if style in ("van", "bus") else 0.84)
        cab_y = clearance + lower_h + cabin_h / 2 - 0.02
        cab_z = L * cabin_offset
        b.add("body", "rounded_box", body, name="cabin", pos=(0, cab_y, cab_z), width=cab_w, height=cabin_h, depth=cab_len,
              radius=min(0.18, cabin_h * 0.35), segments=2 + detail)
        # windows: one pane per side plus windscreen and rear window
        win_h = cabin_h * 0.6
        win_y = cab_y + cabin_h * 0.05
        panes = 1 if style not in ("bus", "van") else (8 if style == "bus" else 3)
        pane_len = cab_len * 0.84 / panes
        for side in (-1, 1):
            for i in range(panes):
                z = cab_z - cab_len * 0.42 + pane_len * (i + 0.5)
                b.add("window", "box", glass, name="side_window", pos=(side * (cab_w / 2 + 0.004), win_y, z),
                      width=0.012, height=win_h, depth=pane_len * 0.9)
        b.add("window", "box", glass, name="windshield", pos=(0, win_y, cab_z + cab_len / 2 + 0.004), rot=(-8 if style != "bus" else 0, 0, 0),
              width=cab_w * 0.88, height=win_h, depth=0.012)
        b.add("window", "box", glass, name="rear_window", pos=(0, win_y, cab_z - cab_len / 2 - 0.004),
              width=cab_w * 0.8, height=win_h * 0.85, depth=0.012)
        if style == "pickup":
            bed_z = -L * 0.27
            bed_len = L * 0.4
            for side in (-1, 1):
                b.add("body", "box", body, name="bed_wall", pos=(side * (W / 2 - 0.04), clearance + lower_h + 0.22, bed_z),
                      width=0.08, height=0.44, depth=bed_len)
            b.add("body", "box", body, name="tailgate", pos=(0, clearance + lower_h + 0.22, bed_z - bed_len / 2 + 0.04),
                  width=W - 0.08, height=0.44, depth=0.08)
        # wheels
        axle = L * (0.34 if style != "bus" else 0.36)
        rear_axle = -axle if style != "bus" else -L * 0.3
        for z in (axle, rear_axle):
            for side in (-1, 1):
                x = side * (W / 2 - ww / 2 + 0.03)
                b.add("wheel", "cylinder", tire, name="tire", pos=(x, wr, z), rot=(0, 0, 90),
                      radius_top=wr, radius_bottom=wr, height=ww, segments=seg)
                b.add("rim", "cylinder", rim, name="rim", pos=(x + side * 0.01, wr, z), rot=(0, 0, 90),
                      radius_top=wr * 0.62, radius_bottom=wr * 0.62, height=ww + 0.01, segments=seg)
                if detail >= 2:
                    for k in range(5):
                        a = 2 * math.pi * k / 5
                        b.add("rim", "box", rim, name="spoke", pos=(x + side * (ww / 2 + 0.02), wr + math.sin(a) * wr * 0.3, z + math.cos(a) * wr * 0.3),
                              rot=(math.degrees(-a), 0, 0), width=0.02, height=0.04, depth=wr * 0.6)
        # lights & bumpers
        front, back = L / 2, -L / 2
        ly = clearance + lower_h * 0.68
        for side in (-1, 1):
            b.add("light", "box", light, name="headlight", pos=(side * W * 0.33, ly, front + 0.005), width=W * 0.18, height=lower_h * 0.18, depth=0.02)
            b.add("light", "box", tail, name="taillight", pos=(side * W * 0.36, ly, back - 0.005), width=W * 0.16, height=lower_h * 0.16, depth=0.02)
        b.add("bumper", "rounded_box", trim, name="front_bumper", pos=(0, clearance + lower_h * 0.18, front + 0.02),
              width=W * 0.96, height=lower_h * 0.25, depth=0.1, radius=0.03, segments=2)
        b.add("bumper", "rounded_box", trim, name="rear_bumper", pos=(0, clearance + lower_h * 0.18, back - 0.02),
              width=W * 0.96, height=lower_h * 0.25, depth=0.1, radius=0.03, segments=2)
        if detail >= 2:
            b.add("grille", "box", trim, pos=(0, ly - lower_h * 0.12, front + 0.006), width=W * 0.38, height=lower_h * 0.22, depth=0.015)
            for side in (-1, 1):
                b.add("mirror", "rounded_box", body, name="mirror", pos=(side * (cab_w / 2 + 0.09), cab_y - cabin_h * 0.15, cab_z + cab_len * 0.4),
                      width=0.14, height=0.09, depth=0.08, radius=0.02, segments=2)
                for dz in ((0.12, -0.18) if style not in ("bus",) else (0.0,)):
                    b.add("handle", "box", trim, name="door_handle", pos=(side * (W / 2 + 0.006), clearance + lower_h * 0.85, cab_z + cab_len * dz),
                          width=0.015, height=0.025, depth=0.12)
            b.add("plate", "box", b.material("plate", "#f2f2ee", roughness=0.5), name="license_plate",
                  pos=(0, clearance + lower_h * 0.4, back - 0.03), width=0.52, height=0.12, depth=0.01)
        if detail >= 3:
            b.add("exhaust", "cylinder", rim, pos=(W * 0.3, clearance + 0.05, back - 0.05), rot=(90, 0, 0),
                  radius_top=0.035, radius_bottom=0.035, height=0.14, segments=12)
            b.add("roof", "box", trim, name="roof_rail", pos=(-cab_w * 0.4, clearance + lower_h + cabin_h + 0.01, cab_z), width=0.03, height=0.03, depth=cab_len * 0.8)
            b.add("roof", "box", trim, name="roof_rail", pos=(cab_w * 0.4, clearance + lower_h + cabin_h + 0.01, cab_z), width=0.03, height=0.03, depth=cab_len * 0.8)
        return b


class AirplaneGenerator(Generator):
    id = "vehicle.airplane"
    object_type = "airplane"
    category = "vehicle"
    description = "Fixed-wing aircraft: airliner or light propeller plane."
    aliases = {"airplane": {}, "aeroplane": {}, "plane": {}, "jet": {"style": "airliner"}, "airliner": {"style": "airliner"},
               "aircraft": {}, "propeller plane": {"style": "prop"}, "cessna": {"style": "prop"}}
    params = {
        "style": Param("airliner", "choice", choices=("airliner", "prop")),
        "length": Param(30.0, min=5, max=80), "wingspan": Param(28.0, min=5, max=80),
        "body_color": Param("white", "color"), "accent_color": Param("navy", "color"), "engines": Param(2, "int", 0, 4),
    }
    primary_color = "body_color"
    color_params = {"body": "body_color", "fuselage": "body_color", "tail": "accent_color", "accent": "accent_color", "stripe": "accent_color",
                    "wing": "body_color", "engine": "accent_color"}
    size_params = {"wing": ["wingspan"], "body": ["length"], "fuselage": ["length"]}
    dimension_params = {"length": ["length"], "width": ["wingspan"]}
    count_params = {"engine": "engines"}
    required_roles = {"fuselage": 1, "wing": 2, "tail": 1}

    def resolve(self, overrides: dict[str, Any]) -> dict[str, Any]:
        if overrides.get("style") == "prop":
            overrides = {"length": 8.0, "wingspan": 11.0, "engines": 1, **overrides}
        return super().resolve(overrides)

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        body = b.material("body", p["body_color"], metallic=0.4, roughness=0.35)
        acc = b.material("accent", p["accent_color"], metallic=0.3, roughness=0.4)
        glass = b.material("glass", GLASS, roughness=0.05, opacity=0.7)
        L, S = p["length"], p["wingspan"]
        R = L * 0.055
        seg = segs(detail, 20)
        prof = [(0.0, -L / 2), (R * 0.45, -L / 2 + L * 0.02), (R * 0.75, -L * 0.36), (R, -L * 0.2), (R, L * 0.3),
                (R * 0.85, L * 0.4), (R * 0.45, L * 0.47), (0.0, L / 2)]
        y0 = R * 2.2
        b.add("fuselage", "lathe", body, pos=(0, y0, 0), rot=(90, 0, 0), profile=[(r, z) for r, z in prof], segments=seg)
        # Lathe axis Y rotated +90° about X maps +Y to +Z: profile top (+L/2) is the nose.
        chord = L * 0.16
        for side, name in ((-1, "left_wing"), (1, "right_wing")):
            poly = [(side * R * 0.6, chord * 0.5), (side * S / 2, -chord * 0.05), (side * S / 2, -chord * 0.3), (side * R * 0.6, -chord * 0.5)]
            # polygon is in (x, z); rotating +90° about X maps polygon-y to +Z and extrusion to -Y
            b.add("wing", "extrude", body, name=name, pos=(0, y0 - R * 0.4, L * 0.02), rot=(90, 0, 0), polygon=poly, depth=R * 0.18)
        tail_h = L * 0.14
        b.add("tail", "extrude", acc, name="vertical_stabilizer", pos=(0, y0 + R * 0.6, -L * 0.42), rot=(0, 90, 0),
              polygon=[(-L * 0.05, 0), (L * 0.06, 0), (L * 0.07, tail_h), (L * 0.02, tail_h)], depth=R * 0.14)
        hs = S * 0.18
        b.add("tail", "extrude", body, name="horizontal_stabilizer", pos=(0, y0 + R * 0.2, -L * 0.44), rot=(90, 0, 0),
              polygon=[(-hs, -L * 0.02), (-hs * 0.2, L * 0.04), (hs * 0.2, L * 0.04), (hs, -L * 0.02), (hs, -L * 0.05), (-hs, -L * 0.05)], depth=R * 0.1)
        b.add("window", "rounded_box", glass, name="cockpit", pos=(0, y0 + R * 0.45, L * 0.43), rot=(-15, 0, 0),
              width=R * 1.0, height=R * 0.35, depth=R * 1.1, radius=R * 0.12, segments=2)
        n = p["engines"]
        if p["style"] == "prop":
            b.add("engine", "cone", acc, name="spinner", pos=(0, y0, L / 2 + 0.1), rot=(90, 0, 0), radius=R * 0.35, height=R * 0.6, segments=seg)
            for k in range(2):
                b.add("propeller", "box", b.material("prop", "#222222"), pos=(0, y0, L / 2 + 0.05), rot=(0, 0, 90 * k),
                      width=0.12, height=S * 0.17, depth=0.04)
        else:
            positions = {1: [0.0], 2: [-S * 0.18, S * 0.18], 3: [-S * 0.2, 0.0, S * 0.2], 4: [-S * 0.3, -S * 0.15, S * 0.15, S * 0.3]}.get(n, [])
            for x in positions:
                b.add("engine", "cylinder", acc, name="engine", pos=(x, y0 - R * 1.0, L * 0.08), rot=(90, 0, 0),
                      radius_top=R * 0.38, radius_bottom=R * 0.32, height=L * 0.1, segments=seg)
        if detail >= 2:
            for i in range(int(L / 1.5)):
                z = -L * 0.3 + i * 1.0
                if z > L * 0.36:
                    break
                for side in (-1, 1):
                    b.add("window", "sphere", glass, name="cabin_window", pos=(side * R * 0.97, y0 + R * 0.25, z),
                          scale=(0.3, 1, 1), radius=R * 0.09, width_segments=8, height_segments=6)
            b.add("stripe", "cylinder", acc, pos=(0, y0, 0), rot=(90, 0, 0), radius_top=R * 1.005, radius_bottom=R * 1.005,
                  height=L * 0.5, segments=seg, caps=False)
            for z in (L * 0.32, -L * 0.05):
                b.add("landing_gear", "cylinder", b.material("gear", "#222222"), pos=(0, R * 0.45, z), rot=(0, 0, 90),
                      radius_top=R * 0.35, radius_bottom=R * 0.35, height=R * 0.4, segments=12)
        return b


class BoatGenerator(Generator):
    id = "vehicle.boat"
    object_type = "boat"
    category = "vehicle"
    description = "Small boat: motorboat or sailboat with lofted hull."
    aliases = {"boat": {}, "ship": {"length": 20.0}, "sailboat": {"style": "sail"}, "yacht": {"style": "sail", "length": 12.0},
               "motorboat": {"style": "motor"}, "speedboat": {"style": "motor"}, "dinghy": {"style": "motor", "length": 3.0}}
    params = {"style": Param("motor", "choice", choices=("motor", "sail")), "length": Param(7.0, min=2, max=60),
              "beam": Param(2.4, min=0.8, max=15), "hull_color": Param("white", "color"), "deck_color": Param("wood", "color"),
              "sail_color": Param("cream", "color")}
    primary_color = "hull_color"
    color_params = {"hull": "hull_color", "body": "hull_color", "deck": "deck_color", "sail": "sail_color"}
    size_params = {"hull": ["length", "beam"], "sail": ["length"]}
    dimension_params = {"length": ["length"], "width": ["beam"]}
    required_roles = {"hull": 1, "deck": 1}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        hull = b.material("hull", p["hull_color"], roughness=0.35)
        deck = b.material("deck", p["deck_color"], roughness=0.7)
        L, Bm = p["length"], p["beam"]
        D = Bm * 0.45
        n = 8 + detail * 4
        sections = []
        steps = 6 + detail * 2
        for i in range(steps + 1):
            t = i / steps
            z = -L / 2 + t * L
            width = Bm / 2 * (0.75 + 0.25 * math.sin(math.pi * min(1.0, t * 0.9 + 0.1))) * (1 - max(0.0, t - 0.7) / 0.3 * 0.95)
            width = max(width, 0.02)
            depth = D * (0.6 + 0.4 * math.sin(math.pi * t * 0.85 + 0.2))
            ring = []
            for k in range(n):
                a = math.pi + math.pi * k / (n - 1)  # bottom half: V-ish hull
                ring.append((width * math.cos(a), depth * math.sin(a) * (0.8 + 0.2 * abs(math.cos(a)))))
            sections.append((z, list(reversed(ring))))
        b.add("hull", "loft", hull, pos=(0, D, 0), sections=sections)
        b.add("deck", "box", deck, pos=(0, D + 0.02, -L * 0.05), width=Bm * 0.82, height=0.04, depth=L * 0.7)
        if p["style"] == "sail":
            mast_h = L * 1.2
            b.add("mast", "cylinder", b.material("mast", "silver", metallic=0.8, roughness=0.3), pos=(0, D + mast_h / 2, L * 0.08),
                  radius_top=0.04, radius_bottom=0.06, height=mast_h, segments=12)
            sail = b.material("sail", p["sail_color"], roughness=0.8)
            b.add("sail", "extrude", sail, name="mainsail", pos=(0.05, D + 0.4, L * 0.08), rot=(0, 90, 0),
                  polygon=[(0, 0), (L * 0.45, 0), (0, mast_h * 0.88)], depth=0.02)
            b.add("sail", "extrude", sail, name="jib", pos=(0.05, D + 0.4, L * 0.1), rot=(0, -90, 0),
                  polygon=[(0, 0), (L * 0.38, 0), (0, mast_h * 0.75)], depth=0.02)
        else:
            b.add("cabin", "rounded_box", hull, pos=(0, D + 0.45, L * 0.02), width=Bm * 0.6, height=0.8, depth=L * 0.28, radius=0.12, segments=3)
            b.add("window", "box", b.material("glass", GLASS, roughness=0.05, opacity=0.7), name="windscreen",
                  pos=(0, D + 0.75, L * 0.165), rot=(-25, 0, 0), width=Bm * 0.55, height=0.4, depth=0.02)
            b.add("engine", "rounded_box", b.material("engine", "dark grey", metallic=0.5), name="outboard",
                  pos=(0, D + 0.1, -L / 2 - 0.15), width=0.35, height=0.9, depth=0.3, radius=0.06, segments=2)
        if detail >= 2:
            for side in (-1, 1):
                b.add("rail", "tube", b.material("rail", "silver", metallic=0.9, roughness=0.2), name="rail",
                      path=[(side * Bm * 0.4, D + 0.3, -L * 0.35), (side * Bm * 0.38, D + 0.3, L * 0.2), (0, D + 0.3, L * 0.45)], radius=0.015)
        return b


class RocketGenerator(Generator):
    id = "vehicle.rocket"
    object_type = "rocket"
    category = "vehicle"
    description = "Rocket with nose cone, fins and nozzle."
    aliases = {"rocket": {}, "spaceship": {}, "missile replica": {}, "space rocket": {}, "launch vehicle": {}}
    params = {"height": Param(12.0, min=1, max=120), "radius": Param(0.9, min=0.1, max=10), "fins": Param(4, "int", 3, 8),
              "body_color": Param("white", "color"), "accent_color": Param("red", "color")}
    primary_color = "body_color"
    color_params = {"body": "body_color", "nose": "accent_color", "fin": "accent_color", "accent": "accent_color"}
    size_params = {"fin": ["radius"], "body": ["height", "radius"]}
    dimension_params = {"height": ["height"], "width": ["radius"], "length": ["height"]}
    count_params = {"fin": "fins"}
    required_roles = {"body": 1, "nose": 1, "fin": 3}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        body = b.material("body", p["body_color"], metallic=0.3, roughness=0.4)
        acc = b.material("accent", p["accent_color"], metallic=0.2, roughness=0.45)
        H, R = p["height"], p["radius"]
        seg = segs(detail, 24)
        body_h = H * 0.72
        b.add("body", "cylinder", body, pos=(0, H * 0.08 + body_h / 2, 0), radius_top=R, radius_bottom=R, height=body_h, segments=seg)
        nose = [(R, 0)] + [(R * math.cos(math.pi / 2 * t / 8) ** 0.8, H * 0.2 * math.sin(math.pi / 2 * t / 8)) for t in range(1, 8)] + [(0.0, H * 0.2)]
        b.add("nose", "lathe", acc, pos=(0, H * 0.08 + body_h, 0), profile=nose, segments=seg)
        b.add("nozzle", "lathe", b.material("nozzle", "dark grey", metallic=0.8, roughness=0.3),
              pos=(0, 0, 0), profile=[(R * 0.7, 0), (R * 0.45, H * 0.05), (R * 0.5, H * 0.08), (0.0, H * 0.08)], segments=seg)
        for k in range(p["fins"]):
            a = 360.0 * k / p["fins"]
            b.add("fin", "extrude", acc, name="fin", pos=(math.sin(math.radians(a)) * R, 0, math.cos(math.radians(a)) * R), rot=(0, a - 90, 0),
                  polygon=[(0, H * 0.06), (R * 1.1, 0), (R * 1.1, H * 0.06), (0, H * 0.25)], depth=R * 0.08)
        if detail >= 2:
            b.add("window", "cylinder", b.material("glass", GLASS, roughness=0.05), pos=(0, H * 0.6, R * 0.98), rot=(90, 0, 0),
                  radius_top=R * 0.2, radius_bottom=R * 0.2, height=0.05, segments=16)
            for y in (H * 0.3, H * 0.55):
                b.add("stripe", "cylinder", acc, pos=(0, y, 0), radius_top=R * 1.01, radius_bottom=R * 1.01, height=H * 0.02, segments=seg, caps=False)
        return b


class DroneGenerator(Generator):
    id = "aerial.drone"
    object_type = "drone"
    category = "aerial"
    description = "Multirotor drone (quad/hexa/octo) with props, motors, landing gear and camera."
    aliases = {"drone": {}, "quadcopter": {"arms": 4}, "quadrotor": {"arms": 4}, "hexacopter": {"arms": 6}, "octocopter": {"arms": 8},
               "multirotor": {}, "uav": {}}
    params = {"arms": Param(4, "int", 3, 8), "arm_length": Param(0.25, min=0.08, max=1.5), "prop_radius": Param(0.12, min=0.03, max=0.8),
              "body_size": Param(0.16, min=0.05, max=1.0), "body_color": Param("dark grey", "color"), "arm_color": Param("black", "color"),
              "prop_color": Param("light grey", "color"), "camera": Param(True, "bool")}
    primary_color = "body_color"
    color_params = {"body": "body_color", "arm": "arm_color", "prop": "prop_color", "propeller": "prop_color", "motor": "arm_color"}
    size_params = {"prop": ["prop_radius"], "propeller": ["prop_radius"], "arm": ["arm_length"], "body": ["body_size"]}
    count_params = {"arm": "arms", "rotor": "arms", "propeller": "arms", "prop": "arms", "motor": "arms"}
    dimension_params = {"width": ["arm_length"], "length": ["arm_length"], "height": ["body_size"]}
    required_roles = {"body": 1, "arm": 3, "propeller": 3, "motor": 3}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        body = b.material("body", p["body_color"], metallic=0.3, roughness=0.45)
        arm_m = b.material("arm", p["arm_color"], roughness=0.6)
        prop = b.material("prop", p["prop_color"], roughness=0.4, opacity=0.9)
        motor = b.material("motor", "#2a2a2e", metallic=0.8, roughness=0.3)
        n, L, pr, bs = p["arms"], p["arm_length"], p["prop_radius"], p["body_size"]
        seg = segs(detail, 16)
        y0 = bs * 0.9
        b.add("body", "rounded_box", body, pos=(0, y0, 0), width=bs, height=bs * 0.35, depth=bs * 1.2, radius=bs * 0.1, segments=3)
        b.add("battery", "rounded_box", b.material("battery", "#3a5f9a", roughness=0.5), pos=(0, y0 + bs * 0.24, -bs * 0.05),
              width=bs * 0.6, height=bs * 0.15, depth=bs * 0.8, radius=bs * 0.04, segments=2)
        for k in range(n):
            a = 2 * math.pi * (k + 0.5) / n
            dx, dz = math.sin(a), math.cos(a)
            mid = (dx * (L / 2 + bs * 0.25), y0, dz * (L / 2 + bs * 0.25))
            b.add("arm", "box", arm_m, name="arm", pos=mid, rot=(0, math.degrees(a), 0), width=bs * 0.12, height=bs * 0.08, depth=L)
            tip = (dx * (L + bs * 0.25), y0, dz * (L + bs * 0.25))
            b.add("motor", "cylinder", motor, name="motor", pos=(tip[0], y0 + bs * 0.12, tip[2]),
                  radius_top=bs * 0.13, radius_bottom=bs * 0.13, height=bs * 0.22, segments=seg)
            for blade in range(2):
                b.add("propeller", "box", prop, name="propeller" if blade == 0 else "propeller_blade",
                      pos=(tip[0], y0 + bs * 0.25, tip[2]), rot=(0, math.degrees(a) * 1.7 + 90 * blade, 4),
                      width=pr * 2, height=0.004 + bs * 0.01, depth=pr * 0.18)
            if detail >= 2:
                b.add("guard", "torus", arm_m, name="prop_guard", pos=(tip[0], y0 + bs * 0.25, tip[2]), radius=pr * 1.08, tube=0.005 + bs * 0.02,
                      radial_segments=6, tubular_segments=seg * 2)
        for side in (-1, 1):
            strut(b, "landing_gear", arm_m, (side * bs * 0.4, y0 - bs * 0.1, bs * 0.3), (side * bs * 0.55, 0.01, bs * 0.35), bs * 0.03, name="leg")
            strut(b, "landing_gear", arm_m, (side * bs * 0.4, y0 - bs * 0.1, -bs * 0.3), (side * bs * 0.55, 0.01, -bs * 0.35), bs * 0.03, name="leg")
            b.add("landing_gear", "capsule", arm_m, name="skid", pos=(side * bs * 0.55, 0.012, 0), rot=(90, 0, 0), radius=bs * 0.035, length=bs * 1.0)
        if p["camera"]:
            b.add("camera", "sphere", b.material("camera", "#111111", metallic=0.4, roughness=0.2), pos=(0, y0 - bs * 0.25, bs * 0.45),
                  radius=bs * 0.13, width_segments=seg, height_segments=seg // 2)
            b.add("lens", "cylinder", b.material("lens", "#223355", metallic=0.2, roughness=0.05), pos=(0, y0 - bs * 0.25, bs * 0.57), rot=(90, 0, 0),
                  radius_top=bs * 0.06, radius_bottom=bs * 0.06, height=0.01 + bs * 0.04, segments=16)
        if detail >= 2:
            b.add("light", "sphere", b.material("led_front", "#30ff60", emissive="#30ff60"), name="led", pos=(0, y0, bs * 0.61), radius=bs * 0.03)
            b.add("light", "sphere", b.material("led_rear", "#ff3040", emissive="#ff3040"), name="led", pos=(0, y0, -bs * 0.61), radius=bs * 0.03)
        return b


GENERATORS = [CarGenerator(), AirplaneGenerator(), BoatGenerator(), RocketGenerator(), DroneGenerator()]
