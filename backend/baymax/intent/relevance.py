"""Does the user's request plausibly call for this tool?

Small local models (llama3.2:3b) over-call tools: given a greeting they may pick `files.read` with an invented
path, or `desktop.read_window` with no app. Every model-selected tool must pass this deterministic check against
the user's own words before it can run. If it fails, BAYMAX answers conversationally instead (no action).
"""

from __future__ import annotations

import re

# Tool name prefix (or full name) -> words that must appear in the request for that tool to make sense.
_RULES: list[tuple[str, str]] = [
    ("files.delete", r"\b(delete|remove|erase|trash)\b"),
    ("files.write", r"\b(write|save|create|make|edit|modify|change|update|append|put)\b.*\b(file|note|text|readme|config|script)\b|\bfile\b"),
    ("files.", r"\b(files?|folders?|director(y|ies)|paths?|documents?|readme|\.\w{1,5})\b|[~/][\w.-]+/"),
    ("desktop.running_apps", r"\b(apps?|applications?|programs?)\b.*\b(open|running)\b|\b(open|running)\s+(apps?|applications?)\b"),
    ("desktop.active_window", r"\b(window|app|application|title|front ?most|in front)\b"),
    ("desktop.read_window", r"\b(windows?|screen|reading|read|showing|on (the|my) (screen|display))\b"),
    ("desktop.screenshot", r"\b(screenshot|screen ?shot|capture|screen)\b"),
    ("desktop.type_text", r"\b(type|enter|write|fill)\b"),
    ("desktop.move_cursor", r"\b(mouse|cursor|pointer)\b"),
    ("desktop.click", r"\b(click|tap|press)\b"),
    ("desktop.scroll", r"\bscroll\b"),
    ("desktop.", r"\b(window|screen|click|button|element|type|press|menu|app)\b"),
    ("ai.delegate", r"\b(chat ?gpt|claude|gemini|perplexity|openai|bard)\b"),
    ("screen.", r"\b(look|point|find|where|locate|click|press|button|field|menu|tab|icon|link|screen|show me)\b"),
    ("apps.open_project", r"\b(open|load|launch)\b.*\b(project|repo|repository|folder|code|xcode|vs ?code|editor)\b"),
    ("apps.open_url", r"\b(open|go to|visit|browse|navigate|website|site|page|url|link)\b|https?://|\.\w{2,}\b"),
    ("apps.", r"\b(open|launch|start|run|switch to|bring up|show|use|go to|browser|app|application|look at)\b"),
    ("research.", r"\b(search|research|look up|lookup|google|web|online|internet|sources?|find out|latest|news|documentation|docs)\b"),
    ("creation3d.", r"\b(3d|three.?d|model|mesh|glb|gltf|create|make|build|design|render|sculpt|export|bigger|smaller|color|colour|rotate)\b"),
    ("image.", r"\b(image|picture|photo|illustration|drawing|painting|wallpaper|draw|paint)\b"),
    ("documents.diagram", r"\b(diagram|flow ?chart|chart|architecture|graph)\b"),
    ("documents.summarize", r"\b(summar\w+|tl;?dr|gist|overview)\b"),
    ("documents.", r"\b(document|report|write|draft|note|readme|spec|summary|letter)\b"),
    ("simulation.", r"\bsimulat\w*\b"),
    ("coding.java_class", r"\bjava\b"),
    ("coding.explain_error", r"\b(error|exception|traceback|crash|fail\w*|bug|stack ?trace|compiler)\b"),
    ("coding.find", r"\b(function|method|class|code|file|where is|defined|definition|implementation)\b"),
    ("coding.", r"\b(code|repo|repository|project|tests?|bug|fix|refactor|implement|compile|build)\b"),
    ("shell.", r"\b(run|execute|command|terminal|shell|script)\b"),
    ("mail.", r"\b(e-?mail|mail|send)\b"),
    ("hardware.", r"\b(hardware|firmware|flash|arduino|esp32|board|device|serial|usb)\b"),
]


# ---------------------------------------------------------------- request mode
# Questions and requests for advice are answered in words. Desktop tools need something on *this* computer to look at
# or act on; repository-changing tools need an explicit request to change something.
_QUESTION = re.compile(r"^(?:please\s+|can you\s+|could you\s+)?(?:explain|describe|define|what|what's|whats|why|how|when|who|which|"
                       r"is|are|does|do|should|would|tell me (?:about|what|how|why)|give me (?:an? )?(?:example|overview|summary|ideas?|"
                       r"suggestions?)|compare|difference between)\b")
_ADVICE = re.compile(r"\b(suggest\w*|ideas?|brainstorm\w*|recommend\w*|propos\w+|advice|advise|plan(?:ning)?|outline|"
                     r"what (?:features|improvements)|pros and cons|options for|should i)\b")
_IMPLEMENT = re.compile(r"\b(implement|add|create|make|build|write|generate|fix|change|modify|edit|refactor|update|apply|"
                        r"delete|remove|rename|install|run|execute|scaffold|set up|setup)\b")
_DESKTOP_REFERENT = re.compile(r"\b(?:my|this|the current|current|the front(?:most)?|front(?:most)?|active|open|focused)\s+"
                               r"(?:screen|window|windows|desktop|tab|tabs|browser|page)\b"
                               r"|\b(?:this|the current|current|the front(?:most)?|front(?:most)?|active|focused)\s+(?:app|application)\b"
                               r"|\b(?:apps?|applications?)\s+(?:are\s+)?(?:open|running|in front)\b"
                               r"|\bon (?:my|the) (?:screen|display|mac)\b|\b(?:click|press|scroll|tap|type into|point at|look at|"
                               r"read the|which (?:app|application)|what apps?|what applications?)\b|^(?:please\s+)?(?:open|launch|start|"
                               r"switch to|go to|visit)\s+\S")
# Reading local code/files for a question or advice needs an explicit pointer to them.
_LOCAL_REFERENT = re.compile(r"[~/][\w.-]+/|\b\w+\.(?:py|java|ts|tsx|js|swift|md|xml|json|yml|yaml|properties|gradle|txt)\b"
                             r"|\b(?:this|the|my)\s+(?:file|repo|repository|codebase|code|readme)\b|\b(?:look at|read|check|based on|"
                             r"in)\s+(?:my|the|this)\s+(?:code|project|repo|repository|files?)\b")
# Tools that change files or a repository (never offered for questions or advice).
WRITE_TOOLS = ("files.write", "files.delete", "coding.agent", "coding.java_class", "shell.run")


def request_mode(text: str) -> str:
    """'advice' (suggest/plan/brainstorm), 'question' (explain/what/how), or 'action'."""
    t = " ".join(text.lower().split())
    t = re.sub(r"^(?:hey |hi |ok |okay )?(?:baymax|bay max)[ ,!]*", "", t)
    if _ADVICE.search(t) and not re.search(r"\b(implement|apply|make|create|add) (?:it|them|these|those|that|this|the (?:first|second|third))\b", t):
        return "advice"
    if _QUESTION.match(t) and not _IMPLEMENT.match(t):
        return "question"
    return "action"


def tool_is_relevant(tool: str, text: str) -> bool:
    t = text.lower()
    mode = request_mode(text)
    if mode in ("advice", "question"):
        if tool in WRITE_TOOLS or (mode == "advice" and tool.startswith(("creation3d.", "image.", "documents.write", "mail.", "hardware."))):
            return False  # suggestions and explanations never change files
        if tool.startswith(("desktop.", "screen.", "apps.", "ai.")) and not _DESKTOP_REFERENT.search(t):
            return False  # nothing on this computer was referred to: answer in words
        if tool.startswith(("files.", "coding.", "documents.summarize")) and not _LOCAL_REFERENT.search(t):
            return False  # a general question or advice: no need to read local files
    for prefix, pattern in _RULES:
        if tool == prefix or (prefix.endswith(".") and tool.startswith(prefix)):
            return bool(re.search(pattern, t))
    return False  # unknown tool family: never let the model pick it on its own
