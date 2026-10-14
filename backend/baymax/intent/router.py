"""Intent routing.

Deterministic rules handle commands (fast, predictable, testable offline).
Anything else goes to the local model, which may *propose* a tool call; the
proposal is validated against the tool registry and still passes through the
policy layer. The model's output never grants permissions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..creation3d.classifier import RequestClassifier

URL = re.compile(r"\b((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?)", re.I)


@dataclass
class Intent:
    kind: str  # chat | tool | memory_add | memory_list | memory_forget | memory_clear | sleep | wake | cancel | estop | status | clarify | help
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    text: str = ""
    reply: str = ""
    source: str = "rule"  # rule | model


@dataclass
class RouteContext:
    active_model: str | None = None
    llm_available: bool = False


def _clean(text: str) -> str:
    return " ".join(text.strip().split())


class IntentRouter:
    def __init__(self, classifier: RequestClassifier) -> None:
        self.classifier = classifier

    def route(self, text: str, ctx: RouteContext | None = None) -> Intent:
        ctx = ctx or RouteContext()
        raw = _clean(text)
        t = raw.lower().rstrip(".!?")
        t = re.sub(r"^(hey |ok |okay )?baymax[, ]*", "", t)
        t = re.sub(r"^(please |could you |can you |would you )+", "", t).strip()
        words = t.split()

        if re.search(r"\b(emergency stop|e-?stop|stop everything|halt everything|kill all tasks)\b", t):
            return Intent("estop", text=raw)
        if len(words) <= 4 and re.match(r"^(stop|cancel|abort|never ?mind|be quiet|shush|quiet)\b", t):
            return Intent("cancel", text=raw)
        if re.search(r"\b(go to sleep|sleep now|take a nap|good ?night|sleep mode)\b", t) or t == "sleep":
            return Intent("sleep", text=raw)
        if re.match(r"^(wake up|wake|good morning)\b", t):
            return Intent("wake", text=raw)
        if t in ("help", "what can you do", "what can you do for me"):
            return Intent("help", text=raw)
        if re.match(r"^(status|what are you doing|what's running|what is running)\b", t):
            return Intent("status", text=raw)

        # ---- memory (explicit only)
        if re.match(r"^remember(?: that)? (.+)$", t):
            m = re.search(r"\bremember(?: that)? (.+)$", raw, re.I)
            return Intent("memory_add", args={"content": (m.group(1) if m else t).rstrip(".!")}, text=raw)
        if re.search(r"\b(what do you remember|what do you know about me|list (your |my )?memories|show (me )?(your |my )?memories)\b", t):
            return Intent("memory_list", text=raw)
        if re.match(r"^forget (all|everything)( (memories|you know))?$", t):
            return Intent("memory_clear", text=raw)
        m = re.match(r"^forget(?: that| about)? (.+)$", t)
        if m:
            return Intent("memory_forget", args={"phrase": m.group(1)}, text=raw)

        # ---- 3D: modification of the active model
        if ctx.active_model and self._is_model_edit(t):
            return Intent("tool", "creation3d.modify", {"artifact_id": ctx.active_model, "instruction": raw}, "Modify 3D model", raw)

        # ---- 3D creation
        create_verb = re.search(r"\b(create|make|generate|build|model|design|render|sculpt)\b", t)
        mentions_3d = re.search(r"\b(3d|3-d|glb|gltf|mesh|blockout|cad model|3d model)\b", t)
        if create_verb and not re.search(r"\b(image|picture|photo|illustration|drawing|painting|document|report|diagram|flow ?chart|simulation)\b", t):
            cls = self.classifier.classify(t)
            article = re.search(r"\b(create|make|generate|build|model|design|render|sculpt)\b\s+(me\s+)?(a|an|some|the|another|\d+)\b", t)
            if mentions_3d or (cls.status == "supported" and article):
                return Intent("tool", "creation3d.create", {"prompt": raw, "allow_approximation": cls.approximate_allowed},
                              f"Create 3D {cls.object_type or 'model'}", raw)

        # ---- images
        if re.search(r"\b(placeholder)\b.*\b(image|picture|graphic)\b|\b(image|picture|graphic)\b.*\bplaceholder\b", t):
            return Intent("tool", "image.placeholder", {"label": raw[:80]}, "Placeholder image", raw)
        if re.search(r"\b(generate|create|make|draw|paint|render|imagine)\b.*\b(image|picture|photo|illustration|artwork|drawing|painting|wallpaper)\b", t):
            prompt = re.sub(r"(?i)^.*?\b(image|picture|photo|illustration|artwork|drawing|painting|wallpaper)\b\s*(of|showing|with)?\s*", "", raw) or raw
            return Intent("tool", "image.generate", {"prompt": prompt}, "Generate image", raw)

        # ---- research
        m = re.match(r"^(research|search(?: the web| online)?(?: for)?|look up|google|find out(?: about)?|investigate)\s+(.+)$", t)
        if m or re.search(r"\b(search the web|on the web|online research)\b", t):
            query = m.group(2) if m else re.sub(r"\b(search the web|on the web|online research)\b( for| about)?", "", t).strip()
            return Intent("tool", "research.web", {"query": query or t}, f"Research: {query or t}"[:120], raw)

        # ---- simulation
        if re.search(r"\bsimulat\w*\b", t):
            return Intent("tool", "simulation.run", {"request": raw}, "Simulation", raw)

        # ---- diagram / document
        if re.search(r"\b(diagram|flow ?chart)\b", t):
            body = re.sub(r"(?i)^.*?\b(diagram|flow ?chart)\b\s*(of|for|showing|:)?\s*", "", raw)
            return Intent("tool", "documents.diagram", {"title": (body.split("->")[0] if "->" in body else body)[:60] or "Diagram",
                                                        "steps": body or raw}, "Diagram", raw)
        m = re.search(r"\b(write|draft|create|prepare)\b\s+(a|an|the)?\s*(technical )?(document|report|doc|note|readme|spec|specification|documentation|summary)\b\s*(about|on|for|of)?\s*(.*)$", t)
        if m:
            topic = raw[-len(m.group(6)):] if m.group(6) else raw
            return Intent("tool", "documents.write", {"title": topic[:120] or "Document", "brief": raw}, f"Document: {topic[:60]}", raw)

        # ---- coding
        repo = re.search(r"\b(?:in|at|for|of)\s+([~./][\w./-]+)", raw)
        repo_arg = repo.group(1) if repo else "."
        if re.search(r"\b(inspect|analy[sz]e|summari[sz]e)\b.*\b(repo|repository|project|codebase)\b", t):
            return Intent("tool", "coding.inspect", {"repo": repo_arg}, "Inspect repository", raw)
        if re.search(r"\b(run (the )?tests?|fix (the )?(failing )?tests?|make (the )?tests? pass|refactor|implement|fix (the )?bug|add (a )?(unit )?test)\b", t):
            return Intent("tool", "coding.agent", {"repo": repo_arg, "goal": raw}, "Coding task", raw)

        # ---- explicit command
        m = re.match(r"^run (?:the )?(?:command )?`([^`]+)`(?: in ([\w./-]+))?$", raw, re.I) or re.match(r"^run command (.+?)(?: in ([\w./-]+))?$", raw, re.I)
        if m:
            return Intent("tool", "shell.run", {"command": m.group(1), "cwd": m.group(2) or "."}, f"Run {m.group(1)[:60]}", raw)

        # ---- email
        m = re.search(r"\b(?:send (?:an )?email|email)\s+(?:to\s+)?([^\s@]+@[^\s@]+\.[a-z]{2,})\s*(?:(?:with )?subject\s+\"?([^\"]+?)\"?)?\s*(?:saying|that says|with body|body|:)\s+(.+)$", raw, re.I)
        if m:
            return Intent("tool", "mail.send", {"to": m.group(1), "subject": (m.group(2) or "Message from BAYMAX").strip(), "body": m.group(3)},
                          f"Email {m.group(1)}", raw)

        # ---- desktop control
        m = re.match(r"^(?:click|tap)(?: at)? \(?(\d+)[ ,]+(\d+)\)?(?: (twice|double))?$", t)
        if m:
            return Intent("tool", "desktop.click", {"x": float(m.group(1)), "y": float(m.group(2)), "count": 2 if m.group(3) else 1}, "Click", raw)
        m = re.match(r"^(?:move|put) (?:the )?(?:mouse|cursor|pointer) to \(?(\d+)[ ,]+(\d+)\)?$", t)
        if m:
            return Intent("tool", "desktop.move_cursor", {"x": float(m.group(1)), "y": float(m.group(2))}, "Move cursor", raw)
        m = re.match(r"^scroll (up|down)(?: (\d+))?(?: lines?)?$", t)
        if m:
            n = int(m.group(2) or 5)
            return Intent("tool", "desktop.scroll", {"dy": n if m.group(1) == "up" else -n}, "Scroll", raw)
        m = re.match(r"^type [\"“'](.+)[\"”'] (?:in|into) (?:the )?(.+?)(?: app)?$", raw, re.I)
        if m:
            return Intent("tool", "desktop.type_text", {"app": m.group(2).strip(), "text": m.group(1)}, f"Type into {m.group(2)}", raw)
        m = re.match(r"^(?:press|click) (?:the )?[\"“'](.+)[\"”'] (?:button )?in (?:the )?(.+?)(?: app)?$", raw, re.I)
        if m:
            return Intent("tool", "desktop.press_element", {"app": m.group(2).strip(), "name": m.group(1)}, f"Press {m.group(1)}", raw)
        m = re.match(r"^(?:read|what does|what's on|what is on)(?: the)? (?:window|screen)?(?: of| in)? ?(?:the )?(.+?)(?: app)?(?: say| window)?$", t)
        if m and re.search(r"\b(read|window|screen)\b", t) and not re.search(r"\bscreenshot\b", t):
            return Intent("tool", "desktop.read_window", {"app": _title(m.group(1))}, f"Read {m.group(1)}", raw)
        if re.search(r"\b(take a |capture (a |the )?)?screenshot\b|\bcapture the screen\b", t):
            return Intent("tool", "desktop.screenshot", {}, "Screenshot", raw)
        m = re.match(r"^(?:list|show) (?:the )?(?:buttons|elements|controls|ui elements)(?: in| of) (?:the )?(.+?)(?: app)?$", t)
        if m:
            return Intent("tool", "desktop.list_elements", {"app": _title(m.group(1))}, f"Inspect {m.group(1)}", raw)

        # ---- open apps / urls
        m = re.match(r"^(?:open|launch|start|go to|visit|browse to|navigate to)\s+(.+)$", t)
        if m:
            target = m.group(1).strip()
            um = URL.fullmatch(target) or (URL.search(target) if re.search(r"\b(website|site|page|url)\b", target) else None)
            if um:
                url = um.group(1)
                if not url.startswith("http"):
                    url = "https://" + url
                return Intent("tool", "apps.open_url", {"url": url}, f"Open {url}", raw)
            app = re.sub(r"^(the )?(app |application )?", "", target)
            app = re.sub(r"\s+(app|application)$", "", app)
            if 0 < len(app) <= 40:
                return Intent("tool", "apps.open", {"app": _title(app)}, f"Open {_title(app)}", raw)

        return Intent("chat", text=raw)

    @staticmethod
    def _is_model_edit(t: str) -> bool:
        if re.match(r"^(undo|revert)\b", t):
            return True
        if re.search(r"\bexport\b.*\b(glb|gltf|model|this|it)\b|\b(glb|gltf)\b", t):
            return True
        if re.search(r"\b(camera|view from|top view|side view|front view|change the angle|camera angle)\b", t):
            return True
        if re.match(r"^(make|paint|color|colour|change|set|turn|increase|decrease|enlarge|shrink|scale|add|remove|rotate|raise|lower|open|close|use|give)\b", t) \
                and re.search(r"\b(the|it|its|this)\b", t) and not re.search(r"\b(a|an)\s+(new|3d|image|picture)\b", t):
            return True
        if re.search(r"\b(more|less) detail(ed)?\b|\bbigger\b|\bsmaller\b|\btaller\b|\bwider\b", t) and len(t.split()) <= 10:
            return True
        return False


def _title(name: str) -> str:
    special = {"vscode": "Visual Studio Code", "vs code": "Visual Studio Code", "chrome": "Google Chrome", "terminal": "Terminal",
               "finder": "Finder", "safari": "Safari", "notes": "Notes", "mail": "Mail", "calendar": "Calendar", "xcode": "Xcode",
               "system settings": "System Settings", "settings": "System Settings", "music": "Music", "spotify": "Spotify",
               "slack": "Slack", "messages": "Messages", "photos": "Photos", "preview": "Preview", "textedit": "TextEdit"}
    n = name.strip().lower()
    return special.get(n, " ".join(w[:1].upper() + w[1:] for w in name.strip().split()))


def parse_model_proposal(reply: str, allowed: dict[str, Any]) -> Intent:
    """Turn a model's JSON reply into an Intent. Unknown tools become plain chat."""
    try:
        data = json.loads(reply)
    except ValueError:
        m = re.search(r"\{.*\}", reply, re.S)
        try:
            data = json.loads(m.group(0)) if m else {}
        except ValueError:
            data = {}
    if not isinstance(data, dict):
        return Intent("chat", reply=reply.strip()[:4000], source="model")
    tool = data.get("tool")
    if tool and tool in allowed:
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        try:
            allowed[tool].validate(args)
        except Exception as exc:
            return Intent("clarify", reply=f"I wanted to use {tool} but the details were incomplete ({exc}). Could you rephrase?", source="model")
        return Intent("tool", tool, args, str(data.get("reason") or tool)[:120], source="model")
    text = data.get("reply") if isinstance(data.get("reply"), str) else ""
    if not text and not tool:
        text = reply.strip() if not reply.strip().startswith("{") else ""
    return Intent("chat", reply=text[:4000] or "I'm not sure how to help with that. Could you say it another way?", source="model")
