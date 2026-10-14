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
    ("desktop.read_window", r"\b(windows?|screen|reading|read|showing|on (the|my) (screen|display))\b"),
    ("desktop.screenshot", r"\b(screenshot|screen ?shot|capture|screen)\b"),
    ("desktop.type_text", r"\b(type|enter|write|fill)\b"),
    ("desktop.move_cursor", r"\b(mouse|cursor|pointer)\b"),
    ("desktop.click", r"\b(click|tap|press)\b"),
    ("desktop.scroll", r"\bscroll\b"),
    ("desktop.", r"\b(window|screen|click|button|element|type|press|menu|app)\b"),
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


def tool_is_relevant(tool: str, text: str) -> bool:
    t = text.lower()
    for prefix, pattern in _RULES:
        if tool == prefix or (prefix.endswith(".") and tool.startswith(prefix)):
            return bool(re.search(pattern, t))
    return False  # unknown tool family: never let the model pick it on its own
