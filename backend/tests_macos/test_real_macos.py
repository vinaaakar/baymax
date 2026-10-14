"""Real macOS integration tests — run on the Mac only, opt-in, nothing destructive.

    cd ~/Developer/BAYMAX/backend
    BAYMAX_MACOS_INTEGRATION=1 .venv/bin/python -m pytest -v tests_macos

They use the real MacDesktop backend (NSWorkspace, AppleScript/System Events, CGEvent). The TextEdit tests create a
NEW TextEdit document (the first run asks for Automation permission for TextEdit), type Unicode + multi-line text into
it, read it back through Accessibility, and leave the document open for you to close without saving. No files are written, nothing is sent or deleted.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("BAYMAX_MACOS_INTEGRATION") != "1",
    reason="real macOS integration tests: run on the Mac with BAYMAX_MACOS_INTEGRATION=1",
)


@pytest.fixture
def desk():
    from baymax.hostos.desktop import make_desktop
    from baymax.hostos.process import ProcessManager

    return make_desktop(ProcessManager())


_NEW_DOCUMENT = """tell application "TextEdit"
  activate
  set d to make new document
  return name of d
end tell"""


_FRONT_FULLSCREEN = """tell application "System Events"
  set fp to first process whose frontmost is true
  set out to name of fp
  try
    set out to out & " (front window full screen: " & ((value of attribute "AXFullScreen" of window 1 of fp) as text) & ")"
  end try
  return out
end tell"""


async def _front_app_state(desk) -> str:
    """Front app and whether its window is in full screen: a full-screen Terminal is its own desktop Space, and
    Accessibility then sees none of TextEdit's windows."""
    try:
        return (await desk._osascript(_FRONT_FULLSCREEN, timeout=10)).strip()
    except Exception as exc:  # diagnostics only
        return f"unknown ({exc})"


async def _fresh_textedit_document(desk, timeout: float = 8.0):
    """Deterministic TextEdit setup. A new document is made through TextEdit's own scripting (a ⌘N keystroke could land
    in whichever app is in front), then we wait until macOS reports TextEdit frontmost AND the accessibility query is
    reading that document's window AND it exposes a text area. Fails with what was actually seen otherwise."""
    await desk.open_app("TextEdit")
    name = (await desk._osascript(_NEW_DOCUMENT, timeout=15)).strip()
    assert name, "TextEdit did not report the new document's name"
    deadline = time.monotonic() + timeout
    seen: dict = {}
    while True:
        front = await desk.frontmost_app()
        els = await desk.accessibility_elements("TextEdit")
        win = desk.last_ax_window or {}
        areas = [e for e in els if e.role == "AXTextArea"]
        seen = {"frontmost": front, "new document": name, "window inspected": win, "roles": sorted({e.role for e in els})}
        if front == "TextEdit" and win.get("name") == name and areas:
            return name, areas[0], els
        if time.monotonic() > deadline:
            break
        await asyncio.sleep(0.5)
    hint = f" Front app now: {await _front_app_state(desk)}."
    if win.get("windows") == 0:
        hint += (" System Events sees NO TextEdit windows although TextEdit has some: they are on another desktop Space —"
                 " usually because Terminal is in full screen. Exit full screen in Terminal (green button or ⌃⌘F) and run"
                 " again.")
    pytest.fail(f"TextEdit's new document never became the inspected window with a text area: {seen}.{hint}")


async def _focus_or_explain(desk, field):
    """focus_element, and on failure report what macOS showed at that moment (front app, windows visible to
    Accessibility) instead of only the error."""
    from baymax.hostos.desktop import DesktopUnavailable

    try:
        await desk.focus_element("TextEdit", field.role, field.x, field.y)
    except DesktopUnavailable as exc:
        front = await _front_app_state(desk)
        await desk.accessibility_elements("TextEdit")
        pytest.fail(f"focusing the text area at ({field.x:.0f}, {field.y:.0f}) failed: {exc} | macOS said: "
                    f"{getattr(exc, 'detail', '')!r} | front app now: {front} | window now visible to Accessibility: "
                    f"{desk.last_ax_window}")


def test_permissions_are_reported(desk):
    ax = desk.accessibility_trusted()
    print("Accessibility trusted:", ax, "| Screen Recording:", desk.screen_recording_allowed())
    assert ax is not None, "PyObjC (ApplicationServices) is missing: pip install -e '.[macos]'"


async def test_real_app_discovery(desk):
    running, installed = await desk.list_apps()
    names = {a.name for a in installed}
    assert "Finder" in {a.name for a in running} and "Safari" in names, "NSWorkspace / Info.plist discovery failed"
    from baymax.hostos.apps import resolve_app

    assert resolve_app("vs code", running, installed).status in ("resolved", "unknown")
    assert resolve_app("Hello", running, installed).status != "resolved"


async def test_frontmost_app_and_window(desk):
    w = await desk.active_window()
    print("front:", w.app, "| title:", w.title, w.title_note)
    assert w.app and w.width > 0


async def test_type_unicode_multiline_into_textedit_and_read_back(desk):
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility to the terminal running pytest first")
    _, field, _ = await _fresh_textedit_document(desk)
    await _focus_or_explain(desk, field)  # raises if focus is not confirmed
    # Lines start with a digit: macOS "Capitalize words automatically" (on by default) would otherwise turn a typed
    # "second" into "Second" after the line break - the app's change, not BAYMAX's. The comparison stays exact.
    text = "BAYMAX test ✓ Grüße 東京 👋\n2nd line — ünïcödé\n3rd line."
    await desk.type_text("TextEdit", text)
    await asyncio.sleep(0.5)
    got = await desk.element_value("TextEdit", field.role, field.x, field.y)
    # Cocoa may store Shift+Return as U+2028 (line separator); everything else must match exactly.
    normalised = got.replace("\u2028", "\n").replace("\r\n", "\n").replace("\r", "\n")
    assert normalised == text, f"read back {got!r}"
    print("Typed and read back exactly. Close the TextEdit window WITHOUT saving.")


async def test_focus_on_a_missing_element_is_reported(desk):
    from baymax.hostos.desktop import DesktopUnavailable

    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    await desk.open_app("TextEdit")
    await asyncio.sleep(0.5)
    with pytest.raises(DesktopUnavailable) as exc:
        await desk.focus_element("TextEdit", "AXTextArea", -5000, -5000)
    assert "not found" in str(exc.value) or "no window I can see" in str(exc.value)


async def test_pointer_moves_where_asked(desk):
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    x0, y0 = await desk.cursor_position()
    d = (await desk.displays())[0]
    tx, ty = d.x + d.width / 2, d.y + d.height / 2
    await desk.move_cursor(tx, ty)
    await asyncio.sleep(0.1)
    x, y = await desk.cursor_position()
    await desk.move_cursor(x0, y0)  # put it back
    assert abs(x - tx) < 2 and abs(y - ty) < 2


async def test_ax_dump_reports_enabled_state_and_pointer_reaches_a_real_element_centre(desk):
    """Semantic pointing on the real Mac: a TextEdit control from the live accessibility tree, the pointer moved to
    its frame centre (x + w/2, y + h/2, global points — correct on Retina and multi-display setups) and read back.
    Nothing is clicked."""
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    _, _, els = await _fresh_textedit_document(desk)  # the elements read while the document was verified in front
    controls = [e for e in els if e.role in ("AXButton", "AXTextArea", "AXCheckBox", "AXPopUpButton")]
    assert controls, f"no controls in TextEdit (roles: {sorted({e.role for e in els})})"
    assert any(e.enabled is not None for e in controls), "AXEnabled was not reported for any control"
    target = next((e for e in controls if e.role == "AXTextArea"), controls[0])
    cx, cy = target.center
    displays = await desk.displays()
    assert any(d.contains(cx, cy) for d in displays), f"centre {cx, cy} is on no display {displays}"
    x0, y0 = await desk.cursor_position()
    await desk.move_cursor(cx, cy)
    await asyncio.sleep(0.1)
    x, y = await desk.cursor_position()
    await desk.move_cursor(x0, y0)
    print(f"pointed at {target.role} “{target.label}” centre ({cx:.0f}, {cy:.0f}); pointer read back ({x:.0f}, {y:.0f})")
    assert abs(x - cx) < 2 and abs(y - cy) < 2


async def test_typing_stops_when_input_is_cancelled(desk):
    from baymax.hostos.desktop import InputCancelled

    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    _, field, _ = await _fresh_textedit_document(desk)
    await _focus_or_explain(desk, field)
    desk.typing_interval_s = 0.03
    job = asyncio.create_task(desk.type_text("TextEdit", "stop me " * 60))
    await asyncio.sleep(0.6)
    desk.cancel_input()  # what Emergency Stop calls
    with pytest.raises(InputCancelled):
        await job
    got = await desk.element_value("TextEdit", field.role, field.x, field.y)
    print(f"typed {len(got)} of {len('stop me ' * 60)} characters before the stop. Close the window WITHOUT saving.")
    assert 0 < len(got) < len("stop me " * 60)
