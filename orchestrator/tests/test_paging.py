"""Findings und NAT seitenweise mit Gesamtzahl; Zähler per Summary (keine stille Kappung)."""

from app.models import (
    Finding,
    FindingStatus,
    FindingType,
    IpamType,
    NatRule,
    Platform,
    SourceSystem,
    SubjectKind,
    Tenant,
)
from app.models.tenant import DnsType


def _seed(session) -> int:
    t = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    session.add(t)
    session.flush()
    fw = SourceSystem(tenant_id=t.id, name="fw", platform=Platform.FORTIGATE, base_url="")
    session.add(fw)
    session.flush()
    for i in range(7):
        session.add(
            Finding(
                tenant_id=t.id,
                type=FindingType.UNMATCHED_OBJECT if i < 5 else FindingType.UNUSED_OBJECT,
                status=FindingStatus.OPEN if i < 6 else FindingStatus.IGNORED,
                subject_kind=SubjectKind.NETWORK_OBJECT,
                subject_id=f"H_srv{i:02d}",
                detail={"value": f"10.0.0.{i}"},
            )
        )
    for i in range(4):
        session.add(
            NatRule(
                tenant_id=t.id,
                source_system_id=fw.id,
                name=f"VIP_{i}",
                external_id=f"v{i}",
                nat_type="dnat_vip" if i < 3 else "snat_pool",
            )
        )
    session.commit()
    return t.id


def test_findings_seitenweise_suche_und_summary(session_factory, client):
    tid = _seed(session_factory())
    page = client.get("/findings", params={"tenant_id": tid, "limit": 2})
    assert len(page.json()) == 2 and page.headers["X-Total-Count"] == "7"
    rest = client.get("/findings", params={"tenant_id": tid, "limit": 5, "offset": 5})
    assert [f["subject_id"] for f in rest.json()] == ["H_srv05", "H_srv06"]

    opened = client.get("/findings", params={"tenant_id": tid, "status": "open"})
    assert opened.headers["X-Total-Count"] == "6"
    hit = client.get("/findings", params={"tenant_id": tid, "q": "10.0.0.3"})  # im Detail
    assert [f["subject_id"] for f in hit.json()] == ["H_srv03"]

    s = client.get("/findings/summary", params={"tenant_id": tid}).json()
    assert s["total"] == 7 and s["open"] == 6
    assert s["by_status"] == {"open": 6, "ignored": 1}
    assert s["by_type"] == {
        "unmatched_object": {"open": 5, "total": 5},
        "unused_object": {"open": 1, "total": 2},
    }
    typed = client.get("/findings/summary", params={"tenant_id": tid, "type": "unused_object"})
    assert typed.json()["by_status"] == {"open": 1, "ignored": 1}
    assert client.get("/findings", params={"limit": 0}).status_code == 422


def test_nat_seitenweise_mit_typfilter(session_factory, client):
    tid = _seed(session_factory())
    page = client.get("/nat", params={"tenant_id": tid, "limit": 1})
    assert len(page.json()) == 1 and page.headers["X-Total-Count"] == "4"
    pools = client.get("/nat", params={"tenant_id": tid, "nat_type": "snat_pool"})
    assert pools.headers["X-Total-Count"] == "1" and pools.json()[0]["name"] == "VIP_3"
