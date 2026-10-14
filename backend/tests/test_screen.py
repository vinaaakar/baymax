"""Screen understanding: element matching, coordinate conversion, tools, gaze sync, permissions.

The accessibility dumps below are in the exact format produced by the macOS dumper
(hostos/desktop.py _AS_LOCATE). Real-Mac behaviour still needs the manual check in docs.
"""

from __future__ import annotations

import asyncio

import pytest

from baymax.assistant import Assistant
from baymax.character.state_machine import CharacterState
from baymax.screen.elements import Display, ScreenElement, parse_ax_dump, resolve, to_gaze, vision_box_to_global
from baymax.server.hub import DeviceHub  # noqa: F401  (import check)
from baymax.tasks.models import TaskStatus

T = "\t"


def ax_line(role, name, x, y, w, h, desc="", value=""):
    return T.join([role, name, desc, value, str(x), str(y), str(w), str(h)])


VSCODE = "\n".join([
    ax_line("AXMenuBarItem", "File", 40, 0, 36, 24),
    ax_line("AXMenuBarItem", "Run", 300, 0, 36, 24),
    ax_line("AXButton", "", 1290, 40, 26, 26, desc="Run Python File"),
    ax_line("AXButton", "Run and Debug", 8, 210, 40, 40),
    ax_line("AXStaticText", "", 60, 300, 120, 18, value="cursor.py"),
    ax_line("AXTextField", "Search", 500, 6, 400, 22),
    ax_line("AXButton", "Hidden", 10, 10, 0, 0),
])

PRIMARY = Display(1, 0, 0, 1512, 982, 2.0)
LEFT = Display(2, -1920, -98, 1920, 1080, 1.0)


def test_parse_ax_dump_keeps_visible_labelled_elements():
    els = parse_ax_dump(VSCODE, "Code")
    labels = [(e.role, e.label) for e in els]
    assert ("AXButton", "Run Python File") in labels  # description used when the name is empty
    assert ("AXStaticText", "cursor.py") in labels      # value used for static text
    assert all(e.label != "Hidden" for e in els)        # zero-size elements dropped
    assert all(e.source == "ax" and e.app == "Code" for e in els)


def test_vision_boxes_map_to_global_points_on_any_display():
    # Vision: normalised, origin bottom-left. A box at the top-left corner of the left-hand external display:
    x, y, w, h = vision_box_to_global((0.0, 0.9, 0.1, 0.1), LEFT)
    assert (x, y, w, h) == pytest.approx((-1920, -98, 192, 108))
    # Retina scale does not matter: boxes are fractions of the display, mapped through bounds in points.
    x, y, w, h = vision_box_to_global((0.5, 0.5, 0.02, 0.02), PRIMARY)
    assert (x, y) == pytest.approx((756, 491 - 0.02 * 982))


def test_resolve_found_ambiguous_ordinal_and_not_found():
    els = parse_ax_dump(VSCODE, "Code")
    r = resolve("run and debug button", els)
    assert r.status == "found" and r.element.label == "Run and Debug"
    r = resolve("search field", els)
    assert r.status == "found" and r.element.role == "AXTextField"
    amb = parse_ax_dump("\n".join([ax_line("AXButton", "Run", 100, 50, 40, 20), ax_line("AXButton", "Run", 900, 800, 40, 20)]), "X")
    r = resolve("run button", amb)
    assert r.status == "ambiguous" and len(r.candidates) == 2
    assert "top" in r.describe_candidates([PRIMARY]) and "bottom" in r.describe_candidates([PRIMARY])
    r = resolve("second run button", amb)
    assert r.status == "found" and r.element.x == 900
    assert resolve("deploy button", els).status == "not_found"


def test_role_hint_and_duplicate_collapse():
    els = [ScreenElement("AXStaticText", "Run", 100, 100, 30, 16), ScreenElement("AXButton", "Run", 400, 100, 40, 20)]
    r = resolve("run button", els)
    assert r.status == "found" and r.element.role == "AXButton"
    # The same control seen twice (accessibility + OCR text inside it) is one candidate, not an ambiguity.
    dup = [ScreenElement("AXButton", "Save", 100, 100, 60, 24), ScreenElement("text", "Save", 104, 103, 50, 18, "ocr", 0.98)]
    assert resolve("save", dup).status == "found"


def test_gaze_payload_uses_real_display_geometry():
    el = ScreenElement("AXButton", "Run", 1290, 40, 26, 26)
    g = to_gaze(el, [PRIMARY, LEFT])
    assert g["displayId"] == 1 and g["gx"] == 1303 and g["gy"] == 53
    assert g["vx"] == pytest.approx(1303 / 1512, abs=1e-4) and g["vy"] == pytest.approx(53 / 982, abs=1e-4)
    left_el = ScreenElement("text", "Build", -960, 400, 50, 20, "ocr", 0.9)
    g = to_gaze(left_el, [PRIMARY, LEFT])
    assert g["displayId"] == 2 and g["vx"] < 0  # left of the primary display -> phone looks left
    assert g["x"] == pytest.approx((-935 + 1920) / 1920, abs=1e-4)
    with pytest.raises(ValueError):
        to_gaze(ScreenElement("AXButton", "Gone", 9000, 9000, 10, 10), [PRIMARY])  # stale: display unplugged


@pytest.fixture
async def assistant(services):
    a = Assistant(services)
    a.start()
    yield a
    await a.stop()


async def _events(sub, types, timeout=5.0):
    got = {}

    async def run():
        while not set(types) <= set(got):
            ev = await sub.get()
            if ev.type in types and ev.type not in got:
                got[ev.type] = ev.payload
    await asyncio.wait_for(run(), timeout)
    return got


async def test_look_at_run_button_points_both_avatars(services, assistant, fake_desktop):
    fake_desktop.front = "Code"
    fake_desktop.ax_dump["Code"] = VSCODE
    sub = services.bus.subscribe()
    r = await assistant.handle_text("Baymax, look at the Run and Debug button")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and t.verified
    ev = await _events(sub, ["gaze.target"])
    g = ev["gaze.target"]
    assert g["kind"] == "element" and g["label"] == "Run and Debug" and g["gx"] == 28 and g["gy"] == 230
    # The phone's version of the message is schema-validated (gx/gy are Mac-local and stripped).
    from baymax.protocol import messages as proto
    phone = proto.make("gaze.target", g).payload
    assert "gx" not in phone and phone["vx"] == g["vx"]
    assert ("ocr",) not in fake_desktop.calls  # accessibility answered; no screen capture needed


async def test_ambiguous_target_asks_and_shows_confused(services, assistant, fake_desktop):
    fake_desktop.front = "Code"
    fake_desktop.ax_dump["Code"] = "\n".join([ax_line("AXButton", "Run", 100, 50, 40, 20), ax_line("AXButton", "Run", 900, 600, 40, 20)])
    r = await assistant.handle_text("look at the Run button")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and "Which one" in (t.error or "")
    assert services.character.state is CharacterState.CONFUSED


async def test_ocr_fallback_when_accessibility_has_no_match(services, assistant, fake_desktop):
    fake_desktop.front = "Game"
    fake_desktop.ax_dump["Game"] = ""  # canvas apps expose nothing
    fake_desktop.ocr = [ScreenElement("text", "Start Mission", 600, 400, 140, 30, "ocr", 0.97)]
    sub = services.bus.subscribe()
    r = await assistant.handle_text("where is the start mission button")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED, t.error
    assert "text recognition" in t.result["summary"]
    assert (await _events(sub, ["gaze.target"]))["gaze.target"]["label"] == "Start Mission"


async def test_missing_permissions_are_explained_not_bypassed(services, assistant, fake_desktop):
    fake_desktop.accessibility = False
    fake_desktop.screen_recording = False
    r = await assistant.handle_text("look at the Run button")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED
    msg = t.result["summary"]
    assert "Accessibility" in msg and "Screen" in msg and "not pointing" in msg


async def test_not_found_is_honest(services, assistant, fake_desktop):
    fake_desktop.front = "Code"
    fake_desktop.ax_dump["Code"] = VSCODE
    r = await assistant.handle_text("look at the Deploy button")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and "couldn't find" in t.result["summary"] and "6 accessibility elements" in t.result["summary"]


async def test_click_presses_via_accessibility_and_verifies_change(services, assistant, fake_desktop):
    fake_desktop.front = "Code"
    fake_desktop.ax_dump["Code"] = VSCODE
    fake_desktop.ax_after_press["Code"] = VSCODE + "\n" + ax_line("AXStaticText", "", 300, 700, 400, 18, value="Debug console")
    r = await assistant.handle_text("click the Run and Debug button")
    t = await services.tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    assert ("press", "Code", "AXButton", "Run and Debug") in fake_desktop.calls


async def test_click_without_visible_change_is_not_claimed_verified(services, assistant, fake_desktop):
    fake_desktop.front = "Code"
    fake_desktop.ax_dump["Code"] = VSCODE
    r = await assistant.handle_text("click the Run and Debug button")
    t = await services.tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.COMPLETED and not t.verified
    assert "no visible change" in t.result["summary"]


async def test_click_never_targets_ocr_only_text(services, assistant, fake_desktop):
    fake_desktop.front = "Game"
    fake_desktop.ax_dump["Game"] = ""
    fake_desktop.ocr = [ScreenElement("text", "Start", 600, 400, 60, 30, "ocr", 0.97)]
    r = await assistant.handle_text("click the Start button")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.FAILED and "won't click it blindly" in t.result["summary"]
    assert not any(c[0] in ("press", "click") for c in fake_desktop.calls)


def test_prompt_box_matches_by_partial_label():
    els = parse_ax_dump("\n".join([ax_line("AXTextArea", "", 400, 700, 600, 40, desc="Message ChatGPT"),
                                   ax_line("AXButton", "Send prompt", 1010, 705, 30, 30)]), "Safari")
    r = resolve("ChatGPT prompt box", els)
    assert r.status == "found" and r.element.role == "AXTextArea"


async def test_active_window_title_and_pointing(services, assistant, fake_desktop):
    fake_desktop.front = "Code"
    sub = services.bus.subscribe()
    r = await assistant.handle_text("Read the title of the active window.")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and "README.md" in t.result["summary"]
    fake_desktop.window_title = None
    r = await assistant.handle_text("which app am I using")
    t = await services.tasks.wait(r["taskId"], 5)
    assert "can't read the window title" in t.result["summary"]  # honest when the title is unavailable
    r = await assistant.handle_text("Point toward the window I am using.")
    t = await services.tasks.wait(r["taskId"], 5)
    assert t.status is TaskStatus.COMPLETED and t.verified
    g = (await _events(sub, ["gaze.target"]))["gaze.target"]
    assert g["gx"] == 550 and g["gy"] == 380  # centre of the real window frame (100,80 900x600)


async def test_what_can_you_do_with_the_current_screen_inspects_it_for_real(services, assistant, fake_desktop):
    fake_desktop.front = "Code"
    fake_desktop.ax_dump["Code"] = VSCODE
    r = await assistant.handle_text("What can you do with the current screen?")
    t = await services.tasks.wait(r["taskId"], 5)
    s = t.result["summary"]
    assert t.status is TaskStatus.COMPLETED and t.verified
    assert "You're in Code" in s and "“Run Python File” button" in s and "type into a field" in s  # real controls, not a generic list
    assert ("ax", "Code") in fake_desktop.calls


async def test_screen_inspection_is_honest_without_accessibility(services, assistant, fake_desktop):
    fake_desktop.front = "Safari"
    fake_desktop.accessibility = False
    r = await assistant.handle_text("what can you help me with on this page")
    t = await services.tasks.wait(r["taskId"], 5)
    s = t.result["summary"]
    assert "You're in Safari" in s and "Accessibility" in s and "can't inspect its controls" in s
