"""Geräte-Zugangsdaten + Agent-Zuweisung (ADR 0009)."""

from app.models import AuditLog, CollectorRun, IpamType, SourceSystem, Tenant
from app.models.tenant import DnsType

ADMIN = {"X-Dev-Roles": "admin"}
RESULT = {"status": "completed", "rules": []}


def _tenant(client, name: str) -> int:
    return client.post("/tenants", json={"name": name}, headers=ADMIN).json()["id"]


def _agent(client, name: str, tenant_id: int | None) -> tuple[int, str]:
    a = client.post("/agents", json={"name": name, "tenant_id": tenant_id}, headers=ADMIN).json()
    return a["id"], a["token"]


FGT_TOKEN = "fgt-geheim"  # noqa: S105 - Testwert


def _device(client, tenant_id: int, agent_id: int | None, token: str | None = FGT_TOKEN):
    body = {
        "tenant_id": tenant_id,
        "name": "fw-01",
        "platform": "fortigate",
        "base_url": "https://fw-01.kunde.lan",
        "agent_id": agent_id,
        "verify_tls": False,
        "token": token,
    }
    r = client.post("/systems", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


def _collect(client, tenant_id: int, sid: int, execution: str = "inprocess") -> dict:
    body = {
        "platform": "fortigate",
        "tenant_id": tenant_id,
        "source_system_id": sid,
        "execution": execution,
    }
    r = client.post("/jobs/collect", json=body, headers=ADMIN)
    assert r.status_code == 202, r.text
    return r.json()


def test_token_nur_schreibbar_und_verschluesselt(session_factory, client):
    t = _tenant(client, "kunde-a")
    agent_id, _ = _agent(client, "agent-a", t)
    dev = _device(client, t, agent_id)
    assert dev["has_credentials"] is True and dev["agent_id"] == agent_id
    assert "token" not in dev and "fgt-geheim" not in str(client.get("/systems").json())

    s = session_factory()
    raw = s.get(SourceSystem, dev["id"]).credentials
    assert raw and "fgt-geheim" not in raw  # nur verschlüsselt gespeichert
    detail = s.query(AuditLog).filter_by(action="source.created").one().detail
    assert detail["credentials_set"] is True and "fgt-geheim" not in str(detail)
    s.close()

    # Token entfernen ("") bzw. Agent-Zuweisung aufheben (null)
    r = client.patch(f"/systems/{dev['id']}", json={"token": ""}, headers=ADMIN)
    assert r.json()["has_credentials"] is False
    r = client.patch(f"/systems/{dev['id']}", json={"agent_id": None}, headers=ADMIN)
    assert r.json()["agent_id"] is None


def test_agent_muss_zum_mandanten_passen(client):
    a, b = _tenant(client, "kunde-a"), _tenant(client, "kunde-b")
    agent_b, _ = _agent(client, "agent-b", b)
    body = {"tenant_id": a, "name": "fw", "platform": "fortigate", "agent_id": agent_b}
    assert client.post("/systems", json=body, headers=ADMIN).status_code == 422
    global_agent, _ = _agent(client, "agent-global", None)  # mandantsübergreifend erlaubt
    assert (
        client.post("/systems", json={**body, "agent_id": global_agent}, headers=ADMIN).status_code
        == 201
    )
    assert (
        client.post(
            "/systems", json={**body, "name": "x", "agent_id": 999}, headers=ADMIN
        ).status_code
        == 422
    )


def test_nur_zugewiesener_agent_holt_ab_und_bekommt_zugangsdaten(session_factory, client):
    t = _tenant(client, "kunde-a")
    agent_a, token_a = _agent(client, "agent-a", t)
    _, token_b = _agent(client, "agent-b", t)  # gleicher Mandant, aber nicht zugewiesen
    dev = _device(client, t, agent_a)

    job = _collect(client, t, dev["id"])  # "inprocess" angefragt ...
    assert job["execution"] == "external"  # ... aber Gerät hat einen Agenten

    other = client.get("/internal/agent/next-run", headers={"X-Agent-Token": token_b}).json()
    assert other["run"] is None
    got = client.get("/internal/agent/next-run", headers={"X-Agent-Token": token_a}).json()["run"]
    assert got["run_id"] == job["run_id"]
    assert got["target"] == {
        "source_system_id": dev["id"],
        "name": "fw-01",
        "platform": "fortigate",
        "base_url": "https://fw-01.kunde.lan",
        "verify_tls": False,
        "token": "fgt-geheim",
    }

    targets = client.get("/internal/agent/targets", headers={"X-Agent-Token": token_a}).json()
    assert [x["token"] for x in targets] == ["fgt-geheim"]
    assert client.get("/internal/agent/targets", headers={"X-Agent-Token": token_b}).json() == []

    agents = {a["name"]: a for a in client.get("/agents", headers=ADMIN).json()}
    assert [x["name"] for x in agents["agent-a"]["systems"]] == ["fw-01"]


def test_ergebnis_nur_vom_abholenden_agenten(session_factory, client):
    t = _tenant(client, "kunde-a")
    agent_a, token_a = _agent(client, "agent-a", t)
    _, token_b = _agent(client, "agent-b", t)
    dev = _device(client, t, agent_a)
    run_id = _collect(client, t, dev["id"])["run_id"]
    client.get("/internal/agent/next-run", headers={"X-Agent-Token": token_a})

    url = f"/internal/collector-runs/{run_id}/result"
    assert client.post(url, json=RESULT, headers={"X-Agent-Token": token_b}).status_code == 403
    assert client.post(url, json=RESULT, headers={"X-Agent-Token": "falsch"}).status_code == 401
    assert client.post(url, json=RESULT).status_code == 401
    assert client.post(url, json=RESULT, headers={"X-Agent-Token": token_a}).status_code == 204

    s = session_factory()
    run = s.get(CollectorRun, run_id)
    assert run.status == "completed" and run.agent_id == agent_a
    assert (
        s.query(AuditLog).filter_by(action="collector_run.completed").one().actor == "agent:agent-a"
    )
    s.close()


def test_zentraler_worker_bekommt_ziel_nur_fuer_eigene_laeufe(session_factory, client):
    s = session_factory()
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    s.add(t)
    s.commit()
    tid = t.id
    s.close()
    internal = {"X-Internal-Token": "dev-only-token"}

    central = _device(client, tid, None)  # ohne Agent -> zentraler Worker
    run_id = _collect(client, tid, central["id"])["run_id"]
    r = client.get(f"/internal/collector-runs/{run_id}/target", headers=internal)
    assert r.status_code == 200 and r.json()["target"]["token"] == "fgt-geheim"
    assert client.get(f"/internal/collector-runs/{run_id}/target").status_code == 422  # ohne Token

    agent_id, _ = _agent(client, "agent-a", tid)
    client.patch(f"/systems/{central['id']}", json={"agent_id": agent_id}, headers=ADMIN)
    ext = _collect(client, tid, central["id"])["run_id"]
    assert client.get(f"/internal/collector-runs/{ext}/target", headers=internal).status_code == 403


def test_mandant_loeschen_entkoppelt_agent_zuweisungen(client):
    t = _tenant(client, "kunde-a")
    agent_id, token = _agent(client, "agent-a", t)
    dev = _device(client, t, agent_id)
    _collect(client, t, dev["id"])
    client.get("/internal/agent/next-run", headers={"X-Agent-Token": token})
    r = client.delete(f"/tenants/{t}?confirm=kunde-a", headers=ADMIN)
    assert r.status_code == 200 and r.json()["agents"] == 1


def test_schluesseldatei_wird_einmalig_erzeugt(tmp_path):
    import os

    from app import credentials
    from app.config import get_settings

    settings = get_settings()
    key_file = tmp_path / "keys" / "credentials.key"
    old = (settings.credentials_key, settings.credentials_key_file)
    settings.credentials_key, settings.credentials_key_file = "", str(key_file)
    credentials._fernet.cache_clear()
    try:
        token = credentials.encrypt({"token": "x"})
        assert key_file.is_file() and (os.stat(key_file).st_mode & 0o777) == 0o600
        credentials._fernet.cache_clear()  # Neustart: gleicher Schlüssel aus der Datei
        assert credentials.decrypt(token) == {"token": "x"}
        key_file.write_bytes(b"ein-anderer-schluessel-passt-nicht-zu-fernet")
        credentials._fernet.cache_clear()
        try:
            credentials.decrypt(token)
            raise AssertionError("falscher Schlüssel darf nicht entschlüsseln")
        except credentials.CredentialsUnavailable:
            pass
    finally:
        settings.credentials_key, settings.credentials_key_file = old
        credentials._fernet.cache_clear()
