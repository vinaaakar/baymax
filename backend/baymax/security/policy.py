"""Risk-based authorization policy.

Every tool call is classified into a :class:`Risk` level. The policy decides
whether it runs immediately, needs explicit approval, or is blocked. The
language model can *propose* tool calls but can never change their risk level
or approve them; approvals come only from a human via an authenticated client.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Risk(str, Enum):
    LOW = "low"          # read-only or easily reversible inside the workspace
    MEDIUM = "medium"    # visible side effects (open app, type text) — runs, audited
    HIGH = "high"        # needs explicit approval
    BLOCKED = "blocked"  # never executed by the assistant


class Decision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


# Categories named in the product requirements that always need approval.
HIGH_IMPACT_CATEGORIES = {
    "send_message", "publish", "delete_files", "purchase", "spend_money",
    "install_software", "system_settings", "privileged_command", "flash_firmware",
    "outside_workspace", "network_upload",
}
BLOCKED_CATEGORIES = {"disable_security", "credential_exfiltration", "unrestricted_shell"}


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    risk: Risk
    reason: str


@dataclass(frozen=True)
class ActionRequest:
    tool: str
    args: dict[str, Any]
    risk: Risk
    categories: frozenset[str] = frozenset()
    source: str = "local"  # "local" (Mac UI) | "device:<id>" | "agent"

    def fingerprint(self) -> str:
        """Stable hash binding an approval to this exact action."""
        blob = json.dumps({"tool": self.tool, "args": self.args}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()


class Policy:
    def __init__(self, *, allow_desktop_control: bool = True, hardware_enabled: bool = False) -> None:
        self.allow_desktop_control = allow_desktop_control
        self.hardware_enabled = hardware_enabled

    def evaluate(self, req: ActionRequest) -> PolicyDecision:
        cats = set(req.categories)
        if req.risk is Risk.BLOCKED or cats & BLOCKED_CATEGORIES:
            return PolicyDecision(Decision.DENY, Risk.BLOCKED, "this action is blocked by the safety policy")
        if req.tool.startswith("hardware.") and not self.hardware_enabled:
            return PolicyDecision(Decision.DENY, Risk.BLOCKED, "hardware control is disabled until devices are configured")
        if req.tool.startswith("desktop.") and not self.allow_desktop_control:
            return PolicyDecision(Decision.DENY, req.risk, "desktop control is disabled in settings")
        risk = req.risk
        if cats & HIGH_IMPACT_CATEGORIES:
            risk = Risk.HIGH
        if risk is Risk.HIGH:
            return PolicyDecision(Decision.REQUIRE_APPROVAL, risk, "high-impact action: " + ", ".join(sorted(cats)) if cats else "high-impact action")
        return PolicyDecision(Decision.ALLOW, risk, "allowed")

    @staticmethod
    def may_approve(risk: Risk, approver: str, remote_high_risk_approval: bool) -> bool:
        """Local Mac UI may approve anything approvable; phones only if configured."""
        if approver == "local":
            return True
        if approver.startswith("device:"):
            return risk is not Risk.HIGH or remote_high_risk_approval
        return False  # the model/agent can never approve
