"""Independent GLB validation and reload.

This parser does not trust the exporter: it re-reads the bytes, checks the
container, every buffer view/accessor bound, decodes all positions and
indices, and verifies the semantic roles required for the requested object.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .gltf import CHUNK_BIN, CHUNK_JSON, FLOAT, GLB_MAGIC, UINT16, UINT32
from .scene import ModelSpec

_COMP = {FLOAT: ("<f4", 4), UINT16: ("<u2", 2), UINT32: ("<u4", 4), 5121: ("<u1", 1)}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


@dataclass
class ValidationReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "stats": self.stats}


class GLBError(Exception):
    pass


def parse_glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    if len(data) < 20:
        raise GLBError("file too small to be GLB")
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise GLBError("bad magic (not a GLB file)")
    if version != 2:
        raise GLBError(f"unsupported glTF version {version}")
    if length != len(data):
        raise GLBError(f"header length {length} != file size {len(data)}")
    offset = 12
    gltf: dict[str, Any] | None = None
    binary = b""
    while offset < len(data):
        if offset + 8 > len(data):
            raise GLBError("truncated chunk header")
        clen, ctype = struct.unpack_from("<II", data, offset)
        offset += 8
        if offset + clen > len(data):
            raise GLBError("chunk exceeds file length")
        chunk = data[offset: offset + clen]
        offset += clen
        if ctype == CHUNK_JSON:
            try:
                gltf = json.loads(chunk.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise GLBError(f"invalid JSON chunk: {exc}") from None
        elif ctype == CHUNK_BIN:
            binary = chunk
    if gltf is None:
        raise GLBError("missing JSON chunk")
    return gltf, binary


def _read_accessor(gltf: dict[str, Any], binary: bytes, index: int) -> np.ndarray:
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    dtype, size = _COMP[acc["componentType"]]
    n = _NCOMP[acc["type"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    need = acc["count"] * n * size
    if start + need > view.get("byteOffset", 0) + view["byteLength"] or start + need > len(binary):
        raise GLBError(f"accessor {index} reads past its buffer view")
    arr = np.frombuffer(binary, dtype=dtype, count=acc["count"] * n, offset=start)
    return arr.reshape(-1, n) if n > 1 else arr


def validate_glb(data: bytes, required_roles: dict[str, int] | None = None, object_type: str | None = None) -> ValidationReport:
    rep = ValidationReport(ok=False)
    try:
        gltf, binary = parse_glb(data)
    except GLBError as exc:
        rep.errors.append(str(exc))
        return rep
    if gltf.get("asset", {}).get("version") != "2.0":
        rep.errors.append("asset.version must be 2.0")
    buffers = gltf.get("buffers", [])
    if not buffers or buffers[0].get("byteLength", -1) > len(binary):
        rep.errors.append("buffer 0 larger than BIN chunk")
    for i, v in enumerate(gltf.get("bufferViews", [])):
        if v.get("byteOffset", 0) + v.get("byteLength", 0) > len(binary):
            rep.errors.append(f"bufferView {i} out of range")
    n_mat = len(gltf.get("materials", []))
    vertices = triangles = 0
    lo = np.array([np.inf] * 3)
    hi = np.array([-np.inf] * 3)
    meshes = gltf.get("meshes", [])
    if not meshes:
        rep.errors.append("no meshes")
    for mi, mesh in enumerate(meshes):
        for prim in mesh.get("primitives", []):
            try:
                pos = _read_accessor(gltf, binary, prim["attributes"]["POSITION"])
                idx = _read_accessor(gltf, binary, prim["indices"])
            except (GLBError, KeyError, IndexError, ValueError) as exc:
                rep.errors.append(f"mesh {mi}: {exc}")
                continue
            if pos.shape[0] == 0 or idx.size == 0 or idx.size % 3:
                rep.errors.append(f"mesh {mi}: empty geometry")
                continue
            if not np.isfinite(pos).all():
                rep.errors.append(f"mesh {mi}: non-finite positions")
            if int(idx.max()) >= pos.shape[0]:
                rep.errors.append(f"mesh {mi}: index out of range")
                continue
            acc = gltf["accessors"][prim["attributes"]["POSITION"]]
            if "min" in acc and "max" in acc:
                if not (np.allclose(pos.min(axis=0), acc["min"], atol=1e-4) and np.allclose(pos.max(axis=0), acc["max"], atol=1e-4)):
                    rep.errors.append(f"mesh {mi}: POSITION min/max do not match data")
            else:
                rep.errors.append(f"mesh {mi}: POSITION accessor lacks min/max")
            if "material" in prim and not (0 <= prim["material"] < n_mat):
                rep.errors.append(f"mesh {mi}: invalid material index")
            tri = idx.reshape(-1, 3)
            p = pos.astype(np.float64)
            area = np.linalg.norm(np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]]), axis=1)
            if float(area.sum()) <= 0:
                rep.errors.append(f"mesh {mi}: zero surface area")
            vertices += pos.shape[0]
            triangles += tri.shape[0]
    roles: dict[str, int] = {}
    world_lo, world_hi = _world_bounds(gltf, binary)
    for node in gltf.get("nodes", []):
        if "mesh" in node:
            if not (0 <= node["mesh"] < len(meshes)):
                rep.errors.append(f"node {node.get('name')} references missing mesh")
            role = (node.get("extras") or {}).get("role")
            if role:
                roles[role] = roles.get(role, 0) + 1
    scenes = gltf.get("scenes", [])
    if not scenes or not scenes[gltf.get("scene", 0)].get("nodes"):
        rep.errors.append("no scene root")
    spec_dict = (gltf.get("asset", {}).get("extras") or {}).get("baymax")
    actual_type = spec_dict.get("object_type") if isinstance(spec_dict, dict) else None
    if object_type and actual_type != object_type:
        rep.errors.append(f"file describes '{actual_type}', expected '{object_type}'")
    for role, need in (required_roles or {}).items():
        if roles.get(role, 0) < need:
            rep.errors.append(f"expected at least {need} '{role}' part(s), found {roles.get(role, 0)}")
    if world_lo is not None:
        lo, hi = world_lo, world_hi
    rep.stats = {"bytes": len(data), "meshes": len(meshes), "nodes": len(gltf.get("nodes", [])), "materials": n_mat,
                 "vertices": int(vertices), "triangles": int(triangles), "roles": roles, "objectType": actual_type,
                 "bounds": {"min": [round(float(v), 4) for v in lo], "max": [round(float(v), 4) for v in hi]} if np.isfinite(lo).all() else None}
    if world_lo is not None and float(np.max(world_hi - world_lo)) <= 0:
        rep.errors.append("model has zero extent")
    rep.ok = not rep.errors
    return rep


def _quat_matrix(q: list[float]) -> np.ndarray:
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _world_bounds(gltf: dict[str, Any], binary: bytes) -> tuple[np.ndarray | None, np.ndarray | None]:
    """World-space bounds for mesh nodes (one level of hierarchy, which is what BAYMAX exports)."""
    lo = np.array([np.inf] * 3)
    hi = np.array([-np.inf] * 3)
    found = False
    for node in gltf.get("nodes", []):
        if "mesh" not in node:
            continue
        try:
            prim = gltf["meshes"][node["mesh"]]["primitives"][0]
            acc = gltf["accessors"][prim["attributes"]["POSITION"]]
        except (KeyError, IndexError):
            continue
        corners = np.array([[x, y, z] for x in (acc["min"][0], acc["max"][0]) for y in (acc["min"][1], acc["max"][1])
                            for z in (acc["min"][2], acc["max"][2])])
        s = np.array(node.get("scale", [1, 1, 1]))
        r = _quat_matrix(node.get("rotation", [0, 0, 0, 1]))
        t = np.array(node.get("translation", [0, 0, 0]))
        world = (r @ (corners * s).T).T + t
        lo = np.minimum(lo, world.min(axis=0))
        hi = np.maximum(hi, world.max(axis=0))
        found = True
    return (lo, hi) if found else (None, None)


def load_spec(data: bytes) -> ModelSpec:
    gltf, _ = parse_glb(data)
    spec = (gltf.get("asset", {}).get("extras") or {}).get("baymax")
    if not isinstance(spec, dict):
        raise GLBError("GLB has no embedded BAYMAX model specification")
    return ModelSpec.from_dict(spec)
