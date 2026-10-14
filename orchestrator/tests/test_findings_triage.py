"""Finding-Triage-Schreibpfad (operator): Status/Notiz setzen, scoped + auditiert."""

from app import audit
from app.models import (
    AuditLog,
    Finding,
    FindingStatus,
    FindingType,
    IpamType,
    SubjectKind,
    Tenant,
)
from app.models.tenant import DnsType


def _tenant_with_finding(session, name: str) -> tuple[int, int]:
    t = Tenant(name=name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    session.add(t)
    session.flush()
    f = Finding(
        tenant_id=t.id,
        type=FindingType.UNMATCHED_OBJECT,
        subject_kind=SubjectKind.RULE,
        subject_id="r1",
    )
    session.add(f)
    session.flush()
    return t.id, f.id


def test_viewer_darf_nicht_triagieren(session_factory, client):
    seed = session_factory()
    _, fid = _tenant_with_finding(seed, "kunde-a")
    seed.commit()
    r = client.patch(
        f"/findings/{fid}", json={"status": "acknowledged"}, headers={"X-Dev-Roles": "viewer"}
    )
    assert r.status_code == 403


def test_operator_triagiert_und_auditiert(session_factory, client):
    seed = session_factory()
    tid, fid = _tenant_with_finding(seed, "kunde-a")
    seed.commit()
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}

    r = client.patch(
        f"/findings/{fid}", json={"status": "resolved", "note": "im DDI angelegt"}, headers=op
    )
    assert r.status_code == 200
    assert r.json()["status"] == "resolved" and r.json()["note"] == "im DDI angelegt"

    s = session_factory()
    assert s.get(Finding, fid).status is FindingStatus.RESOLVED
    rows = [a for a in s.query(AuditLog).all() if a.action == "finding.triaged"]
    assert len(rows) == 1
    assert audit.decode_detail(rows[0]) == {"finding_id": fid, "status": "resolved"}


def test_ungueltiger_status_422(session_factory, client):
    seed = session_factory()
    tid, fid = _tenant_with_finding(seed, "kunde-a")
    seed.commit()
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    assert client.patch(f"/findings/{fid}", json={"status": "bogus"}, headers=op).status_code == 422


def test_fremder_mandant_403(session_factory, client):
    seed = session_factory()
    a, _ = _tenant_with_finding(seed, "kunde-a")
    _, fb = _tenant_with_finding(seed, "kunde-b")
    seed.commit()
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(a)}  # nur a
    assert (
        client.patch(f"/findings/{fb}", json={"status": "ignored"}, headers=op).status_code == 403
    )
