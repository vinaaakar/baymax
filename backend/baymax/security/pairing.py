"""Explicit device pairing and challenge-response authentication.

Pairing flow
------------
1. The Mac user clicks "Pair iPhone". :meth:`DeviceRegistry.start_pairing`
   creates a short-lived 6-digit code (default 5 minutes, 5 attempts).
2. The Mac shows a QR code containing host, port, TLS fingerprint and code.
3. The phone connects over TLS (pinning the fingerprint) and sends
   ``pair.request`` with the code. On success the Mac returns a random 256-bit
   device key, stored in the Mac's secret store and the phone's Keychain.

Authentication
--------------
Every later connection gets ``auth.challenge {nonce}``. The phone answers with
``HMAC-SHA256(deviceKey, "baymax-auth-v1|" + nonce + "|" + deviceId)`` (hex).
Nonces are single-use and expire after 60 seconds.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from ..db import Database
from .secrets import SecretStore

_DEVICE_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
NONCE_TTL_S = 60.0


class PairingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class PairingSession:
    code: str
    expires_at: float
    attempts_left: int


@dataclass
class Device:
    id: str
    name: str
    paired_at: float
    last_seen: float | None
    revoked: bool

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "pairedAt": self.paired_at, "lastSeen": self.last_seen, "revoked": self.revoked}


def compute_mac(device_key: str, nonce: str, device_id: str) -> str:
    msg = f"baymax-auth-v1|{nonce}|{device_id}".encode()
    return hmac.new(device_key.encode(), msg, hashlib.sha256).hexdigest()


class DeviceRegistry:
    def __init__(self, db: Database, secrets_store: SecretStore, *, ttl_s: int = 300, max_attempts: int = 5,
                 clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.secrets = secrets_store
        self.ttl_s = ttl_s
        self.max_attempts = max_attempts
        self.clock = clock
        self._session: PairingSession | None = None
        self._nonces: dict[str, float] = {}

    # -- pairing ----------------------------------------------------------
    def start_pairing(self) -> PairingSession:
        code = f"{secrets.randbelow(1_000_000):06d}"
        self._session = PairingSession(code=code, expires_at=self.clock() + self.ttl_s, attempts_left=self.max_attempts)
        return self._session

    def cancel_pairing(self) -> None:
        self._session = None

    @property
    def pairing_active(self) -> bool:
        return self._session is not None and self._session.expires_at > self.clock()

    def complete_pairing(self, code: str, device_id: str, device_name: str) -> str:
        session = self._session
        if session is None or session.expires_at <= self.clock():
            self._session = None
            raise PairingError("pair_failed", "no active pairing session; start pairing on the Mac")
        if session.attempts_left <= 0:
            self._session = None
            raise PairingError("pair_locked", "too many attempts; start pairing again on the Mac")
        if not _DEVICE_ID.match(device_id):
            raise PairingError("pair_failed", "invalid device id")
        session.attempts_left -= 1
        if not hmac.compare_digest(code.strip(), session.code):
            if session.attempts_left <= 0:
                self._session = None
                raise PairingError("pair_locked", "too many attempts; start pairing again on the Mac")
            raise PairingError("pair_failed", "incorrect pairing code")
        self._session = None  # single use
        key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        self.secrets.set(_secret_name(device_id), key)
        name = (device_name or "iPhone").strip()[:64]
        now = self.clock()
        self.db.execute(
            "INSERT INTO devices(id, name, paired_at, last_seen, revoked) VALUES (?,?,?,?,0) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, paired_at=excluded.paired_at, revoked=0",
            (device_id, name, now, now),
        )
        return key

    # -- authentication ---------------------------------------------------
    def new_nonce(self) -> str:
        now = self.clock()
        self._nonces = {n: t for n, t in self._nonces.items() if t > now}
        nonce = secrets.token_hex(16)
        self._nonces[nonce] = now + NONCE_TTL_S
        return nonce

    def verify(self, device_id: str, nonce: str, mac: str) -> bool:
        expiry = self._nonces.pop(nonce, None)
        if expiry is None or expiry < self.clock():
            return False
        if not _DEVICE_ID.match(device_id):
            return False
        device = self.get(device_id)
        if device is None or device.revoked:
            return False
        key = self.secrets.get(_secret_name(device_id))
        if not key:
            return False
        ok = hmac.compare_digest(compute_mac(key, nonce, device_id), mac.lower())
        if ok:
            self.db.execute("UPDATE devices SET last_seen=? WHERE id=?", (self.clock(), device_id))
        return ok

    # -- management -------------------------------------------------------
    def get(self, device_id: str) -> Device | None:
        row = self.db.one("SELECT * FROM devices WHERE id=?", (device_id,))
        return _row(row) if row else None

    def list(self) -> list[Device]:
        return [_row(r) for r in self.db.query("SELECT * FROM devices ORDER BY paired_at DESC")]

    def revoke(self, device_id: str) -> bool:
        cur = self.db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))
        self.secrets.delete(_secret_name(device_id))
        return cur.rowcount > 0


def _secret_name(device_id: str) -> str:
    return f"device-{device_id}"


def _row(row) -> Device:
    return Device(id=row["id"], name=row["name"], paired_at=row["paired_at"], last_seen=row["last_seen"], revoked=bool(row["revoked"]))
