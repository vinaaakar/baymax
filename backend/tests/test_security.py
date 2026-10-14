import os

import pytest

from baymax.db import Database
from baymax.security.audit import AuditLog
from baymax.security.commands import CommandRejected, validate_command
from baymax.security.estop import EmergencyStop, EmergencyStopEngaged
from baymax.security.pairing import DeviceRegistry, PairingError, compute_mac
from baymax.security.policy import ActionRequest, Decision, Policy, Risk
from baymax.security.secrets import SecretStore
from baymax.security.tls import ensure_certificate, fingerprint
from baymax.security.workspace import Workspace, WorkspaceViolation


# ----------------------------------------------------------------- workspace
def test_workspace_confinement(tmp_path):
    ws = Workspace(tmp_path / "ws")
    (ws.root / "a").mkdir()
    assert ws.resolve("a/b.txt") == ws.root / "a" / "b.txt"
    for bad in ("../x", "a/../../x", "/etc/passwd", str(tmp_path / "other"), "~/.ssh/id_rsa"):
        with pytest.raises(WorkspaceViolation):
            ws.resolve(bad)
    with pytest.raises(WorkspaceViolation):
        ws.resolve("a\x00b")


def test_workspace_symlink_escape(tmp_path):
    ws = Workspace(tmp_path / "ws")
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "key").write_text("k")
    os.symlink(outside, ws.root / "link")
    os.symlink(outside / "key", ws.root / "filelink")
    with pytest.raises(WorkspaceViolation):
        ws.resolve("link/key")
    with pytest.raises(WorkspaceViolation):
        ws.resolve("filelink")
    with pytest.raises(WorkspaceViolation):
        ws.resolve("link/new-file.txt")
    # a symlink that stays inside is fine
    (ws.root / "real").mkdir()
    os.symlink(ws.root / "real", ws.root / "inner")
    assert ws.resolve("inner/x").parent == ws.root / "real"


# ------------------------------------------------------------------ commands
@pytest.mark.parametrize("cmd,risk", [
    ("python -m pytest -q", Risk.LOW), ("npm test", Risk.LOW), ("mvn -q test", Risk.LOW), ("./gradlew test", Risk.LOW),
    ("git status", Risk.LOW), ("git commit -m x", Risk.MEDIUM), ("npm run build", Risk.MEDIUM),
    ("npm install left-pad", Risk.HIGH), ("git push", Risk.HIGH), ("python -m pip install requests", Risk.HIGH), ("go test ./...", Risk.LOW),
])
def test_allowed_commands(cmd, risk):
    assert validate_command(cmd).risk is risk


@pytest.mark.parametrize("cmd", [
    "rm -rf /", "sudo ls", "bash -c 'echo hi'", "curl http://evil", "pytest; rm -rf ~", "npm test && reboot", "echo $(whoami)",
    "python script.py", "python -c 'import os'", "/bin/ls", "osascript -e 'x'", "ls | sh", "unknowncmd", "python -m http.server", "", "pip uninstall x",
])
def test_rejected_commands(cmd):
    with pytest.raises(CommandRejected):
        validate_command(cmd)


def test_install_commands_need_approval_category():
    assert "install_software" in validate_command("npm install x").categories
    assert "publish" in validate_command("git push").categories


# -------------------------------------------------------------------- policy
def test_policy_decisions():
    p = Policy()
    assert p.evaluate(ActionRequest("files.read", {}, Risk.LOW)).decision is Decision.ALLOW
    assert p.evaluate(ActionRequest("apps.open", {}, Risk.MEDIUM)).decision is Decision.ALLOW
    assert p.evaluate(ActionRequest("x", {}, Risk.LOW, frozenset({"send_message"}))).decision is Decision.REQUIRE_APPROVAL
    assert p.evaluate(ActionRequest("x", {}, Risk.LOW, frozenset({"purchase"}))).decision is Decision.REQUIRE_APPROVAL
    assert p.evaluate(ActionRequest("x", {}, Risk.LOW, frozenset({"unrestricted_shell"}))).decision is Decision.DENY
    assert p.evaluate(ActionRequest("x", {}, Risk.BLOCKED)).decision is Decision.DENY
    assert p.evaluate(ActionRequest("hardware.flash_firmware", {}, Risk.HIGH)).decision is Decision.DENY  # hardware disabled
    assert Policy(allow_desktop_control=False).evaluate(ActionRequest("desktop.click", {}, Risk.MEDIUM)).decision is Decision.DENY


def test_approval_rights():
    assert Policy.may_approve(Risk.HIGH, "local", False)
    assert not Policy.may_approve(Risk.HIGH, "device:abc", False)
    assert Policy.may_approve(Risk.HIGH, "device:abc", True)
    assert Policy.may_approve(Risk.MEDIUM, "device:abc", False)
    assert not Policy.may_approve(Risk.LOW, "agent", True)
    assert not Policy.may_approve(Risk.LOW, "model", True)


def test_action_fingerprint_binds_args():
    a = ActionRequest("mail.send", {"to": "a@b.c"}, Risk.HIGH)
    b = ActionRequest("mail.send", {"to": "evil@b.c"}, Risk.HIGH)
    assert a.fingerprint() != b.fingerprint()


# --------------------------------------------------------------------- estop
def test_estop_blocks_and_runs_all_handlers():
    e = EmergencyStop()
    calls = []
    e.on_engage(lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
    e.on_engage(lambda r: calls.append(r))
    e.ensure_clear()
    e.engage("test")
    assert calls == ["test"]  # second handler ran despite the first failing
    with pytest.raises(EmergencyStopEngaged):
        e.ensure_clear()
    e.reset()
    e.ensure_clear()


# ------------------------------------------------------------------- pairing
@pytest.fixture
def registry(tmp_path):
    clock = {"t": 1000.0}
    reg = DeviceRegistry(Database(tmp_path / "db.sqlite"), SecretStore(tmp_path, use_keychain=False), ttl_s=60, max_attempts=3,
                         clock=lambda: clock["t"])
    return reg, clock


def test_pairing_and_authentication(registry):
    reg, clock = registry
    with pytest.raises(PairingError):
        reg.complete_pairing("000000", "device-0001", "Phone")  # no session
    code = reg.start_pairing().code
    key = reg.complete_pairing(code, "device-0001", "My iPhone")
    assert len(key) >= 40
    with pytest.raises(PairingError):  # single use
        reg.complete_pairing(code, "device-0002", "Other")
    nonce = reg.new_nonce()
    assert reg.verify("device-0001", nonce, compute_mac(key, nonce, "device-0001"))
    assert not reg.verify("device-0001", nonce, compute_mac(key, nonce, "device-0001"))  # nonce replay
    n2 = reg.new_nonce()
    assert not reg.verify("device-0001", n2, compute_mac("wrong-key", n2, "device-0001"))
    n3 = reg.new_nonce()
    clock["t"] += 120  # expired nonce
    assert not reg.verify("device-0001", n3, compute_mac(key, n3, "device-0001"))
    reg.revoke("device-0001")
    n4 = reg.new_nonce()
    assert not reg.verify("device-0001", n4, compute_mac(key, n4, "device-0001"))


def test_pairing_lockout_and_expiry(registry):
    reg, clock = registry
    reg.start_pairing()
    for _ in range(2):
        with pytest.raises(PairingError) as exc:
            reg.complete_pairing("999999" if reg._session.code != "999999" else "111111", "device-0001", "P")
        assert exc.value.code == "pair_failed"
    with pytest.raises(PairingError) as exc:
        reg.complete_pairing("x", "device-0001", "P")
    assert exc.value.code == "pair_locked"
    code = reg.start_pairing().code
    clock["t"] += 61
    with pytest.raises(PairingError):
        reg.complete_pairing(code, "device-0001", "P")


def test_pairing_rejects_bad_device_id(registry):
    reg, _ = registry
    code = reg.start_pairing().code
    with pytest.raises(PairingError):
        reg.complete_pairing(code, "../../etc", "P")


def test_secret_store_permissions(tmp_path):
    st = SecretStore(tmp_path, use_keychain=False)
    st.set("device-abc", "s3cret")
    path = tmp_path / "secrets" / "device-abc"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert st.get("device-abc") == "s3cret"
    with pytest.raises(ValueError):
        st.set("../evil", "x")
    st.delete("device-abc")
    assert st.get("device-abc") is None


def test_tls_certificate(tmp_path):
    cert, key = ensure_certificate(tmp_path / "tls")
    fp = fingerprint(cert)
    assert len(fp) == 64
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert ensure_certificate(tmp_path / "tls") == (cert, key) and fingerprint(cert) == fp  # reused, stable


# --------------------------------------------------------------------- audit
def test_audit_chain_and_redaction(tmp_path):
    db = Database(tmp_path / "a.db")
    log = AuditLog(db)
    log.record("local", "files.write", "medium", "ok", {"path": "x"})
    log.record("local", "config", "low", "ok", "token=abcdef123 password: hunter2")
    entries = log.recent()
    assert "hunter2" not in entries[0]["detail"] and "abcdef123" not in entries[0]["detail"]
    assert log.verify_chain()
    db.execute("UPDATE audit SET outcome='denied' WHERE id=1")
    assert not log.verify_chain()
