from __future__ import annotations

import json
from pathlib import Path

import pytest

from baymax.config import load_settings
from baymax.hostos.desktop import DesktopBackend, DesktopPermissionError, DesktopUnavailable, UIElement
from baymax.security.secrets import SecretStore

REPO = Path(__file__).resolve().parents[2]
SPEC = json.loads((REPO / "protocol" / "baymax-protocol.v1.json").read_text())


class FakeDesktop(DesktopBackend):
    """Records calls; behaves like a Mac with permissions granted (configurable)."""

    platform = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.running: set[str] = set()
        self.cursor = (0.0, 0.0)
        self.accessibility = True
        self.front = "Finder"

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

    async def read_window_text(self, app, limit=4000):
        self._need_ax()
        return "Hello from the window"

    async def screenshot(self, path):
        raise DesktopPermissionError("BAYMAX needs the Screen Recording permission for this.")


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
