"""Tool layer primitives.

The language model never executes anything directly. It may propose a tool
name and arguments; the arguments are validated against the tool's schema,
the risk is classified by the tool (not by the model), and the policy decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..security.policy import Risk

if TYPE_CHECKING:  # pragma: no cover
    from ..services import Services


class ToolError(Exception):
    """A tool failed in an expected, explainable way."""


class ToolUnavailable(ToolError):
    """A required permission, runtime or platform feature is missing."""


class ArgumentError(ToolError):
    pass


@dataclass(frozen=True)
class Arg:
    type: str  # str | int | float | bool | list | dict
    required: bool = True
    description: str = ""
    default: Any = None
    max_len: int = 4000
    choices: tuple[str, ...] = ()


_PY = {"str": str, "int": int, "float": (int, float), "bool": bool, "list": list, "dict": dict}


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    verified: bool = False
    verification: str = ""
    untrusted: bool = False  # contains external content (web pages, documents)
    needs_clarification: str = ""  # set when the tool could not proceed without user input
    retryable: bool = False  # a transient failure (still loading, busy) that the task runner may retry, bounded

    @property
    def outcome(self) -> str:
        """One of the result contract's states (never 'completed' without verification)."""
        kind = str(self.data.get("errorKind", ""))
        if self.ok:
            return "completed_verified" if self.verified else "dispatched_unverified"
        if self.needs_clarification and kind in ("", "invalid_arguments", "ambiguous"):
            return "target_ambiguous" if "Which" in self.needs_clarification or kind == "ambiguous" else "rejected_validation"
        return {"permission_denied": "permission_denied", "not_installed": "app_not_installed", "not_running": "app_not_running",
                "not_found": "target_not_found", "invalid_arguments": "rejected_validation",
                "login_required": "blocked_login"}.get(kind, "failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "summary": self.summary, "data": self.data, "artifacts": self.artifacts,
            "verified": self.verified, "verification": self.verification, "untrusted": self.untrusted,
            "needsClarification": self.needs_clarification, "retryable": self.retryable, "outcome": self.outcome,
        }


ProgressFn = Callable[[float | None, str], None]


@dataclass
class ToolContext:
    services: "Services"
    task_id: str
    source: str
    progress: ProgressFn
    is_cancelled: Callable[[], bool]

    def checkpoint(self) -> None:
        """Raise if Emergency Stop is engaged or the task was cancelled."""
        self.services.estop.ensure_clear()
        if self.is_cancelled():
            import asyncio
            raise asyncio.CancelledError()


class Tool:
    name: str = ""
    description: str = ""
    risk: Risk = Risk.LOW
    categories: frozenset[str] = frozenset()
    args: dict[str, Arg] = {}
    # Whether the planner may offer this tool to the language model.
    model_callable: bool = True
    # Typed metadata (see tools/catalog.py; filled in at registration unless the class sets it).
    intents: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    timeout_s: float = 0.0
    verification: str = ""
    recovery: str = ""

    def validate(self, args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict):
            raise ArgumentError("arguments must be an object")
        clean: dict[str, Any] = {}
        for key in args:
            if key not in self.args:
                raise ArgumentError(f"{self.name}: unknown argument {key!r}")
        for key, spec in self.args.items():
            if key not in args or args[key] is None:
                if spec.required:
                    raise ArgumentError(f"{self.name}: missing argument {key!r}")
                if spec.default is not None:
                    clean[key] = spec.default
                continue
            value = args[key]
            expected = _PY[spec.type]
            if isinstance(value, bool) and spec.type in ("int", "float"):
                raise ArgumentError(f"{self.name}: {key!r} must be {spec.type}")
            if not isinstance(value, expected):
                raise ArgumentError(f"{self.name}: {key!r} must be {spec.type}")
            if spec.type == "str" and len(value) > spec.max_len:
                raise ArgumentError(f"{self.name}: {key!r} too long")
            if spec.choices and value not in spec.choices:
                raise ArgumentError(f"{self.name}: {key!r} must be one of {', '.join(spec.choices)}")
            clean[key] = value
        return clean

    def classify(self, args: dict[str, Any]) -> tuple[Risk, frozenset[str]]:
        """Risk for these specific arguments. Override for argument-dependent risk."""
        return self.risk, self.categories

    def describe(self, args: dict[str, Any]) -> str:
        """Human-readable one-line summary used in approval prompts."""
        shown = ", ".join(f"{k}={str(v)[:80]!r}" for k, v in args.items())
        return f"{self.name}({shown})"

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name, "description": self.description, "risk": self.risk.value,
            "args": {k: {"type": a.type, "required": a.required, "description": a.description,
                         **({"choices": list(a.choices)} if a.choices else {})} for k, a in self.args.items()},
        }

    def catalog_entry(self) -> dict[str, Any]:
        """Full typed declaration (API / diagnostics). The model only ever sees schema()."""
        return {**self.schema(), "intents": list(self.intents), "permissions": list(self.permissions), "timeoutS": self.timeout_s,
                "verification": self.verification, "recovery": self.recovery,
                "confirmation": "required" if self.risk is Risk.HIGH else "by category/policy",
                "categories": sorted(self.categories)}


AsyncTool = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]
