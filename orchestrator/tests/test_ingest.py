"""Ingest + Matching + Findings gegen einen Fake-SoT-Adapter (kein Lab, ADR 0004)."""

from app.models import (
    Finding,
    FindingType,
    IpamType,
    MatchStatus,
    Platform,
    Rule,
    ServiceObject,
    SourceSystem,
    Tenant,
    Zone,
)
from app.models.tenant import DnsType
from app.schemas.collector import CollectorResult
from app.store import ingest
from app.store import rules as rules_store
from sot_gateway.models import NetworkObject, NetworkObjectType


class FakeSoT:
    """In-memory SoT-Adapter fuer Tests (erfuellt get_by_name/search_by_ip)."""

    name = "netbox"

    def __init__(self, by_ip=None, by_name=None):
        self._by_ip = by_ip or {}
        self._by_name = by_name or {}

    def search_by_ip(self, ip):
        return self._by_ip.get(ip, [])

    def get_by_name(self, name):
        return self._by_name.get(name)


def _seed_run(session):
    tenant = Tenant(name="kunde-a", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(
        name="fw-01", platform=Platform.FORTIGATE, base_url="https://fw", enabled=True
    )
    session.add_all([tenant, fw])
    session.flush()
    from app.models import CollectorRun

    run = CollectorRun(platform="fortigate", tenant_id=tenant.id, source_system_id=fw.id)
    session.add(run)
    session.flush()
    return run


def _result():
    return CollectorResult.model_validate(
        {
            "status": "completed",
            "rules": [
                {
                    "source_platform": "fortigate",
                    "external_id": "1",
                    "name": "allow-dmz-to-web",
                    "action": "allow",
                    "sources": ["10.10.20.0/24"],
                    "destinations": ["10.10.10.5"],
                    "services": ["tcp/443"],
                    "src_zone": "dmz",
                    "dst_zone": "server",
                },
                {
                    "source_platform": "fortigate",
                    "external_id": "2",
                    "name": "deny-ghost",
                    "action": "deny",
                    "sources": ["N_ghost-net"],
                    "destinations": ["all"],
                    "services": ["ALL"],
                },
                {
                    "source_platform": "fortigate",
                    "external_id": "3",
                    "name": "allow-ext",
                    "action": "allow",
                    "sources": ["H-EXT_partner.example"],
                    "destinations": ["10.10.10.5"],
                    "services": ["tcp/443"],
                },
            ],
        }
    )


def _fake_sot():
    host = NetworkObject(
        name="web01",
        type=NetworkObjectType.HOST,
        value="10.10.10.5",
        tags=["zone-server"],
        source="netbox",
    )
    net = NetworkObject(
        name="dmz", type=NetworkObjectType.NETWORK, value="10.10.20.0/24", source="netbox"
    )
    return FakeSoT(by_ip={"10.10.10.5": [host], "10.10.20.0": [net]})


def test_ingest_persistiert_regeln_und_matcht(session_factory):
    session = session_factory()
    run = _seed_run(session)

    ingest.persist_result(session, run, _result(), _fake_sot())
    session.commit()

    rules = rules_store.list_rules(session, tenant_id=run.tenant_id)
    assert len(rules) == 3

    # Service dedupliziert kanonisch
    assert session.query(ServiceObject).filter_by(canonical_name="tcp/443").count() == 1

    # Zone aus SoT-Tag am Subnetz uebernommen
    assert session.query(Zone).filter_by(name="server").count() == 1

    # matched-Flags: 10.10.10.5 und 10.10.20.0/24 sind aufloesbar
    allow_rule = next(r for r in rules if r.external_id == "1")
    assert all(e.matched for e in allow_rule.endpoints)


def test_unmatched_erzeugt_finding_extern_nicht(session_factory):
    session = session_factory()
    run = _seed_run(session)
    ingest.persist_result(session, run, _result(), _fake_sot())
    session.commit()

    findings = session.query(Finding).all()
    # N_ghost-net ist nicht aufloesbar -> genau ein UNMATCHED_OBJECT-Finding
    unmatched = [f for f in findings if f.type is FindingType.UNMATCHED_OBJECT]
    assert [f.subject_id for f in unmatched] == ["N_ghost-net"]

    # H-EXT_ ist bewusst extern -> expected_external, KEIN Finding
    from app.models import NetworkObject as NObj

    ext = session.query(NObj).filter_by(name="H-EXT_partner.example").one()
    assert ext.match_status is MatchStatus.EXPECTED_EXTERNAL
    assert not any(f.subject_id == "H-EXT_partner.example" for f in findings)


def test_ingest_ist_idempotent(session_factory):
    session = session_factory()
    run = _seed_run(session)
    ingest.persist_result(session, run, _result(), _fake_sot())
    session.commit()
    ingest.persist_result(session, run, _result(), _fake_sot())
    session.commit()

    # Regeln des Quellsystems werden ersetzt, nicht dupliziert
    assert session.query(Rule).filter_by(source_system_id=run.source_system_id).count() == 3


def test_gleicher_service_mehrfach_je_regel_einmal(session_factory):
    """ACI: mehrere Filter-Eintraege ergeben denselben Service (Dev-Befund rule_service_pkey)."""
    session = session_factory()
    run = _seed_run(session)
    result = CollectorResult.model_validate(
        {
            "status": "completed",
            "rules": [
                {
                    "source_platform": "cisco_aci",
                    "external_id": "uni/tn-TE_a/brc-web/subj-s1",
                    "name": "web",
                    "action": "allow",
                    "sources": ["epg:EPG_app"],
                    "destinations": ["epg:EPG_web"],
                    "services": ["tcp/443", "TCP/443", "tcp/0443", "udp/53", "tcp/443"],
                }
            ],
        }
    )
    ingest.persist_result(session, run, result, _fake_sot())
    session.commit()
    (rule,) = session.query(Rule).all()
    assert sorted(s.canonical_name for s in rule.services) == ["tcp/443", "udp/53"]


def test_typisierte_endpunkte_werden_klassifiziert(session_factory):
    from app.models import NetworkObject as NObj

    session = session_factory()
    run = _seed_run(session)
    result = CollectorResult.model_validate(
        {
            "status": "completed",
            "rules": [
                {
                    "source_platform": "fortigate",
                    "external_id": "10",
                    "name": "mix",
                    "action": "allow",
                    "sources": [
                        "fqdn:ntp.example",
                        "geo:GEO_ch",
                        "range:172.26.0.100-172.26.0.150",
                    ],
                    "destinations": ["all"],
                    "services": ["udp/53"],
                }
            ],
        }
    )
    # Range-Start liegt in einem importierten Prefix -> Range matcht ueber das Prefix
    net = NetworkObject(
        name="ra", type=NetworkObjectType.NETWORK, value="172.26.0.0/24", source="netbox"
    )
    ingest.persist_result(session, run, result, FakeSoT(by_ip={"172.26.0.100": [net]}))
    session.commit()

    assert session.query(NObj).filter_by(name="ntp.example").one().match_status is (
        MatchStatus.EXPECTED_EXTERNAL
    )
    assert session.query(NObj).filter_by(name="GEO_ch").one().match_status is (
        MatchStatus.NOT_APPLICABLE
    )
    assert session.query(NObj).filter_by(name="172.26.0.100-172.26.0.150").one().match_status is (
        MatchStatus.MATCHED
    )
    # fqdn/geo erzeugen KEINE Fehl-Findings
    findings = session.query(Finding).all()
    assert not any(f.subject_id in ("ntp.example", "GEO_ch") for f in findings)


def test_vip_matcht_ueber_externe_ip(session_factory):
    from app.models import NetworkObject as NObj
    from app.models import ObjectClass

    session = session_factory()
    run = _seed_run(session)
    result = CollectorResult.model_validate(
        {
            "status": "completed",
            "rules": [
                {
                    "source_platform": "fortigate",
                    "external_id": "20",
                    "name": "inbound-vip",
                    "action": "allow",
                    "sources": ["all"],
                    "destinations": ["vip:VIP_web=203.0.113.10"],
                    "services": ["tcp/443"],
                }
            ],
        }
    )
    extip = NetworkObject(
        name="vip-web", type=NetworkObjectType.HOST, value="203.0.113.10", source="netbox"
    )
    ingest.persist_result(session, run, result, FakeSoT(by_ip={"203.0.113.10": [extip]}))
    session.commit()

    vip = session.query(NObj).filter_by(name="VIP_web").one()
    assert vip.object_class is ObjectClass.VIP
    assert vip.value == "203.0.113.10"
    assert vip.match_status is MatchStatus.MATCHED
    # VIP-Name bleibt erhalten, kein Finding
    assert not any(f.subject_id == "VIP_web" for f in session.query(Finding).all())


def test_nat_rules_werden_persistiert_und_ersetzt(session_factory):
    from app.models import NatRule

    session = session_factory()
    run = _seed_run(session)
    result = CollectorResult.model_validate(
        {
            "status": "completed",
            "rules": [],
            "nat_rules": [
                {
                    "source_platform": "fortigate",
                    "external_id": "VIP_web01",
                    "name": "VIP_web01",
                    "ext_ip": "203.0.113.10",
                    "ext_port": "443",
                    "mapped_ip": "10.10.10.5",
                    "mapped_port": "8443",
                    "protocol": "tcp",
                    "portforward": True,
                }
            ],
        }
    )
    ingest.persist_result(session, run, result, _fake_sot())
    ingest.persist_result(session, run, result, _fake_sot())  # idempotent -> ersetzt
    session.commit()

    nats = session.query(NatRule).filter_by(source_system_id=run.source_system_id).all()
    assert len(nats) == 1
    assert nats[0].mapped_ip == "10.10.10.5"
    assert nats[0].mapped_port == "8443"
    assert nats[0].portforward is True


def test_phase15_protections_farbe_und_snat(session_factory):
    from app.models import NatRule
    from app.models import NetworkObject as NObj

    session = session_factory()
    run = _seed_run(session)
    result = CollectorResult.model_validate(
        {
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
                    "protections": ["WF_Client", "IPS_Media", "SSL_Media"],
                }
            ],
            "nat_rules": [
                {
                    "source_platform": "fortigate",
                    "external_id": "V-EXT_www",
                    "name": "V-EXT_www",
                    "nat_type": "dnat_vip",
                    "ext_ip": "203.0.113.40",
                    "mapped_ip": "10.20.4.10",
                },
                {
                    "source_platform": "fortigate",
                    "external_id": "IPP_inet",
                    "name": "IPP_inet",
                    "nat_type": "snat_pool",
                    "ext_ip": "203.0.113.168",
                },
            ],
            "objects": [
                {"name": "H_web01", "obj_type": "ipmask", "color": 13, "value": "10.10.10.5"},
                {"name": "N_dmz", "obj_type": "ipmask", "color": 16, "value": "10.10.20.0/24"},
            ],
        }
    )
    ingest.persist_result(session, run, result, _fake_sot())
    session.commit()

    rule = rules_store.list_rules(session, tenant_id=run.tenant_id)[0]
    assert rule.protections == ["WF_Client", "IPS_Media", "SSL_Media"]

    # Farbcode via Wert-Match auf die aufgeloesten Objekte uebertragen
    assert session.query(NObj).filter_by(value="10.10.10.5").one().color == 13
    assert session.query(NObj).filter_by(value="10.10.20.0/24").one().color == 16

    nats = {n.name: n for n in session.query(NatRule).all()}
    assert nats["V-EXT_www"].nat_type == "dnat_vip"
    assert nats["IPP_inet"].nat_type == "snat_pool"
    assert nats["IPP_inet"].mapped_ip is None


def test_resolve_prefixes_nutzt_mandanten_profil(session_factory):
    """tenant.naming_profile_id -> object_prefix-Woerterbuch steuert den Parser."""
    from app import naming
    from app.models import ObjectClass
    from app.store import naming_profiles as pstore

    s = session_factory()
    tenant = Tenant(name="kunde-x", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    s.add(tenant)
    s.flush()

    # ohne Profil und ohne Standardprofil -> None (eingebaute Standard-Konvention)
    assert ingest._resolve_prefixes(s, tenant.id) is None

    # ohne eigenes Profil gilt das Standardprofil „default" (inkl. Anpassungen)
    default = pstore.seed_default_profile(s)
    pstore.upsert_entry(s, default.id, "object_prefix", "VM_", "host")
    s.flush()
    fallback = ingest._resolve_prefixes(s, tenant.id)
    assert naming.parse_object_name("VM_app01.kunde.ch", fallback).object_class is ObjectClass.HOST

    # mit Profil + eigenem Praefix
    p = pstore.get_or_create_profile(s, customer="ACME", name="Std")
    pstore.upsert_entry(s, p.id, "object_prefix", "SRV_", "host")
    tenant.naming_profile_id = p.id
    s.flush()

    prefixes = ingest._resolve_prefixes(s, tenant.id)
    assert prefixes is not None
    assert naming.parse_object_name("SRV_app01.kunde.ch", prefixes).object_class is ObjectClass.HOST


def test_aci_epg_endpunkte_ohne_finding(session_factory):
    """ACI-Regeln mit epg:-Endpunkten werden als EPG-Kind gefuehrt (kein invalid_name)."""
    from app.models import CollectorRun, EndpointKind, RuleEndpoint

    s = session_factory()
    tenant = Tenant(name="kunde-aci", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(
        name="apic", platform=Platform.CISCO_ACI, base_url="https://apic", enabled=True
    )
    s.add_all([tenant, fw])
    s.flush()
    run = CollectorRun(platform="cisco_aci", tenant_id=tenant.id, source_system_id=fw.id)
    s.add(run)
    s.flush()

    result = CollectorResult.model_validate(
        {
            "status": "completed",
            "rules": [
                {
                    "source_platform": "cisco_aci",
                    "external_id": "uni/tn-TE_share/brc-CT_web/SJ_mysql",
                    "name": "CT_web:SJ_mysql",
                    "action": "allow",
                    "sources": ["epg:EG_web"],
                    "destinations": ["epg:EG_db"],
                    "services": ["tcp/3306"],
                    "src_zone": "L3O_inet",
                    "dst_zone": "BD_db",
                }
            ],
        }
    )
    ingest.persist_result(s, run, result, FakeSoT())
    s.commit()

    eps = s.query(RuleEndpoint).all()
    assert len(eps) == 2
    assert all(e.kind is EndpointKind.EPG for e in eps)
    assert {e.inline_value for e in eps} == {"EG_web", "EG_db"}
    assert s.query(Finding).filter(Finding.type == FindingType.INVALID_NAME).count() == 0


def test_aci_epgs_persistiert_und_endpunkt_verlinkt(session_factory):
    """EPGs werden angelegt (ap/bd/subnets), rule.aci_scope gesetzt, Endpunkt -> epg_id."""
    from app.models import CollectorRun, Epg, EpgSubnet, Rule, RuleEndpoint

    s = session_factory()
    tenant = Tenant(name="kunde-aci2", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(
        name="apic2", platform=Platform.CISCO_ACI, base_url="https://apic", enabled=True
    )
    s.add_all([tenant, fw])
    s.flush()
    run = CollectorRun(platform="cisco_aci", tenant_id=tenant.id, source_system_id=fw.id)
    s.add(run)
    s.flush()

    result = CollectorResult.model_validate(
        {
            "status": "completed",
            "epgs": [
                {"name": "EG_web", "ap": "AP_001", "bd": "BD_app", "subnets": ["10.20.1.1/24"]},
                {"name": "EG_db", "ap": "AP_001", "bd": "BD_db", "subnets": []},
            ],
            "rules": [
                {
                    "source_platform": "cisco_aci",
                    "external_id": "uni/tn-TE_share/brc-CT_web/SJ_mysql",
                    "name": "CT_web:SJ_mysql",
                    "action": "allow",
                    "sources": ["epg:EG_web"],
                    "destinations": ["epg:EG_db"],
                    "services": ["tcp/3306"],
                    "aci_scope": "context",
                }
            ],
        }
    )
    ingest.persist_result(s, run, result, FakeSoT())
    s.commit()

    web = s.query(Epg).filter(Epg.name == "EG_web").one()
    assert web.ap == "AP_001" and web.bd == "BD_app"
    assert s.query(EpgSubnet).filter(EpgSubnet.epg_id == web.id).count() == 1

    rule = s.query(Rule).one()
    assert rule.aci_scope == "context"
    eps = {e.inline_value: e for e in s.query(RuleEndpoint).all()}
    assert eps["EG_web"].epg_id == web.id and eps["EG_web"].matched is True


def test_aci_epg_endpoint_und_host_link(session_factory):
    """Gelernte EPG-Endpunkte werden persistiert; EPG wird an das Host-Objekt geknuepft."""
    from app.models import CollectorRun, Epg, EpgEndpoint, NetworkObject, ObjectClass

    s = session_factory()
    tenant = Tenant(name="kunde-aci3", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    fw = SourceSystem(
        name="apic3", platform=Platform.CISCO_ACI, base_url="https://apic", enabled=True
    )
    s.add_all([tenant, fw])
    s.flush()
    # Host-Objekt (z. B. aus einem FortiGate-Lauf) mit passender IP vorhanden
    s.add(
        NetworkObject(
            tenant_id=tenant.id, name="H_app01", object_class=ObjectClass.HOST, value="10.20.1.50"
        )
    )
    s.flush()
    run = CollectorRun(platform="cisco_aci", tenant_id=tenant.id, source_system_id=fw.id)
    s.add(run)
    s.flush()

    result = CollectorResult.model_validate(
        {
            "status": "completed",
            "epgs": [
                {
                    "name": "EG_web",
                    "ap": "AP_001",
                    "bd": "BD_app",
                    "subnets": ["10.20.1.1/24"],
                    "endpoints": [{"ip": "10.20.1.50", "mac": "00:50:56:AA:BB:CC"}],
                }
            ],
        }
    )
    ingest.persist_result(s, run, result, FakeSoT())
    s.commit()

    epg = s.query(Epg).filter(Epg.name == "EG_web").one()
    assert s.query(EpgEndpoint).filter(EpgEndpoint.epg_id == epg.id).count() == 1
    host = s.query(NetworkObject).filter(NetworkObject.value == "10.20.1.50").one()
    assert epg.network_object_id == host.id  # EPG -> Server verknuepft
