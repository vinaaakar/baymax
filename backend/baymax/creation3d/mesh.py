"""Procedural mesh primitives (Y-up, metres, right-handed, +Z is "front").

Every primitive returns a :class:`Mesh` with float32 positions/normals and
uint32 triangle indices (counter-clockwise front faces). These are reusable
building blocks: generators compose objects out of them rather than shipping
hand-modelled demo assets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


@dataclass
class Mesh:
    positions: np.ndarray  # (N, 3) float32
    normals: np.ndarray    # (N, 3) float32
    indices: np.ndarray    # (M,) uint32

    @property
    def vertex_count(self) -> int:
        return int(self.positions.shape[0])

    @property
    def triangle_count(self) -> int:
        return int(self.indices.shape[0] // 3)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.positions.min(axis=0), self.positions.max(axis=0)

    def validate(self) -> None:
        if self.positions.ndim != 2 or self.positions.shape[1] != 3 or self.positions.shape[0] == 0:
            raise ValueError("mesh has no vertices")
        if self.normals.shape != self.positions.shape:
            raise ValueError("normals/positions mismatch")
        if self.indices.size == 0 or self.indices.size % 3:
            raise ValueError("mesh has no triangles")
        if int(self.indices.max()) >= self.vertex_count:
            raise ValueError("index out of range")
        if not np.isfinite(self.positions).all() or not np.isfinite(self.normals).all():
            raise ValueError("non-finite vertex data")


def _mesh(pos: Iterable, nrm: Iterable, idx: Iterable) -> Mesh:
    p = np.asarray(pos, dtype=np.float32).reshape(-1, 3)
    n = np.asarray(nrm, dtype=np.float32).reshape(-1, 3)
    i = np.asarray(idx, dtype=np.uint32).reshape(-1)
    lengths = np.linalg.norm(n, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    return Mesh(p, (n / lengths).astype(np.float32), i)


def merge(meshes: Sequence[Mesh]) -> Mesh:
    pos, nrm, idx = [], [], []
    offset = 0
    for m in meshes:
        pos.append(m.positions)
        nrm.append(m.normals)
        idx.append(m.indices + offset)
        offset += m.vertex_count
    return Mesh(np.concatenate(pos), np.concatenate(nrm), np.concatenate(idx).astype(np.uint32))


def smooth_normals(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    tris = indices.reshape(-1, 3)
    v0, v1, v2 = positions[tris[:, 0]], positions[tris[:, 1]], positions[tris[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    normals = np.zeros_like(positions)
    for k in range(3):
        np.add.at(normals, tris[:, k], fn)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    return (normals / lengths).astype(np.float32)


# ----------------------------------------------------------------- primitives
def box(width: float = 1.0, height: float = 1.0, depth: float = 1.0) -> Mesh:
    hx, hy, hz = width / 2, height / 2, depth / 2
    faces = [
        ((1, 0, 0), [(hx, -hy, hz), (hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz)]),
        ((-1, 0, 0), [(-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz)]),
        ((0, 1, 0), [(-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz), (-hx, hy, -hz)]),
        ((0, -1, 0), [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)]),
        ((0, 0, 1), [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]),
        ((0, 0, -1), [(hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)]),
    ]
    pos, nrm, idx = [], [], []
    for n, quad in faces:
        b = len(pos)
        pos += quad
        nrm += [n] * 4
        idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    return _mesh(pos, nrm, idx)


def rounded_box(width: float = 1.0, height: float = 1.0, depth: float = 1.0, radius: float = 0.1, segments: int = 4) -> Mesh:
    """Box with uniformly rounded edges (cube-grid projected onto an inner box)."""
    radius = max(1e-4, min(radius, width / 2, height / 2, depth / 2))
    half = np.array([width / 2, height / 2, depth / 2], dtype=np.float64)
    inner = half - radius
    n = max(2, segments * 2 + 2)
    pos, nrm, idx = [], [], []
    for axis in range(3):
        for sign in (1, -1):
            u_axis, v_axis = [a for a in range(3) if a != axis]
            if sign < 0:
                u_axis, v_axis = v_axis, u_axis
            base = len(pos)
            for i in range(n + 1):
                for j in range(n + 1):
                    p = np.zeros(3)
                    p[axis] = sign * half[axis]
                    p[u_axis] = -half[u_axis] + 2 * half[u_axis] * i / n
                    p[v_axis] = -half[v_axis] + 2 * half[v_axis] * j / n
                    core = np.clip(p, -inner, inner)
                    d = p - core
                    ln = np.linalg.norm(d)
                    normal = d / ln if ln > 1e-9 else np.eye(3)[axis] * sign
                    pos.append(core + normal * radius)
                    nrm.append(normal)
            for i in range(n):
                for j in range(n):
                    a = base + i * (n + 1) + j
                    b, c, d_ = a + (n + 1), a + (n + 1) + 1, a + 1
                    idx += [a, b, c, a, c, d_]
    m = _mesh(pos, nrm, idx)
    return _fix_winding(m)


def _fix_winding(m: Mesh) -> Mesh:
    """Flip triangles whose geometric normal opposes the vertex normals."""
    tris = m.indices.reshape(-1, 3).copy()
    p = m.positions
    fn = np.cross(p[tris[:, 1]] - p[tris[:, 0]], p[tris[:, 2]] - p[tris[:, 0]])
    vn = m.normals[tris[:, 0]] + m.normals[tris[:, 1]] + m.normals[tris[:, 2]]
    flip = (fn * vn).sum(axis=1) < 0
    tris[flip] = tris[flip][:, [0, 2, 1]]
    return Mesh(m.positions, m.normals, tris.reshape(-1).astype(np.uint32))


def cylinder(radius_top: float = 0.5, radius_bottom: float = 0.5, height: float = 1.0, segments: int = 24,
             caps: bool = True) -> Mesh:
    hy = height / 2
    pos, nrm, idx = [], [], []
    slope = (radius_bottom - radius_top) / height if height else 0.0
    for i in range(segments + 1):
        a = 2 * math.pi * i / segments
        c, s = math.cos(a), math.sin(a)
        n = (c, slope, s)
        pos += [(radius_bottom * c, -hy, radius_bottom * s), (radius_top * c, hy, radius_top * s)]
        nrm += [n, n]
    for i in range(segments):
        a, b, c_, d = 2 * i, 2 * i + 1, 2 * i + 3, 2 * i + 2
        idx += [a, b, c_, a, c_, d]
    if caps:
        for y, r, ny in ((hy, radius_top, 1.0), (-hy, radius_bottom, -1.0)):
            if r <= 0:
                continue
            center = len(pos)
            pos.append((0, y, 0))
            nrm.append((0, ny, 0))
            for i in range(segments + 1):
                a = 2 * math.pi * i / segments
                pos.append((r * math.cos(a), y, r * math.sin(a)))
                nrm.append((0, ny, 0))
            for i in range(segments):
                if ny > 0:
                    idx += [center, center + i + 2, center + i + 1]
                else:
                    idx += [center, center + i + 1, center + i + 2]
    return _fix_winding(_mesh(pos, nrm, idx))


def cone(radius: float = 0.5, height: float = 1.0, segments: int = 24) -> Mesh:
    return cylinder(0.0, radius, height, segments)


def sphere(radius: float = 0.5, width_segments: int = 24, height_segments: int = 16) -> Mesh:
    pos, nrm, idx = [], [], []
    for y in range(height_segments + 1):
        v = y / height_segments
        theta = v * math.pi
        for x in range(width_segments + 1):
            u = x / width_segments
            phi = u * 2 * math.pi
            n = (-math.cos(phi) * math.sin(theta), math.cos(theta), math.sin(phi) * math.sin(theta))
            pos.append(tuple(radius * c for c in n))
            nrm.append(n)
    row = width_segments + 1
    for y in range(height_segments):
        for x in range(width_segments):
            a = y * row + x
            b, c, d = a + row, a + row + 1, a + 1
            if y != 0:
                idx += [a, b, d]
            if y != height_segments - 1:
                idx += [b, c, d]
    return _fix_winding(_mesh(pos, nrm, idx))


def capsule(radius: float = 0.25, length: float = 1.0, segments: int = 20, rings: int = 8) -> Mesh:
    """Capsule along Y; ``length`` is the cylindrical section."""
    profile = []
    for i in range(rings + 1):
        a = -math.pi / 2 + (math.pi / 2) * i / rings
        profile.append((radius * math.cos(a), -length / 2 + radius * math.sin(a)))
    for i in range(rings + 1):
        a = (math.pi / 2) * i / rings
        profile.append((radius * math.cos(a), length / 2 + radius * math.sin(a)))
    profile[0] = (0.0, profile[0][1])
    profile[-1] = (0.0, profile[-1][1])
    return lathe(profile, segments)


def torus(radius: float = 0.5, tube: float = 0.15, radial_segments: int = 12, tubular_segments: int = 32) -> Mesh:
    """Torus lying in the XZ plane (axis = Y)."""
    pos, nrm, idx = [], [], []
    for j in range(radial_segments + 1):
        v = j / radial_segments * 2 * math.pi
        for i in range(tubular_segments + 1):
            u = i / tubular_segments * 2 * math.pi
            cx, cz = radius * math.cos(u), radius * math.sin(u)
            x = (radius + tube * math.cos(v)) * math.cos(u)
            y = tube * math.sin(v)
            z = (radius + tube * math.cos(v)) * math.sin(u)
            pos.append((x, y, z))
            nrm.append((x - cx, y, z - cz))
    row = tubular_segments + 1
    for j in range(radial_segments):
        for i in range(tubular_segments):
            a, b = j * row + i, (j + 1) * row + i
            idx += [a, b, a + 1, b, b + 1, a + 1]
    return _fix_winding(_mesh(pos, nrm, idx))


def lathe(profile: Sequence[tuple[float, float]], segments: int = 32) -> Mesh:
    """Revolve a (radius, y) profile around the Y axis. Profile runs bottom -> top."""
    if len(profile) < 2:
        raise ValueError("lathe profile needs at least two points")
    pos = []
    for i in range(segments + 1):
        a = 2 * math.pi * i / segments
        c, s = math.cos(a), math.sin(a)
        for r, y in profile:
            pos.append((max(r, 0.0) * c, y, max(r, 0.0) * s))
    rows = len(profile)
    idx = []
    for i in range(segments):
        for j in range(rows - 1):
            a = i * rows + j
            b = (i + 1) * rows + j
            idx += [a, a + 1, b + 1, a, b + 1, b]
    p = np.asarray(pos, dtype=np.float32)
    i_arr = np.asarray(idx, dtype=np.uint32)
    n = smooth_normals(p, i_arr)
    # Fix poles/seams: radial outward normals where degenerate.
    bad = np.linalg.norm(n, axis=1) < 0.5
    if bad.any():
        n[bad] = np.array([0, 1, 0], dtype=np.float32)
    m = Mesh(p, n, i_arr)
    return _orient_outward(m)


def _orient_outward(m: Mesh) -> Mesh:
    """Ensure triangles face away from the mesh centroid on average (for closed-ish solids)."""
    tris = m.indices.reshape(-1, 3)
    p = m.positions
    fn = np.cross(p[tris[:, 1]] - p[tris[:, 0]], p[tris[:, 2]] - p[tris[:, 0]])
    centroid = p.mean(axis=0)
    centers = p[tris].mean(axis=1)
    radial = centers - centroid
    radial[:, 1] = 0  # revolve meshes: outward is radial in XZ
    score = (fn * radial).sum()
    if score < 0:
        idx = tris[:, [0, 2, 1]].reshape(-1).astype(np.uint32)
        return Mesh(m.positions, smooth_normals(p, idx), idx)
    return m


def triangulate(polygon: Sequence[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Ear-clipping triangulation of a simple polygon (CCW or CW)."""
    pts = [tuple(map(float, p)) for p in polygon]
    n = len(pts)
    if n < 3:
        raise ValueError("polygon needs 3+ points")
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)) / 2
    order = list(range(n)) if area > 0 else list(reversed(range(n)))
    tris: list[tuple[int, int, int]] = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= -1e-12 and cross(b, c, p) >= -1e-12 and cross(c, a, p) >= -1e-12

    guard = 0
    while len(order) > 3 and guard < 10000:
        guard += 1
        found = False
        for k in range(len(order)):
            i0, i1, i2 = order[k - 1], order[k], order[(k + 1) % len(order)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if cross(a, b, c) <= 1e-12:
                continue
            if any(inside(pts[j], a, b, c) for j in order if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            order.pop(k)
            found = True
            break
        if not found:  # degenerate input: fall back to a fan
            break
    for k in range(1, len(order) - 1):
        tris.append((order[0], order[k], order[k + 1]))
    return tris


def extrude(polygon: Sequence[tuple[float, float]], depth: float = 0.1) -> Mesh:
    """Extrude an XY polygon along Z (centered on z=0) with hard edges."""
    pts = [tuple(map(float, p)) for p in polygon]
    n = len(pts)
    hz = depth / 2
    tris = triangulate(pts)
    pos, nrm, idx = [], [], []
    # caps
    for z, nz in ((hz, 1.0), (-hz, -1.0)):
        base = len(pos)
        for x, y in pts:
            pos.append((x, y, z))
            nrm.append((0, 0, nz))
        for a, b, c in tris:
            idx += [base + a, base + b, base + c] if nz > 0 else [base + a, base + c, base + b]
    # walls
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    ccw = area > 0
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        ex, ey = x1 - x0, y1 - y0
        nx, ny = (ey, -ex) if ccw else (-ey, ex)
        base = len(pos)
        pos += [(x0, y0, -hz), (x1, y1, -hz), (x1, y1, hz), (x0, y0, hz)]
        nrm += [(nx, ny, 0)] * 4
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return _fix_winding(_mesh(pos, nrm, idx))


def loft(sections: Sequence[tuple[float, Sequence[tuple[float, float]]]], closed_ends: bool = True) -> Mesh:
    """Loft closed XY cross-sections placed along Z. Each section: (z, [(x, y), ...]); equal point counts."""
    if len(sections) < 2:
        raise ValueError("loft needs 2+ sections")
    count = len(sections[0][1])
    if any(len(s[1]) != count for s in sections):
        raise ValueError("all loft sections need the same number of points")
    pos = [(x, y, z) for z, ring in sections for (x, y) in ring]
    idx = []
    for s in range(len(sections) - 1):
        for i in range(count):
            a = s * count + i
            b = s * count + (i + 1) % count
            c = (s + 1) * count + (i + 1) % count
            d = (s + 1) * count + i
            idx += [a, b, c, a, c, d]
    p = np.asarray(pos, dtype=np.float32)
    meshes = [Mesh(p, smooth_normals(p, np.asarray(idx, dtype=np.uint32)), np.asarray(idx, dtype=np.uint32))]
    if closed_ends:
        for z, ring in (sections[0], sections[-1]):
            cap = extrude(ring, 1e-4)
            cap.positions[:, 2] += z
            meshes.append(cap)
    m = merge(meshes)
    return _orient_from_center(m)


def _orient_from_center(m: Mesh) -> Mesh:
    tris = m.indices.reshape(-1, 3).copy()
    p = m.positions
    fn = np.cross(p[tris[:, 1]] - p[tris[:, 0]], p[tris[:, 2]] - p[tris[:, 0]])
    centroid = p.mean(axis=0)
    out = p[tris].mean(axis=1) - centroid
    if (fn * out).sum() < 0:
        tris = tris[:, [0, 2, 1]]
        idx = tris.reshape(-1).astype(np.uint32)
        return Mesh(m.positions, -m.normals, idx)
    return m


def tube(path: Sequence[tuple[float, float, float]], radius: float = 0.02, segments: int = 10) -> Mesh:
    """Sweep a circle along a polyline (parallel-transport frames)."""
    pts = np.asarray(path, dtype=np.float64)
    if len(pts) < 2:
        raise ValueError("tube path needs 2+ points")
    tangents = np.gradient(pts, axis=0)
    tangents /= np.linalg.norm(tangents, axis=1, keepdims=True) + 1e-12
    ref = np.array([0, 1, 0]) if abs(tangents[0][1]) < 0.9 else np.array([1, 0, 0])
    normal = np.cross(tangents[0], ref)
    normal /= np.linalg.norm(normal)
    pos, nrm = [], []
    for k, (p, t) in enumerate(zip(pts, tangents)):
        if k > 0:
            normal = normal - t * np.dot(normal, t)
            ln = np.linalg.norm(normal)
            normal = normal / ln if ln > 1e-9 else np.cross(t, ref) / (np.linalg.norm(np.cross(t, ref)) + 1e-12)
        binormal = np.cross(t, normal)
        for i in range(segments + 1):
            a = 2 * math.pi * i / segments
            d = math.cos(a) * normal + math.sin(a) * binormal
            pos.append(p + radius * d)
            nrm.append(d)
    idx = []
    row = segments + 1
    for k in range(len(pts) - 1):
        for i in range(segments):
            a = k * row + i
            b = (k + 1) * row + i
            idx += [a, b, a + 1, b, b + 1, a + 1]
    return _fix_winding(_mesh(pos, nrm, idx))


def helix(radius: float, pitch: float, turns: float, wire: float, segments_per_turn: int = 24) -> Mesh:
    n = max(8, int(turns * segments_per_turn))
    path = [(radius * math.cos(2 * math.pi * turns * i / n), pitch * turns * i / n, radius * math.sin(2 * math.pi * turns * i / n))
            for i in range(n + 1)]
    return tube(path, wire, 8)


def gear_profile(teeth: int, outer_radius: float, root_radius: float) -> list[tuple[float, float]]:
    pts = []
    teeth = max(4, min(teeth, 120))
    step = 2 * math.pi / teeth
    for t in range(teeth):
        a0 = t * step
        for frac, r in ((0.0, root_radius), (0.15, outer_radius), (0.5, outer_radius), (0.65, root_radius)):
            a = a0 + frac * step
            pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


def gear(teeth: int = 20, outer_radius: float = 0.5, root_radius: float = 0.42, thickness: float = 0.1) -> Mesh:
    return extrude(gear_profile(teeth, outer_radius, root_radius), thickness)


def regular_polygon(sides: int, radius: float, rotation: float = 0.0) -> list[tuple[float, float]]:
    return [(radius * math.cos(rotation + 2 * math.pi * i / sides), radius * math.sin(rotation + 2 * math.pi * i / sides))
            for i in range(sides)]


def ellipse(rx: float, ry: float, n: int = 24) -> list[tuple[float, float]]:
    return [(rx * math.cos(2 * math.pi * i / n), ry * math.sin(2 * math.pi * i / n)) for i in range(n)]


PRIMITIVES = {
    "box": box,
    "rounded_box": rounded_box,
    "cylinder": cylinder,
    "cone": cone,
    "sphere": sphere,
    "capsule": capsule,
    "torus": torus,
    "lathe": lathe,
    "extrude": extrude,
    "loft": loft,
    "tube": tube,
    "helix": helix,
    "gear": gear,
}


def build_primitive(kind: str, params: dict) -> Mesh:
    fn = PRIMITIVES.get(kind)
    if fn is None:
        raise ValueError(f"unknown primitive {kind!r}")
    mesh = fn(**params)
    mesh.validate()
    return mesh
