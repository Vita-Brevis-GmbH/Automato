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


def test_fortigate_fqdn_bleibt_name_und_custom_service():
    r3 = {r.external_id: r for r in _fortigate_rules()}["3"]
    # FQDN-Objekt ist nicht als IP aufloesbar -> Name bleibt (H-EXT_ -> extern im Ingest)
    assert r3.destinations == ["H-EXT_partner-api"]
    # Custom-Service aufgeloest
    assert r3.services == ["tcp/8443"]


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
