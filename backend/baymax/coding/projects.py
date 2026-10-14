"""Finding the user's projects, code definitions, compiler errors and document text — all local, read-only."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .project import IGNORE_DIRS

DEFAULT_SEARCH_ROOTS = ("~/Developer", "~/Projects", "~/projects", "~/code", "~/src", "~/Documents/GitHub", "~/Documents", "~")


# ------------------------------------------------------------------ projects
@dataclass
class ProjectMatch:
    path: Path
    score: float


def find_projects(name: str, roots: list[Path], max_depth: int = 2) -> list[ProjectMatch]:
    """Directories named like `name` under the search roots (exact > prefix > contains; git repos preferred)."""
    want = re.sub(r"[^a-z0-9]", "", name.lower())
    if not want:
        return []
    seen: set[Path] = set()
    out: list[ProjectMatch] = []

    def visit(d: Path, depth: int) -> None:
        try:
            children = [c for c in d.iterdir() if c.is_dir() and not c.name.startswith(".") and c.name not in IGNORE_DIRS]
        except OSError:
            return
        for c in children:
            key = re.sub(r"[^a-z0-9]", "", c.name.lower())
            score = 1.0 if key == want else 0.8 if key.startswith(want) else 0.6 if want in key else 0.0
            if score:
                real = c.resolve()
                if real not in seen:
                    seen.add(real)
                    if (c / ".git").exists():
                        score += 0.1
                    out.append(ProjectMatch(real, score - 0.02 * depth))
            if depth < max_depth:
                visit(c, depth + 1)

    for r in roots:
        r = r.expanduser()
        if r.is_dir():
            visit(r, 0)
    return sorted(out, key=lambda m: (-m.score, len(str(m.path))))


# ------------------------------------------------------------------ code search
GENERATED_DIRS = {"dist-electron", "out", ".build", "bin", "obj", "release", "Release", "Debug"}
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".kt", ".swift", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb"}
_DEF = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:public|private|protected|internal|static|final|abstract|async|override|open|fileprivate|\s)*"
    r"(?:def|function|func|fn|class|struct|enum|interface|protocol|extension|type|const|let|var)\s+([A-Za-z_][\w]*)"
    r"|^\s*(?:public|private|protected|static|final|synchronized|\s)+[\w<>\[\], ]+\s+([a-z_]\w*)\s*\([^;]*$"  # Java/C# methods
)


def _words(identifier: str) -> list[str]:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier).replace("_", " ").lower().split()
    return parts


_STEM = re.compile(r"(ing|ers|er|ed|es|s)$")


def _stem(w: str) -> str:
    return _STEM.sub("", w) if len(w) > 4 else w


@dataclass
class CodeHit:
    path: Path
    line: int
    name: str
    signature: str
    score: float
    context: list[str] = field(default_factory=list)


def search_definitions(root: Path, query: str, limit: int = 8, max_files: int = 4000) -> list[CodeHit]:
    stop = {"the", "a", "an", "for", "of", "to", "that", "which", "is", "responsible", "function", "method", "class", "code", "handles",
            "handling", "does", "where", "find", "me", "show", "in", "my", "this", "repo", "repository", "project"}
    raw_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", query)
    identifiers = {tok.lower() for tok in raw_tokens if re.search(r"[a-z][A-Z]|_", tok)}  # camelCase / snake_case only
    q = [_stem(w) for tok in raw_tokens for w in _words(tok) if w not in stop]
    if not q:
        return []
    hits: list[CodeHit] = []
    n = 0
    for path in sorted(root.rglob("*")):
        if any(part in IGNORE_DIRS or part in GENERATED_DIRS or part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue
        if path.suffix not in CODE_EXT or not path.is_file():
            continue
        n += 1
        if n > max_files:
            break
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        path_words = {_stem(w) for w in _words(path.stem)} | {_stem(w) for p in path.relative_to(root).parts[:-1] for w in _words(p)}
        for i, line in enumerate(lines):
            m = _DEF.match(line)
            if not m:
                continue
            name = m.group(1) or m.group(2)
            if not name:
                continue
            name_words = {_stem(w) for w in _words(name)}
            # comments/docstring right above or below the definition
            near = " ".join(lines[max(0, i - 4):i] + lines[i + 1:i + 4]).lower()
            near_words = {_stem(w) for w in re.findall(r"[a-z0-9]+", near)}
            s = 0.0
            for w in q:
                s += 1.0 if w in name_words else 0.45 if w in path_words else 0.25 if w in near_words else 0.0
            s /= len(q)
            if name.lower() in identifiers:  # the user named the symbol exactly (e.g. startCursorTracking)
                s += 2.0
            if s >= 0.5:
                hits.append(CodeHit(path, i + 1, name, line.strip()[:160], round(s, 3), lines[max(0, i - 2):i + 6]))
    hits.sort(key=lambda h: (-h.score, len(str(h.path)), h.line))
    return hits[:limit]


# ------------------------------------------------------------------ compiler / runtime errors
@dataclass
class ErrorLocation:
    file: str
    line: int | None
    column: int | None
    message: str
    tool: str


_ERR_PATTERNS = [
    ("javac", re.compile(r"^(?P<file>[^\s:][^:]*\.java):(?P<line>\d+): error: (?P<msg>.+)$")),
    ("tsc", re.compile(r"^(?P<file>[^\s(]+\.[tj]sx?)\((?P<line>\d+),(?P<col>\d+)\): error (?P<msg>TS\d+: .+)$")),
    ("tsc", re.compile(r"^(?P<file>[^\s:]+\.[tj]sx?):(?P<line>\d+):(?P<col>\d+) - error (?P<msg>TS\d+: .+)$")),
    ("clang/gcc/swift", re.compile(r"^(?P<file>[^\s:][^:]*\.(?:c|cc|cpp|h|hpp|m|mm|swift|rs|go)):(?P<line>\d+):(?P<col>\d+): (?:fatal )?error: (?P<msg>.+)$")),
    ("rustc", re.compile(r"^\s*--> (?P<file>[^:]+\.rs):(?P<line>\d+):(?P<col>\d+)$")),
    ("python", re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)')),
    ("maven", re.compile(r"^\[ERROR\] (?P<file>/[^:]+\.java):\[(?P<line>\d+),(?P<col>\d+)\] (?P<msg>.+)$")),
]


def parse_errors(text: str) -> list[ErrorLocation]:
    out: list[ErrorLocation] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for tool, pat in _ERR_PATTERNS:
            m = pat.match(line.rstrip())
            if not m:
                continue
            g = m.groupdict()
            msg = g.get("msg") or ""
            if tool == "python":  # the exception is the last non-indented line of the traceback
                tail = [ln for ln in lines[i + 1:] if ln and not ln.startswith(" ")]
                msg = tail[0] if tail else ""
            if tool == "rustc" and i > 0:
                msg = lines[i - 1].strip()
            out.append(ErrorLocation(g["file"], int(g["line"]) if g.get("line") else None,
                                     int(g["col"]) if g.get("col") else None, msg.strip()[:300], tool))
            break
    if any(e.tool == "python" for e in out):  # keep only the innermost frame of a traceback
        py = [e for e in out if e.tool == "python"]
        out = [e for e in out if e.tool != "python"] + py[-1:]
    return out


def looks_like_error(text: str) -> bool:
    return bool(parse_errors(text)) or bool(re.search(r"\b(error|exception|traceback|failed|cannot find symbol|undefined)\b", text, re.I))


# ------------------------------------------------------------------ documents
TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".log", ".yaml", ".yml", ".toml", ".xml"} | CODE_EXT
TEXTUTIL_EXT = {".rtf", ".rtfd", ".doc", ".docx", ".odt", ".html", ".htm", ".webarchive", ".pages"}
