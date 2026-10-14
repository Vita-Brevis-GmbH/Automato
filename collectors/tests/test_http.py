"""HTTP-Konfiguration zum Orchestrator: optionales mTLS/CA-Pinning (ADR 0008)."""

from collectors.common.http import client_kwargs
from collectors.config import Settings


def test_default_system_trust():
    k = client_kwargs(Settings())
    assert k["verify"] is True and k["cert"] is None


def test_ca_bundle_pinnt_server():
    k = client_kwargs(Settings(orchestrator_ca_bundle="/etc/automato/ca.pem"))
    assert k["verify"] == "/etc/automato/ca.pem"


def test_client_cert_key_aktiviert_mtls():
    k = client_kwargs(
        Settings(client_cert="/etc/automato/agent.crt", client_key="/etc/automato/agent.key")
    )
    assert k["cert"] == ("/etc/automato/agent.crt", "/etc/automato/agent.key")


def test_kombiniertes_pem():
    k = client_kwargs(Settings(client_cert="/etc/automato/agent-combined.pem"))
    assert k["cert"] == "/etc/automato/agent-combined.pem"


def test_verify_abschaltbar():
    assert client_kwargs(Settings(orchestrator_verify_tls=False))["verify"] is False
