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

    # ohne Profil -> None (SOP-Default)
    assert ingest._resolve_prefixes(s, tenant.id) is None

    # mit Profil + eigenem Praefix
    p = pstore.get_or_create_profile(s, customer="ACME", name="Std")
    pstore.upsert_entry(s, p.id, "object_prefix", "SRV_", "host")
    tenant.naming_profile_id = p.id
    s.flush()

    prefixes = ingest._resolve_prefixes(s, tenant.id)
    assert prefixes is not None
    assert naming.parse_object_name("SRV_app01.kunde.ch", prefixes).object_class is ObjectClass.HOST
