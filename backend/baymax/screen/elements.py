"""Pure logic for finding UI elements described in natural language.

Coordinates are macOS global points: origin at the top-left of the primary display, y down
(the same space as the accessibility API's AXPosition and Electron's screen API). Pixel density
never enters: OCR boxes are normalised to the captured display and mapped through its bounds in points.

Nothing here guesses a location. An element is "found" only if it was reported by the
accessibility tree or recognised by OCR in a fresh capture, and matched the description well
enough; otherwise the caller gets "ambiguous" (with candidates) or "not found".
"""

from __future__ import annotations

import difflib
import itertools
import re
import uuid
import time
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class Display:
    id: int
    x: float
    y: float
    width: float
    height: float
    scale: float = 1.0

    @property
    def is_primary(self) -> bool:
        return self.x == 0 and self.y == 0

    def contains(self, gx: float, gy: float) -> bool:
        return self.x <= gx < self.x + self.width and self.y <= gy < self.y + self.height


@dataclass
class ScreenElement:
    role: str  # AX role (AXButton, ...) or "text" for OCR
    label: str  # what the user would call it: title / name / description / recognised text
    x: float  # global points, top-left of the frame
    y: float
    width: float
    height: float
    source: str = "ax"  # "ax" | "ocr"
    confidence: float = 1.0  # OCR recognition confidence; 1.0 for accessibility elements
    app: str = ""
    description: str = ""
    captured_at: float = field(default_factory=time.time)
    value: str = ""  # current text of fields/static text (used to verify typed text)
    enabled: bool | None = None  # AXEnabled (None = not reported)
    identifier: str = ""  # AXIdentifier, when the app sets one (stable across label changes)

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    def to_dict(self) -> dict[str, Any]:
        cx, cy = self.center
        return {"role": self.role, "label": self.label, "x": round(self.x, 1), "y": round(self.y, 1), "width": round(self.width, 1),
                "height": round(self.height, 1), "centerX": round(cx, 1), "centerY": round(cy, 1), "source": self.source,
                "confidence": round(self.confidence, 3), "app": self.app, "capturedAt": self.captured_at,
                **({"enabled": self.enabled} if self.enabled is not None else {}),
                **({"identifier": self.identifier} if self.identifier else {})}


TEXT_INPUT_ROLES = ("AXTextArea", "AXTextField", "AXComboBox", "AXSearchField")


# ----------------------------------------------------------------- parsing
def parse_ax_dump(text: str, app: str = "", now: float | None = None) -> list[ScreenElement]:
    """Parse lines `role<TAB>name<TAB>description<TAB>value<TAB>x<TAB>y<TAB>w<TAB>h[<TAB>enabled<TAB>identifier]` from the
    AppleScript/AX dumper (enabled is "1"/"0"/"" — the last two columns are optional)."""
    out: list[ScreenElement] = []
    now = time.time() if now is None else now
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        role, name, desc, value = (p.strip() for p in parts[:4])
        try:
            x, y, w, h = (float(p) for p in parts[4:8])
        except ValueError:
            continue
        if w <= 1 or h <= 1:
            continue  # invisible / collapsed
        label = name or desc or (value if role in ("AXStaticText", "AXLink", "AXMenuItem", "AXCell") else "")
        if (not label or label == "missing value") and role in TEXT_INPUT_ROLES:
            label = "text input"  # an unlabelled field is still a field (e.g. a chat prompt box with no placeholder)
        if not label or label == "missing value":
            continue
        en = parts[8].strip() if len(parts) > 8 else ""
        ident = parts[9].strip() if len(parts) > 9 else ""
        out.append(ScreenElement(role, label[:200], x, y, w, h, "ax", 1.0, app, desc[:200], now,
                                 value="" if value == "missing value" else value[:2000],
                                 enabled=True if en == "1" else False if en == "0" else None,
                                 identifier="" if ident == "missing value" else ident[:120]))
    return out


def vision_box_to_global(box: tuple[float, float, float, float], display: Display) -> tuple[float, float, float, float]:
    """Vision boundingBox (x, y, w, h normalised, origin bottom-left of the captured display image)
    -> (x, y, w, h) in global points, origin top-left. Independent of the backing scale factor."""
    bx, by, bw, bh = box
    return (display.x + bx * display.width, display.y + (1.0 - by - bh) * display.height, bw * display.width, bh * display.height)


# ----------------------------------------------------------------- matching
_ROLE_HINTS: dict[str, tuple[str, ...]] = {
    "button": ("AXButton", "AXPopUpButton", "AXMenuButton", "AXDisclosureTriangle"),
    "field": ("AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"),
    "box": ("AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXCheckBox"),
    "checkbox": ("AXCheckBox",),
    "tab": ("AXTab", "AXRadioButton", "AXTabGroup"),
    "menu": ("AXMenuItem", "AXMenuBarItem", "AXMenuButton", "AXPopUpButton"),
    "link": ("AXLink",),
    "icon": ("AXImage", "AXButton"),
    "text": ("AXStaticText", "text"),
    "label": ("AXStaticText", "text"),
}
_FILLER = {"the", "a", "an", "this", "that", "on", "in", "screen", "my", "please", "button", "field", "box", "checkbox", "tab",
           "menu", "link", "icon", "text", "label", "item", "called", "named", "labelled", "labeled", "option"}
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "last": -1}


@dataclass
class Query:
    words: list[str]
    role_hint: tuple[str, ...]
    ordinal: int | None

    @property
    def text(self) -> str:
        return " ".join(self.words)


def parse_query(q: str) -> Query:
    toks = re.findall(r"[a-z0-9+#.]+", q.lower())
    hint: tuple[str, ...] = ()
    ordinal = None
    for t in toks:
        if t in _ROLE_HINTS and not hint:
            hint = _ROLE_HINTS[t]
        if t in _ORDINALS and ordinal is None:
            ordinal = _ORDINALS[t]
    words = [t for t in toks if t not in _FILLER and t not in _ORDINALS]
    return Query(words, hint, ordinal)


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+#.]+", s.lower()))


def score(q: Query, el: ScreenElement) -> float:
    label = _norm(el.label)
    target = q.text
    if not target or not label:
        return 0.0
    if label == target:
        s = 1.0
    elif label.startswith(target + " ") or label.endswith(" " + target) or f" {target} " in f" {label} ":
        s = 0.9 - min(0.2, 0.01 * (len(label) - len(target)))  # contains the whole phrase as words
    elif set(q.words) <= set(label.split()):
        s = 0.8
    else:
        s = 0.85 * difflib.SequenceMatcher(None, target, label).ratio()
        overlap = len(set(q.words) & set(label.split())) / len(set(q.words))
        if overlap >= 0.5:  # e.g. "ChatGPT prompt box" vs the field labelled "Message ChatGPT"
            s = max(s, 0.55 + 0.2 * overlap)
    if q.role_hint:
        s += 0.08 if el.role in q.role_hint else -0.12
    if el.source == "ocr":
        s *= 0.9 * (0.5 + 0.5 * max(0.0, min(1.0, el.confidence)))
    return max(0.0, min(1.0, s))


def rank(query: str | Query, elements: Iterable[ScreenElement]) -> list[tuple[float, ScreenElement]]:
    q = parse_query(query) if isinstance(query, str) else query
    scored = [(score(q, e), e) for e in elements]
    return sorted((p for p in scored if p[0] > 0), key=lambda p: (-p[0], p[1].y, p[1].x))


@dataclass
class Resolution:
    status: str  # "found" | "ambiguous" | "not_found"
    element: ScreenElement | None = None
    score: float = 0.0
    candidates: list[tuple[float, ScreenElement]] = field(default_factory=list)

    def describe_candidates(self, displays: list[Display] | None = None) -> str:
        parts = []
        for i, (_, e) in enumerate(self.candidates[:4], 1):
            parts.append(f"{i}) “{e.label}” ({friendly_role(e.role)}, {region(e, displays)})")
        return "; ".join(parts)


MATCH_THRESHOLD = 0.62
AMBIGUITY_MARGIN = 0.06


def _distinct(a: ScreenElement, b: ScreenElement) -> bool:
    (ax, ay), (bx, by) = a.center, b.center
    return abs(ax - bx) + abs(ay - by) > 12  # the AX tree and OCR often report the same control twice


def resolve(query: str, elements: list[ScreenElement]) -> Resolution:
    q = parse_query(query)
    ranked = [p for p in rank(q, elements) if p[0] >= MATCH_THRESHOLD]
    # Collapse duplicates of the same on-screen control (e.g. a button and its own text label).
    unique: list[tuple[float, ScreenElement]] = []
    for s, e in ranked:
        if all(_distinct(e, u) for _, u in unique):
            unique.append((s, e))
    if not unique:
        return Resolution("not_found", candidates=rank(q, elements)[:4])
    if q.ordinal is not None:
        best = unique[0][0]
        same = sorted([p for p in unique if best - p[0] <= AMBIGUITY_MARGIN], key=lambda p: (round(p[1].y / 8), p[1].x))
        idx = q.ordinal - 1 if q.ordinal > 0 else len(same) - 1
        if 0 <= idx < len(same):
            return Resolution("found", same[idx][1], same[idx][0], same)
        return Resolution("ambiguous", candidates=same)
    top = unique[0]
    rivals = [p for p in unique[1:] if top[0] - p[0] < AMBIGUITY_MARGIN]
    if rivals:
        return Resolution("ambiguous", candidates=[top] + rivals)
    return Resolution("found", top[1], top[0], unique[:4])


def friendly_role(role: str) -> str:
    return {"AXButton": "button", "AXPopUpButton": "pop-up button", "AXMenuButton": "menu button", "AXTextField": "text field",
            "AXSearchField": "search field", "AXCheckBox": "checkbox", "AXRadioButton": "tab/option", "AXTab": "tab",
            "AXMenuItem": "menu item", "AXMenuBarItem": "menu", "AXLink": "link", "AXStaticText": "text", "AXImage": "image",
            "text": "text recognised on screen"}.get(role, role.removeprefix("AX").lower())


def display_for(gx: float, gy: float, displays: list[Display]) -> Display | None:
    for d in displays:
        if d.contains(gx, gy):
            return d
    return None


def region(el: ScreenElement, displays: list[Display] | None = None) -> str:
    cx, cy = el.center
    d = display_for(cx, cy, displays or []) if displays else None
    if d is None:
        return f"at ({cx:.0f}, {cy:.0f})"
    rx, ry = (cx - d.x) / d.width, (cy - d.y) / d.height
    v = "top" if ry < 0.33 else "bottom" if ry > 0.66 else "middle"
    h = "left" if rx < 0.33 else "right" if rx > 0.66 else "center"
    where = "center" if (v, h) == ("middle", "center") else f"{v}-{h}" if v != "middle" else h
    return where + ("" if d.is_primary or len(displays or []) < 2 else f" of display {d.id}")


_POINT_SEQ = itertools.count(1)


def to_gaze(el: ScreenElement, displays: list[Display], label: str | None = None, duration_ms: int = 5000) -> dict[str, Any]:
    """gaze.target payload: x,y on the element's display; vx,vy relative to the primary display; gx,gy global points.
    pointId names this pointing event (a later "clear" can withdraw exactly it); seq orders events so a receiver drops
    one that arrives after a newer one; durationMs is its expiry."""
    gx, gy = el.center
    d = display_for(gx, gy, displays)
    primary = next((p for p in displays if p.is_primary), displays[0] if displays else None)
    if d is None or primary is None:
        raise ValueError("element is not on any connected display (stale coordinates?)")
    return {"kind": "element", "x": round((gx - d.x) / d.width, 4), "y": round((gy - d.y) / d.height, 4),
            "vx": round((gx - primary.x) / primary.width, 4), "vy": round((gy - primary.y) / primary.height, 4),
            "gx": round(gx, 1), "gy": round(gy, 1), "displayId": d.id, "label": (label or el.label)[:120],
            "confidence": round(el.confidence, 3), "durationMs": duration_ms, "pointId": uuid.uuid4().hex[:12],
            "seq": next(_POINT_SEQ)}
