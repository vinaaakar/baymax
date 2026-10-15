"""References from one plan step's arguments to a typed output of an earlier step.

A reference is exactly {"$from": <earlier step index>, "output": <declared output name>} - nothing else is
interpreted: no expressions, no string interpolation, no shell. Two checks:

* when the plan is built (check_plan): the index points to an earlier step, that step's tool declares the output in
  Tool.outputs, the consuming argument exists and its type accepts the output's type; every other argument is
  validated by the tool as usual;
* just before the step runs (resolve): the earlier step's result must be ok AND verified (Phase 1 rule), carry the
  output with the right type, and a "path" must still pass the file-scope check (exists, regular file, home folder,
  not ~/Library or hidden folders). Anything else stops the dependent step with a typed reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..security.user_files import check_user_file
from ..tools.base import ArgumentError, ToolResult

# output type -> argument types that may receive it
_COMPATIBLE = {"path": ("str",), "str": ("str",)}
_PLACEHOLDER = {"str": "x"}


def is_ref(value: Any) -> bool:
    return isinstance(value, dict) and "$from" in value


def _shape_ok(value: dict[str, Any]) -> bool:
    return (set(value) == {"$from", "output"} and isinstance(value["$from"], int) and not isinstance(value["$from"], bool)
            and isinstance(value["output"], str))


def refs_in(args: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in args.items() if is_ref(v)}


def check_plan(steps: list[tuple[str, dict[str, Any]]], tools: Any) -> list[dict[str, Any]]:
    """[(tool name, args)] -> validated args per step (references kept as-is). Raises ArgumentError."""
    out: list[dict[str, Any]] = []
    for i, (name, args) in enumerate(steps):
        tool = tools.get(name)
        if tool is None:
            raise ArgumentError(f"step {i + 1}: unknown tool {name!r}")
        refs = refs_in(args)
        stand_in = dict(args)
        for key, ref in refs.items():
            if not _shape_ok(ref):
                raise ArgumentError(f"step {i + 1}: {key!r} is not a valid reference")
            j = ref["$from"]
            if not 0 <= j < i:
                raise ArgumentError(f"step {i + 1}: {key!r} refers to step {j + 1}, which doesn't run before it")
            producer = tools.get(steps[j][0])
            otype = (getattr(producer, "outputs", {}) or {}).get(ref["output"]) if producer else None
            if otype is None:
                raise ArgumentError(f"step {i + 1}: step {j + 1} ({steps[j][0]}) has no output {ref['output']!r}")
            spec = tool.args.get(key)
            if spec is None or spec.type not in _COMPATIBLE.get(otype, ()):
                raise ArgumentError(f"step {i + 1}: {tool.name} can't take a {otype} as {key!r}")
            stand_in[key] = _PLACEHOLDER[spec.type]
        clean = tool.validate(stand_in)  # every non-reference argument as usual
        clean.update(refs)
        out.append(clean)
    return out


@dataclass
class RefFailure:
    kind: str  # unverified_prerequisite | missing_output | ambiguous_reference | stale_output | permission_denied | invalid_reference
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def result(self) -> ToolResult:
        question = self.message if self.kind == "ambiguous_reference" else ""
        return ToolResult(False, self.message, {"errorKind": self.kind, **self.data}, needs_clarification=question)


def resolve(args: dict[str, Any], results: list[ToolResult], titles: list[str], tools: Any, plan_tools: list[str]) -> dict[str, Any] | RefFailure:
    """Substitute verified earlier outputs into args, or explain why the step must not run."""
    resolved = dict(args)
    for key, ref in refs_in(args).items():
        if not _shape_ok(ref) or not 0 <= ref["$from"] < len(results):
            return RefFailure("invalid_reference", f"I stopped: the input “{key}” refers to a step that hasn't produced a result.")
        j, name = ref["$from"], ref["output"]
        r, title = results[j], titles[j]
        if not r.ok:
            return RefFailure("unverified_prerequisite", f"I stopped: “{title}” didn't succeed, so its result can't be used.")
        if not r.verified:
            return RefFailure("unverified_prerequisite", f"I stopped: “{title}” ran but couldn't be verified, so I won't use its result.",
                              {"prerequisiteOutcome": r.outcome})
        value = r.data.get(name)
        if value is None:
            paths = r.data.get("paths") or []
            if name == "path" and len(paths) > 1:
                listing = "\n".join(f"{n}. {p}" for n, p in enumerate(paths[:8], 1))
                return RefFailure("ambiguous_reference", f"Several files match, so I didn't pick one:\n{listing}\nWhich one should I use? "
                                                         "Say “number 2”, for example.", {"candidates": paths[:8], "fromStep": j})
            return RefFailure("missing_output", f"I stopped: “{title}” didn't produce the {name} the next step needs.")
        otype = (getattr(tools.get(plan_tools[j]), "outputs", {}) or {}).get(name)
        if not isinstance(value, str):
            return RefFailure("invalid_reference", f"I stopped: “{title}” produced an unexpected {name}.")
        if otype == "path":
            path, kind, reason = check_user_file(value)
            if path is None:
                return RefFailure("stale_output" if kind in ("stale", "invalid") else "permission_denied",
                                  f"I stopped before using it: {reason}.", {"path": value})
            value = str(path)
        resolved[key] = value
    return resolved


def literal_path(path: str) -> str | None:
    """A user-chosen candidate, re-checked like any reference."""
    p, _, _ = check_user_file(path)
    return str(p) if p is not None else None


__all__ = ["RefFailure", "check_plan", "is_ref", "literal_path", "refs_in", "resolve", "Path"]
