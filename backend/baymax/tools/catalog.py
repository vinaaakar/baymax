"""Typed metadata for every tool: intents, permissions, timeout, verification and recovery.

The registry attaches this to each tool when it is registered, so the planner, the task manager (timeouts),
the API (GET /api/tools) and the request trace all read the same declaration. A tool class may also set
these attributes itself; class attributes win over this table.
"""

from __future__ import annotations

from dataclasses import dataclass

# Permission identifiers (macOS privacy permissions and BAYMAX's own gates).
ACCESSIBILITY = "macos.accessibility"
SCREEN_RECORDING = "macos.screen_recording"
AUTOMATION = "macos.automation"
NETWORK = "network"
WORKSPACE = "workspace.files"
MICROPHONE = "microphone"


@dataclass(frozen=True)
class ToolMeta:
    intents: tuple[str, ...]
    permissions: tuple[str, ...]
    timeout_s: float
    verification: str
    recovery: str
    output: str = "summary text + structured data"


# Longest matching prefix wins ("apps.open_url" before "apps.").
_TABLE: dict[str, ToolMeta] = {
    "apps.open_url": ToolMeta(("open_website", "delegate_ai"), (AUTOMATION,), 30,
                              "the browser's front tab URL is read back and compared with the destination",
                              "report the page actually shown, or that the address bar could not be read"),
    "apps.open_project": ToolMeta(("open_app",), (WORKSPACE,), 60, "the project path exists and the editor reports it open",
                                  "ask which project when several match"),
    "apps.": ToolMeta(("open_app",), (), 30, "the application is detected as running after launch",
                      "say whether the app is not installed or failed to launch; never retry with a guessed name"),
    "ai.delegate": ToolMeta(("delegate_ai",), (ACCESSIBILITY, AUTOMATION), 90,
                            "the prompt text is read back from the service's input field; submission only after approval",
                            "stop at login/CAPTCHA or a changed interface and tell the user what to do"),
    "screen.inspect": ToolMeta(("screen_question", "read_window"), (ACCESSIBILITY,), 30,
                               "controls come from the live accessibility tree of the front window", "name the missing permission"),
    "screen.point_at": ToolMeta(("point",), (ACCESSIBILITY,), 30,
                                "the pointer position is read back at the element's verified centre; nothing is clicked",
                                "ambiguous → ask which one; not found → say where it looked; never move to guessed coordinates"),
    "screen.type_into": ToolMeta(("fill_form", "computer_use"), (ACCESSIBILITY,), 45,
                                 "keyboard focus confirmed on the field, then its value is read back and compared exactly",
                                 "ambiguous → ask which field; not found or disabled → type nothing; never submits"),
    "screen.": ToolMeta(("find_element", "point", "click_element"), (ACCESSIBILITY, SCREEN_RECORDING), 30,
                        "element found in the accessibility tree or fresh OCR; clicks re-read the tree for a change",
                        "ambiguous → ask which one; not found → say where it looked; never click OCR-only text"),
    "desktop.active_window": ToolMeta(("read_window", "screen_question"), (), 15, "front window reported by macOS",
                                      "say the title is unavailable rather than guessing"),
    "desktop.read_window": ToolMeta(("read_window",), (ACCESSIBILITY,), 30, "text read from the real front (or named) window",
                                    "ask the user to click the window when the front app is BAYMAX itself"),
    "desktop.running_apps": ToolMeta(("app_question",), (), 15, "running applications listed by macOS (NSWorkspace)",
                                     "say the list is unavailable; never guess"),
    "desktop.shortcut": ToolMeta(("computer_use",), (ACCESSIBILITY,), 20,
                                 "browser tab change read back for new-tab; other shortcuts reported as sent, not verified",
                                 "only allowlisted shortcuts; nothing that closes, quits, deletes or sends"),
    "files.find_by_name": ToolMeta(("files",), (), 25, "Spotlight paths checked to exist", "say nothing was found; never invent paths"),
    "desktop.new_document": ToolMeta(("computer_use",), (AUTOMATION,), 25,
                                     "a window titled with the new document's name is found",
                                     "say the app reported a document but no window was visible; never claim it without one"),
    "desktop.screenshot": ToolMeta(("screenshot",), (SCREEN_RECORDING,), 30, "the image file exists and is non-empty",
                                   "name the Screen Recording permission"),
    "desktop.": ToolMeta(("computer_use",), (ACCESSIBILITY,), 20, "state read back after the action (cursor, element, text)",
                         "report the exact failed step; no blind retries"),
    "research.": ToolMeta(("research",), (NETWORK,), 180, "report saved with the URLs that were actually fetched",
                          "say that no search/page could be read; keep local answers available"),
    "files.delete": ToolMeta(("files",), (WORKSPACE,), 30, "the file no longer exists", "approval required; nothing outside the workspace"),
    "files.": ToolMeta(("files",), (WORKSPACE,), 30, "file re-read after writing", "refuse paths outside the workspace"),
    "coding.agent": ToolMeta(("coding",), (WORKSPACE,), 900, "tests/compiler run after changes", "report the failing step and output"),
    "coding.": ToolMeta(("coding",), (WORKSPACE,), 300, "compiler/test output checked", "report the failing step and output"),
    "shell.": ToolMeta(("coding",), (WORKSPACE,), 300, "exit code and output captured", "approval for anything outside the allowlist"),
    "creation3d.": ToolMeta(("create",), (WORKSPACE,), 300, "exported model passes structural validation", "report validation errors"),
    "image.": ToolMeta(("create",), (WORKSPACE,), 300, "image file written and decodable", "say no image generator is configured"),
    "documents.": ToolMeta(("create",), (WORKSPACE,), 180, "document written and re-read", "report what could not be written"),
    "simulation.": ToolMeta(("create",), (WORKSPACE,), 120, "simulation output file written", "report invalid parameters"),
    "mail.": ToolMeta(("send_message",), (NETWORK,), 60, "mail client reports the message queued", "approval required before sending"),
    "hardware.": ToolMeta(("hardware",), (), 300, "device responds after the operation", "approval required; report the device error"),
}


def meta_for(name: str) -> ToolMeta | None:
    best = None
    for prefix, meta in _TABLE.items():
        if (name == prefix or (prefix.endswith(".") and name.startswith(prefix))) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, meta)
    return best[1] if best else None
