"""Software simulations producing telemetry.

All output is labelled ``"source": "simulated"``. Nothing here reads from or
drives physical hardware.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np


@dataclass
class Telemetry:
    model: str
    title: str
    params: dict[str, float]
    series: dict[str, list[float]]
    units: dict[str, str]
    summary: dict[str, float] = field(default_factory=dict)
    source: str = "simulated"

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "model": self.model, "title": self.title, "params": self.params, "series": self.series,
                "units": self.units, "summary": self.summary}

    def validate(self) -> None:
        lengths = {len(v) for v in self.series.values()}
        if len(lengths) != 1 or 0 in lengths:
            raise ValueError("telemetry series are empty or of unequal length")
        for k, v in self.series.items():
            if not all(math.isfinite(x) for x in v):
                raise ValueError(f"series {k} contains non-finite values")


def _r(a: np.ndarray, n: int = 6) -> list[float]:
    return [round(float(x), n) for x in a]


def projectile(speed: float = 20.0, angle_deg: float = 45.0, drag: float = 0.0, mass: float = 1.0, dt: float = 0.01, g: float = 9.81) -> Telemetry:
    a = math.radians(angle_deg)
    x, y, vx, vy, t = 0.0, 0.0, speed * math.cos(a), speed * math.sin(a), 0.0
    T, X, Y, V = [0.0], [0.0], [0.0], [speed]
    for _ in range(200000):
        v = math.hypot(vx, vy)
        ax = -drag / mass * v * vx
        ay = -g - drag / mass * v * vy
        vx += ax * dt
        vy += ay * dt
        x += vx * dt
        y += vy * dt
        t += dt
        if y < 0:
            break
        T.append(t), X.append(x), Y.append(y), V.append(math.hypot(vx, vy))
    tel = Telemetry("projectile", f"Projectile at {speed:g} m/s, {angle_deg:g}°", {"speed": speed, "angle_deg": angle_deg, "drag": drag, "mass": mass},
                    {"t": _r(np.array(T), 4), "x": _r(np.array(X), 4), "y": _r(np.array(Y), 4), "speed": _r(np.array(V), 4)},
                    {"t": "s", "x": "m", "y": "m", "speed": "m/s"})
    tel.summary = {"range_m": round(X[-1], 3), "max_height_m": round(max(Y), 3), "flight_time_s": round(T[-1], 3)}
    if drag == 0:
        tel.summary["analytic_range_m"] = round(speed ** 2 * math.sin(2 * a) / g, 3)
    return tel


def pendulum(length: float = 1.0, angle_deg: float = 30.0, damping: float = 0.05, duration: float = 10.0, dt: float = 0.002, g: float = 9.81) -> Telemetry:
    th, w = math.radians(angle_deg), 0.0
    n = int(duration / dt)
    T, TH, W = np.zeros(n), np.zeros(n), np.zeros(n)
    for i in range(n):
        # semi-implicit Euler
        w += (-(g / length) * math.sin(th) - damping * w) * dt
        th += w * dt
        T[i], TH[i], W[i] = (i + 1) * dt, math.degrees(th), w
    step = max(1, n // 1500)
    tel = Telemetry("pendulum", f"Damped pendulum L={length:g} m", {"length": length, "angle_deg": angle_deg, "damping": damping, "duration": duration},
                    {"t": _r(T[::step], 4), "angle_deg": _r(TH[::step], 4), "omega": _r(W[::step], 4)}, {"t": "s", "angle_deg": "°", "omega": "rad/s"})
    tel.summary = {"small_angle_period_s": round(2 * math.pi * math.sqrt(length / g), 4), "final_angle_deg": round(float(TH[-1]), 4)}
    return tel


def spring_mass(mass: float = 1.0, stiffness: float = 20.0, damping: float = 0.5, x0: float = 0.1, duration: float = 10.0, dt: float = 0.001) -> Telemetry:
    x, v = x0, 0.0
    n = int(duration / dt)
    T, X, V = np.zeros(n), np.zeros(n), np.zeros(n)
    for i in range(n):
        a = (-stiffness * x - damping * v) / mass
        v += a * dt
        x += v * dt
        T[i], X[i], V[i] = (i + 1) * dt, x, v
    step = max(1, n // 1500)
    tel = Telemetry("spring_mass", f"Spring-mass k={stiffness:g} N/m", {"mass": mass, "stiffness": stiffness, "damping": damping, "x0": x0, "duration": duration},
                    {"t": _r(T[::step], 4), "x": _r(X[::step]), "v": _r(V[::step])}, {"t": "s", "x": "m", "v": "m/s"})
    tel.summary = {"natural_frequency_hz": round(math.sqrt(stiffness / mass) / (2 * math.pi), 4), "final_x_m": round(float(X[-1]), 6)}
    return tel


def dc_motor_pid(target_rpm: float = 1500.0, kp: float = 3.0, ki: float = 20.0, kd: float = 0.0, duration: float = 3.0, dt: float = 0.0002,
                 load_torque: float = 0.0) -> Telemetry:
    """DC motor speed loop (mechanical time constant J*R/(Kt*Ke + b*R) ~ 2.9 s). Default PI gains were chosen
    by a grid search for <1% overshoot; settling (~1.7 s to 1500 rpm) is limited by the 24 V supply.
    Conditional, clamped integration prevents wind-up while the voltage is saturated."""
    # J dw/dt = Kt i - b w - T_load ; L di/dt = V - R i - Ke w
    J, b, Kt, Ke, R, L, vmax = 0.01, 0.001, 0.05, 0.05, 1.0, 0.5e-3, 24.0
    w = i = i_term = prev_err = 0.0
    target = target_rpm * 2 * math.pi / 60
    n = int(duration / dt)
    T, RPM, VOLT, CUR = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
    for k in range(n):
        err = target - w
        deriv = (err - prev_err) / dt if k else 0.0
        prev_err = err
        u = kp * err + i_term + kd * deriv
        v = max(-vmax, min(vmax, u))
        # anti-windup: integrate only when not pushing further into saturation, and bound the integral term
        if v == u or (u > vmax) != (err > 0):
            i_term = max(-vmax, min(vmax, i_term + ki * err * dt))
        di = (v - R * i - Ke * w) / L
        i += di * dt
        dw = (Kt * i - b * w - load_torque) / J
        w += dw * dt
        T[k], RPM[k], VOLT[k], CUR[k] = (k + 1) * dt, w * 60 / (2 * math.pi), v, i
    step = max(1, n // 1500)
    rpm = RPM[::step]
    within = np.where(np.abs(RPM - target_rpm) <= 0.02 * abs(target_rpm))[0]
    settle = float(T[within[0]]) if within.size and np.all(np.abs(RPM[within[0]:] - target_rpm) <= 0.02 * abs(target_rpm)) else float("nan")
    tel = Telemetry("dc_motor_pid", f"PID speed control to {target_rpm:g} rpm", {"target_rpm": target_rpm, "kp": kp, "ki": ki, "kd": kd,
                    "duration": duration, "load_torque": load_torque},
                    {"t": _r(T[::step], 5), "rpm": _r(rpm, 3), "voltage": _r(VOLT[::step], 4), "current": _r(CUR[::step], 4)},
                    {"t": "s", "rpm": "rpm", "voltage": "V", "current": "A"})
    tel.summary = {"final_rpm": round(float(RPM[-1]), 2), "overshoot_pct": round(max(0.0, (float(RPM.max()) - target_rpm) / target_rpm * 100), 2)}
    if math.isfinite(settle):
        tel.summary["settling_time_s"] = round(settle, 4)
    return tel


def drone_altitude(target_m: float = 10.0, mass: float = 1.2, kp: float = 4.0, ki: float = 0.6, kd: float = 3.0, duration: float = 15.0,
                   dt: float = 0.002, max_thrust: float = 30.0) -> Telemetry:
    g = 9.81
    z = vz = integ = 0.0
    prev = target_m
    n = int(duration / dt)
    T, Z, VZ, TH = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
    for k in range(n):
        err = target_m - z
        integ = max(-20.0, min(20.0, integ + err * dt))
        deriv = -(z - prev) / dt if k else 0.0
        prev = z
        thrust = max(0.0, min(max_thrust, mass * g + mass * (kp * err + ki * integ + kd * deriv)))
        az = thrust / mass - g - 0.1 * vz
        vz += az * dt
        z = max(0.0, z + vz * dt)
        if z == 0.0 and vz < 0:
            vz = 0.0
        T[k], Z[k], VZ[k], TH[k] = (k + 1) * dt, z, vz, thrust
    step = max(1, n // 1500)
    tel = Telemetry("drone_altitude", f"Drone altitude hold at {target_m:g} m", {"target_m": target_m, "mass": mass, "kp": kp, "ki": ki, "kd": kd,
                    "duration": duration}, {"t": _r(T[::step], 4), "altitude": _r(Z[::step], 4), "vz": _r(VZ[::step], 4), "thrust": _r(TH[::step], 4)},
                    {"t": "s", "altitude": "m", "vz": "m/s", "thrust": "N"})
    tel.summary = {"final_altitude_m": round(float(Z[-1]), 3), "max_altitude_m": round(float(Z.max()), 3)}
    return tel


SIMULATIONS: dict[str, tuple[Callable[..., Telemetry], tuple[str, ...]]] = {
    "projectile": (projectile, ("projectile", "ballistic", "cannon", "throw", "launch angle", "trajectory")),
    "pendulum": (pendulum, ("pendulum", "swing")),
    "spring_mass": (spring_mass, ("spring", "mass-spring", "spring-mass", "oscillator", "harmonic")),
    "dc_motor_pid": (dc_motor_pid, ("motor", "pid", "rpm", "speed control")),
    "drone_altitude": (drone_altitude, ("drone", "altitude", "hover", "quadcopter", "uav")),
}

_NUM = r"(-?\d+(?:\.\d+)?)"


def pick_simulation(text: str) -> str | None:
    t = text.lower()
    best = None
    for name, (_, keys) in SIMULATIONS.items():
        for k in keys:
            if k in t and (best is None or len(k) > best[1]):
                best = (name, len(k))
    return best[0] if best else None


def parse_params(name: str, text: str) -> dict[str, float]:
    t = text.lower()
    out: dict[str, float] = {}

    def grab(pattern: str, key: str) -> None:
        m = re.search(pattern, t)
        if m:
            out[key] = float(m.group(1))

    if name == "projectile":
        grab(_NUM + r"\s*(?:m/s|meters per second|mps)", "speed")
        grab(_NUM + r"\s*(?:°|deg|degrees)", "angle_deg")
        grab(r"drag(?: coefficient)?(?: of)?\s*" + _NUM, "drag")
    elif name == "pendulum":
        grab(_NUM + r"\s*(?:m|meter|metre)s?\b(?! ?/)", "length")
        grab(_NUM + r"\s*(?:°|deg|degrees)", "angle_deg")
        grab(r"damping(?: of)?\s*" + _NUM, "damping")
        grab(_NUM + r"\s*(?:s|sec|seconds)\b", "duration")
    elif name == "spring_mass":
        grab(_NUM + r"\s*kg", "mass")
        grab(_NUM + r"\s*n/m", "stiffness")
        grab(r"damping(?: of)?\s*" + _NUM, "damping")
        grab(_NUM + r"\s*(?:s|sec|seconds)\b", "duration")
    elif name == "dc_motor_pid":
        grab(_NUM + r"\s*rpm", "target_rpm")
        grab(r"kp\s*=?\s*" + _NUM, "kp")
        grab(r"ki\s*=?\s*" + _NUM, "ki")
        grab(r"kd\s*=?\s*" + _NUM, "kd")
        grab(_NUM + r"\s*(?:s|sec|seconds)\b", "duration")
    elif name == "drone_altitude":
        grab(_NUM + r"\s*(?:m|meters|metres)\b", "target_m")
        grab(_NUM + r"\s*kg", "mass")
        grab(r"kp\s*=?\s*" + _NUM, "kp")
        grab(r"ki\s*=?\s*" + _NUM, "ki")
        grab(r"kd\s*=?\s*" + _NUM, "kd")
        grab(_NUM + r"\s*(?:s|sec|seconds)\b", "duration")
    return out


LIMITS = {"duration": (0.1, 120.0), "speed": (0.1, 2000.0), "angle_deg": (-89.0, 89.0), "length": (0.01, 100.0), "mass": (0.01, 10000.0),
          "stiffness": (0.01, 1e6), "target_rpm": (1.0, 20000.0), "target_m": (0.1, 1000.0), "damping": (0.0, 100.0), "drag": (0.0, 10.0)}


def run_simulation(name: str, params: dict[str, float]) -> Telemetry:
    if name not in SIMULATIONS:
        raise ValueError(f"unknown simulation {name!r}; available: {', '.join(SIMULATIONS)}")
    fn = SIMULATIONS[name][0]
    clean = {}
    import inspect

    allowed = set(inspect.signature(fn).parameters)
    for k, v in params.items():
        if k not in allowed:
            raise ValueError(f"{name}: unknown parameter {k!r}")
        lo, hi = LIMITS.get(k, (-1e6, 1e6))
        clean[k] = max(lo, min(hi, float(v)))
    tel = fn(**clean)
    tel.validate()
    return tel
