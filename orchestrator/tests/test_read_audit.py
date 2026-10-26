"""Per-Mandant zuschaltbares Lesezugriffs-Audit (E1, ADR 0007 D5)."""

from app.models import (
    AuditLog,
    IpamType,
    Platform,
    Rule,
    RuleAction,
    RuleDomain,
    SourceSystem,
    Tenant,
)
from app.models.tenant import DnsType


def _tenant_with_rule(session, name: str, audit_reads: bool = False) -> int:
    t = Tenant(
        name=name,
        ipam_type=IpamType.NETBOX,
        dns_type=DnsType.ACTIVE_DIRECTORY,
        audit_reads=audit_reads,
    )
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


def _read_rows(session):
    return [r for r in session.query(AuditLog).all() if r.action.startswith("read.")]


def test_read_audit_aus_default(session_factory, client):
    seed = session_factory()
    tid = _tenant_with_rule(seed, "kunde-a", audit_reads=False)
    seed.commit()
    assert client.get(f"/rules?tenant_id={tid}").status_code == 200
    assert _read_rows(session_factory()) == []  # kein Audit, Flag aus


def test_read_audit_an_protokolliert(session_factory, client):
    seed = session_factory()
    tid = _tenant_with_rule(seed, "kunde-a", audit_reads=True)
    seed.commit()

    assert client.get(f"/rules?tenant_id={tid}").status_code == 200
    assert client.get(f"/nat?tenant_id={tid}").status_code == 200

    rows = _read_rows(session_factory())
    actions = sorted(r.action for r in rows)
    assert actions == ["read.nat", "read.rules"]
    from app import audit

    assert audit.decode_detail(rows[0]) == {
        "tenant_id": tid,
        "resource": rows[0].action.split(".")[1],
    }


def test_read_audit_nur_bei_konkretem_mandant(session_factory, client):
    # tenant_id=None (alle Mandanten / Admin-Gesamtsicht) wird nicht auditiert
    seed = session_factory()
    _tenant_with_rule(seed, "kunde-a", audit_reads=True)
    seed.commit()
    assert client.get("/rules").status_code == 200  # ohne tenant_id
    assert _read_rows(session_factory()) == []


def test_patch_schaltet_read_audit_ein(session_factory, client):
    seed = session_factory()
    tid = _tenant_with_rule(seed, "kunde-a", audit_reads=False)
    seed.commit()
    admin = {"X-Dev-Roles": "admin"}

    r = client.patch(f"/tenants/{tid}", json={"audit_reads": True}, headers=admin)
    assert r.status_code == 200 and r.json()["audit_reads"] is True
    # jetzt wird gelesen -> auditiert
    client.get(f"/findings?tenant_id={tid}")
    assert any(r.action == "read.findings" for r in _read_rows(session_factory()))
