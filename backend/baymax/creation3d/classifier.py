"""Natural-language 3D request classifier.

Finds the requested object (longest matching alias, earliest on ties) and
extracts colours, counts, size and detail hints. When no generator supports
the object, the result is ``unsupported`` with the unrecognised noun — the
pipeline never silently substitutes a different object.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from .generators.base import Generator
from .registry import GeneratorRegistry
from .scene import COLORS

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "single": 1, "double": 2, "triple": 3}
DETAIL_HIGH = re.compile(r"\b(detailed|high[- ]detail|highly detailed|realistic|intricate|complex|more detail)\b")
DETAIL_LOW = re.compile(r"\b(simple|basic|low[- ]poly|minimal|rough|blockout|block-out)\b")
APPROX = re.compile(r"\b(approximat\w*|rough(ly)?|blockout|block-out|anyway|best guess|something like)\b")
SIZE_WORDS = {"huge": 1.6, "giant": 1.6, "big": 1.3, "large": 1.3, "small": 0.75, "little": 0.75, "tiny": 0.5, "mini": 0.6, "miniature": 0.5}
DIM_WORDS = {"tall": ("height", 1.3), "short": ("height", 0.75), "wide": ("width", 1.3), "narrow": ("width", 0.75),
             "long": ("length", 1.3), "slim": ("width", 0.8), "thin": ("width", 0.8), "chunky": ("width", 1.25)}
_STOP = {"a", "an", "the", "of", "me", "please", "model", "3d", "make", "create", "generate", "build", "design", "render", "with", "and",
         "for", "that", "is", "some", "new", "simple", "detailed", "object", "mesh", "can", "you", "could", "i", "want", "need", "like",
         "would", "to", "in", "my", "it", "this", "on", "glb", "gltf", "approximate", "rough", "realistic", "low", "poly"}


@dataclass
class ColorHint:
    color: str
    role: str | None  # None => primary colour


@dataclass
class Classification:
    status: str  # "supported" | "unsupported"
    request: str
    generator: Generator | None = None
    alias: str = ""
    preset: dict[str, Any] = field(default_factory=dict)
    colors: list[ColorHint] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    size_factor: float = 1.0
    dimension_factors: dict[str, float] = field(default_factory=dict)
    role_size: dict[str, float] = field(default_factory=dict)  # "big wheels" -> {"wheel": 1.3}
    choice_hints: dict[str, str] = field(default_factory=dict)
    detail: int = 2
    approximate_allowed: bool = False
    unknown_object: str = ""
    suggestions: list[str] = field(default_factory=list)

    @property
    def object_type(self) -> str:
        return self.generator.object_type if self.generator else self.unknown_object


def _normalise(text: str) -> str:
    t = text.lower()
    t = re.sub(r"[^\w#\s-]", " ", t)
    return " ".join(t.split())


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ves") and len(word) > 4:
        return word[:-3] + "f"
    if word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


class RequestClassifier:
    def __init__(self, registry: GeneratorRegistry) -> None:
        self.registry = registry
        aliases = sorted(registry.aliases(), key=len, reverse=True)
        self._alias_patterns = [(a, re.compile(rf"\b{re.escape(a)}(?:e?s)?\b")) for a in aliases]
        self._color_names = sorted(COLORS, key=len, reverse=True)

    def classify(self, request: str) -> Classification:
        text = _normalise(request)
        best: tuple[int, int, str] | None = None  # (-len, position, alias)
        for alias, pattern in self._alias_patterns:
            m = pattern.search(text)
            if m:
                key = (-len(alias), m.start(), alias)
                if best is None or (key[0], key[1]) < (best[0], best[1]):
                    best = key
        approx = bool(APPROX.search(text))
        detail = 3 if DETAIL_HIGH.search(text) else (1 if DETAIL_LOW.search(text) else 2)
        if best is None:
            noun = self._unknown_noun(text)
            return Classification("unsupported", request, detail=detail, approximate_allowed=approx, unknown_object=noun,
                                  suggestions=self._suggest(noun))
        alias = best[2]
        gen, preset = self.registry.aliases()[alias]
        cls = Classification("supported", request, gen, alias, dict(preset), detail=detail, approximate_allowed=approx)
        self._colors(text, alias, gen, cls)
        self._counts(text, gen, cls)
        self._sizes(text, alias, gen, cls)
        self._choices(text, gen, cls)
        return cls

    # ----------------------------------------------------------------- hints
    def _colors(self, text: str, alias: str, gen: Generator, cls: Classification) -> None:
        for name in self._color_names:
            for m in re.finditer(rf"\b{re.escape(name)}\b", text):
                after = text[m.end():].split()
                before = text[: m.start()].split()
                role = None
                # "<color> <role>" e.g. "red wheels", "blue roof"
                if after:
                    cand = _singular(after[0])
                    if cand in gen.color_params and not alias.startswith(after[0]):
                        role = cand
                # "<role> in <color>" / "<role> painted <color>" / "<role> is <color>"
                if role is None and len(before) >= 2 and before[-1] in ("in", "painted", "is", "are", "colored", "coloured"):
                    cand = _singular(before[-2])
                    if cand in gen.color_params:
                        role = cand
                if role is not None and gen.color_params.get(role) == gen.primary_color:
                    role = None
                if any(h.color == COLORS[name] and h.role == role for h in cls.colors):
                    continue
                cls.colors.append(ColorHint(COLORS[name], role))
        for m in re.finditer(r"#[0-9a-f]{6}\b", text):
            cls.colors.append(ColorHint(m.group(0), None))
        # Words like "glass" are both a colour and a material; skip a colour that is literally the matched alias.
        cls.colors = [c for c in cls.colors if not (c.role is None and COLORS.get(alias) == c.color)]
        # A colour word that is also a style choice of this generator ("glass skyscraper") is a style, not a colour.
        choices = {ch for p in gen.params.values() if p.kind == "choice" for ch in p.choices}
        cls.colors = [c for c in cls.colors if not (c.role is None and any(COLORS.get(ch) == c.color and re.search(rf"\b{ch}\b", text) for ch in choices))]

    def _counts(self, text: str, gen: Generator, cls: Classification) -> None:
        for m in re.finditer(r"\b(\d{1,3}|" + "|".join(NUMBER_WORDS) + r")[ -]+(\w+)", text):
            raw, noun = m.group(1), _singular(m.group(2))
            n = int(raw) if raw.isdigit() else NUMBER_WORDS[raw]
            noun = {"storey": "floor", "story": "floor", "level": "floor", "rotor": "rotor"}.get(noun, noun)
            if noun in gen.count_params:
                cls.counts[noun] = n
            # "a 3-storey house", "two-floor house"
            elif noun in ("floor", "storey", "story", "level") and "floor" in gen.count_params:
                cls.counts["floor"] = n

    def _sizes(self, text: str, alias: str, gen: Generator, cls: Classification) -> None:
        tokens = text.split()
        words = set(tokens)
        for i, tok in enumerate(tokens):
            if tok in SIZE_WORDS and tok not in alias.split():
                nxt = _singular(tokens[i + 1]) if i + 1 < len(tokens) else ""
                if nxt in gen.size_params and gen.size_params[nxt] and not alias.startswith(tokens[i + 1]):
                    cls.role_size[nxt] = cls.role_size.get(nxt, 1.0) * SIZE_WORDS[tok]
                else:
                    cls.size_factor *= SIZE_WORDS[tok]
        for w, (dim, f) in DIM_WORDS.items():
            if w in words and w not in alias.split():
                cls.dimension_factors[dim] = cls.dimension_factors.get(dim, 1.0) * f

    def _choices(self, text: str, gen: Generator, cls: Classification) -> None:
        words = set(text.split())
        for pname, param in gen.params.items():
            if param.kind != "choice" or pname in cls.preset:
                continue
            for choice in param.choices:
                if choice in words:
                    cls.choice_hints[pname] = choice

    # ------------------------------------------------------------ unsupported
    def _unknown_noun(self, text: str) -> str:
        m = re.search(r"\b(?:of|an?|the)\s+((?:[a-z-]+\s+){0,2}[a-z-]+)", text)
        candidates = (m.group(1) if m else text).split()
        words = [w for w in candidates if w not in _STOP and w not in COLORS and w not in SIZE_WORDS and w not in DIM_WORDS]
        if not words:
            words = [w for w in text.split() if w not in _STOP]
        return " ".join(words[-2:]) if words else "object"

    def _suggest(self, noun: str) -> list[str]:
        if not noun:
            return []
        return difflib.get_close_matches(noun, list(self.registry.aliases()), n=3, cutoff=0.6)
