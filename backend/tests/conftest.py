from __future__ import annotations

import json
from pathlib import Path

import pytest

from baymax.config import load_settings
from baymax.hostos.desktop import ACCESSIBILITY_HELP, SCREEN_HELP, DesktopBackend, DesktopPermissionError, DesktopUnavailable, UIElement
from baymax.hostos.apps import AppInfo
from baymax.screen.elements import Display, ScreenElement, parse_ax_dump
from baymax.security.secrets import SecretStore

REPO = Path(__file__).resolve().parents[2]
SPEC = json.loads((REPO / "protocol" / "baymax-protocol.json").read_text())


class FakeDesktop(DesktopBackend):
    """Records calls; behaves like a Mac with permissions granted (configurable)."""

    platform = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.running: set[str] = set()
        self.cursor = (0.0, 0.0)
        self.accessibility = True
        self.screen_recording = True
        self.front = "Finder"
        # Screen understanding fixtures: a built-in display plus an external one to its left.
        self.display_list = [Display(1, 0, 0, 1512, 982, 2.0), Display(2, -1920, -98, 1920, 1080, 1.0)]
        self.ax_dump: dict[str, str] = {}
        self.ax_after_press: dict[str, str] = {}
        self.ocr: list[ScreenElement] = []
        self.titles: dict[str, list[str]] = {}
        # Installed apps on the fake Mac (display name, bundle id). VS Code runs under the process name "Code".
        self.installed = [AppInfo("Safari", "", "com.apple.Safari"), AppInfo("Terminal", "", "com.apple.Terminal"),
                          AppInfo("Visual Studio Code", "", "com.microsoft.VSCode"), AppInfo("Finder", "", "com.apple.finder"),
                          AppInfo("Notes", "", "com.apple.Notes"), AppInfo("Xcode", "", "com.apple.dt.Xcode"),
                          AppInfo("Spotify", "", "com.spotify.client"), AppInfo("Mail", "", "com.apple.mail")]
        self.clipboard = ""
        self.window_title: str | None = "BAYMAX — README.md"
        self.document = None

    def capabilities(self):
        return {"platform": "fake", "available": True, "accessibilityTrusted": self.accessibility}

    def _need_ax(self):
        if not self.accessibility:
            raise DesktopPermissionError("BAYMAX needs the Accessibility permission for this.")

    async def open_app(self, name):
        self.calls.append(("open_app", name))
        if name == "Nonexistent":
            raise DesktopUnavailable("could not open 'Nonexistent': application not found")
        self.running.add(name)
        self.front = name

    async def is_app_running(self, name):
        return name in self.running

    async def open_url(self, url):
        self.calls.append(("open_url", url))

    async def frontmost_app(self):
        return self.front

    async def move_cursor(self, x, y):
        self._need_ax()
        self.calls.append(("move", x, y))
        self.cursor = (x, y)

    async def cursor_position(self):
        return self.cursor

    async def click(self, x, y, button="left", count=1):
        self._need_ax()
        self.calls.append(("click", x, y, button, count))

    async def scroll(self, dx, dy):
        self._need_ax()
        self.calls.append(("scroll", dx, dy))

    async def type_text(self, app, text):
        self._need_ax()
        self.calls.append(("type", app, text))
        self.front = app

    async def list_elements(self, app, limit=60):
        self._need_ax()
        return [UIElement("AXButton", "OK"), UIElement("AXTextField", "Search")]

    async def press_element(self, app, role, name):
        self._need_ax()
        self.calls.append(("press", app, role, name))
        if app in self.ax_after_press:
            self.ax_dump[app] = self.ax_after_press[app]

    async def read_window_text(self, app, limit=4000):
        self._need_ax()
        alive = set(self.running) | {self.front} | ({"Code"} if "Visual Studio Code" in self.running else set())
        if app not in alive:
            raise DesktopUnavailable(f"{app} isn't running")
        self.calls.append(("read_window", app))
        return "Hello from the window"

    async def screenshot(self, path):
        raise DesktopPermissionError("BAYMAX needs the Screen Recording permission for this.")

    async def displays(self):
        return list(self.display_list)

    async def list_apps(self):
        bundles = {a.name: a.bundle_id for a in self.installed}
        names = set(self.running) | ({self.front} if self.front else set())
        proc = {"Visual Studio Code": "Code"}
        return [AppInfo(proc.get(n, n), proc.get(n, n), bundles.get(n, ""), True) for n in sorted(names)], list(self.installed)

    async def open_path_in_app(self, app, path):
        self.calls.append(("open_path", app, str(path)))
        self.running.add(app)
        self.front = app
        self.titles[app] = [f"{path.name} — {app}"]

    async def window_titles(self, app):
        self._need_ax()
        return list(self.titles.get(app, []))

    async def clipboard_text(self):
        return self.clipboard

    async def front_document(self):
        return self.document

    async def active_window(self):
        from baymax.hostos.desktop import ActiveWindow

        if not self.front:
            raise DesktopUnavailable("no window is in front")
        return ActiveWindow(self.front, self.window_title, 100, 80, 900, 600)

    async def target_app(self):
        if not self.front:
            raise DesktopUnavailable("I couldn't tell which app you mean (BAYMAX itself is in front). Click the window first, or name the app.")
        return self.front

    async def accessibility_elements(self, app, limit=400):
        if not self.accessibility:
            raise DesktopPermissionError(ACCESSIBILITY_HELP)
        self.calls.append(("ax", app))
        return parse_ax_dump(self.ax_dump.get(app, ""), app)

    async def ocr_elements(self):
        if not self.screen_recording:
            raise DesktopPermissionError(SCREEN_HELP)
        self.calls.append(("ocr",))
        return list(self.ocr)


@pytest.fixture
def spec():
    return SPEC


@pytest.fixture
def settings(tmp_path):
    s = load_settings(tmp_path / "data", {
        "workspace_dir": str(tmp_path / "workspace"),
        "llm": {"base_url": "http://127.0.0.1:9", "connect_timeout_s": 0.5, "request_timeout_s": 2.0},
        "network": {"local_port": 0},
        "voice": {"tts_engine": "none", "stt_engine": "whisper.cpp", "whisper_binary": "definitely-not-installed"},
        "security": {"approval_timeout_s": 5},
        "research": {"local_only": True},
    })
    s.ensure_dirs()
    return s


@pytest.fixture
def fake_desktop():
    return FakeDesktop()


@pytest.fixture
async def services(settings, fake_desktop):
    from baymax.services import build_services

    s = build_services(settings, desktop=fake_desktop, secrets=SecretStore(settings.data_dir, use_keychain=False))
    yield s
    await s.close()
