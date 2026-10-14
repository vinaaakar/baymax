"""Documents, data files, charts and diagrams written to the workspace.

Every writer returns the path and re-reads the file to confirm it was written.
SVG charts and diagrams are generated directly (no external renderer).
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
from pathlib import Path
from typing import Any, Sequence

PALETTE = ["#FF334F", "#7cc4f0", "#f9d71c", "#9ccc3c", "#c792ea", "#ff9e64"]


def slugify(text: str, limit: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "document"


def _write_checked(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if path.read_text(encoding="utf-8") != content:
        raise OSError(f"verification failed writing {path}")
    return path


def write_markdown(path: Path, title: str, body: str, meta: dict[str, Any] | None = None) -> Path:
    front = ""
    if meta:
        front = "---\n" + "".join(f"{k}: {json.dumps(v) if not isinstance(v, str) else v}\n" for k, v in meta.items()) + "---\n\n"
    content = front + (body if body.lstrip().startswith("#") else f"# {title}\n\n{body}")
    return _write_checked(path, content.rstrip() + "\n")


def write_csv(path: Path, series: dict[str, Sequence[float]]) -> Path:
    keys = list(series)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(keys)
    for row in zip(*(series[k] for k in keys)):
        w.writerow(row)
    return _write_checked(path, buf.getvalue())


def write_json(path: Path, data: Any) -> Path:
    return _write_checked(path, json.dumps(data, indent=1, default=str))


def line_chart_svg(x: Sequence[float], ys: dict[str, Sequence[float]], *, title: str = "", x_label: str = "", y_label: str = "",
                   width: int = 760, height: int = 360, note: str = "") -> str:
    pad_l, pad_r, pad_t, pad_b = 64, 20, 40, 48
    xs = list(map(float, x))
    if not xs:
        raise ValueError("empty series")
    all_y = [float(v) for s in ys.values() for v in s]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(all_y), max(all_y)
    if x1 == x0:
        x1 = x0 + 1
    if y1 == y0:
        y1 = y0 + 1
    span = y1 - y0
    y0 -= span * 0.05
    y1 += span * 0.05

    def px(v: float) -> float:
        return pad_l + (v - x0) / (x1 - x0) * (width - pad_l - pad_r)

    def py(v: float) -> float:
        return height - pad_b - (v - y0) / (y1 - y0) * (height - pad_t - pad_b)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" font-family="system-ui, sans-serif">',
           f'<rect width="{width}" height="{height}" fill="#120408"/>']
    for i in range(6):
        gy = y0 + (y1 - y0) * i / 5
        out.append(f'<line x1="{pad_l}" y1="{py(gy):.1f}" x2="{width - pad_r}" y2="{py(gy):.1f}" stroke="#3a1a20" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 8}" y="{py(gy) + 4:.1f}" fill="#c9a9ae" font-size="11" text-anchor="end">{gy:.3g}</text>')
    for i in range(6):
        gx = x0 + (x1 - x0) * i / 5
        out.append(f'<text x="{px(gx):.1f}" y="{height - pad_b + 18}" fill="#c9a9ae" font-size="11" text-anchor="middle">{gx:.3g}</text>')
    for idx, (name, series) in enumerate(ys.items()):
        pts = list(zip(xs, map(float, series)))
        stride = max(1, len(pts) // 800)
        d = " ".join(f"{'M' if j == 0 else 'L'}{px(a):.1f},{py(b):.1f}" for j, (a, b) in enumerate(pts[::stride]))
        color = PALETTE[idx % len(PALETTE)]
        out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>')
        out.append(f'<rect x="{pad_l + 10 + idx * 130}" y="{pad_t - 22}" width="12" height="3" fill="{color}"/>')
        out.append(f'<text x="{pad_l + 28 + idx * 130}" y="{pad_t - 17}" fill="#f5f5f2" font-size="12">{html.escape(name)}</text>')
    out.append(f'<text x="{width / 2}" y="18" fill="#f5f5f2" font-size="14" text-anchor="middle">{html.escape(title)}</text>')
    out.append(f'<text x="{width / 2}" y="{height - 8}" fill="#c9a9ae" font-size="12" text-anchor="middle">{html.escape(x_label)}</text>')
    out.append(f'<text x="14" y="{height / 2}" fill="#c9a9ae" font-size="12" text-anchor="middle" transform="rotate(-90 14 {height / 2})">{html.escape(y_label)}</text>')
    if note:
        out.append(f'<text x="{width - pad_r}" y="{height - 8}" fill="#FF334F" font-size="11" text-anchor="end">{html.escape(note)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def parse_steps(text: str) -> list[str]:
    """Extract diagram steps from '->', numbered lists, bullets or 'then'."""
    t = text.strip()
    t = re.sub(r"(?i)^(please\s+)?(draw|create|make|generate)\s+(a|an|the)?\s*(flow ?chart|diagram|flow)\s*(of|for|showing|:)?\s*", "", t)
    if "->" in t or "→" in t:
        parts = re.split(r"\s*(?:->|→)\s*", t)
    elif re.search(r"(?m)^\s*(\d+[.)]|[-*•])\s+", t):
        parts = re.findall(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+(.+)$", t)
    else:
        parts = re.split(r"\s*(?:,\s*then\s+|\bthen\b|;|,)\s*", t)
    return [p.strip(" .") for p in parts if p and p.strip(" .")][:24]


def flow_diagram_svg(steps: Sequence[str], title: str = "") -> str:
    if not steps:
        raise ValueError("diagram needs at least one step")
    box_w, box_h, gap = 300, 54, 34
    width = box_w + 120
    height = 60 + len(steps) * (box_h + gap)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" font-family="system-ui, sans-serif">',
           f'<rect width="{width}" height="{height}" fill="#120408"/>',
           '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
           '<path d="M0,0 L10,5 L0,10 z" fill="#FF334F"/></marker></defs>',
           f'<text x="{width / 2}" y="28" fill="#f5f5f2" font-size="15" text-anchor="middle">{html.escape(title)}</text>']
    x = (width - box_w) / 2
    for i, step in enumerate(steps):
        y = 50 + i * (box_h + gap)
        out.append(f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="12" fill="#2a0a12" stroke="#B5122A" stroke-width="2"/>')
        label = step if len(step) <= 40 else step[:38] + "…"
        out.append(f'<text x="{width / 2}" y="{y + box_h / 2 + 5}" fill="#f5f5f2" font-size="13" text-anchor="middle">{html.escape(label)}</text>')
        if i < len(steps) - 1:
            out.append(f'<line x1="{width / 2}" y1="{y + box_h}" x2="{width / 2}" y2="{y + box_h + gap - 4}" stroke="#FF334F" stroke-width="2" marker-end="url(#arrow)"/>')
    out.append("</svg>")
    return "\n".join(out)


def mermaid_flow(steps: Sequence[str]) -> str:
    lines = ["flowchart TD"]
    for i, s in enumerate(steps):
        lines.append(f'    S{i}["{s.replace(chr(34), "")}"]')
    for i in range(len(steps) - 1):
        lines.append(f"    S{i} --> S{i + 1}")
    return "\n".join(lines) + "\n"


def write_svg(path: Path, svg: str) -> Path:
    if not svg.lstrip().startswith("<svg"):
        raise ValueError("not an SVG document")
    return _write_checked(path, svg)
