"""Zonen komplett, Teil 2 (0.81.0, ADR 0024 Nr. 18):

1. Relevanz nach dem Weg in der Zonenmatrix: eine Zone faellt fuer eine Regel weg, wenn das
   Routing der Firewall ihre Adressen nicht ueber Ein- bzw. Ausgang der Regel fuehrt - wie in
   der Doku (ADR 0019 Punkt 6). Gezaehlt als „nicht möglich“, nicht als Verstoss.
2. Zone nur hinter einem Router: das Netz des Routers ist der Uebergang zur Zone, wenn ein
   Knoten ihr Netz (nicht ueber die Default-Route) dorthin routet.
"""

import copy

import test_zones
from test_paths import _r
from test_zones import OP, _cells, _seed, _sel, _zone, _zones

# fw-01 kennt das LAN nur ueber to-fw02: eine Regel mit Eingang srv kann es nicht als Quelle sehen
WRONG = test_zones._policy(7, "office-ueber-srv", ["10.60.0.0/24"], ["10.50.1.10"])
WRONG["raw"] = {**WRONG["raw"], "srcintf": [{"name": "srv"}], "dstintf": [{"name": "to-aci"}]}


def test_matrix_nach_dem_weg(client, monkeypatch):
    monkeypatch.setattr(test_zones, "RULES", [*test_zones.RULES, WRONG])
    tid, ids = _seed(client)
    z = _zones(client, tid, ids)
    lan, web = z["lan"]["id"], z["web"]["id"]
    m = client.get("/zones/matrix", params={"tenant_id": tid}).json()
    cell = _cells(m)[(lan, web)]
    # nur lan-to-web (deny, any-Interfaces) zaehlt; office-ueber-srv ist nach dem Weg unmoeglich
    assert (cell["allow"], cell["deny"], cell["impossible"]) == (0, 1, 1)
    assert m["summary"]["off_path"] == 1 and m["summary"]["rules"] == 7
    params = {"tenant_id": tid, "src": lan, "dst": web}
    rules = client.get("/zones/matrix/rules", params=params).json()
    assert [x["name"] for x in rules["items"]] == ["lan-to-web"] and rules["impossible"] == 1
    off = client.get("/zones/matrix/rules", params={**params, "impossible": True}).json()
    (row,) = off["items"]
    assert row["name"] == "office-ueber-srv"
    assert "fw-01" in row["impossible"] and "srv" in row["impossible"]

    # Absicht LAN -> Web gesperrt: die unmoegliche Regel ist kein Verstoss
    body = {"tenant_id": tid, "src_zone_id": lan, "dst_zone_id": web, "intent": "deny"}
    assert client.put("/zones/policy", json=body, headers=OP).status_code == 200
    m = client.get("/zones/matrix", params={"tenant_id": tid}).json()
    assert _cells(m)[(lan, web)]["violations"] == 0
    f = client.get("/findings", params={"tenant_id": tid, "type": "zone_policy_violation"}).json()
    assert (f["items"] if isinstance(f, dict) else f) == []


def test_zone_nur_hinter_einem_router(client, monkeypatch):
    fw1 = copy.deepcopy(test_zones.FW1)
    fw1["routing"]["routes"].append(
        _r("fw-01", "kunde-a", "10.88.0.0/16", [{"ip": "10.255.1.1", "interface": "vl-kunde"}])
    )
    monkeypatch.setattr(test_zones, "FW1", fw1)
    tid, ids = _seed(client)
    vdom = _sel("fw_vdom", "kunde-a", source_system_id=ids["fw-01"])
    kunde = _zone(client, tid, "Kunde A", [vdom], code="KUA")
    site = _zone(client, tid, "Standort B", [_sel("prefix", "10.88.0.0/16")], kind="access",
                 code="STB")  # fmt: skip
    plan = client.get("/zones/plan", params={"tenant_id": tid}).json()
    trans = {(t["a"], t["b"]): t for t in plan["transitions"]}
    t = trans.get((kunde["id"], site["id"])) or trans.get((site["id"], kunde["id"]))
    assert t is not None, list(trans)
    (net,) = t["networks"]
    assert net["network"] == "10.255.1.0/30"
    assert [r["ip"] for r in net["routers"]] == ["10.255.1.1"]
    assert net["routes"] == [{"zone": site["id"], "prefixes": ["10.88.0.0/16"]}]
    # nur ueber den Router: kein Uebergang mehr nach „ausserhalb“ fuer dieses Netz
    assert not any("outside" in (x["a"], x["b"]) and x["networks"][0]["network"] == "10.255.1.0/30"
                   for x in plan["transitions"])  # fmt: skip
    # die Zone Standort B behaelt ihr Netz
    nets = client.get(f"/zones/{site['id']}/nets").json()["items"]
    assert [n["network"] for n in nets] == ["10.88.0.0/16"]


def test_default_route_ist_kein_uebergang(client):
    tid, ids = _seed(client)  # kunde-a hat nur 0.0.0.0/0 ueber 10.255.1.1
    _zone(client, tid, "Kunde A", [_sel("fw_vdom", "kunde-a", source_system_id=ids["fw-01"])])
    site = _zone(client, tid, "Standort B", [_sel("prefix", "10.88.0.0/16")])
    plan = client.get("/zones/plan", params={"tenant_id": tid}).json()
    assert not [t for t in plan["transitions"] if site["id"] in (t["a"], t["b"])]
