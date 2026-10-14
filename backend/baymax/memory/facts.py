"""Turn "remember …" instructions into concise facts, and answer "what is my …?" from them.

"Remember this for our current conversation: my test project is called Project Phoenix. What is my project called?"
→ scope "conversation", fact ("test project", "Project Phoenix"), question "What is my project called?"
→ answer "Your test project is called Project Phoenix."
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Fact:
    label: str  # "test project"
    value: str  # "Project Phoenix"
    verb: str = "is"  # "is called" | "is named" | "is"

    @property
    def key(self) -> str:
        return re.sub(r"\W+", "_", self.label.lower()).strip("_") + ("_name" if "called" in self.verb or "named" in self.verb else "")

    def stored(self) -> str:
        """The concise form kept in memory ("test project name: Project Phoenix")."""
        return f"{self.label}{' name' if 'called' in self.verb or 'named' in self.verb else ''}: {self.value}"

    def sentence(self) -> str:
        return f"your {self.label} {self.verb} {self.value}"


@dataclass
class RememberRequest:
    scope: str  # "conversation" | "persistent"
    facts: list[Fact]
    other: str  # anything to remember that isn't a "my X is Y" fact
    question: str | None


_SCOPE = re.compile(r"\b(for (?:this|our|the)(?: current)? (?:conversation|chat|session)|for now|just for now|temporarily|"
                    r"until (?:we're|we are) done)\b", re.I)
_FACT = re.compile(r"^my\s+(?P<label>[\w' -]{1,40}?)\s+(?P<verb>is called|is named|'s called|'s named|is|'s)\s+(?P<value>.+)$", re.I)


def _clean_value(v: str) -> str:
    return v.strip().strip("\"“”'").rstrip(".!").strip()


def parse_remember(raw: str) -> RememberRequest:
    text = raw.strip()
    scope = "conversation" if _SCOPE.search(text) else "persistent"
    body = re.sub(r"^(?:(?:hey|hi|ok|okay)\s+)?(?:baymax[, ]*)?(?:please\s+)?remember(?:\s+(?:this|that))?", "", text, flags=re.I)
    body = _SCOPE.sub("", body).strip(" :,-")
    # Split off a trailing question ("… What is my project called?").
    question = None
    m = re.search(r"(?:^|(?<=[.!?])\s+)([^.!?]*\?)\s*$", body)
    if m:
        question = m.group(1).strip()
        body = body[: m.start(1)].strip()
    facts: list[Fact] = []
    other: list[str] = []
    for sentence in re.split(r"(?<=[.!;])\s+", body):
        s = sentence.strip().rstrip(".!;").strip()
        if not s:
            continue
        s = re.sub(r"^that\s+", "", s, flags=re.I)
        fm = _FACT.match(s)
        if fm:
            verb = fm.group("verb").lower().replace("'s called", "is called").replace("'s named", "is named").replace("'s", "is")
            facts.append(Fact(fm.group("label").strip().lower(), _clean_value(fm.group("value")), verb))
        else:
            other.append(s)
    return RememberRequest(scope, facts, ". ".join(other), question)


_ASK = re.compile(r"^(?:what(?:'s| is)|tell me|do you (?:know|remember))\s+(?:what\s+)?my\s+(?P<label>[\w' -]{1,40}?)"
                  r"(?:\s+(?:is\s+)?(?:called|named|is))?\s*\??$", re.I)


def asked_label(question: str) -> str | None:
    """'What is my project called?' -> 'project'."""
    m = _ASK.match(" ".join(question.strip().split()))
    return m.group("label").lower().strip() if m else None


def find_fact(label: str, facts: list[Fact]) -> Fact | None:
    """Best stored fact for the asked label ('project' matches 'test project'); exact label first."""
    words = set(label.split())
    exact = [f for f in facts if f.label == label]
    if exact:
        return exact[-1]
    partial = [f for f in facts if words and words <= set(f.label.split())]
    return partial[-1] if partial else None


def fact_from_stored(content: str) -> Fact | None:
    """Read back a persistent memory written by Fact.stored() ("test project name: Project Phoenix")."""
    m = re.match(r"^(?P<label>[\w' -]{1,50}?)(?P<name> name)?:\s*(?P<value>.+)$", content.strip())
    if not m:
        return None
    return Fact(m.group("label").lower(), m.group("value").strip(), "is called" if m.group("name") else "is")
