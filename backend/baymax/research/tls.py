"""Outbound TLS for research fetches.

Certificate verification is always on. The common macOS failure is a python.org
Python whose OpenSSL has no CA bundle ("Install Certificates.command" never run),
which makes every HTTPS request fail with CERTIFICATE_VERIFY_FAILED. We avoid that by
verifying against the operating system's trust store (``truststore``: macOS Keychain,
including corporate/proxy CAs the user trusts), falling back to ``certifi``'s Mozilla
bundle, then to OpenSSL's defaults. ``SSL_CERT_FILE`` overrides the bundle explicitly.
"""

from __future__ import annotations

import os
import ssl
import time
from functools import lru_cache


@lru_cache(maxsize=1)
def client_context() -> tuple[ssl.SSLContext, str]:
    """Returns a verifying client context and a description of its trust source."""
    cafile = os.environ.get("SSL_CERT_FILE")
    if cafile and os.path.isfile(cafile):
        return ssl.create_default_context(cafile=cafile), f"SSL_CERT_FILE ({cafile})"
    try:
        import truststore

        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return ctx, "system trust store (truststore)"
    except Exception:  # not installed, or unsupported platform
        pass
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where()), "certifi CA bundle"
    except Exception:
        pass
    return ssl.create_default_context(), "OpenSSL default paths"


def describe_tls_error(exc: BaseException, url: str) -> str:
    """Human explanation for a TLS failure. Never suggests disabling verification."""
    _, source = client_context()
    detail = str(getattr(exc, "certificate_error", None) or exc)
    hints = []
    if "expired" in detail or "not yet valid" in detail:
        hints.append(f"check the system clock (now {time.strftime('%Y-%m-%d %H:%M')})")
    if os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"):
        hints.append("an HTTPS proxy is configured; its CA must be trusted by the system")
    if "self-signed" in detail or "unable to get local issuer" in detail:
        hints.append("the certificate chain is not trusted: a network filter or proxy may be intercepting TLS, "
                     "or this Python has no CA bundle (run `pip install truststore certifi` in the backend venv)")
    hint = "; ".join(hints) or "the server's certificate could not be verified"
    return f"secure connection to {url} failed certificate verification ({detail[:200]}); trust source: {source}. {hint}. Verification was not disabled."
