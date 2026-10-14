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
        # Speech recognition often mishears the name ("Bimex", "Bimax", "Bay Max"): accept close variants.
        greeted = bool(re.match(r"^(hey|hi|hello|ok|okay|yo)\b", t))
        t = re.sub(r"^((hey|hi|hello|ok|okay|yo)[ ,!]*)?" + WAKE + r"[ ,!.?]*", "", t)
        t = re.sub(r"^(hey|hi|hello)[ ,!]+(?=\w)", "", t)
        conversational = t
        t = re.sub(r"^(please |could you |can you |would you )+", "", t).strip()
        words = t.split()

        # ---- conversation that needs no tool (never routed to desktop automation)
        bare = conversational.strip(" !.?,")
        if not bare and (greeted or raw.strip()):
            return Intent("greeting", text=raw)
        if re.fullmatch(r"(hi|hello|hey|yo|good (morning|afternoon|evening)|how are you( doing)?|are you (there|awake|listening|ok|okay)|"
                        r"(can|do) you hear me( now)?|can you listen( to me)?|you there|testing( testing)?|test|mic test|"
                        r"thank you|thanks|nice to meet you)( baymax)?", bare):
            return Intent("greeting", text=raw)
        # ---- posts on X: drafting only shows the text; publishing goes through the composer and needs approval
        m = re.match(r"^(?:draft|write|compose|prepare)\s+(?:a\s+|an?\s+x\s+|a\s+twitter\s+)?(?:post|tweet)\s*(?:saying|that says|:)?\s*[\"“']?(.+?)[\"”']?$", t)
        if m:
            draft = _strip_original(raw, m.group(1)).strip().strip("\"“”'")
            return Intent("draft", args={"text": draft}, text=raw)
        m = re.match(r"^(?:post|tweet|publish)\s+[\"“'](.+)[\"”']\s+(?:on|to)\s+(?:x|twitter)$", t)
        if m:
            return Intent("tool", "ai.delegate", {"service": "x", "prompt": _exact_tail(raw, m.group(1)), "submit": True},
                          "Publish a post on X", raw)
        if re.search(r"\b(?:x|twitter)\b", t) and re.search(r"\b(?:enter|type|paste|put|write)\b.{0,20}\b(?:post|draft|tweet|it)\b", t):
            publish = bool(re.search(r"\b(?:publish|post|tweet|send)\s+it\b", t))
            return Intent("tool", "ai.delegate", {"service": "x", "prompt": LAST_DRAFT, "submit": publish},
                          "Publish the draft on X" if publish else "Put the draft in X's composer", raw)
        if re.fullmatch(r"(?:now\s+)?(?:post|publish|tweet)\s+it(?:\s+on\s+(?:x|twitter))?", t):
            return Intent("tool", "ai.delegate", {"service": "x", "prompt": LAST_DRAFT, "submit": True}, "Publish the draft on X", raw)

        # ---- questions about apps and the front window (answered from macOS, not guessed)
        if re.search(r"\b(what|which)\s+(?:apps|applications|programs)\s+(?:are|do i have)\s+(?:currently\s+)?(?:open|running)\b"
                     r"|\blist\s+(?:the\s+|my\s+)?(?:open|running)\s+(?:apps|applications)\b", t):
            return Intent("tool", "desktop.running_apps", {}, "List open apps", raw)
        if re.search(r"\b(which|what)\s+(?:app|application|program)\s+(?:is\s+)?(?:currently\s+)?(?:active|in front|focused|frontmost)\b", t):
            return Intent("tool", "desktop.active_window", {}, "Front app", raw)

        # ---- safe keyboard shortcuts ("open a new tab in Chrome", "reload the page", "press command t")
        app_tail = r"(?:\s+in\s+(?:the\s+)?(.+?))?"
        for pattern, action in ((r"^open\s+(?:a\s+)?new\s+tab" + app_tail + "$", "new_tab"),
                                (r"^open\s+(?:a\s+)?new\s+window" + app_tail + "$", "new_window"),
                                (r"^(?:reload|refresh)\s+(?:the\s+|this\s+)?(?:page|tab)" + app_tail + "$", "reload")):
            m = re.match(pattern, t)
            if m:
                return Intent("tool", "desktop.shortcut", {"action": action, "app": _title(m.group(1)) if m.group(1) else ""},
                              action.replace("_", " "), raw)
        m = re.match(r"^(?:press|hit|use)\s+(?:the\s+)?(?:command|cmd|⌘)\s*[+-]?\s*(\S)(?:\s+shortcut)?" + app_tail + "$", t)
        if m:
            action = {"t": "new_tab", "n": "new_window", "r": "reload", "f": "find", "l": "address_bar", "[": "back", "]": "forward",
                      "a": "select_all", "c": "copy", "v": "paste", "z": "undo", "=": "zoom_in", "-": "zoom_out"}.get(m.group(1))
            if action is None:
                return Intent("chat", reply="I only press safe shortcuts (⌘T, ⌘N, ⌘R, ⌘F, ⌘L, ⌘[, ⌘], ⌘A, ⌘C, ⌘V, ⌘Z, ⌘=, ⌘-). "
                                          "Which of those did you mean?", text=raw)
            return Intent("tool", "desktop.shortcut", {"action": action, "app": _title(m.group(2)) if m.group(2) else ""},
                          f"Press ⌘{m.group(1).upper()}", raw)

        # ---- files by name (Spotlight) and documentation lookups (web research)
        m = re.match(r"^(?:find|locate|where is|search for)\s+(?:the\s+|my\s+)?file\s+(?:named|called)\s+[\"“']?(.+?)[\"”']?$", t) \
            or re.match(r"^(?:find|locate|where is)\s+(?:the\s+|my\s+)?([\w .-]+\.(?:pdf|docx?|xlsx?|pptx?|txt|md|csv|zip|png|jpe?g|key|pages|numbers|swift|py|java|js|ts))$", t)
        if m:
            return Intent("tool", "files.find_by_name", {"name": _strip_original(raw, m.group(1))}, "Find file", raw)
        m = re.match(r"^(?:go to|open|search|check|use|look in|look at)\s+(?:the\s+)?(?:official\s+)?([\w.+#-]+(?: [\w.+#-]+)?)\s+"
                     r"(?:documentation|docs)\s*,?\s*(?:and\s+)?(?:then\s+)?(?:find|look up|search for|search|read|get|show me|tell me)"
                     r"\s+(?:me\s+)?(?:some\s+)?(?:information|info|details|the section|what it says)?\s*(?:about|on|for|regarding)?\s+(.+)$", t)
        if m:
            topic = m.group(2).strip()
            return Intent("tool", "research.web", {"query": f"{m.group(1)} official documentation {topic}"},
                          f"Look up {topic} in the {m.group(1)} docs"[:120], raw)
        m = re.match(r"^(?:find|get|look up|show me)\s+(?:the\s+)?(?:latest\s+|current\s+|official\s+)?(?:documentation|docs|release notes)\s+(?:for|on|about)\s+(.+)$", t)
        if m:
            return Intent("tool", "research.web", {"query": f"official documentation {m.group(1)}"}, "Research documentation", raw)

        # ---- double / right click an element described in words
        m = re.match(r"^(double|right)[- ]?click\s+(?:on\s+)?(?:the\s+)?(.+)$", t)
        if m:
            target, app = _split_app(m.group(2))
            args = {"query": target, "button": "right" if m.group(1) == "right" else "left", "count": 2 if m.group(1) == "double" else 1}
            if app:
                args["app"] = app
            return Intent("tool", "screen.click_element", args, f"{m.group(1).title()}-click {target}", raw)

        # ---- delegate to another AI ("ask ChatGPT to …", "prepare this prompt in Claude: …", "send … to Gemini")
        ai = r"(chat ?gpt|claude|gemini|perplexity)"
        m = re.match(r"^(?:ask|tell|have|get)\s+" + ai + r"\s*,?\s+(?:to\s+)?(.+)$", t)
        if m:
            return _delegate(raw, m.group(1), m.group(2), submit=True)
        m = re.match(r"^(?:open|launch|use|go to)\s+" + ai + r"(?:\s+app)?\s*,?\s+(?:and|then)\s+(enter|type|paste|put|write|ask)"
                     r"(?:\s+(?:this|the|a|my))?(?:\s+(?:prompt|message|question))?\s*:?\s+(?:to\s+)?(.+)$", t)
        if m:
            return _delegate(raw, m.group(1), m.group(3), submit=m.group(2) == "ask")
        m = re.match(r"^(?:prepare|draft|write|type|enter|put|paste)(?:\s+(?:this|the|a|my))?(?:\s+(?:prompt|message|question))?\s+"
                     r"(?:in|into|for|to)\s+" + ai + r"(?:\s+app)?\s*(?::|saying|that says|asking)?\s*(.+)$", t)
        if m:
            return _delegate(raw, m.group(1), m.group(2), submit=False)
        m = re.match(r"^(?:type|enter|put|paste)\s+[\"“'](.+)[\"”']\s+(?:in|into)\s+" + ai + r"$", t)
        if m:
            return _delegate(raw, m.group(2), m.group(1), submit=False)
        m = re.match(r"^send\s+[\"“']?(.+?)[\"”']?\s+to\s+" + ai + r"$", t)
        if m:
            return _delegate(raw, m.group(2), m.group(1), submit=True)

        # ---- capabilities *in the current context* ("what can you do with this screen / here / in this app"): inspect it for real
        if re.search(r"\b(can|could) you (do|help( me)?)\b.{0,25}\b(with|on|in|about|for)\b.{0,20}\b(screen|window|app|page|tab|site)\b"
                     r"|\bwhat can you do (here|now)\b", bare):
            return Intent("tool", "screen.inspect", {}, "Inspect the current screen", raw)
        if re.search(r"\bwhat (else )?(work|works|walks|things|stuff|tasks|jobs|features|kind of (things|tasks|work))?\s*(can|could|do) you (do|perform|help( me)? with)\b"
                     r"|\bwhat are (you|your) (able to do|capable of|capabilities|abilities|features|skills)\b"
                     r"|\bwhat can you do\b|\bhow can you help\b|\bwhat do you do\b|^(help|capabilities|features)$"
                     r"|\bwhat\b.{0,40}\b(you can|can you|could you|you could|you are able to|are you able to)\s+(do|perform|handle|help|work on|assist)\b", bare):
            return Intent("help", text=raw)
        if re.search(r"\b(is|are) (my )?(i?phone|mobile|device)s? (connected|paired|online|linked)\b|\b(phone|iphone) (connection|status)\b"
                     r"|\bconnection status\b|\b(i?phone|mobile)\b.{0,20}\b(connected|paired|online|linked)\b", bare):
            return Intent("status", text=raw)

        if re.search(r"\b(emergency stop|e-?stop|stop everything|halt everything|kill all tasks)\b", t):
            return Intent("estop", text=raw)
        if len(words) <= 4 and re.match(r"^(stop|cancel|abort|never ?mind|be quiet|shush|quiet)\b", t):
            return Intent("cancel", text=raw)
        if re.search(r"\b(go to sleep|sleep now|take a nap|good ?night|sleep mode)\b", t) or t == "sleep":
            return Intent("sleep", text=raw)
        if re.match(r"^(wake up|wake|good morning)\b", t):
            return Intent("wake", text=raw)
        if re.match(r"^(status|what are you doing|what's running|what is running)\b", t):
            return Intent("status", text=raw)

        # ---- memory (explicit only)
        if re.match(r"^(?:please )?remember(?: that| this)?\b(.+)$", t):
            m = re.search(r"\bremember(?: that| this)?\b[\s:,-]*(.+)$", raw, re.I)
            return Intent("memory_add", args={"content": (m.group(1) if m else t).rstrip(".!"), "raw": raw}, text=raw)
        from ..memory.facts import asked_label
        label = asked_label(raw.strip().rstrip("."))
        if label:  # "what is my project called?" — answered from stored facts (falls back to chat if nothing stored)
            return Intent("memory_recall", args={"label": label}, text=raw)
        if re.search(r"\b(what do you remember|what do you know about me|list (your |my )?memories|show (me )?(your |my )?memories)\b", t):
            return Intent("memory_list", text=raw)
        if re.match(r"^forget (all|everything)( (memories|you know))?$", t):
            return Intent("memory_clear", text=raw)
        m = re.match(r"^forget(?: that| about)? (.+)$", t)
        if m:
            return Intent("memory_forget", args={"phrase": m.group(1)}, text=raw)

        # ---- developer assistance (before generic open/create/find rules)
        m = re.match(r"^(?:open|load)\s+(.+?)\s+in\s+(vs ?code|visual studio code|code|xcode|intellij(?: idea)?|cursor|sublime(?: text)?|pycharm|android studio|finder|terminal)$", t)
        if m:
            return Intent("tool", "apps.open_project", {"project": _strip_original(raw, m.group(1)), "app": m.group(2)}, f"Open {m.group(1)}", raw)
        m = re.match(r"^(?:find|where is|where's|show me|locate|which)\s+(?:is\s+)?(?:the\s+)?(?:function|method|class|code|file)\b\s*(.*)$", t)
        if m and m.group(1):
            q = _strip_original(raw, m.group(1)).rstrip(" .?!")  # keep identifiers' original case (startCursorTracking)
            return Intent("tool", "coding.find", {"query": q}, f"Find code: {q}"[:120], raw)
        m = re.match(r"^(?:explain|what does|what's|why)\b.*\b(error|exception|traceback|stack ?trace|compiler|build fail\w*|crash)\b(.*)$", t)
        if m:
            pasted = raw.split(":", 1)[1].strip() if ":" in raw and len(raw.split(":", 1)[1].strip()) > 10 else ""
            where = re.search(r"\b(terminal|iterm2?|console|xcode|vs ?code|visual studio code)\b", t)
            args = {"text": pasted, **({"app": _title(where.group(1))} if where and not pasted else {})}
            return Intent("tool", "coding.explain_error", args, "Explain error", raw)
        if re.search(r"\b(create|make|write|generate)\b.*\bjava class\b", t):
            nm = re.search(r"\b(?:called|named)\s+([A-Za-z_]\w*)", raw)
            desc = re.split(r"\b(?:that|which|to)\b", raw, maxsplit=1)
            return Intent("tool", "coding.java_class", {"name": nm.group(1) if nm else "", "description": desc[1].strip() if len(desc) > 1 else ""},
                          "Create Java class", raw)
        m = re.match(r"^(?:summari[sz]e|give me a summary of|tl;?dr)\s+(?:of\s+)?(?:this|the|that|my)?\s*(document|file|pdf|paper|page|report|doc)?\s*(.*)$", t)
        if m and not re.search(r"\b(repo|repository|project|codebase)\b", t):
            target = _strip_original(raw, m.group(2)) if m.group(2) else ""
            looks_path = bool(re.search(r"[/~.]\w", target))
            return Intent("tool", "documents.summarize", {"path": target if looks_path else ""}, "Summarize document", raw)

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
        # ---- form fields: "fill the Name field with …", "type 'x' into the Email field" (typing only, never submitting)
        m = re.match(r"^(?:fill(?: in| out)?|complete)\s+(?:the\s+)?(.+?\b(?:field|box|input))(?:\s+in\s+(?:the\s+)?(.+?)(?:\s+app)?)?"
                     r"\s+with\s+(.+)$", t) \
            or re.match(r"^(?:type|enter|write|put)\s+[\"“']?(?P<v>.+?)[\"”']?\s+(?:in|into)\s+(?:the\s+)?(?P<f>.+?\b(?:field|box|input))"
                        r"(?:\s+in\s+(?:the\s+)?(?P<a>.+?)(?:\s+app)?)?$", t)
        if m:
            if m.re.groupindex.get("v"):
                fld, app_name, val = m.group("f"), m.group("a"), m.group("v")
            else:
                fld, app_name, val = m.group(1), m.group(2), m.group(3)
            replace = bool(re.search(r"\b(replace|clear|overwrite)\b", t))
            args = {"field": fld.strip(), "text": _exact_tail(raw, val.strip()).strip("\"“”'"), "replace": replace,
                    **({"app": _title(app_name)} if app_name else {})}
            return Intent("tool", "screen.type_into", args, f"Fill the {fld.strip()}"[:120], raw)
        m = re.match(r"^type [\"“'](.+)[\"”'] (?:in|into) (?:the )?(.+?)(?: app)?$", raw, re.I)
        if m:
            return Intent("tool", "desktop.type_text", {"app": m.group(2).strip(), "text": m.group(1)}, f"Type into {m.group(2)}", raw)
        m = re.match(r"^(?:press|click) (?:the )?[\"“'](.+)[\"”'] (?:button )?in (?:the )?(.+?)(?: app)?$", raw, re.I)
        if m:
            return Intent("tool", "desktop.press_element", {"app": m.group(2).strip(), "name": m.group(1)}, f"Press {m.group(1)}", raw)
        # ---- "what's on my screen / what can you do with the current screen": inspect the real front window
        if re.search(r"\b(what('?s| is) on (my |the |this )?screen|what do you see|(current|this|my) screen)\b", t) \
                and not re.search(r"\bscreenshot\b", t):
            return Intent("tool", "desktop.read_window", {}, "Read the current window", raw)

        # ---- switch apps; "read it" reads the window in front
        m = re.match(r"^(?:switch|go back|change|swap|flip)\s+(?:over\s+|back\s+)?to\s+(?:the\s+)?(.+?)(?:\s+(?:app|application|window))?$", t)
        if m and len(m.group(1)) <= 40:
            return Intent("tool", "apps.open", {"app": _title(m.group(1))}, f"Switch to {_title(m.group(1))}", raw)
        if re.fullmatch(r"(?:read|show me|tell me)\s+(?:me\s+)?(?:it|this|that|what it says|what's (?:in|on) it|its (?:text|contents?)|"
                        r"the text|what is (?:in|on) it)(?:\s+(?:to me|aloud|out loud))?", t):
            return Intent("tool", "desktop.read_window", {}, "Read the window", raw)

        # ---- the window the user is working in
        if re.search(r"\b(point|look|turn|face)\b.{0,20}\b(toward|towards|at|to)\b.{0,12}\b(the |my )?(window|app)\b.{0,25}"
                     r"(i'?m|i am|i'?m using|i am using|using|active|current|front)", t) \
                or re.search(r"\b(point|look)\b.{0,15}\b(my|the (current|active|front))\s+(window|app)\b", t):
            return Intent("tool", "screen.point_window", {}, "Look at your window", raw)
        if re.search(r"\b(title of (the |my )?(active|current|front|frontmost)? ?window|(active|current) window'?s? title|"
                     r"which (app|application|window) am i (in|using)|what (app|application|window) am i (in|using)|"
                     r"what('?s| is) (the )?(active|current|front) (window|app))\b", t):
            return Intent("tool", "desktop.active_window", {}, "Active window", raw)

        # ---- screen understanding: find / look at / point at / click a described UI element
        ui_word = r"\b(button|field|box|tab|menu|link|icon|checkbox|toolbar|toggle|switch|dropdown|slider)\b"
        m = re.match(r"^(look at|point at|point to|show me|find|where is|where's|locate|highlight)\s+(.+)$", t)
        if m and (m.group(1) in ("look at", "point at", "point to", "highlight") or re.search(ui_word, t) or re.search(r"\bon (the |my )?screen\b", t)):
            target, app = _split_app(m.group(2))
            if target:
                args = {"query": target, **({"app": app} if app else {})}
                if m.group(1) in ("point at", "point to"):  # pointing moves the real pointer too (never clicks)
                    return Intent("tool", "screen.point_at", args, f"Point at {target}"[:120], raw)
                return Intent("tool", "screen.find_element", args, f"Find {target}"[:120], raw)
        m = re.match(r"^(?:move|put|take) (?:the |my )?(?:mouse|cursor|pointer)(?: pointer)? (?:to|over|onto|on) (.+)$", t)
        if m and not re.match(r"^\(?\d", m.group(1)):
            target, app = _split_app(m.group(1))
            if target and target not in ("it", "there", "that"):
                args = {"query": target, **({"app": app} if app else {})}
                return Intent("tool", "screen.point_at", args, f"Point at {target}"[:120], raw)
        m = re.match(r"^(?:click|press|tap|hit)(?: on)?\s+(.+)$", t)
        if m and not re.match(r"^\(?\d", m.group(1)):
            target, app = _split_app(m.group(1))
            if target:
                args = {"query": target, **({"app": app} if app else {})}
                return Intent("tool", "screen.click_element", args, f"Press {target}"[:120], raw)
        m = re.match(r"^(?:read|what does|what's on|what is on)(?: the)? (?:window|screen)?(?: of| in)? ?(?:the )?(.+?)(?: app)?(?: say| window)?$", t)
        if m and re.search(r"\b(read|window|screen)\b", t) and not re.search(r"\bscreenshot\b", t):
            name = re.sub(r"\b(current|this|active|front|frontmost|my|the|open)\b", "", m.group(1)).strip()
            name = re.sub(r"\b(window|screen|app|say|says|show|shows|display|displays|contain|contains|mean|means|have|has)\b", "", name).strip()
            args = {"app": _title(name)} if name else {}
            return Intent("tool", "desktop.read_window", args, f"Read {name or 'current window'}", raw)
        if re.search(r"\b(take a |capture (a |the )?)?screenshot\b|\bcapture the screen\b", t):
            return Intent("tool", "desktop.screenshot", {}, "Screenshot", raw)
        m = re.match(r"^(?:list|show) (?:the )?(?:buttons|elements|controls|ui elements)(?: in| of) (?:the )?(.+?)(?: app)?$", t)
        if m:
            return Intent("tool", "desktop.list_elements", {"app": _title(m.group(1))}, f"Inspect {m.group(1)}", raw)

        # ---- "open <site> in <browser>": the browser is the app, the site is the destination
        m = re.match(r"^(?:open|launch|go to|visit|browse to|navigate to|load|show me)\s+(.+?)\s+(?:in|on|using|with)\s+"
                     r"(safari|google chrome|chrome|firefox|arc|brave(?: browser)?|edge|microsoft edge|opera|vivaldi|chromium"
                     r"|(?:the |my |a )?(?:web )?browser)$", t)
        if m:
            dest = _strip_original(raw, m.group(1))
            browser = "" if m.group(2).endswith("browser") and not m.group(2).startswith("brave") else _title(m.group(2))
            return Intent("tool", "apps.open_url", {"url": dest, "browser": browser},
                          f"Open {dest} in {browser or 'the browser'}", raw)

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


LAST_DRAFT = "\u0000last-draft\u0000"  # placeholder resolved by the assistant from the conversation (the latest draft)

WAKE = r"(baymax|bay ?max|bay-max|bimax|bimex|beymax|baymex|beemax|bymax|bay mac|bae max|baemax|maymax)"


def split_compound(router: "IntentRouter", raw: str, ctx: "RouteContext | None" = None) -> "list[Intent] | None":
    """'Open Safari and then open Terminal' -> two tool intents, run in order as one task. Only when every part is
    itself a recognised action (2-4 parts); otherwise None and the sentence is handled as a whole."""
    parts = [p.strip(" ,.") for p in re.split(r"\s*,?\s*\b(?:and then|then|and also|and|after that)\b\s*", raw, flags=re.I)]
    parts = [p for p in parts if p]
    if not 2 <= len(parts) <= 4:
        return None
    intents = [router.route(p, ctx) for p in parts]
    if all(i.kind == "tool" and i.tool for i in intents):
        for prev, cur in zip(intents, intents[1:]):  # "switch to TextEdit and read it": "it" is the app just opened
            if cur.tool == "desktop.read_window" and not cur.args.get("app") and prev.tool == "apps.open":
                cur.args = {**cur.args, "app": prev.args["app"]}
        return intents
    return None


def _exact_tail(raw: str, lowered: str) -> str:
    """Original-case fragment, keeping the punctuation the router stripped when the fragment ends the sentence."""
    i = raw.lower().find(lowered)
    if i < 0:
        return lowered
    end = i + len(lowered)
    rest = raw[end:]
    if re.fullmatch(r"[.!?…]+[\"”']?\s*", rest):
        end += len(rest.rstrip())
    return raw[i:end]


def _delegate(raw: str, service: str, prompt_lower: str, *, submit: bool) -> "Intent":
    prompt = _exact_tail(raw, prompt_lower).strip()
    if len(prompt) > 1 and prompt[0] in "\"“'" and prompt[-1] in "\"”'":
        prompt = prompt[1:-1]
    prompt = prompt[:1].upper() + prompt[1:]
    svc = service.replace(" ", "")
    label = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini", "perplexity": "Perplexity"}.get(svc, svc)
    return Intent("tool", "ai.delegate", {"service": svc, "prompt": prompt, "submit": submit},
                  f"{'Ask' if submit else 'Prepare a prompt in'} {label}", raw)


def _strip_original(raw: str, lowered: str) -> str:
    """Recover the original-case text for a fragment matched in the lower-cased utterance."""
    i = raw.lower().find(lowered)
    return raw[i:i + len(lowered)] if i >= 0 else lowered


def _split_app(phrase: str) -> tuple[str, str | None]:
    """'the run button in vs code on screen' -> ('run button', 'Visual Studio Code')."""
    p = re.sub(r"\s+(on|in) (the |my )?(screen|display|monitor)$", "", phrase.strip())
    m = re.match(r"^(.+?)\s+in\s+(?:the\s+)?(.+?)(?:\s+(?:app|application|window))?$", p)
    if m and len(m.group(2)) <= 40:
        p, app = m.group(1), _title(m.group(2))
    else:
        app = None
    p = re.sub(r"^(the|a|an|this|that)\s+", "", p).strip()
    return p, app


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
