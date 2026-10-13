"""Normalisierung beider Plattformen gegen die Beispieldaten."""

import json
from importlib import resources

from collectors.cisco_aci.normalize import normalize_contract
from collectors.cisco_aci.tasks import fetch_contracts
from collectors.fortigate import normalize as norm


def _sample(name: str):
    return json.loads(resources.files("collectors.fortigate").joinpath(name).read_text())


def _fortigate_rules():
    return norm.normalize(
        _sample("sample_policies.json"),
        _sample("sample_addresses.json"),
        _sample("sample_addrgrps.json"),
        _sample("sample_services.json"),
    )


def test_fortigate_adressen_werden_zu_werten_aufgeloest():
    rules = {r.external_id: r for r in _fortigate_rules()}
    assert len(rules) == 3

    r1 = rules["1"]
    assert r1.action == "allow"
    assert r1.src_zone == "port2"
    assert r1.sources == ["10.10.20.0/24"]  # N_dmz-net -> CIDR
    assert r1.destinations == ["10.10.10.5"]  # H_web01 /32 -> Host-IP
    assert r1.services == ["tcp/443"]  # Built-in HTTPS aufgeloest

    r2 = rules["2"]
    assert r2.action == "deny"
    assert r2.destinations == ["all"]
    assert r2.services == ["all"]


def test_fortigate_fqdn_wird_typisiert_und_custom_service():
    r3 = {r.external_id: r for r in _fortigate_rules()}["3"]
    # FQDN-Objekt -> typisierter Wert (Orchestrator: expected_external, kein Fehler)
    assert r3.destinations == ["fqdn:api.partner.example"]
    # Custom-Service aufgeloest
    assert r3.services == ["tcp/8443"]


def test_fortigate_resolve_address_typen():
    amap = {
        "IFSUB": {
            "name": "IFSUB",
            "type": "interface-subnet",
            "subnet": "172.25.15.0 255.255.255.0",
        },
        "RNG": {
            "name": "RNG",
            "type": "iprange",
            "start-ip": "172.26.0.100",
            "end-ip": "172.26.0.150",
        },
        "FQ": {"name": "FQ", "type": "fqdn", "fqdn": "ntp.example"},
        "GEO": {"name": "GEO", "type": "geography", "country": "CH"},
        "MC": {"name": "MC", "type": "mac"},
        "DYN": {"name": "DYN", "type": "dynamic"},
    }
    assert norm.resolve_address("IFSUB", amap) == ["172.25.15.0/24"]  # echtes Subnetz
    assert norm.resolve_address("RNG", amap) == ["range:172.26.0.100-172.26.0.150"]
    assert norm.resolve_address("FQ", amap) == ["fqdn:ntp.example"]
    assert norm.resolve_address("GEO", amap) == ["geo:GEO"]
    assert norm.resolve_address("MC", amap) == ["mac:MC"]
    assert norm.resolve_address("DYN", amap) == ["dynamic:DYN"]
    # VIP (nicht in der Adress-Tabelle) wird ueber die VIP-Map zu externer IP + Name
    assert norm.resolve_address("VIP_web", {}, {"VIP_web": "203.0.113.10"}) == [
        "vip:VIP_web=203.0.113.10"
    ]


def test_fortigate_normalize_nat():
    vips = [
        {
            "name": "VIP_web01",
            "extip": "203.0.113.10",
            "mappedip": [{"range": "10.10.10.5"}],
            "extport": "443",
            "mappedport": "8443",
            "protocol": "tcp",
            "portforward": "enable",
        }
    ]
    nat = norm.normalize_nat(vips)
    assert len(nat) == 1
    n = nat[0]
    assert n.name == "VIP_web01"
    assert n.ext_ip == "203.0.113.10"
    assert n.mapped_ip == "10.10.10.5"
    assert n.ext_port == "443"
    assert n.mapped_port == "8443"
    assert n.protocol == "tcp"
    assert n.portforward is True


def test_aci_contract_wird_auf_regeln_abgebildet():
    contracts = fetch_contracts()
    rules = [r for c in contracts for r in normalize_contract(c)]
    assert len(rules) == 1

    rule = rules[0]
    assert rule.source_platform == "cisco_aci"
    assert rule.sources == ["prod/web"]
    assert rule.destinations == ["prod/db"]
    assert rule.services == ["tcp/3306"]
    assert rule.external_id == "uni/tn-prod/brc-web-to-db/mysql"
