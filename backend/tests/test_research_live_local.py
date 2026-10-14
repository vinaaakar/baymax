"""Research over real sockets: TLS verification behaviour and citation preservation.

Uses local HTTP(S) servers (a SearXNG-compatible search endpoint and real pages), so it
exercises the actual aiohttp client, redirects, parsing, the research tool, the event bus
and the report on disk. Public-internet behaviour must still be checked on the Mac.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import ssl

import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from baymax.assistant import Assistant
from baymax.config import ResearchConfig, load_settings
from baymax.research import tls
from baymax.research.web import ResearchUnavailable, WebClient
from baymax.security.secrets import SecretStore
from baymax.tasks.models import TaskStatus

PAGE_A = "<html><head><title>Spring Boot Reference</title></head><body><p>Spring Boot makes it easy to create stand-alone applications.</p></body></html>"
PAGE_B = "<html><head><title>Getting Started</title></head><body><p>Spring Boot auto-configuration configures beans for you.</p></body></html>"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "SSL_CERT_FILE"):
        monkeypatch.delenv(k, raising=False)
    tls.client_context.cache_clear()
    yield
    tls.client_context.cache_clear()


def _h(**kw):
    async def handler(_r):
        return web.Response(**kw)
    return handler


def _self_signed(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "baymax-test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(key, hashes.SHA256()))
    c, k = tmp_path / "cert.pem", tmp_path / "key.pem"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return c, k


async def _serve(app, ssl_ctx=None):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=ssl_ctx)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


async def test_untrusted_certificate_fails_clearly_and_trusted_one_works(tmp_path, monkeypatch):
    cert, key = _self_signed(tmp_path)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(cert, key)
    app = web.Application()
    app.router.add_get("/doc", _h(text=PAGE_A, content_type="text/html"))
    runner, port = await _serve(app, sctx)
    url = f"https://127.0.0.1:{port}/doc"
    try:
        c = WebClient(ResearchConfig(allow_private_fetch=True))
        with pytest.raises(ResearchUnavailable) as e:
            await c.fetch(url)
        msg = str(e.value)
        assert "certificate verification" in msg and "Verification was not disabled" in msg
        await c.close()

        # Trusting the certificate explicitly (as a user would for a corporate CA) makes it work; verification stays on.
        monkeypatch.setenv("SSL_CERT_FILE", str(cert))
        tls.client_context.cache_clear()
        ctx, source = tls.client_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED and "SSL_CERT_FILE" in source
        c = WebClient(ResearchConfig(allow_private_fetch=True))
        page = await c.fetch(url)
        assert page.title == "Spring Boot Reference" and page.url == url
        await c.close()
    finally:
        await runner.cleanup()


def test_default_context_always_verifies():
    ctx, source = tls.client_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert source  # names its trust source for diagnostics


async def test_private_addresses_still_blocked_by_default():
    c = WebClient(ResearchConfig())
    from baymax.research.web import FetchBlocked
    with pytest.raises(FetchBlocked):
        await c.fetch("http://127.0.0.1:9/x")
    await c.close()


async def test_research_end_to_end_preserves_real_urls(tmp_path):
    app = web.Application()
    state = {}

    async def search(r):
        base = state["base"]
        return web.json_response({"results": [
            {"title": "Spring Boot Reference", "url": f"{base}/a", "content": "reference"},
            {"title": "Broken", "url": f"{base}/missing", "content": "gone"},
            {"title": "Getting Started", "url": f"{base}/redirect", "content": "guide"},
        ]})

    app.router.add_get("/search", search)
    app.router.add_get("/a", _h(text=PAGE_A, content_type="text/html"))
    app.router.add_get("/b", _h(text=PAGE_B, content_type="text/html"))
    app.router.add_get("/missing", _h(status=404, text="no", content_type="text/plain"))

    async def redirect(r):
        raise web.HTTPFound("/b")

    app.router.add_get("/redirect", redirect)
    runner, port = await _serve(app)
    base = state["base"] = f"http://127.0.0.1:{port}"
    settings = load_settings(tmp_path / "data", {
        "workspace_dir": str(tmp_path / "ws"),
        "llm": {"base_url": "http://127.0.0.1:9", "connect_timeout_s": 0.3, "request_timeout_s": 1.0},
        "network": {"local_port": 0},
        "voice": {"tts_engine": "none", "whisper_binary": "definitely-not-installed"},
        "research": {"local_only": False, "search_provider": "searxng", "searxng_url": base, "allow_private_fetch": True},
    })
    settings.ensure_dirs()
    from baymax.services import build_services

    s = build_services(settings, secrets=SecretStore(settings.data_dir, use_keychain=False))
    a = Assistant(s)
    a.start()
    sub = s.bus.subscribe()
    try:
        r = await a.handle_text("search the web for Spring Boot documentation")
        t = await s.tasks.wait(r["taskId"], 20)
        assert t.status is TaskStatus.COMPLETED, t.error
        result = msg = None
        while result is None or msg is None:
            ev = await asyncio.wait_for(sub.get(), 5)
            if ev.type == "research.result":
                result = ev.payload
            if ev.type == "assistant.message" and result is not None:
                msg = ev.payload
        urls = [x["url"] for x in result["sources"]]
        # Only pages that were actually fetched; the redirect is recorded at its final URL; the 404 is excluded.
        assert urls == [f"{base}/a", f"{base}/b"]
        assert result["summaryKind"] == "source excerpts"  # no local model: excerpts, not an invented analysis
        assert "stand-alone applications" in result["summary"]
        for u in urls:
            assert u in msg["text"]  # sources shown in the chat reply
        report = next(p for p in (settings.artifacts_dir / "research").glob("*.md"))
        body = report.read_text()
        assert f"{base}/a" in body and f"{base}/b" in body
    finally:
        await a.stop()
        await s.close()
        await runner.cleanup()
