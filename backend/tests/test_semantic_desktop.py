"""Semantic desktop automation: pointing (physical pointer + avatars), form fields, cancellable typing, E-stop, and the
pointing protocol.

Simulated: the macOS desktop (FakeDesktop, or MacDesktop with a fake Quartz/osascript). Real: router, assistant,
tools, task manager, event bus, protocol validation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from baymax.assistant import Assistant
from baymax.hostos.desktop import InputCancelled, MacDesktop
from baymax.protocol import messages as proto
from baymax.screen.elements import parse_ax_dump
from baymax.tasks.models import TaskStatus

T = "\t"
ROOT = Path(__file__).resolve().parents[2]


def ax(role, name, x, y, w, h, desc="", value="", enabled="", ident=""):
    return T.join([role, name, desc, value, str(x), str(y), str(w), str(h), enabled, ident])


CHAT = "\n".join([ax("AXTextArea", "", 300, 700, 600, 40, desc="Message"),
                  ax("AXButton", "Send", 920, 705, 40, 30, enabled="1", ident="send-button"),
                  ax("AXButton", "Attach", 250, 705, 30, 30, enabled="0")])
FORM = "\n".join([ax("AXStaticText", "", 100, 200, 80, 20, value="Full name"),
                  ax("AXTextField", "", 200, 196, 300, 28),
                  ax("AXStaticText", "", 100, 260, 80, 20, value="Email"),
                  ax("AXTextField", "", 200, 256, 300, 28),
                  ax("AXButton", "Submit", 200, 320, 90, 30, enabled="1")])


@pytest.fixture
async def assistant(services):
    a = Assistant(services)
    a.start()
    yield a
    await a.stop()


async def _gaze(sub, timeout=5.0):
    async def run():
        while True:
            ev = await sub.get()
            if ev.type == "gaze.target":
                return ev.payload
    return await asyncio.wait_for(run(), timeout)


# ------------------------------------------------------------------ Feature A: richer accessibility attributes
def test_ax_dump_carries_enabled_state_and_identifier_and_old_dumps_still_parse():
    els = parse_ax_dump(CHAT, "Chat")
    send = next(e for e in els if e.label == "Send")
    attach = next(e for e in els if e.label == "Attach")
    assert send.enabled is True and send.identifier == "send-button" and attach.enabled is False
    assert send.center == (940, 720)  # x + w/2, y + h/2 in global points
    assert send.to_dict()["identifier"] == "send-button"
    old = parse_ax_dump(T.join(["AXButton", "OK", "", "", "10", "10", "50", "20"]), "X")  # 8-column dump
    assert old[0].enabled is None and old[0].identifier == "" and "enabled" not in old[0].to_dict()


# ------------------------------------------------------------------ Feature E/F: "Point to the Send button"
async def test_point_to_send_moves_the_real_pointer_and_both_avatars_but_never_clicks(services, assistant, fake_desktop):
    fake_desktop.front = "Chat"
    fake_desktop.ax_dump["Chat"] = CHAT
    sub = services.bus.subscribe()
    r = await assistant.handle_text("Point to the Send button")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    assert ("move", 940.0, 720.0) in fake_desktop.calls and fake_desktop.cursor == (940.0, 720.0)
    assert not [c for c in fake_desktop.calls if c[0] in ("click", "press", "keys", "return", "type")]
    assert "haven't clicked" in t.result["summary"] and t.result["data"]["clicked"] is False
    g = await _gaze(sub)
    assert g["kind"] == "element" and g["label"] == "Send" and g["pointId"] and isinstance(g["seq"], int)
    phone = proto.make("gaze.target", g).payload  # what the iPhone receives over the authenticated channel
    assert phone["pointId"] == g["pointId"] and phone["seq"] == g["seq"] and "gx" not in phone
    assert 0 <= phone["vx"] <= 1 and 0 <= phone["vy"] <= 1


async def test_pointing_ids_are_unique_and_sequence_increases(services, assistant, fake_desktop):
    fake_desktop.front = "Chat"
    fake_desktop.ax_dump["Chat"] = CHAT
    sub = services.bus.subscribe()
    seen = []
    for _ in range(2):
        r = await assistant.handle_text("point at the Send button")
        await services.tasks.wait(r["taskId"], 5)
        seen.append(await _gaze(sub))
        assistant._recent_starts.clear()  # the duplicate guard would (correctly) merge identical back-to-back requests
    assert seen[0]["pointId"] != seen[1]["pointId"] and seen[1]["seq"] > seen[0]["seq"]


async def test_pointing_at_something_missing_moves_nothing(services, assistant, fake_desktop):
    fake_desktop.front = "Chat"
    fake_desktop.ax_dump["Chat"] = CHAT
    r = await assistant.handle_text("point to the Publish button")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and "couldn't find" in t.result["summary"]
    assert not [c for c in fake_desktop.calls if c[0] in ("move", "click")]


async def test_disabled_button_is_not_pressed(services, assistant, fake_desktop):
    fake_desktop.front = "Chat"
    fake_desktop.ax_dump["Chat"] = CHAT
    r = await assistant.handle_text("click the Attach button")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and "disabled" in t.result["summary"]
    assert not [c for c in fake_desktop.calls if c[0] in ("press", "click")]


# ------------------------------------------------------------------ Feature D/E: form filling (typing only)
async def test_form_field_found_by_its_label_typed_and_read_back_without_submitting(services, assistant, fake_desktop):
    fake_desktop.front = "Safari"
    fake_desktop.running.add("Safari")
    fake_desktop.ax_dump["Safari"] = FORM
    r = await assistant.handle_text("Fill the email field with ana@example.com")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    assert ("focus", "Safari", "AXTextField", 200, 256) in fake_desktop.calls  # the field next to "Email", not "Full name"
    assert ("type", "Safari", "ana@example.com") in fake_desktop.calls
    assert fake_desktop.field_raw[("Safari", "AXTextField", 200, 256)] == "ana@example.com"
    assert "haven't submitted" in t.result["summary"]
    assert not [c for c in fake_desktop.calls if c[0] in ("press", "click", "return")]


async def test_form_field_that_does_not_exist_types_nothing(services, assistant, fake_desktop):
    fake_desktop.front = "Safari"
    fake_desktop.ax_dump["Safari"] = FORM
    r = await assistant.handle_text("fill the phone number field with 555 0100")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and "didn't type anything" in t.result["summary"]
    assert not [c for c in fake_desktop.calls if c[0] == "type"]


async def test_form_field_unicode_multiline_read_back_is_exact(services, fake_desktop):
    from baymax.tasks.models import PlanStep

    fake_desktop.front = "Notes"
    fake_desktop.ax_dump["Notes"] = ax("AXTextArea", "Body", 50, 50, 500, 300)
    text = "Café 👋🏽 naïve\nline two — ✓"
    task = services.tasks.submit("desktop", "type", "fill body", [PlanStep("type", "screen.type_into", {"field": "body", "text": text})])
    t = await services.tasks.wait(task.id, 5)
    assert t.status is TaskStatus.COMPLETED and t.verified and "matched exactly" in t.result["verification"]


# ------------------------------------------------------------------ Feature E: switch apps and read
async def test_switch_to_textedit_and_read_it(services, assistant, fake_desktop):
    from baymax.hostos.apps import AppInfo

    fake_desktop.installed.append(AppInfo("TextEdit", "", "com.apple.TextEdit"))
    r = await assistant.handle_text("Switch to TextEdit and read it")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED, t.error
    assert ("open_app", "TextEdit") in fake_desktop.calls and ("read_window", "TextEdit") in fake_desktop.calls


# ------------------------------------------------------------------ Feature C/H: typing cadence and cancellation
class _FakeQuartz:
    kCGHIDEventTap = 0
    kCGEventFlagMaskShift = 1

    def __init__(self):
        self.events: list[tuple[str, bool]] = []

    def CGEventCreateKeyboardEvent(self, _src, code, down):
        return {"code": code, "down": down, "text": ""}

    def CGEventKeyboardSetUnicodeString(self, ev, _n, s):
        ev["text"] = s

    def CGEventSetFlags(self, ev, flags):
        ev["flags"] = flags

    def CGEventPost(self, _tap, ev):
        self.events.append((ev["text"] or str(ev["code"]), ev["down"]))


def _mac_with_fake_quartz():
    d = MacDesktop.__new__(MacDesktop)
    d._quartz = _FakeQuartz()

    async def osa(*_a, **_k):
        return "ok"

    d._osascript = osa  # activation only
    d.accessibility_trusted = lambda: True
    return d


async def test_typing_cadence_is_configurable():
    d = _mac_with_fake_quartz()
    d.typing_interval_s = 0.0
    await d.type_text("TextEdit", "abcdefghij" * 3 + "\nend")
    typed = "".join(t for t, down in d._quartz.events if down and t != "36")
    assert typed == "abcdefghij" * 3 + "end" and ("36", True) in d._quartz.events  # newline = Shift+Return


async def test_emergency_stop_interrupts_typing_mid_text_with_no_key_left_down():
    d = _mac_with_fake_quartz()
    d.typing_interval_s = 0.02
    job = asyncio.create_task(d.type_text("TextEdit", "x" * 400))  # 0.2 s activation, then 40 chunks ≈ 0.8 s
    await asyncio.sleep(0.35)
    d.cancel_input()
    with pytest.raises(InputCancelled):
        await job
    ev = d._quartz.events
    assert 0 < len(ev) < 80 and len(ev) % 2 == 0 and ev[-1][1] is False  # every key down has its key up


async def test_stop_during_activation_types_nothing():
    d = _mac_with_fake_quartz()
    job = asyncio.create_task(d.type_text("TextEdit", "hello"))
    await asyncio.sleep(0.05)  # still inside the activation pause
    d.cancel_input()
    with pytest.raises(InputCancelled):
        await job
    assert d._quartz.events == []


async def test_estop_cancels_input_clears_pointing_and_stops_tasks(services, fake_desktop):
    sub = services.bus.subscribe()
    gen = fake_desktop._input_gen
    services.estop.engage("test")
    assert fake_desktop._input_gen == gen + 1
    g = await _gaze(sub)
    assert g["kind"] == "clear"
    services.estop.reset()


async def test_typing_interval_setting_is_applied(tmp_path, fake_desktop):
    from baymax.config import load_settings
    from baymax.security.secrets import SecretStore
    from baymax.services import build_services

    settings = load_settings(tmp_path / "d", {"workspace_dir": str(tmp_path / "ws"), "security": {"typing_interval_ms": 40}})
    settings.ensure_dirs()
    s = build_services(settings, desktop=fake_desktop, secrets=SecretStore(settings.data_dir, use_keychain=False))
    assert fake_desktop.typing_interval_s == pytest.approx(0.04)
    await s.close()


# ------------------------------------------------------------------ Feature F: protocol stays identical everywhere
def test_pointing_fields_match_in_python_json_and_swift():
    spec = json.loads((ROOT / "protocol/baymax-protocol.json").read_text())
    gaze = next(v for k, v in _walk(spec) if k == "gaze.target")
    assert {"pointId": "str", "seq": "int"}.items() <= gaze["optional"].items()
    assert {"pointId": "str", "seq": "int"}.items() <= proto.SERVER_TO_CLIENT["gaze.target"][1].items()
    swift = (ROOT / "ios/BaymaxKit/Sources/BaymaxKit/ProtocolMessages.swift").read_text()
    assert '"pointId": .str, "seq": .int' in swift
    with pytest.raises(ValueError):
        proto.make("gaze.target", {"kind": "element", "x": 0.5, "y": 0.5, "seq": "1"})


def _walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v)


# ------------------------------------------------------------------ routing
@pytest.mark.parametrize("q,tool,args", [
    ("Point to the Send button", "screen.point_at", {"query": "send button"}),
    ("move the mouse to the search field", "screen.point_at", {"query": "search field"}),
    ("look at the Run button", "screen.find_element", {"query": "run button"}),
    ("move the cursor to 100, 200", "desktop.move_cursor", {"x": 100.0, "y": 200.0}),
    ("switch to TextEdit", "apps.open", {"app": "TextEdit"}),
    ('type "hello" into the Name field', "screen.type_into", {"field": "name field", "text": "hello", "replace": False}),
    ("Go to the Python documentation and find information about dictionaries", "research.web",
     {"query": "python official documentation dictionaries"}),
])
def test_semantic_requests_route_to_the_right_tool(q, tool, args):
    from baymax.creation3d.classifier import RequestClassifier
    from baymax.creation3d.registry import GeneratorRegistry
    from baymax.intent.router import IntentRouter

    i = IntentRouter(RequestClassifier(GeneratorRegistry())).route(q)
    assert i.kind == "tool" and i.tool == tool and i.args == args


# ------------------------------------------------------------------ which window the accessibility scripts read
class _Res:
    def __init__(self, stdout):
        self.ok, self.stdout, self.stderr, self.timed_out = True, stdout, "", False


class _DumpRunner:
    def __init__(self, stdout):
        self.stdout = stdout

    async def run(self, argv, timeout=20):
        return _Res(self.stdout)


async def test_locate_reports_the_window_it_read_and_the_header_is_not_an_element():
    from baymax.hostos.desktop import parse_window_header

    dump = "#window\t6\tmain\tUntitled 6\tAXStandardWindow\n" + ax("AXTextArea", "", 0, 52, 600, 400) + "\n" \
           + ax("AXMenuBarItem", "File", 40, 0, 36, 24) + "\n"
    d = MacDesktop.__new__(MacDesktop)
    d.p = _DumpRunner(dump)
    els = await d.accessibility_elements("TextEdit")
    assert [e.role for e in els] == ["AXTextArea", "AXMenuBarItem"]
    assert d.last_ax_window == {"windows": 6, "chosen": "main", "name": "Untitled 6", "subrole": "AXStandardWindow"}
    assert parse_window_header("#window\t0\tnone\t\t\n" + ax("AXMenuBarItem", "File", 40, 0, 36, 24))["windows"] == 0
    assert parse_window_header(ax("AXButton", "OK", 1, 1, 9, 9)) is None  # older dumps have no header


def test_locate_focus_and_read_back_inspect_the_same_chosen_window():
    from baymax.hostos import desktop as dk

    for script in (dk._AS_LOCATE, dk._AS_FOCUS, dk._AS_VALUE):
        assert "on pickWindow(appName)" in script and "my pickWindow(appName)" in script
        assert "entire contents of window 1" not in script  # never a blind "window 1" (could be a panel or nothing)
        assert '"AXMain"' in script and '"AXFocusedWindow"' in script


# Words that are commands/properties inside `tell application "System Events"` (or AppleScript itself). Using one as a
# variable name doesn't compile on macOS ("Expected expression but found command name", -2741) - this happened with
# `pick`. AppleScript can't be compiled on Linux CI, so check the variable names instead. ("set frontmost to true" is a
# deliberate property assignment, so frontmost is not listed.)
_SE_TERMS = {"pick", "click", "select", "perform", "keystroke", "key", "delay", "name", "value", "position", "size", "role",
             "description", "title", "focused", "enabled", "selected", "window", "windows", "process", "item", "text",
             "contents", "action", "attribute", "menu", "button", "file", "folder", "disk", "application", "id", "class",
             "properties", "visible", "subrole", "help", "entire", "count", "exists", "quit", "open", "close",
             "save", "print", "make", "move", "duplicate", "delete", "increment", "decrement", "confirm", "cancel", "show",
             "raise", "result", "error", "return", "minimized", "scroll", "list", "record", "reference"}


def _applescripts():
    import re as _re

    from baymax.hostos import desktop as dk

    scripts = {n: v for n, v in vars(dk).items() if n.startswith("_AS_") and isinstance(v, str)}
    diag = (ROOT / "backend/tests_macos/diagnose_ax.py").read_text()
    for i, m in enumerate(_re.finditer(r'"""(on run argv.*?)"""', diag, _re.S)):
        scripts[f"diagnose_ax#{i}"] = m.group(1)
    return scripts


def test_applescript_variables_never_reuse_system_events_terms():
    import re as _re

    scripts = _applescripts()
    assert "_AS_LOCATE" in scripts and any(k.startswith("diagnose_ax") for k in scripts)
    bad = []
    for name, src in scripts.items():
        for var in _re.findall(r"\bset\s+(\w+)\s+to\b|\brepeat with\s+(\w+)\s+(?:in|from)\b|\bon\s+\w+\((\w+)\)", src):
            v = next(x for x in var if x)
            if v.lower() in _SE_TERMS:
                bad.append((name, v))
    assert not bad, f"AppleScript variables named like System Events terms (won't compile on macOS): {bad}"
