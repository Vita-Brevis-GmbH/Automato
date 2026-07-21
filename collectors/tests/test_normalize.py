"""Normalisierung beider Plattformen gegen die Beispieldaten."""

from collectors.cisco_aci.normalize import normalize_contract
from collectors.cisco_aci.tasks import fetch_contracts
from collectors.fortigate.normalize import normalize_policy
from collectors.fortigate.tasks import fetch_policies


def test_fortigate_policy_wird_normalisiert():
    rules = [normalize_policy(p) for p in fetch_policies()]
    assert len(rules) == 2

    allow = rules[0]
    assert allow.source_platform == "fortigate"
    assert allow.action == "allow"
    assert allow.src_zone == "dmz"
    assert allow.services == ["HTTPS"]

    deny = rules[1]
    assert deny.action == "deny"


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
