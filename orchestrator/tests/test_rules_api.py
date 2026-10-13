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


def test_tenants_endpoint(session_factory, client):
    seed = session_factory()
    _seed(seed)

    r = client.get("/tenants")
    assert r.status_code == 200
    rows = r.json()
    assert [t["name"] for t in rows] == ["lab"]
    assert rows[0]["ipam_type"] == "netbox"
