"""W1: /rules liefert Quelle/Ziel-Objektnamen (mehrere); /tenants fuer den Switcher."""

from app.models import (
    EndpointKind,
    EndpointRole,
    IpamType,
    MatchStatus,
    NetworkObject,
    ObjectClass,
    Platform,
    Proto,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    ServiceObject,
    SourceSystem,
    Tenant,
    Zone,
)
from app.models.tenant import DnsType


def _seed(session) -> int:
    tenant = Tenant(name="lab", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(
        name="fw-01", platform=Platform.FORTIGATE, base_url="https://fw", enabled=True
    )
    z_app = Zone(name="app")
    z_db = Zone(name="db")
    session.add_all([tenant, fw, z_app, z_db])
    session.flush()

    src1 = NetworkObject(
        tenant_id=tenant.id,
        name="SRV-APP-01",
        object_class=ObjectClass.HOST,
        value="10.20.6.11",
        match_status=MatchStatus.MATCHED,
    )
    src2 = NetworkObject(
        tenant_id=tenant.id,
        name="SRV-APP-02",
        object_class=ObjectClass.HOST,
        value="10.20.6.12",
        match_status=MatchStatus.MATCHED,
    )
    dst1 = NetworkObject(
        tenant_id=tenant.id,
        name="NET-DB",
        object_class=ObjectClass.NETWORK,
        value="10.20.9.0/24",
        match_status=MatchStatus.MATCHED,
    )
    dst2 = NetworkObject(
        tenant_id=tenant.id,
        name="NET-DB-REPL",
        object_class=ObjectClass.NETWORK,
        value="10.20.19.0/24",
        match_status=MatchStatus.UNMATCHED,
    )
    svc = ServiceObject(proto=Proto.TCP, port_start=1433, port_end=1433, canonical_name="tcp/1433")
    session.add_all([src1, src2, dst1, dst2, svc])
    session.flush()

    rule = Rule(
        tenant_id=tenant.id,
        source_system_id=fw.id,
        domain=RuleDomain.FIREWALL,
        external_id="18",
        name="APP_TIER_SQL",
        action=RuleAction.ALLOW,
        sequence=10,
        src_zone_id=z_app.id,
        dst_zone_id=z_db.id,
    )
    for obj in (src1, src2):
        rule.endpoints.append(
            RuleEndpoint(
                role=EndpointRole.SRC,
                kind=EndpointKind.HOST,
                network_object_id=obj.id,
                matched=True,
            )
        )
    for obj, matched in ((dst1, True), (dst2, False)):
        rule.endpoints.append(
            RuleEndpoint(
                role=EndpointRole.DST,
                kind=EndpointKind.NETWORK,
                network_object_id=obj.id,
                matched=matched,
            )
        )
    rule.services.append(svc)
    session.add(rule)
    session.commit()
    return tenant.id


def test_rules_list_trennt_quelle_ziel_mehrere_objekte(session_factory, client):
    seed = session_factory()
    _seed(seed)

    r = client.get("/rules")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    row = data[0]
    assert row["sources"] == ["SRV-APP-01", "SRV-APP-02"]
    assert set(row["destinations"]) == {"NET-DB", "NET-DB-REPL"}
    assert row["num_src"] == 2 and row["num_dst"] == 2
    assert row["num_unmatched"] == 1


def _seed_mixed(session) -> tuple[int, int, int]:
    """5 FortiGate-Policies + 2 ACI-Contract-Subjects (APIC nach der FortiGate angelegt)."""
    tenant = Tenant(name="mix", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(name="fw", platform=Platform.FORTIGATE, base_url="https://fw", enabled=True)
    apic = SourceSystem(
        name="apic", platform=Platform.CISCO_ACI, base_url="https://apic", enabled=True
    )
    session.add_all([tenant, fw, apic])
    session.flush()
    for i in range(5):
        session.add(
            Rule(
                tenant_id=tenant.id,
                source_system_id=fw.id,
                domain=RuleDomain.FIREWALL,
                external_id=str(i),
                name=f"FW_{i}",
                action=RuleAction.ALLOW,
                sequence=i,
            )
        )
    dn = "uni/tn-TE_share/brc-CT_web"
    for subject in ("SJ_https", "SJ_dns"):
        rule = Rule(
            tenant_id=tenant.id,
            source_system_id=apic.id,
            domain=RuleDomain.ACI_CONTRACT,
            external_id=f"{dn}/{subject}",
            name=f"CT_web:{subject}",
            action=RuleAction.ALLOW,
            aci_scope="tenant",
            raw={"dn": dn, "name": "CT_web", "scope": "tenant"},
        )
        rule.endpoints.append(
            RuleEndpoint(role=EndpointRole.SRC, kind=EndpointKind.EPG, inline_value="EPG_app")
        )
        rule.endpoints.append(
            RuleEndpoint(role=EndpointRole.DST, kind=EndpointKind.EPG, inline_value="EPG_web")
        )
        session.add(rule)
    session.commit()
    return tenant.id, fw.id, apic.id


def test_aci_contracts_trotz_seitengrenze_sichtbar(session_factory, client):
    """Dev-Befund: bei vielen FortiGate-Policies fielen die ACI-Contracts aus der Liste."""
    tenant_id, fw_id, apic_id = _seed_mixed(session_factory())

    page = client.get("/rules", params={"tenant_id": tenant_id, "limit": 3})
    assert len(page.json()) == 3 and page.headers["X-Total-Count"] == "7"
    rest = client.get("/rules", params={"tenant_id": tenant_id, "limit": 3, "offset": 6})
    assert [r["domain"] for r in rest.json()] == ["aci_contract"]

    aci = client.get("/rules", params={"tenant_id": tenant_id, "domain": "aci_contract"})
    rows = aci.json()
    assert aci.headers["X-Total-Count"] == "2" and len(rows) == 2
    assert rows[0]["sources"] == ["EPG_app"] and rows[0]["destinations"] == ["EPG_web"]
    assert rows[0]["aci"] == {
        "tenant": "TE_share",
        "contract": "CT_web",
        "subject": "SJ_https",
        "scope": "tenant",
        "dn": "uni/tn-TE_share/brc-CT_web",
    }
    fw = client.get("/rules", params={"tenant_id": tenant_id, "source_system_id": fw_id})
    assert fw.headers["X-Total-Count"] == "5" and all(r["aci"] is None for r in fw.json())

    # Suche findet Contracts auch jenseits der ersten Seite
    hit = client.get("/rules", params={"tenant_id": tenant_id, "q": "sj_dns", "limit": 3})
    assert [r["name"] for r in hit.json()] == ["CT_web:SJ_dns"]
    assert hit.headers["X-Total-Count"] == "1"
    detail = client.get(f"/rules/{hit.json()[0]['id']}").json()
    assert detail["aci"]["subject"] == "SJ_dns"

    s = client.get("/rules/summary", params={"tenant_id": tenant_id}).json()
    assert s["total"] == 7 and s["by_domain"] == {"firewall": 5, "aci_contract": 2}
    assert s["source_system_ids"] == sorted([fw_id, apic_id]) and s["endpoints"] == 4
    assert client.get("/rules", params={"domain": "taboo"}).status_code == 422


def test_rules_summary_zaehlt_unaufgeloeste_und_zonen(session_factory, client):
    tenant_id = _seed(session_factory())
    s = client.get("/rules/summary", params={"tenant_id": tenant_id}).json()
    assert s["total"] == 1 and s["endpoints"] == 4 and s["unmatched"] == 1
    assert s["by_dst_zone"] == [["db", 1]]
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tenant_id + 1)}
    r = client.get("/rules/summary", params={"tenant_id": tenant_id}, headers=viewer)
    assert r.status_code == 403  # fremder Mandant


def test_tenants_endpoint(session_factory, client):
    seed = session_factory()
    _seed(seed)

    r = client.get("/tenants")
    assert r.status_code == 200
    rows = r.json()
    assert [t["name"] for t in rows] == ["lab"]
    assert rows[0]["ipam_type"] == "netbox"
