"""Repository inspection: languages, build systems and test commands."""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".gradle", ".idea", ".next",
               ".pytest_cache", ".mypy_cache", "DerivedData", "Pods", ".tox", "coverage", ".turbo"}
LANG = {".py": "Python", ".java": "Java", ".kt": "Kotlin", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
        ".swift": "Swift", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".c": "C", ".h": "C", ".cpp": "C++", ".cs": "C#", ".php": "PHP",
        ".scala": "Scala", ".sh": "Shell", ".html": "HTML", ".css": "CSS", ".sql": "SQL"}


@dataclass
class ProjectInfo:
    root: Path
    languages: dict[str, int] = field(default_factory=dict)
    build_systems: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    test_commands: list[list[str]] = field(default_factory=list)
    key_files: list[str] = field(default_factory=list)
    top_level: list[str] = field(default_factory=list)
    file_count: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"root": str(self.root), "languages": self.languages, "buildSystems": self.build_systems, "frameworks": self.frameworks,
                "testCommands": [" ".join(c) for c in self.test_commands], "keyFiles": self.key_files, "topLevel": self.top_level,
                "fileCount": self.file_count, "truncated": self.truncated}

    def summary(self) -> str:
        langs = ", ".join(f"{k} ({v})" for k, v in sorted(self.languages.items(), key=lambda x: -x[1])[:5]) or "none detected"
        return (f"{self.file_count} files{'+' if self.truncated else ''}; languages: {langs}; build: {', '.join(self.build_systems) or 'unknown'}"
                f"{'; frameworks: ' + ', '.join(self.frameworks) if self.frameworks else ''}; "
                f"tests: {', '.join(' '.join(c) for c in self.test_commands) or 'no test command detected'}")


def walk_files(root: Path, limit: int = 5000) -> tuple[list[Path], bool]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".") or d == ".github")
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if p.is_symlink():
                continue
            out.append(p)
            if len(out) >= limit:
                return out, True
    return out, False


def inspect_project(root: Path) -> ProjectInfo:
    info = ProjectInfo(root=root)
    files, info.truncated = walk_files(root)
    info.file_count = len(files)
    counts = Counter(LANG[p.suffix] for p in files if p.suffix in LANG)
    info.languages = dict(counts)
    info.top_level = sorted(p.name + ("/" if p.is_dir() else "") for p in root.iterdir() if p.name not in IGNORE_DIRS)[:60]
    names = {p.name for p in root.iterdir()}
    for k in ("README.md", "README", "package.json", "pyproject.toml", "setup.py", "requirements.txt", "pom.xml", "build.gradle",
              "build.gradle.kts", "go.mod", "Cargo.toml", "Package.swift", "Makefile", "tsconfig.json", "pytest.ini", "setup.cfg"):
        if k in names:
            info.key_files.append(k)
    if names & {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pytest.ini"} or counts.get("Python"):
        info.build_systems.append("python")
        if (root / "tests").exists() or (root / "test").exists() or "pytest.ini" in names or any(p.name.startswith("test_") for p in files):
            info.test_commands.append(["python3", "-m", "pytest", "-q"])
    if "package.json" in names:
        info.build_systems.append("npm")
        try:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        for fw in ("react", "vue", "svelte", "next", "express", "electron", "vite", "three"):
            if fw in deps:
                info.frameworks.append(fw)
        test = (pkg.get("scripts") or {}).get("test", "")
        if test and "no test specified" not in test:
            info.test_commands.append(["npm", "test"])
    if "pom.xml" in names:
        info.build_systems.append("maven")
        try:
            if "spring-boot" in (root / "pom.xml").read_text(encoding="utf-8", errors="ignore"):
                info.frameworks.append("spring-boot")
        except OSError:
            pass
        info.test_commands.append(["./mvnw", "-q", "test"] if (root / "mvnw").exists() else ["mvn", "-q", "test"])
    if names & {"build.gradle", "build.gradle.kts"}:
        info.build_systems.append("gradle")
        try:
            txt = "".join((root / n).read_text(encoding="utf-8", errors="ignore") for n in ("build.gradle", "build.gradle.kts") if (root / n).exists())
            if "org.springframework.boot" in txt:
                info.frameworks.append("spring-boot")
        except OSError:
            pass
        info.test_commands.append(["./gradlew", "test"] if (root / "gradlew").exists() else ["gradle", "test"])
    if "go.mod" in names:
        info.build_systems.append("go")
        info.test_commands.append(["go", "test", "./..."])
    if "Cargo.toml" in names:
        info.build_systems.append("cargo")
        info.test_commands.append(["cargo", "test"])
    if "Package.swift" in names:
        info.build_systems.append("swiftpm")
        info.test_commands.append(["swift", "test"])
    return info
