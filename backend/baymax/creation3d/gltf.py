"""glTF 2.0 binary (GLB) export.

One mesh per part, one node per part (with TRS so viewers can edit transforms),
PBR metallic-roughness materials, and the full :class:`ModelSpec` embedded in
``asset.extras.baymax`` so the file can be reopened for further editing.
"""

from __future__ import annotations

import json
import struct
from typing import Any

import numpy as np

from .mesh import build_primitive
from .scene import ModelSpec, hex_to_rgba

GLB_MAGIC = 0x46546C67  # 'glTF'
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
FLOAT, UINT16, UINT32 = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


def _pad(data: bytes, byte: bytes = b"\x00") -> bytes:
    return data + byte * ((4 - len(data) % 4) % 4)


def export_glb(spec: ModelSpec) -> bytes:
    if not spec.parts:
        raise ValueError("model has no parts")
    binary = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    material_index: dict[str, int] = {}
    materials: list[dict[str, Any]] = []

    def add_view(data: bytes, target: int) -> int:
        nonlocal binary
        offset = len(binary)
        binary += _pad(data)
        buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        return len(buffer_views) - 1

    for name, m in spec.materials.items():
        mat: dict[str, Any] = {
            "name": name,
            "pbrMetallicRoughness": {"baseColorFactor": hex_to_rgba(m.color, m.opacity), "metallicFactor": float(m.metallic),
                                     "roughnessFactor": float(m.roughness)},
            "doubleSided": True,
        }
        if m.opacity < 1.0:
            mat["alphaMode"] = "BLEND"
        if m.emissive:
            mat["emissiveFactor"] = hex_to_rgba(m.emissive)[:3]
        material_index[name] = len(materials)
        materials.append(mat)

    for part in spec.parts:
        mesh = build_primitive(part.primitive, part.params)
        pos = np.ascontiguousarray(mesh.positions, dtype="<f4")
        nrm = np.ascontiguousarray(mesh.normals, dtype="<f4")
        use16 = mesh.vertex_count < 65535
        idx = np.ascontiguousarray(mesh.indices, dtype="<u2" if use16 else "<u4")
        pv = add_view(pos.tobytes(), ARRAY_BUFFER)
        nv = add_view(nrm.tobytes(), ARRAY_BUFFER)
        iv = add_view(idx.tobytes(), ELEMENT_ARRAY_BUFFER)
        lo, hi = pos.min(axis=0), pos.max(axis=0)
        accessors.append({"bufferView": pv, "componentType": FLOAT, "count": int(pos.shape[0]), "type": "VEC3",
                          "min": [float(v) for v in lo], "max": [float(v) for v in hi]})
        pa = len(accessors) - 1
        accessors.append({"bufferView": nv, "componentType": FLOAT, "count": int(nrm.shape[0]), "type": "VEC3"})
        na = len(accessors) - 1
        accessors.append({"bufferView": iv, "componentType": UINT16 if use16 else UINT32, "count": int(idx.shape[0]), "type": "SCALAR"})
        ia = len(accessors) - 1
        prim: dict[str, Any] = {"attributes": {"POSITION": pa, "NORMAL": na}, "indices": ia, "mode": 4}
        if part.material in material_index:
            prim["material"] = material_index[part.material]
        meshes.append({"name": part.name, "primitives": [prim]})
        tr = part.transform
        node: dict[str, Any] = {"name": part.name, "mesh": len(meshes) - 1, "extras": {"role": part.role, "primitive": part.primitive}}
        if any(tr.position):
            node["translation"] = [float(v) for v in tr.position]
        if any(tr.rotation):
            node["rotation"] = tr.quaternion()
        if any(v != 1.0 for v in tr.scale):
            node["scale"] = [float(v) for v in tr.scale]
        nodes.append(node)

    root = {"name": spec.title or spec.object_type, "children": list(range(len(nodes))),
            "extras": {"objectType": spec.object_type, "generator": spec.generator, "approximation": spec.approximation}}
    nodes.append(root)
    gltf = {
        "asset": {"version": "2.0", "generator": "BAYMAX creation3d 0.1", "extras": {"baymax": spec.to_dict()}},
        "scene": 0,
        "scenes": [{"name": spec.title, "nodes": [len(nodes) - 1]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(binary)}],
    }
    json_bytes = _pad(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    bin_bytes = bytes(binary)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    out = bytearray()
    out += struct.pack("<III", GLB_MAGIC, 2, total)
    out += struct.pack("<II", len(json_bytes), CHUNK_JSON) + json_bytes
    out += struct.pack("<II", len(bin_bytes), CHUNK_BIN) + bin_bytes
    return bytes(out)
