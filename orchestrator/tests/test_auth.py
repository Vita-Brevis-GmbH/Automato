"""AuthN/RBAC + Mandanten-Scoping (E1, ADR 0007).

dev-Modus: ohne Header = Admin (alle Mandanten); Override via X-Dev-Roles/X-Dev-Tenants.
"""

from app.auth import roles as R
from app.models import (
    IpamType,
    Platform,
    Rule,
    RuleAction,
    RuleDomain,
    SourceSystem,
    Tenant,
)
from app.models.tenant import DnsType


def _tenant_with_rule(session, name: str) -> int:
    t = Tenant(name=name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(
        name=f"fw-{name}", platform=Platform.FORTIGATE, base_url="https://fw", enabled=True
    )
    session.add_all([t, fw])
    session.flush()
    session.add(
        Rule(
            tenant_id=t.id,
            source_system_id=fw.id,
            domain=RuleDomain.FIREWALL,
            external_id="1",
            name=f"r-{name}",
            action=RuleAction.ALLOW,
        )
    )
    session.flush()
    return t.id


def test_rollen_hierarchie():
    assert R.satisfies(frozenset({R.ADMIN}), R.VIEWER)
    assert R.satisfies(frozenset({R.OPERATOR}), R.VIEWER)
    assert R.satisfies(frozenset({R.OPERATOR}), R.OPERATOR)
    assert not R.satisfies(frozenset({R.VIEWER}), R.OPERATOR)
    assert not R.satisfies(frozenset(), R.VIEWER)


def test_dev_default_ist_admin_sieht_alles(session_factory, client):
    seed = session_factory()
    _tenant_with_rule(seed, "kunde-a")
    b = _tenant_with_rule(seed, "kunde-b")
    seed.commit()

    r = client.get("/rules")  # ohne Header -> dev-Admin
    assert r.status_code == 200
    assert len(r.json()) == 2
    assert client.get(f"/rules?tenant_id={b}").status_code == 200


def test_viewer_scoping_und_fremder_mandant_403(session_factory, client):
    seed = session_factory()
    a = _tenant_with_rule(seed, "kunde-a")
    b = _tenant_with_rule(seed, "kunde-b")
    seed.commit()

    h = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(a)}
    r = client.get("/rules", headers=h)  # ohne tenant_id -> auf a verengt
    assert r.status_code == 200
    assert {row["tenant_id"] for row in r.json()} == {a}

    assert client.get(f"/rules?tenant_id={b}", headers=h).status_code == 403  # fremd

    tr = client.get("/tenants", headers=h)
    assert [t["id"] for t in tr.json()] == [a]  # sieht nur den eigenen Mandanten


def test_ohne_rolle_403(session_factory, client):
    seed = session_factory()
    _tenant_with_rule(seed, "kunde-a")
    seed.commit()
    assert client.get("/rules", headers={"X-Dev-Roles": ""}).status_code == 403


def test_collect_erfordert_operator(session_factory, client):
    seed = session_factory()
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(name="fw", platform=Platform.FORTIGATE, base_url="https://fw", enabled=True)
    seed.add_all([t, fw])
    seed.flush()
    body = {"platform": "fortigate", "tenant_id": t.id, "source_system_id": fw.id}
    seed.commit()

    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(body["tenant_id"])}
    assert client.post("/jobs/collect", json=body, headers=viewer).status_code == 403

    operator = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(body["tenant_id"])}
    assert client.post("/jobs/collect", json=body, headers=operator).status_code == 202
