"""Zugehoerigkeit von Regeln nach dem Weg (0.58.0, ADR 0018 Schritt 4, Relevanz aus ADR 0019).

Topologie wie in der Weg-Suche (test_paths): fw-01 mit Servernetz 10.20.0.0/24 am Interface srv,
Transfer zur ACI (to-aci), zu fw-02 (to-fw02) und Provider (wan1/wan2). Der Kunde hat das
Servernetz; Regeln, die es nur ueber Quelle bzw. Ziel beruehren, zaehlen nur, wenn fw-01 das
Netz ueber den Ein- bzw. Ausgang der Regel erreicht.
"""

import copy

from test_customers import APIC, _policy
from test_paths import ADMIN, FW1, _deliver, _system

OP = {"X-Dev-Roles": "operator"}

RULES = [
    _policy(1, "lan-to-srv", ["10.60.0.0/24"], ["10.20.0.0/24"], srcintf="to-fw02", dstintf="srv"),
    _policy(2, "wan-to-srv-falsch", ["all"], ["10.20.0.5"], srcintf="wan1", dstintf="to-aci"),
    _policy(3, "srv-out", ["10.20.0.0/24"], ["all"], srcintf="srv", dstintf="wan1"),
    _policy(4, "srv-out-falsch", ["10.20.0.0/24"], ["all"], srcintf="to-fw02", dstintf="wan1"),
    _policy(5, "unbekannt", ["all"], ["10.20.0.5"], srcintf="wan1", dstintf="foo"),
    _policy(6, "beide-any", ["all"], ["10.20.0.5"], srcintf="any", dstintf="any"),
]


def _seed(client) -> tuple[int, dict[str, int]]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    fw = copy.deepcopy(FW1)
    fw["rules"] = RULES
    ids = {}
    for name, platform, result in (("fw-01", "fortigate", fw), ("apic-01", "cisco_aci", APIC)):
        ids[name] = _system(client, tid, name, platform)
        _deliver(client, tid, ids[name], platform, result)
    return tid, ids


def _rules(client, cid) -> dict[str, str]:
    r = client.get(f"/customers/{cid}/members", params={"kind": "rule"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return {m["name"]: m["via"] for m in r.json()}


def test_regeln_ueber_netze_nur_auf_dem_weg(client):
    tid, ids = _seed(client)
    sel = [{"kind": "prefix", "value": "10.20.0.0/24"}]
    r = client.post("/customers", json={"tenant_id": tid, "name": "Kunde S", "selectors": sel},
                    headers=OP)  # fmt: skip
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    rules = _rules(client, cid)
    # Ausgang srv bzw. Eingang srv: fw-01 leitet das Servernetz dorthin; unbekanntes Interface
    # und any bleiben (nicht pruefbar bzw. jede Richtung)
    assert set(rules) == {"lan-to-srv", "srv-out", "unbekannt", "beide-any"}
    assert rules["lan-to-srv"] == "Netz 10.20.0.0/24"

    # Vorschau: die Regeln nicht auf dem Weg mit Begruendung
    body = {"tenant_id": tid, "selectors": sel}
    p = client.post("/customers/preview", json=body, headers=OP).json()
    assert p["counts"]["rule"] == 4
    off = {x["name"]: x["why"] for x in p["off_path"]["samples"]}
    assert p["off_path"]["count"] == 2 and set(off) == {"wan-to-srv-falsch", "srv-out-falsch"}
    assert "über srv" in off["wan-to-srv-falsch"] and "Ausgang to-aci" in off["wan-to-srv-falsch"]
    assert "Eingang to-fw02" in off["srv-out-falsch"]

    # ueber ein Interface des Kunden zaehlt die Regel ohne Pruefung (Selektor vor Netz)
    sel_if = sel + [{"kind": "fw_interface", "value": "to-aci", "source_system_id": ids["fw-01"]}]
    body = {"tenant_id": tid, "name": "Kunde S", "selectors": sel_if}
    assert client.put(f"/customers/{cid}", json=body, headers=OP).status_code == 200
    rules = _rules(client, cid)
    assert rules["wan-to-srv-falsch"] == "Interface to-aci"
    assert "srv-out-falsch" not in rules


def test_contracts_ueber_netze_bleiben_ohne_widerspruch(client):
    """Contracts, die nur ueber ein Netz dazukommen, prueft die Zuordnung im ACI; eine EPG im
    Fabric ohne Widerspruch bleibt dabei (wie bisher)."""
    tid, _ids = _seed(client)
    sel = [{"kind": "prefix", "value": "10.50.1.0/24"}]  # Subnetz von EPG_web, ohne EPG-Selektor
    r = client.post("/customers", json={"tenant_id": tid, "name": "Kunde W", "selectors": sel},
                    headers=OP)  # fmt: skip
    rules = _rules(client, r.json()["id"])
    assert {"CT_web", "CT_app"} <= set(rules) and "CT_dns" not in rules
