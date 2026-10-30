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


def _more(session, tid: int, n: int) -> list[int]:
    out = []
    for i in range(n):
        f = Finding(tenant_id=tid, type=FindingType.UNMATCHED_OBJECT,
                    subject_kind=SubjectKind.RULE, subject_id=f"r{i + 2}")  # fmt: skip
        session.add(f)
        session.flush()
        out.append(f.id)
    return out


def test_mehrere_auf_einmal_ignorieren(session_factory, client):
    """0.117.0 (Anwender: Multi-Checkbox für das Ignorieren): Status und Notiz für alle, je
    Finding ein Audit-Eintrag."""
    seed = session_factory()
    tid, fid = _tenant_with_finding(seed, "kunde-a")
    ids = [fid, *_more(seed, tid, 2)]
    seed.commit()
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    r = client.post("/findings/triage", json={"ids": [*ids, fid], "note": "bekannt"}, headers=op)
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 3, "status": "ignored"}
    s = session_factory()
    assert {s.get(Finding, i).status for i in ids} == {FindingStatus.IGNORED}
    assert {s.get(Finding, i).note for i in ids} == {"bekannt"}
    rows = [
        audit.decode_detail(a) for a in s.query(AuditLog).all() if a.action == "finding.triaged"
    ]
    assert sorted(d["finding_id"] for d in rows) == sorted(ids)
    assert all(d["bulk"] is True and d["status"] == "ignored" for d in rows)
    # Status frei wählbar, ohne Notiz bleibt die Notiz
    r = client.post("/findings/triage", json={"ids": ids[:1], "status": "open"}, headers=op)
    assert r.json()["status"] == "open"
    s = session_factory()
    assert s.get(Finding, ids[0]).status is FindingStatus.OPEN and s.get(Finding, ids[0]).note


def test_mehrere_ganz_oder_gar_nicht(session_factory, client):
    seed = session_factory()
    a, fa = _tenant_with_finding(seed, "kunde-a")
    _, fb = _tenant_with_finding(seed, "kunde-b")
    seed.commit()
    op = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(a)}  # nur a
    assert client.post("/findings/triage", json={"ids": [fa, fb]}, headers=op).status_code == 403
    assert client.post("/findings/triage", json={"ids": [fa, 999999]},
                       headers=op).status_code == 404  # fmt: skip
    assert client.post("/findings/triage", json={"ids": [fa], "status": "bogus"},
                       headers=op).status_code == 422  # fmt: skip
    assert client.post("/findings/triage", json={"ids": []}, headers=op).status_code == 422
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(a)}
    assert client.post("/findings/triage", json={"ids": [fa]}, headers=viewer).status_code == 403
    s = session_factory()
    assert s.get(Finding, fa).status is FindingStatus.OPEN  # nichts geändert
