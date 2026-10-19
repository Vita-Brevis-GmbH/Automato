"""Löschen von Quellsystemen und Mandanten: Kaskade, Schutz vor Versehen, RBAC, Audit."""

from app.models import (
    Agent,
    AuditLog,
    CollectorRun,
    Epg,
    EpgEndpoint,
    EpgSubnet,
    Finding,
    IpamType,
    NatRule,
    NetworkObject,
    Platform,
    Rule,
    RuleEndpoint,
    Site,
    SourceSystem,
    Tenant,
    Zone,
)
from app.models.tenant import DnsType
from tests.test_internal_ingest import TOKEN, _FakeSoT

ADMIN = {"X-Dev-Roles": "admin"}

RESULT = {
    "status": "completed",
    "rules": [
        {
            "source_platform": "fortigate",
            "external_id": "1",
            "name": "allow-web",
            "action": "allow",
            "sources": ["10.10.20.0/24"],
            "destinations": ["10.10.10.5"],
            "services": ["tcp/443"],
            "dst_zone": "server",
        }
    ],
    "nat_rules": [
        {
            "source_platform": "fortigate",
            "external_id": "VIP_web",
            "name": "VIP_web",
            "ext_ip": "203.0.113.10",
            "mapped_ip": "10.10.10.5",
        }
    ],
}


def _tenant_with_data(client, session_factory, name: str) -> tuple[int, int]:
    """Mandant + Quellsystem + ein echter Lauf (Regeln, NAT, Objekte, Zone, Finding) +
    Standort, EPG mit Subnetz/Endpunkt und Agent."""
    s = session_factory()
    t = Tenant(name=name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    s.add(t)
    s.flush()
    fw = SourceSystem(
        tenant_id=t.id, name="fw-01", platform=Platform.FORTIGATE, base_url="", enabled=True
    )
    site = Site(tenant_id=t.id, code="000", name="RZ01")
    epg = Epg(tenant_id=t.id, name="EG_app")
    s.add_all([fw, site, epg])
    s.flush()
    s.add_all(
        [
            EpgSubnet(epg_id=epg.id, ip="10.20.0.0/24"),
            EpgEndpoint(epg_id=epg.id, ip="10.20.0.5"),
            Agent(name=f"agent-{name}", token_hash=f"h-{name}", tenant_id=t.id),
        ]
    )
    s.commit()
    tid, sid = t.id, fw.id
    s.close()

    run_id = client.post(
        "/jobs/collect",
        json={"platform": "fortigate", "tenant_id": tid, "source_system_id": sid},
    ).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=RESULT)
    assert r.status_code == 204
    return tid, sid


def _count(session_factory, model, **where) -> int:
    s = session_factory()
    n = s.query(model).filter_by(**where).count()
    s.close()
    return n


def test_quellsystem_loeschen_nimmt_regeln_und_nat_mit(session_factory, client, monkeypatch):
    monkeypatch.setattr("app.api.internal.build_adapter", lambda tenant, settings: _FakeSoT())
    a, sid_a = _tenant_with_data(client, session_factory, "kunde-a")
    b, _ = _tenant_with_data(client, session_factory, "kunde-b")

    system = next(x for x in client.get(f"/systems?tenant_id={a}").json() if x["id"] == sid_a)
    assert (system["rule_count"], system["nat_count"]) == (1, 1)  # Basis für die Rückfrage

    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(a)}
    assert client.delete(f"/systems/{sid_a}", headers=op).status_code == 403
    r = client.delete(f"/systems/{sid_a}", headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == {"id": sid_a, "rules": 1, "nat_rules": 1, "findings": 1}

    assert _count(session_factory, SourceSystem, id=sid_a) == 0
    assert _count(session_factory, Rule, tenant_id=a) == 0
    assert _count(session_factory, NatRule, tenant_id=a) == 0
    # Lauf bleibt als Historie, ohne Quellsystem; Mandantendaten (Objekte) bleiben
    assert _count(session_factory, CollectorRun, tenant_id=a, source_system_id=None) == 1
    assert _count(session_factory, NetworkObject, tenant_id=a) > 0
    assert _count(session_factory, Finding, tenant_id=a) == 0  # Befund der gelöschten Regel
    # anderer Mandant unberührt
    assert _count(session_factory, Rule, tenant_id=b) == 1
    assert _count(session_factory, Finding, tenant_id=b) == 1
    assert client.delete(f"/systems/{sid_a}", headers=ADMIN).status_code == 404

    s = session_factory()
    detail = s.query(AuditLog).filter_by(action="source.deleted").one().detail
    assert detail["name"] == "fw-01" and detail["rules"] == 1 and detail["tenant_id"] == a
    s.close()


def test_mandant_loeschen_nur_mit_bestaetigung_und_vollstaendig(
    session_factory, client, monkeypatch
):
    monkeypatch.setattr("app.api.internal.build_adapter", lambda tenant, settings: _FakeSoT())
    a, _ = _tenant_with_data(client, session_factory, "kunde-a")
    b, _ = _tenant_with_data(client, session_factory, "kunde-b")

    assert client.delete(f"/tenants/{a}", headers=ADMIN).status_code == 422  # ohne confirm
    assert client.delete(f"/tenants/{a}?confirm=kunde-b", headers=ADMIN).status_code == 422
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(a)}
    assert client.delete(f"/tenants/{a}?confirm=kunde-a", headers=op).status_code == 403

    r = client.delete(f"/tenants/{a}?confirm=kunde-a", headers=ADMIN)
    assert r.status_code == 200
    counts = r.json()
    assert counts["rules"] == 1 and counts["nat_rules"] == 1 and counts["source_systems"] == 1
    assert counts["sites"] == 1 and counts["epgs"] == 1 and counts["agents"] == 1
    assert counts["runs"] == 1 and counts["objects"] > 0

    for model in (Rule, NatRule, Finding, Epg, NetworkObject, Zone, Site, SourceSystem, Agent):
        assert _count(session_factory, model, tenant_id=a) == 0, model.__name__
    assert _count(session_factory, CollectorRun, tenant_id=a) == 0
    assert _count(session_factory, Tenant, id=a) == 0
    assert _count(session_factory, EpgSubnet) == 1  # nur noch die von kunde-b
    assert _count(session_factory, RuleEndpoint) == 2  # Regel von kunde-b (src + dst)

    # kunde-b vollständig erhalten
    for model in (Rule, NatRule, SourceSystem, Site, Epg, Agent):
        assert _count(session_factory, model, tenant_id=b) == 1, model.__name__
    assert [t["name"] for t in client.get("/tenants").json()] == ["kunde-b"]
    assert client.delete(f"/tenants/{a}?confirm=kunde-a", headers=ADMIN).status_code == 404

    s = session_factory()
    detail = s.query(AuditLog).filter_by(action="tenant.deleted").one().detail
    assert detail["name"] == "kunde-a" and detail["agents"] == 1
    s.close()
