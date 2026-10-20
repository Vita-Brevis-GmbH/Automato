"""Agent-Kanal nur ueber das mTLS-Agent-Gateway (require/bind)."""

import pytest
from app.auth.mtls import common_name
from app.config import Settings, check_security_config, get_settings
from test_reference_sources import ADMIN, _agent, _tenant

SECRET = "gw-secret-0123456789abcdef"


def _gw(cn: str | None = None, secret: str = SECRET) -> dict:
    h = {"X-Gateway-Secret": secret}
    if cn:
        h["X-Client-Cert-Subject"] = f"CN={cn},O=Automato"
    return h


@pytest.fixture
def mode(monkeypatch):
    def set_mode(value: str) -> None:
        monkeypatch.setattr(get_settings(), "internal_mtls_mode", value)
        monkeypatch.setattr(get_settings(), "agent_gateway_secret", SECRET)

    return set_mode


def test_common_name():
    assert common_name("CN=agent-a,O=Automato") == "agent-a"
    assert common_name("O=Automato, CN=agent-b") == "agent-b"
    assert common_name("O=Automato") is None and common_name(None) is None


def test_off_agent_token_genuegt(client):
    _, token = _agent(client, _tenant(client))
    assert (
        client.get("/internal/agent/next-run", headers={"X-Agent-Token": token}).status_code == 200
    )


def test_require_nur_ueber_gateway(client, mode):
    mode("require")
    _, token = _agent(client, _tenant(client))
    agent = {"X-Agent-Token": token}
    direct = client.get("/internal/agent/next-run", headers=agent)
    assert direct.status_code == 403 and "mTLS gateway" in direct.json()["detail"]
    wrong = client.get("/internal/agent/next-run", headers={**agent, **_gw("x", "falsch")})
    assert wrong.status_code == 403
    no_cert = client.get("/internal/agent/next-run", headers={**agent, **_gw()})
    assert no_cert.status_code == 403
    ok = client.get("/internal/agent/next-run", headers={**agent, **_gw("beliebig")})
    assert ok.status_code == 200
    # Ergebnis-Upload mit Agent-Token ebenso
    r = client.post(
        "/internal/collector-runs/nicht-da/result", headers=agent, json={"status": "completed"}
    )
    assert r.status_code == 403
    # interner Token (zentraler Worker) bleibt direkt moeglich
    r = client.post(
        "/internal/collector-runs/nicht-da/result",
        headers={"X-Internal-Token": "dev-only-token"},
        json={"status": "completed"},
    )
    assert r.status_code == 404


def test_bind_cn_gleich_agent_name(client, mode):
    mode("bind")
    _, token = _agent(client, _tenant(client))  # Name "agent-a"
    agent = {"X-Agent-Token": token}
    other = client.get("/internal/agent/next-run", headers={**agent, **_gw("agent-b")})
    assert other.status_code == 403 and "does not belong" in other.json()["detail"]
    assert (
        client.get("/internal/agent/next-run", headers={**agent, **_gw("agent-a")}).status_code
        == 200
    )
    # Admin-API ist nicht betroffen
    assert client.get("/agents", headers=ADMIN).status_code == 200


def test_start_ohne_secret_verweigert():
    with pytest.raises(RuntimeError, match="AGENT_GATEWAY_SECRET"):
        check_security_config(Settings(internal_mtls_mode="require", agent_gateway_secret="kurz"))
    check_security_config(Settings(internal_mtls_mode="bind", agent_gateway_secret=SECRET))
