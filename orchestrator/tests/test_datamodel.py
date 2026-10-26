"""Datenmodell UC1 (ADR 0004): Round-Trip ueber die Kern-Entitaeten."""

from app.models import (
    EndpointKind,
    EndpointRole,
    Finding,
    FindingType,
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
    SubjectKind,
    Tenant,
    Zone,
)
from app.models.tenant import DnsType
from app.store import rules as rules_store


def _seed_rule(session):
    tenant = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(
        name="fw-01", platform=Platform.FORTIGATE, base_url="https://fw-01.lab", enabled=True
    )
    zone_dmz = Zone(name="DMZ")
    session.add_all([tenant, fw, zone_dmz])
    session.flush()

    host = NetworkObject(
        tenant_id=tenant.id,
        name="H_web01.kunde-a.local",
        object_class=ObjectClass.HOST,
        value="10.1.2.3",
        zone_id=zone_dmz.id,
        match_status=MatchStatus.MATCHED,
    )
    svc = ServiceObject(proto=Proto.TCP, port_start=443, port_end=443, canonical_name="tcp/443")
    session.add_all([host, svc])
    session.flush()

    rule = Rule(
        tenant_id=tenant.id,
        source_system_id=fw.id,
        domain=RuleDomain.FIREWALL,
        external_id="1",
        name="allow-web",
        action=RuleAction.ALLOW,
        sequence=10,
        section_label="Zone 4",
        dst_zone_id=zone_dmz.id,
    )
    rule.endpoints.append(
        RuleEndpoint(
            role=EndpointRole.DST, kind=EndpointKind.HOST, network_object_id=host.id, matched=True
        )
    )
    rule.services.append(svc)
    session.add(rule)
    session.commit()
    return tenant.id


def test_regel_mit_endpunkt_und_service(session_factory):
    session = session_factory()
    tenant_id = _seed_rule(session)

    rules = rules_store.list_rules(session, tenant_id=tenant_id)
    assert len(rules) == 1
    r = rules[0]
    assert r.action is RuleAction.ALLOW
    assert r.section_label == "Zone 4"
    assert len(r.endpoints) == 1
    assert r.endpoints[0].kind is EndpointKind.HOST
    assert r.endpoints[0].matched is True
    assert [s.canonical_name for s in r.services] == ["tcp/443"]


def test_finding_anlegen(session_factory):
    session = session_factory()
    tenant = Tenant(name="kunde-b", ipam_type=IpamType.INFOBLOX, dns_type=DnsType.INFOBLOX)
    session.add(tenant)
    session.flush()

    finding = Finding(
        tenant_id=tenant.id,
        type=FindingType.UNMATCHED_OBJECT,
        subject_kind=SubjectKind.NETWORK_OBJECT,
        subject_id="H_ghost.kunde-b.local",
        detail={"reason": "nicht im IPAM gefunden"},
    )
    session.add(finding)
    session.commit()

    stored = session.get(Finding, finding.id)
    assert stored.status.value == "open"
    assert stored.detail["reason"] == "nicht im IPAM gefunden"
