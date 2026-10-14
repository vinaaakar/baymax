"""Persistence of 3D model artifacts with version history (undo) and validated export.

Layout::

    <artifacts>/models/<id>/
        versions/v0001.json ...   model specs (history)
        model.glb                 current version (always validated after writing)
        exports/<name>-v0003.glb  explicit exports (validated by re-reading from disk)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..artifacts import Artifact, ArtifactRegistry
from .gltf import export_glb
from .planner import Planner
from .scene import ModelSpec
from .validate import ValidationReport, load_spec, validate_glb


class ExportFailed(Exception):
    def __init__(self, report: ValidationReport) -> None:
        super().__init__("; ".join(report.errors) or "export validation failed")
        self.report = report


@dataclass
class SavedModel:
    artifact: Artifact
    spec: ModelSpec
    report: ValidationReport
    path: Path

    def to_dict(self) -> dict[str, Any]:
        return {"artifact": self.artifact.to_dict(), "validation": self.report.to_dict(), "objectType": self.spec.object_type,
                "title": self.spec.title, "version": self.spec.version, "params": self.spec.params, "detail": self.spec.detail,
                "approximation": self.spec.approximation, "notes": self.spec.notes,
                "parts": [{"name": p.name, "role": p.role, "material": p.material} for p in self.spec.parts],
                "materials": {k: m.to_dict() for k, m in self.spec.materials.items()}}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "model"


class ModelStore:
    def __init__(self, root: Path, registry: ArtifactRegistry, planner: Planner) -> None:
        self.root = root / "models"
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self.planner = planner

    def _dir(self, artifact_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{12}", artifact_id):
            raise ValueError("invalid artifact id")
        return self.root / artifact_id

    def _write_validated(self, spec: ModelSpec, path: Path) -> ValidationReport:
        data = export_glb(spec)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        # validate what is actually on disk, then atomically move into place
        report = validate_glb(tmp.read_bytes(), self.planner.generator_for(spec).required(spec.params), spec.object_type)
        if not report.ok:
            tmp.unlink(missing_ok=True)
            raise ExportFailed(report)
        tmp.replace(path)
        return report

    def create(self, spec: ModelSpec, task_id: str | None = None) -> SavedModel:
        art = self.registry.add("model3d", spec.title, self.root, task_id=task_id, meta={"objectType": spec.object_type})
        d = self._dir(art.id)
        (d / "versions").mkdir(parents=True, exist_ok=True)
        spec.version = 1
        return self._save_version(art.id, spec, task_id)

    def _save_version(self, artifact_id: str, spec: ModelSpec, task_id: str | None) -> SavedModel:
        d = self._dir(artifact_id)
        (d / "versions").mkdir(parents=True, exist_ok=True)
        path = d / "model.glb"
        report = self._write_validated(spec, path)
        (d / "versions" / f"v{spec.version:04d}.json").write_text(json.dumps(spec.to_dict()), encoding="utf-8")
        art = self.registry.update(artifact_id, path=path, validated=True, title=spec.title,
                                   meta={"objectType": spec.object_type, "version": spec.version, "stats": report.stats,
                                         "approximation": spec.approximation, "generator": spec.generator})
        assert art is not None
        return SavedModel(art, spec, report, path)

    def load(self, artifact_id: str) -> SavedModel:
        art = self.registry.get(artifact_id)
        if art is None or art.kind != "model3d":
            raise FileNotFoundError(artifact_id)
        path = self._dir(artifact_id) / "model.glb"
        data = path.read_bytes()
        spec = load_spec(data)  # reopen from the saved file itself
        gen = self.planner.generator_for(spec)
        report = validate_glb(data, gen.required(spec.params), spec.object_type)
        return SavedModel(art, spec, report, path)

    def save_modified(self, artifact_id: str, spec: ModelSpec, task_id: str | None = None) -> SavedModel:
        versions = self.versions(artifact_id)
        spec.version = (versions[-1] if versions else 0) + 1
        return self._save_version(artifact_id, spec, task_id)

    def versions(self, artifact_id: str) -> list[int]:
        vdir = self._dir(artifact_id) / "versions"
        if not vdir.exists():
            return []
        return sorted(int(p.stem[1:]) for p in vdir.glob("v*.json"))

    def undo(self, artifact_id: str) -> SavedModel:
        versions = self.versions(artifact_id)
        if len(versions) < 2:
            raise ValueError("nothing to undo")
        vdir = self._dir(artifact_id) / "versions"
        latest = vdir / f"v{versions[-1]:04d}.json"
        previous = ModelSpec.from_dict(json.loads((vdir / f"v{versions[-2]:04d}.json").read_text(encoding="utf-8")))
        path = self._dir(artifact_id) / "model.glb"
        report = self._write_validated(previous, path)
        latest.unlink()
        art = self.registry.update(artifact_id, path=path, validated=True, title=previous.title,
                                   meta={"objectType": previous.object_type, "version": previous.version, "stats": report.stats,
                                         "approximation": previous.approximation, "generator": previous.generator})
        assert art is not None
        return SavedModel(art, previous, report, path)

    def export(self, artifact_id: str, fmt: str = "glb") -> tuple[Path, ValidationReport]:
        if fmt != "glb":
            raise ValueError("only GLB export is implemented (glTF JSON+bin is not)")
        saved = self.load(artifact_id)
        out_dir = self._dir(artifact_id) / "exports"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"{_slug(saved.spec.title)}-v{saved.spec.version:04d}.glb"
        report = self._write_validated(saved.spec, path)
        # reopen the exported file and make sure the spec round-trips
        reopened = load_spec(path.read_bytes())
        if reopened.object_type != saved.spec.object_type or len(reopened.parts) != len(saved.spec.parts):
            raise ExportFailed(ValidationReport(False, ["exported file did not reopen to the same model"]))
        return path, report
