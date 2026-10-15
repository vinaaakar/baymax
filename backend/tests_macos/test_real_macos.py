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
        if front != "TextEdit":  # focus went elsewhere (or never arrived): bring TextEdit forward again
            from baymax.hostos.desktop import _AS_ACTIVATE

            await desk._osascript(_AS_ACTIVATE, "TextEdit", timeout=10)
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


# ------------------------------------------------------------------ the full tool path (task manager + real Mac)
@pytest.fixture
async def real_services(tmp_path, desk):
    """BAYMAX services on the real MacDesktop (no model needed: plans are submitted directly, as the router would)."""
    from baymax.config import load_settings
    from baymax.security.secrets import SecretStore
    from baymax.services import build_services

    settings = load_settings(tmp_path / "data", {"workspace_dir": str(tmp_path / "ws"), "network": {"local_port": 0},
                                                 "voice": {"tts_engine": "none"}, "research": {"local_only": True}})
    settings.ensure_dirs()
    s = build_services(settings, desktop=desk, secrets=SecretStore(settings.data_dir, use_keychain=False))
    yield s
    await s.close()


async def _run(s, *steps, timeout=60):
    from baymax.tasks.models import PlanStep

    task = s.tasks.submit("desktop", "macos-test", "macos integration", [PlanStep(t, t, a) for t, a in steps])
    return await s.tasks.wait(task.id, timeout)


async def test_create_textedit_document_type_three_lines_and_read_back_through_the_tools(desk, real_services):
    """'Create a new TextEdit document' then 'Type three separate lines into TextEdit', as routed from the iPhone."""
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    from baymax.tasks.models import TaskStatus

    t = await _run(real_services, ("desktop.new_document", {"app": "TextEdit"}))
    assert t.status is TaskStatus.COMPLETED and t.verified, t.result
    doc = t.result["data"]["document"]
    t = await _run(real_services, ("screen.type_into", {"field": "text area", "app": "TextEdit",
                                                        "text": "Line 1\nLine 2 — ünïcödé ✓\nLine 3 👋"}))
    assert t.status is TaskStatus.COMPLETED and t.verified, t.result
    assert "matched exactly" in t.result["verification"]
    w = await desk.active_window()
    print(f"typed into {doc}; front window: {w.app} “{w.title}”. Close it WITHOUT saving.")
    assert w.app == "TextEdit"


async def test_identify_the_active_window_through_the_tool(desk, real_services):
    """The tool must name the app macOS reports as frontmost at that moment (with its real window title). macOS may hand
    focus back to Terminal right after the test activates TextEdit, so the expectation is read live, before and after
    the tool runs, and the check is repeated if focus moved during it."""
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    from baymax.hostos.desktop import _AS_ACTIVATE
    from baymax.tasks.models import TaskStatus

    name, _, _ = await _fresh_textedit_document(desk)
    for _attempt in range(3):
        await desk._osascript(_AS_ACTIVATE, "TextEdit", timeout=10)
        await asyncio.sleep(0.5)
        before = await desk.frontmost_app()
        t = await _run(real_services, ("desktop.active_window", {}))
        after = await desk.frontmost_app()
        if before == after:
            break
    print(f"front before/after: {before}/{after} | {t.result['summary']}")
    assert before == after, f"[active-window v2] focus kept changing while the tool ran ({before} -> {after})"
    assert t.status is TaskStatus.COMPLETED and t.result["data"]["app"] == before, (
        f"[active-window v2] macOS front app before AND after the tool: {before}; the tool said: "
        f"{t.result.get('data', {}).get('app')} ({t.result.get('summary', '')[:120]})")
    assert "title" in t.result["data"], t.result  # the real title, or None with the reason in the summary
    if before == "TextEdit":
        assert name in t.result["summary"] or "can't read the window title" in t.result["summary"]


async def test_pointer_to_the_center_of_the_main_display_through_the_tool(desk, real_services):
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    from baymax.tasks.models import TaskStatus

    x0, y0 = await desk.cursor_position()
    t = await _run(real_services, ("desktop.move_cursor", {"position": "center"}))
    await desk.move_cursor(x0, y0)  # put it back
    assert t.status is TaskStatus.COMPLETED and t.verified, t.result


async def test_reading_an_interface_is_bounded_in_time(desk):
    """The ChatGPT timeout: reading a big app's interface must finish inside the hard limit (and say if it was cut)."""
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    for app in ("Finder", "TextEdit"):
        await desk.open_app(app)
        start = time.monotonic()
        els = await desk.accessibility_elements(app)
        took = time.monotonic() - start
        print(f"{app}: {len(els)} elements in {took:.1f}s, window {desk.last_ax_window}")
        assert took < 25 and desk.last_ax_window is not None


async def test_pointer_to_the_top_left_corner_through_the_tool(desk, real_services):
    """A named corner is a position on the display under the pointer (5 pt inset: no Hot Corner fires), never OCR."""
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    from baymax.tasks.models import TaskStatus

    x0, y0 = await desk.cursor_position()
    t = await _run(real_services, ("desktop.move_cursor", {"position": "top-left"}))
    x, y = await desk.cursor_position()
    await desk.move_cursor(x0, y0)
    print(t.result["summary"])
    assert t.status is TaskStatus.COMPLETED and t.verified, t.result
    d = next(dp for dp in await desk.displays() if dp.contains(x0, y0))
    assert abs(x - (d.x + 5)) < 2 and abs(y - (d.y + 5)) < 2


async def test_read_the_current_window_in_safari(desk, real_services, tmp_path):
    """'Read the current window' on a Safari page (a local file: no network). Accessibility text first; if Safari
    exposes none, the on-screen text of that window (needs Screen Recording)."""
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    from baymax.tasks.models import TaskStatus

    page = tmp_path / "baymax-read-test.html"
    page.write_text("<html><body><h1>BAYMAX reading test 4217</h1><p>Neural networks learn from examples.</p></body></html>")
    await desk.open_path_in_app("Safari", page)
    for _ in range(20):
        await asyncio.sleep(0.5)
        if await desk.frontmost_app() == "Safari":
            break
    await asyncio.sleep(1.5)  # page render
    t = await _run(real_services, ("desktop.read_window", {}))
    print(t.result["summary"][:400], "| source:", t.result.get("data", {}).get("source"))
    assert t.status is TaskStatus.COMPLETED, t.result
    assert "4217" in t.result["data"]["text"], t.result["data"].get("text", "")[:500]
    print("Close the Safari tab afterwards.")


async def test_type_a_prompt_into_the_chatgpt_app_without_sending(desk, real_services):
    """Only with the ChatGPT app installed (and signed in). Types a prompt into its message box and reads it back; the
    prompt is NOT sent (clear it afterwards). On failure the message says which stage failed and what window was read."""
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    running, installed = await desk.list_apps()
    if not any(a.name == "ChatGPT" for a in installed + running):
        pytest.skip("the ChatGPT app is not installed on this Mac")
    from baymax.tasks.models import TaskStatus

    t = await _run(real_services, ("ai.delegate", {"service": "chatgpt", "prompt": "BAYMAX test prompt - do not send", "via": "app"}),
                   timeout=90)
    print(t.result["summary"], "| window:", t.result.get("data", {}).get("window"), "| elements:",
          t.result.get("data", {}).get("elementsRead"))
    assert t.status is TaskStatus.COMPLETED and t.result["data"].get("entered"), t.result
    assert t.result["data"].get("submitted") is False


async def test_type_text_tool_is_verified_only_by_reading_the_field_back(desk, real_services):
    """desktop.type_text into a new TextEdit document: verified only because the focused field's text is read back
    (an app in front is not proof). Close the document WITHOUT saving."""
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    from baymax.tasks.models import TaskStatus

    t = await _run(real_services, ("desktop.new_document", {"app": "TextEdit"}))
    assert t.status is TaskStatus.COMPLETED and t.verified, t.result
    text = "Phase 1 ✓ Grüße 👋\n2nd line"  # starts with a digit: macOS auto-capitalizes a first word after a newline
    t = await _run(real_services, ("desktop.type_text", {"app": "TextEdit", "text": text}))
    print(t.result["summary"], "|", t.result.get("verification"))
    assert t.status is TaskStatus.COMPLETED and t.verified, t.result
    assert t.result["verification"] == "typed text read back from the focused field"


# ------------------------------------------------------------------ Phase 2 (real Mac)
async def test_session_state_is_available_while_you_are_using_the_mac(desk):
    st = await desk.session_state()
    print("session state:", st)
    assert st["state"] == "available", st


async def test_focus_a_textedit_window_by_title(desk, real_services):
    """Two new TextEdit documents; the tool brings the first one back to the front by its title. Close both WITHOUT saving."""
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    from baymax.tasks.models import TaskStatus

    first, _, _ = await _fresh_textedit_document(desk)
    second, _, _ = await _fresh_textedit_document(desk)
    wins = await desk.list_windows("TextEdit")
    print("TextEdit windows:", [w["title"] for w in wins])
    titles = [w["title"] for w in wins]
    assert first in titles and second in titles, (f"list_windows saw {titles}, expected {first!r} and {second!r}; front app: "
                                                  f"{await desk.frontmost_app()}")
    t = await _run(real_services, ("desktop.focus_window", {"app": "TextEdit", "title": first}))
    print(t.result["summary"])
    assert t.status is TaskStatus.COMPLETED and t.verified, t.result


async def test_a_real_save_sheet_is_detected_and_left_alone(desk):
    """⌘S on a new TextEdit document opens the Save sheet; BAYMAX must report it (it never clicks it). The test then
    presses Escape itself to close the sheet. Close the document WITHOUT saving afterwards."""
    if desk.accessibility_trusted() is not True:
        pytest.skip("grant Accessibility first")
    await _fresh_textedit_document(desk)
    await desk.press_keys("TextEdit", "s", ("command",))
    modals = []
    for _ in range(10):
        await asyncio.sleep(0.5)
        modals = await desk.modal_dialogs("TextEdit")
        if modals:
            break
    print("dialogs:", modals)
    await desk.press_keys("TextEdit", "escape")  # test clean-up (not BAYMAX acting on the dialog)
    assert modals and modals[0]["kind"] in ("save_dialog", "sheet"), modals


async def test_find_a_unique_file_and_open_it_through_a_reference(desk, real_services):
    """A temporary file in ~/Documents is found by Spotlight and opened by the plan's second step through a verified
    reference. Spotlight may need a few seconds to index it. The file is deleted afterwards."""
    import uuid
    from pathlib import Path

    from baymax.tasks.models import PlanStep, TaskStatus

    name = f"baymax-phase2-{uuid.uuid4().hex[:8]}.txt"
    path = Path.home() / "Documents" / name
    path.write_text("BAYMAX phase 2 reference test\n")
    try:
        for _ in range(30):  # wait for Spotlight to index the new file
            if await desk.find_files(name, Path.home()):
                break
            await asyncio.sleep(1)
        task = real_services.tasks.submit("t", "t", "t", [PlanStep("find", "files.find_by_name", {"name": name}),
                                                          PlanStep("open", "files.open", {"path": {"$from": 0, "output": "path"},
                                                                                          "app": "TextEdit"})])
        t = await real_services.tasks.wait(task.id, 60)
        print([s.status for s in t.plan], t.result["summary"] if t.result else t.error)
        assert t.status is TaskStatus.COMPLETED, t.result or t.error
        assert t.plan[0].status == "done" and t.plan[1].status == "done"
    finally:
        path.unlink(missing_ok=True)
