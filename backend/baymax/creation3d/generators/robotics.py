"""Robotic arms and robots."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..scene import Builder
from .base import Generator, Param, direction_euler, segs, strut


class RoboticArmGenerator(Generator):
    id = "robotics.arm"
    object_type = "robotic arm"
    category = "robotics"
    description = "Articulated industrial robotic arm with forward-kinematics pose (base yaw, shoulder, elbow, wrist) and gripper."
    aliases = {"robotic arm": {}, "robot arm": {}, "industrial robot": {}, "manipulator": {}, "robotic manipulator": {},
               "mechanical arm": {}, "cobot": {"color": "white"}}
    params = {"reach": Param(1.2, min=0.2, max=4.0), "base_yaw": Param(20.0, min=-180, max=180), "shoulder_angle": Param(30.0, min=-90, max=110),
              "elbow_angle": Param(75.0, min=-150, max=150), "wrist_angle": Param(30.0, min=-120, max=120), "gripper_open": Param(0.5, min=0, max=1),
              "color": Param("orange", "color"), "joint_color": Param("dark grey", "color"), "base_color": Param("dark grey", "color")}
    primary_color = "color"
    color_params = {"arm": "color", "link": "color", "robot": "color", "body": "color", "joint": "joint_color", "base": "base_color",
                    "gripper": "joint_color", "claw": "joint_color"}
    size_params = {"arm": ["reach"], "link": ["reach"], "robot": ["reach"], "gripper": ["reach"]}
    dimension_params = {"height": ["reach"], "length": ["reach"], "width": ["reach"]}
    required_roles = {"base": 1, "link": 2, "joint": 2, "gripper": 2}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        link_m = b.material("link", p["color"], metallic=0.3, roughness=0.45)
        joint_m = b.material("joint", p["joint_color"], metallic=0.7, roughness=0.35)
        base_m = b.material("base", p["base_color"], metallic=0.5, roughness=0.5)
        R = p["reach"]
        seg = segs(detail, 20)
        upper, fore, hand = R * 0.48, R * 0.42, R * 0.1
        w = R * 0.07
        b.add("base", "cylinder", base_m, name="base_plate", pos=(0, R * 0.02, 0), radius_top=R * 0.16, radius_bottom=R * 0.18, height=R * 0.04, segments=seg)
        b.add("base", "cylinder", link_m, name="turntable", pos=(0, R * 0.04 + R * 0.06, 0), radius_top=R * 0.11, radius_bottom=R * 0.13, height=R * 0.12, segments=seg)
        yaw = math.radians(p["base_yaw"])
        fwd = np.array([math.sin(yaw), 0.0, math.cos(yaw)])
        up = np.array([0.0, 1.0, 0.0])
        side = np.cross(up, fwd)

        def joint(pos, name):
            b.add("joint", "cylinder", joint_m, name=name, pos=tuple(pos), rot=direction_euler(tuple(side)),
                  radius_top=w * 1.15, radius_bottom=w * 1.15, height=w * 2.6, segments=seg)

        shoulder = np.array([0.0, R * 0.16 + w, 0.0])
        joint(shoulder, "shoulder_joint")
        a1 = math.radians(p["shoulder_angle"])  # from vertical, towards fwd
        d1 = up * math.cos(a1) + fwd * math.sin(a1)
        elbow = shoulder + d1 * upper
        a2 = a1 + math.radians(p["elbow_angle"])
        d2 = up * math.cos(a2) + fwd * math.sin(a2)
        wrist = elbow + d2 * fore
        a3 = a2 + math.radians(p["wrist_angle"])
        d3 = up * math.cos(a3) + fwd * math.sin(a3)
        tool = wrist + d3 * hand
        for start, end, d, name, scale in ((shoulder, elbow, d1, "upper_arm", 1.0), (elbow, wrist, d2, "forearm", 0.8)):
            mid = (start + end) / 2
            length = float(np.linalg.norm(end - start))
            b.add("link", "rounded_box", link_m, name=name, pos=tuple(mid), rot=_frame_euler(d, side),
                  width=w * 1.8 * scale, height=length, depth=w * 1.6 * scale, radius=w * 0.5 * scale, segments=2 + detail)
        joint(elbow, "elbow_joint")
        joint(wrist, "wrist_joint")
        strut(b, "link", link_m, tuple(wrist), tuple(tool), w * 0.6, segments=seg, name="wrist_link")
        palm = tool + d3 * w * 0.3
        b.add("gripper", "box", joint_m, name="gripper_palm", pos=tuple(palm), rot=_frame_euler(d3, side), width=w * 2.4, height=w * 0.6, depth=w * 1.2)
        spread = w * (0.4 + 1.0 * p["gripper_open"])
        for s in (-1, 1):
            base_pt = palm + side * s * spread + d3 * w * 0.3
            tip = base_pt + d3 * w * 2.0 - side * s * w * 0.25
            b.add("gripper", "box", joint_m, name="finger", pos=tuple((base_pt + tip) / 2), rot=_frame_euler(tip - base_pt, side),
                  width=w * 0.35, height=float(np.linalg.norm(tip - base_pt)), depth=w * 0.9)
        if detail >= 2:
            cable = [tuple(shoulder + side * w * 1.4), tuple((shoulder + elbow) / 2 + side * w * 1.3), tuple(elbow + side * w * 1.4)]
            b.add("cable", "tube", b.material("cable", "black", roughness=0.8), path=cable, radius=w * 0.15, segments=6)
            b.add("light", "torus", b.material("status_led", "#3cff7a", emissive="#3cff7a"), name="status_ring",
                  pos=(0, R * 0.165, 0), radius=R * 0.115, tube=R * 0.006, radial_segments=6, tubular_segments=seg * 2)
        return b


def _frame_euler(direction, side) -> tuple[float, float, float]:
    """Euler XYZ for a frame whose local +Y points along ``direction`` and local +X along ``side``."""
    y = np.asarray(direction, float)
    y /= np.linalg.norm(y) + 1e-12
    x = np.asarray(side, float)
    x = x - y * np.dot(x, y)
    if np.linalg.norm(x) < 1e-6:
        return direction_euler(tuple(y))
    x /= np.linalg.norm(x)
    z = np.cross(x, y)
    from .base import euler_xyz_from_matrix

    return euler_xyz_from_matrix(np.column_stack([x, y, z]))


class RobotGenerator(Generator):
    id = "robotics.robot"
    object_type = "robot"
    category = "robotics"
    description = "Humanoid robot blockout; 'soft' style gives a rounded inflatable healthcare-robot look."
    aliases = {"robot": {}, "humanoid robot": {"style": "mech"}, "android": {"style": "mech"}, "humanoid": {"style": "mech"},
               "healthcare robot": {"style": "soft"}, "inflatable robot": {"style": "soft"}, "companion robot": {"style": "soft"},
               "service robot": {"style": "soft"}}
    params = {"style": Param("soft", "choice", choices=("soft", "mech")), "height": Param(1.6, min=0.2, max=4.0),
              "color": Param("white", "color"), "eye_color": Param("#111111", "color"), "joint_color": Param("grey", "color")}
    primary_color = "color"
    color_params = {"robot": "color", "body": "color", "head": "color", "arm": "color", "leg": "color", "eye": "eye_color",
                    "joint": "joint_color"}
    size_params = {"robot": ["height"], "body": ["height"]}
    dimension_params = {"height": ["height"], "width": ["height"]}
    required_roles = {"head": 1, "torso": 1, "arm": 2, "leg": 2, "eye": 2}

    def build(self, p: dict[str, Any], detail: int) -> Builder:
        b = Builder()
        soft = p["style"] == "soft"
        body = b.material("body", p["color"], metallic=0.0 if soft else 0.6, roughness=0.55 if soft else 0.35)
        eye = b.material("eye", p["eye_color"], roughness=0.3)
        jm = b.material("joint", p["joint_color"], metallic=0.5, roughness=0.4)
        H = p["height"]
        seg = segs(detail, 24)
        if soft:
            b.add("torso", "sphere", body, pos=(0, H * 0.45, 0), scale=(1.0, 1.15, 0.85), radius=H * 0.26, width_segments=seg, height_segments=seg // 2 + 4)
            b.add("head", "sphere", body, pos=(0, H * 0.86, H * 0.02), scale=(1.15, 0.85, 1.0), radius=H * 0.09, width_segments=seg, height_segments=seg // 2)
            for s in (-1, 1):
                b.add("eye", "sphere", eye, name="eye", pos=(s * H * 0.035, H * 0.865, H * 0.105), scale=(1, 1, 0.4), radius=H * 0.012)
            b.add("eye", "cylinder", eye, name="eye_line", pos=(0, H * 0.865, H * 0.108), rot=(0, 0, 90), radius_top=H * 0.0025,
                  radius_bottom=H * 0.0025, height=H * 0.07, segments=8)
            for s in (-1, 1):
                b.add("arm", "capsule", body, name="arm", pos=(s * H * 0.3, H * 0.47, 0), rot=(0, 0, s * 12), radius=H * 0.075, length=H * 0.28,
                      segments=seg)
                b.add("leg", "capsule", body, name="leg", pos=(s * H * 0.11, H * 0.12, 0), radius=H * 0.085, length=H * 0.1, segments=seg)
        else:
            b.add("torso", "rounded_box", body, pos=(0, H * 0.58, 0), width=H * 0.32, height=H * 0.3, depth=H * 0.18, radius=H * 0.03, segments=2 + detail)
            b.add("pelvis", "rounded_box", jm, pos=(0, H * 0.41, 0), width=H * 0.24, height=H * 0.06, depth=H * 0.14, radius=H * 0.02, segments=2)
            b.add("head", "rounded_box", body, pos=(0, H * 0.84, 0), width=H * 0.15, height=H * 0.15, depth=H * 0.15, radius=H * 0.04, segments=2 + detail)
            for s in (-1, 1):
                b.add("eye", "sphere", b.material("visor", "#30d0ff", emissive="#30d0ff"), name="eye", pos=(s * H * 0.035, H * 0.85, H * 0.076),
                      radius=H * 0.015)
                strut(b, "arm", body, (s * H * 0.19, H * 0.7, 0), (s * H * 0.23, H * 0.45, 0), H * 0.035, name="arm")
                strut(b, "arm", body, (s * H * 0.23, H * 0.45, 0), (s * H * 0.24, H * 0.25, H * 0.04), H * 0.03, name="forearm")
                strut(b, "leg", body, (s * H * 0.07, H * 0.39, 0), (s * H * 0.08, H * 0.2, 0), H * 0.045, name="leg")
                strut(b, "leg", body, (s * H * 0.08, H * 0.2, 0), (s * H * 0.08, H * 0.03, 0), H * 0.04, name="shin")
                b.add("foot", "rounded_box", jm, name="foot", pos=(s * H * 0.08, H * 0.015, H * 0.03), width=H * 0.08, height=H * 0.03, depth=H * 0.14,
                      radius=H * 0.01, segments=2)
                for y in (H * 0.7, H * 0.45, H * 0.2):
                    x = s * (H * 0.19 if y > 0.6 * H else (H * 0.23 if y > 0.3 * H else H * 0.08))
                    b.add("joint", "sphere", jm, name="joint", pos=(x, y, 0), radius=H * 0.04)
        return b


GENERATORS = [RoboticArmGenerator(), RobotGenerator()]
