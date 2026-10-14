"""Real macOS integration tests — run on the Mac only, opt-in, nothing destructive.

    cd ~/Developer/BAYMAX/backend
    BAYMAX_MACOS_INTEGRATION=1 .venv/bin/python -m pytest -v tests_macos

They use the real MacDesktop backend (NSWorkspace, AppleScript/System Events, CGEvent). The typing test opens a NEW
TextEdit document, types Unicode + multi-line text into it, reads it back through Accessibility, and leaves the
document open for you to close without saving. No files are written, nothing is sent or deleted.
"""

from __future__ import annotations

import asyncio
import os
import sys

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
    await desk.open_app("TextEdit")
    await asyncio.sleep(1.0)
    await desk.press_keys("TextEdit", "n", ("command",))  # new, empty document
    await asyncio.sleep(1.0)
    els = await desk.accessibility_elements("TextEdit")
    areas = [e for e in els if e.role == "AXTextArea"]
    assert areas, f"no AXTextArea in TextEdit's front window (found roles: {sorted({e.role for e in els})})"
    field = areas[0]
    await desk.focus_element("TextEdit", field.role, field.x, field.y)  # raises if focus is not confirmed
    text = "BAYMAX test ✓ Grüße 東京 👋\nsecond line — ünïcödé\nthird line."
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
    assert "not found" in str(exc.value) or "no open window" in str(exc.value)


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
