"""Validated development-command policy.

Commands are argv lists, executed without a shell. Each command must match an
allowlisted program; the rule assigns a risk level. Shell metacharacters,
privilege escalation and unknown programs are rejected outright.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from .policy import Risk


class CommandRejected(Exception):
    pass


@dataclass(frozen=True)
class CommandVerdict:
    argv: tuple[str, ...]
    risk: Risk
    categories: frozenset[str] = field(default_factory=frozenset)
    description: str = ""


_META = re.compile(r"[;&|`$<>\n\r]|\$\(")
_BLOCKED_PROGRAMS = {"sudo", "su", "doas", "rm", "dd", "mkfs", "diskutil", "csrutil", "spctl", "chmod", "chown",
                     "launchctl", "defaults", "networksetup", "pfctl", "kill", "killall", "shutdown", "reboot",
                     "curl", "wget", "ssh", "scp", "nc", "bash", "sh", "zsh", "fish", "osascript", "eval", "exec"}

# program -> {subcommand or "*": (risk, categories)}
_RULES: dict[str, dict[str, tuple[Risk, frozenset[str]]]] = {
    "python": {"-m": (Risk.LOW, frozenset())},
    "python3": {"-m": (Risk.LOW, frozenset())},
    "pytest": {"*": (Risk.LOW, frozenset())},
    "npm": {"test": (Risk.LOW, frozenset()), "run": (Risk.MEDIUM, frozenset()), "ci": (Risk.HIGH, frozenset({"install_software"})),
            "install": (Risk.HIGH, frozenset({"install_software"})), "publish": (Risk.HIGH, frozenset({"publish"}))},
    "npx": {"vitest": (Risk.LOW, frozenset()), "tsc": (Risk.LOW, frozenset()), "eslint": (Risk.LOW, frozenset()),
            "jest": (Risk.LOW, frozenset()), "prettier": (Risk.LOW, frozenset())},
    "node": {"--test": (Risk.LOW, frozenset())},
    "mvn": {"test": (Risk.LOW, frozenset()), "-q": (Risk.LOW, frozenset()), "verify": (Risk.LOW, frozenset()),
            "compile": (Risk.LOW, frozenset()), "package": (Risk.LOW, frozenset()), "deploy": (Risk.HIGH, frozenset({"publish"}))},
    "./mvnw": {"test": (Risk.LOW, frozenset()), "-q": (Risk.LOW, frozenset()), "verify": (Risk.LOW, frozenset()),
               "compile": (Risk.LOW, frozenset()), "package": (Risk.LOW, frozenset())},
    "gradle": {"test": (Risk.LOW, frozenset()), "build": (Risk.LOW, frozenset()), "check": (Risk.LOW, frozenset())},
    "./gradlew": {"test": (Risk.LOW, frozenset()), "build": (Risk.LOW, frozenset()), "check": (Risk.LOW, frozenset())},
    "go": {"test": (Risk.LOW, frozenset()), "build": (Risk.LOW, frozenset()), "vet": (Risk.LOW, frozenset())},
    "cargo": {"test": (Risk.LOW, frozenset()), "build": (Risk.LOW, frozenset()), "check": (Risk.LOW, frozenset())},
    "swift": {"test": (Risk.LOW, frozenset()), "build": (Risk.LOW, frozenset())},
    "git": {"status": (Risk.LOW, frozenset()), "diff": (Risk.LOW, frozenset()), "log": (Risk.LOW, frozenset()),
            "show": (Risk.LOW, frozenset()), "branch": (Risk.LOW, frozenset()), "add": (Risk.MEDIUM, frozenset()),
            "commit": (Risk.MEDIUM, frozenset()), "push": (Risk.HIGH, frozenset({"publish"})),
            "reset": (Risk.HIGH, frozenset({"delete_files"})), "clean": (Risk.HIGH, frozenset({"delete_files"}))},
    "make": {"test": (Risk.LOW, frozenset()), "check": (Risk.LOW, frozenset()), "build": (Risk.LOW, frozenset())},
    "pip": {"install": (Risk.HIGH, frozenset({"install_software"}))},
    "brew": {"install": (Risk.HIGH, frozenset({"install_software"}))},
    "ls": {"*": (Risk.LOW, frozenset())},
}

# python -m <module> allowed modules
_PY_MODULES = {"pytest": Risk.LOW, "unittest": Risk.LOW, "compileall": Risk.LOW, "mypy": Risk.LOW, "ruff": Risk.LOW,
               "pip": Risk.HIGH}


def parse_command(command: str | list[str]) -> list[str]:
    if isinstance(command, str):
        if _META.search(command):
            raise CommandRejected("shell operators are not allowed; commands run without a shell")
        argv = shlex.split(command)
    else:
        argv = [str(a) for a in command]
        for a in argv:
            if "\x00" in a or "\n" in a:
                raise CommandRejected("invalid characters in argument")
    if not argv:
        raise CommandRejected("empty command")
    return argv


def validate_command(command: str | list[str]) -> CommandVerdict:
    argv = parse_command(command)
    prog = argv[0]
    base = prog.rsplit("/", 1)[-1] if not prog.startswith("./") else prog
    if base in _BLOCKED_PROGRAMS:
        raise CommandRejected(f"'{base}' is not permitted for the assistant")
    if "/" in prog and not prog.startswith("./"):
        raise CommandRejected("absolute program paths are not permitted; use an allowlisted program name")
    rules = _RULES.get(base)
    if rules is None:
        raise CommandRejected(f"'{base}' is not an allowlisted development command")
    sub = argv[1] if len(argv) > 1 else ""
    if base in ("python", "python3"):
        if sub != "-m" or len(argv) < 3:
            raise CommandRejected("only 'python -m <module>' is permitted")
        module = argv[2]
        if module not in _PY_MODULES:
            raise CommandRejected(f"python module '{module}' is not allowlisted")
        risk = _PY_MODULES[module]
        if module == "pip" and (len(argv) < 4 or argv[3] != "install"):
            raise CommandRejected("only 'python -m pip install' is permitted (with approval)")
        cats = frozenset({"install_software"}) if module == "pip" else frozenset()
        return CommandVerdict(tuple(argv), risk, cats, f"python -m {module}")
    if sub in rules:
        risk, cats = rules[sub]
    elif "*" in rules:
        risk, cats = rules["*"]
    else:
        raise CommandRejected(f"'{base} {sub}' is not allowlisted")
    return CommandVerdict(tuple(argv), risk, cats, f"{base} {sub}".strip())
