"""Pairing survives a Mac restart, and the QR code advertises a reachable address first."""

from __future__ import annotations

from baymax.server import app as app_mod
from baymax.server.app import BaymaxServer


async def _start(services):
    services.settings.network.local_port = 0
    services.settings.network.lan_port = 0
    services.settings.network.lan_host = "127.0.0.1"
    srv = BaymaxServer(services)
    await srv.start()
    return srv


async def test_listener_stays_off_until_something_is_paired(services):
    srv = await _start(services)
    try:
        assert srv.lan_runner is None  # nothing paired: no LAN exposure
    finally:
        await srv.stop()


async def test_paired_phone_can_reconnect_after_restart(services):
    session = services.devices.start_pairing()
    services.devices.complete_pairing(session.code, "iphone-restart", "My iPhone")
    srv = await _start(services)  # simulates relaunching the Mac app
    try:
        assert srv.lan_runner is not None and srv.lan_fingerprint
    finally:
        await srv.stop()


def test_qr_host_prefers_the_default_route_and_skips_unusable_addresses(monkeypatch):
    class FakeSock:
        def __init__(self, *a):
            pass

        def connect(self, addr):
            pass

        def getsockname(self):
            return ("192.168.1.42", 5555)

        def close(self):
            pass

    monkeypatch.setattr(app_mod.socket, "socket", FakeSock)
    monkeypatch.setattr(app_mod.socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("10.0.0.5", 0)), (2, 1, 6, "", ("169.254.3.3", 0)), (2, 1, 6, "", ("127.0.0.1", 0)),
        (2, 1, 6, "", ("192.168.1.42", 0))])
    assert app_mod.lan_addresses() == ["192.168.1.42", "10.0.0.5"]
