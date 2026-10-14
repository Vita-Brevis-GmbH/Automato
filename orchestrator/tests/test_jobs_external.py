"""Externer Lauf-Modus (ADR 0008): execution=external legt den Lauf an, ohne Dispatch."""

from app.models import IpamType, Platform, SourceSystem, Tenant
from app.models.tenant import DnsType


def _seed(session) -> tuple[int, int]:
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(name="fw", platform=Platform.FORTIGATE, base_url="https://fw", enabled=True)
    session.add_all([t, fw])
    session.flush()
    return t.id, fw.id


def test_external_legt_lauf_an_ohne_dispatch(session_factory, client):
    seed = session_factory()
    tid, sid = _seed(seed)
    seed.commit()
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}

    body = {
        "platform": "fortigate",
        "tenant_id": tid,
        "source_system_id": sid,
        "execution": "external",
    }
    r = client.post("/jobs/collect", json=body, headers=op)
    assert r.status_code == 202
    assert client.dispatched == []  # kein Celery-Dispatch im externen Modus
    run_id = r.json()["run_id"]
    assert client.get(f"/jobs/{run_id}", headers=op).status_code == 200  # Lauf existiert


def test_inprocess_dispatcht_weiterhin(session_factory, client):
    seed = session_factory()
    tid, sid = _seed(seed)
    seed.commit()
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}

    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}  # default inprocess
    r = client.post("/jobs/collect", json=body, headers=op)
    assert r.status_code == 202
    assert ("fortigate", r.json()["run_id"]) in client.dispatched
