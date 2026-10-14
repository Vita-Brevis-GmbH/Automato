"""Collector-Agenten (ADR 0008): Registrierung (admin) + Pull-Queue (Agent-Token)."""

from app.models import IpamType, Platform, SourceSystem, Tenant
from app.models.tenant import DnsType


def _seed(session) -> tuple[int, int]:
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(name="fw", platform=Platform.FORTIGATE, base_url="https://fw", enabled=True)
    session.add_all([t, fw])
    session.flush()
    return t.id, fw.id


def _external_run(client, tid, sid) -> str:
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    body = {
        "platform": "fortigate",
        "tenant_id": tid,
        "source_system_id": sid,
        "execution": "external",
    }
    return client.post("/jobs/collect", json=body, headers=op).json()["run_id"]


def test_create_agent_nur_admin(session_factory, client):
    seed = session_factory()
    tid, _ = _seed(seed)
    seed.commit()
    body = {"name": "agent-win-01", "tenant_id": tid}
    assert client.post("/agents", json=body, headers={"X-Dev-Roles": "operator"}).status_code == 403
    r = client.post("/agents", json=body, headers={"X-Dev-Roles": "admin"})
    assert r.status_code == 201
    assert r.json()["name"] == "agent-win-01" and len(r.json()["token"]) > 20


def test_next_run_claimt_externen_lauf(session_factory, client):
    seed = session_factory()
    tid, sid = _seed(seed)
    seed.commit()
    token = client.post(
        "/agents", json={"name": "a1", "tenant_id": tid}, headers={"X-Dev-Roles": "admin"}
    ).json()["token"]
    run_id = _external_run(client, tid, sid)

    h = {"X-Agent-Token": token}
    r = client.get("/internal/agent/next-run", headers=h)
    assert r.status_code == 200
    assert r.json()["run"]["run_id"] == run_id and r.json()["run"]["platform"] == "fortigate"
    # erneut -> bereits zugeteilt (RUNNING), nichts mehr offen
    assert client.get("/internal/agent/next-run", headers=h).json()["run"] is None


def test_next_run_ignoriert_inprocess(session_factory, client):
    seed = session_factory()
    tid, sid = _seed(seed)
    seed.commit()
    token = client.post(
        "/agents", json={"name": "a2", "tenant_id": tid}, headers={"X-Dev-Roles": "admin"}
    ).json()["token"]
    # inprocess-Lauf (Default) -> darf NICHT an den Agenten gehen
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    client.post(
        "/jobs/collect",
        json={"platform": "fortigate", "tenant_id": tid, "source_system_id": sid},
        headers=op,
    )
    assert (
        client.get("/internal/agent/next-run", headers={"X-Agent-Token": token}).json()["run"]
        is None
    )


def test_invalid_agent_token_401(client):
    assert (
        client.get("/internal/agent/next-run", headers={"X-Agent-Token": "nope"}).status_code == 401
    )
    assert (
        client.post("/internal/agent/heartbeat", headers={"X-Agent-Token": "nope"}).status_code
        == 401
    )


def test_heartbeat_ok(session_factory, client):
    token = client.post("/agents", json={"name": "a3"}, headers={"X-Dev-Roles": "admin"}).json()[
        "token"
    ]
    assert (
        client.post("/internal/agent/heartbeat", headers={"X-Agent-Token": token}).status_code
        == 204
    )


def test_disable_agent_sperrt_token_und_zeigt_status(session_factory, client):
    seed = session_factory()
    tid, _ = _seed(seed)
    seed.commit()
    admin = {"X-Dev-Roles": "admin"}
    created = client.post("/agents", json={"name": "a4", "tenant_id": tid}, headers=admin).json()
    aid, token = created["id"], created["token"]

    # deaktivieren (nur admin)
    assert (
        client.patch(
            f"/agents/{aid}", json={"enabled": False}, headers={"X-Dev-Roles": "operator"}
        ).status_code
        == 403
    )
    r = client.patch(f"/agents/{aid}", json={"enabled": False}, headers=admin)
    assert r.status_code == 200 and r.json()["enabled"] is False

    # deaktivierter Agent authentisiert nicht mehr
    assert (
        client.get("/internal/agent/next-run", headers={"X-Agent-Token": token}).status_code == 401
    )

    # Liste zeigt Status + last_seen-Feld
    row = next(a for a in client.get("/agents", headers=admin).json() if a["id"] == aid)
    assert row["enabled"] is False and "last_seen" in row


def test_patch_unbekannter_agent_404(client):
    assert (
        client.patch(
            "/agents/999", json={"enabled": True}, headers={"X-Dev-Roles": "admin"}
        ).status_code
        == 404
    )
