"""Signierte Agent-Updates: offline signieren, einspielen, ausliefern, Zustand."""

import base64
import json
import subprocess
import sys
from pathlib import Path

from app import cli
from app.config import Settings, get_settings
from app.store import agent_package
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_agent_update import _agent, _package

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "agent_signing.py"


def _pub(key) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def test_status_und_pruefung(tmp_path):
    pkg, sig_file = tmp_path / "pkg", tmp_path / "agent-package.sig"
    pkg.mkdir()
    assert agent_package.signature_status(str(pkg), str(sig_file), "")["state"] == "no_package"
    _package(pkg)
    info = agent_package.manifest(str(pkg))
    key = Ed25519PrivateKey.generate()
    assert agent_package.signature_status(str(pkg), str(sig_file), _pub(key))["state"] == "unsigned"

    sig = base64.b64encode(key.sign(agent_package.signed_message(info))).decode()
    sig_file.write_text(json.dumps({"version": info["version"], "signature": sig}))
    status = lambda pub: agent_package.signature_status(str(pkg), str(sig_file), pub)  # noqa: E731
    assert status(_pub(key))["state"] == "signed"
    assert status("")["state"] == "unchecked"
    assert status(_pub(Ed25519PrivateKey.generate()))["state"] == "invalid"

    sig_file.write_text(json.dumps({"version": "0.0.1+alt", "signature": sig}))
    assert status(_pub(key))["state"] == "unsigned"  # Signatur eines anderen Pakets


def test_offline_signieren_einspielen_und_ausliefern(client, tmp_path, monkeypatch):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _package(pkg)
    key_file, message, sig = tmp_path / "signing.key", tmp_path / "msg.txt", tmp_path / "pkg.sig"

    out = subprocess.run(  # noqa: S603 - eigenes Skript
        [sys.executable, str(SCRIPT), "keygen", str(key_file)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    pub = out.split("AUTOMATO_AGENT_SIGNING_PUBKEY=", 1)[1].strip()
    assert key_file.stat().st_mode & 0o777 == 0o600

    settings = Settings(
        agent_package_dir=str(pkg),
        agent_signature_file=str(tmp_path / "data" / "agent-package.sig"),
        agent_signing_pubkey=pub,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert cli.main(["agent-package", "manifest", "--out", str(message)]) == 0
    signed = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "sign", str(key_file), str(message)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    sig.write_text(signed)
    assert cli.main(["agent-package", "install-signature", str(sig)]) == 0

    # falscher Schluessel am Orchestrator -> Einspielen verweigert
    other = Settings(
        **{**settings.model_dump(), "agent_signing_pubkey": _pub(Ed25519PrivateKey.generate())}
    )
    monkeypatch.setattr(cli, "get_settings", lambda: other)
    assert cli.main(["agent-package", "install-signature", str(sig)]) == 2

    client.app.dependency_overrides[get_settings] = lambda: settings
    try:
        _, headers = _agent(client)
        info = client.get("/internal/agent/update", headers=headers).json()
        assert info["signature"] == json.loads(signed)["signature"]
        assert agent_package.verify(pub, info, info["signature"])
    finally:
        client.app.dependency_overrides.pop(get_settings, None)
