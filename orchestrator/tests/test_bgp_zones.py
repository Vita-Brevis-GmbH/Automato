"""BGP-Filter je Zone ableiten (0.114.0, ADR 0041 Stufe 2, Anwender 2026-10-29: alle Zonen, die
ueber den Nachbarn erreicht werden).

Topologie aus `test_paths`: fw-01 (Zone „DC FW“) mit BGP zu fw-02 (10.70.0.2, Zone „Lan“ mit
10.50.0.0/16, 10.99.0.0/16 ohne 10.99.5.0/24, dazu ihr LAN 10.60.0.0/24); hinter Lan die Zone
„Aussen“ (10.80.0.0/16). Der Eingangsfilter PL_FW02 von fw-01 erlaubt 10.60.0.0/24, 10.50.0.0/16
bis /24 und 172.16.0.0/12 - es fehlen 10.99.0.0/16 und 10.80.0.0/16, 172.16.0.0/12 liegt
ausserhalb der Zonen.
"""

import copy

import test_paths as tp
from test_zone_flows import _ids, _zone

ADMIN = {"X-Dev-Roles": "admin"}
FW02 = "10.70.0.2"


def _fw1() -> dict:
    fw = copy.deepcopy(tp.FW1)
    routing = fw["routing"]
    routing["peers"].append({
        "node": "fw-01", "context": "root", "peer_ip": FW02, "remote_as": 65020,
        "state": "Established",
        "attrs": {"config": {"filters": {"in": {"prefix_list": "PL_FW02"}}, "remote_as": "65020"}},
    })  # fmt: skip
    routing["bgp"] = [
        {"node": "fw-01", "context": "root", "kind": "bgp", "name": "bgp",
         "data": {"as": "65001", "neighbors": {FW02: {}}}},
        {"node": "fw-01", "context": "root", "kind": "prefix_list", "name": "PL_FW02", "family": 4,
         "data": {"rules": [
             {"id": 1, "action": "permit", "prefix": "10.60.0.0/24"},
             {"id": 2, "action": "permit", "prefix": "10.50.0.0/16", "le": 24},
             {"id": 3, "action": "permit", "prefix": "172.16.0.0/12", "le": 32}]}},
    ]  # fmt: skip
    return fw


def _seed(client) -> tuple[int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    for name, platform, result in (("fw-01", "fortigate", _fw1()), ("fw-02", "fortigate", tp.FW2),
                                   ("apic-01", "cisco_aci", tp.APIC)):  # fmt: skip
        tp._deliver(client, tid, tp._system(client, tid, name, platform), platform, result)
    sys = _ids(client, tid)

    def dev(sid):
        return {"kind": "fw_vdom", "value": "root", "source_system_id": sid}

    _zone(client, tid, "DC FW", [dev(sys["fw-01"])])
    nets = [{"kind": "prefix", "value": "10.50.0.0/16"},
            {"kind": "prefix", "value": "10.99.0.0/16"},
            {"kind": "prefix", "value": "10.99.5.0/24", "exclude": True}]  # fmt: skip
    _zone(client, tid, "Lan", [dev(sys["fw-02"]), *nets])
    return tid, sys["fw-01"]


def test_soll_aus_den_zonen_dahinter(client, session_factory):
    tid, _fw = _seed(client)
    from app.models import TopoPeer
    from app.store import bgp_zones

    s = session_factory()
    p = s.query(TopoPeer).filter_by(peer_ip=FW02).one()
    r = bgp_zones.for_peer(s, tid, p)
    s.close()
    assert r["home"]["name"] == "DC FW" and r["neighbor_zone"]["name"] == "Lan", r
    assert [z["name"] for z in r["zones"]] == ["Lan"]
    (v4,) = r["lists"]
    entries = {(e["prefix"], e["action"]): e for e in v4["entries"]}
    assert ("10.99.5.0/24", "deny") in entries  # Ausschluss vor den Netzen
    assert {("10.50.0.0/16", "permit"), ("10.99.0.0/16", "permit")} <= set(entries)
    assert entries[("10.50.0.0/16", "permit")]["verdict"] == "permit"
    assert v4["missing"] == ["10.99.0.0/16"]
    assert [t["prefix"] for t in v4["too_open"]] == ["172.16.0.0/12"]
    assert r["state"] == "differs" and not v4["open"]
    cli = v4["cli"]
    assert 'edit "PL_LAN_IN"' in cli and "set action deny" in cli
    assert cli.index("10.99.5.0/24") < cli.index("10.99.0.0/16")  # Ausschluss zuerst
    assert "set prefix 10.50.0.0/16" in cli and "set le 32" in cli
    assert "prefix-list-in" not in cli  # Filter vorhanden: kein Binden vorgeschlagen

    # API und Befund
    got = client.get(f"/routing/bgp/peers/{p.id}/zones", params={"tenant_id": tid}).json()
    assert got["state"] == "differs" and got["lists"][0]["missing"] == ["10.99.0.0/16"]
    found = client.get("/findings", params={"tenant_id": tid, "type": "bgp_zone_mismatch"}).json()
    assert len(found) == 1, found
    f = found[0]
    assert f["severity"] == "info" and f["subject_id"].endswith(f"→ {FW02}")
    assert "passt nicht zu den Zonen dahinter (Lan)" in f["message"]
    assert "gesperrt: 10.99.0.0/16" in f["message"]
    assert "ausserhalb der Zonen erlaubt: 172.16.0.0/12" in f["message"]


# -- Stufe 3 (0.116.0): Ausgangsfilter je Zone und ACI import-rtctrl aus den Zonen ----------


def _seed3(client, lan_kind="internal") -> tuple[int, dict]:
    """fw-01 kuendigt fw-02 ueber PL_TO_FW02 an (10.20.0.0/24, 192.0.2.0/24, 172.31.0.0/16 le
    32; network 10.20.0.0/24 und 192.0.2.0/24); Zone „DC FW“ mit 10.20.0.0/16 ohne 10.20.9.0/24
    und 203.0.113.0/24, „DC ACI“ (VRF TE:VRF1, 10.50.1.0/24) hinter dem L3Out zu fw-01; der
    L3Out erzwingt Import mit 10.60.0.0/24 und 172.20.0.0/16 aggregate."""
    fw = _fw1()
    fw["routing"]["peers"][-1]["attrs"]["config"]["filters"]["out"] = {"prefix_list": "PL_TO_FW02"}
    fw["routing"]["bgp"][0]["data"]["networks"] = [{"prefix": "10.20.0.0/24"},
                                                   {"prefix": "192.0.2.0/24"}]  # fmt: skip
    fw["routing"]["bgp"].append(
        {"node": "fw-01", "context": "root", "kind": "prefix_list", "name": "PL_TO_FW02",
         "family": 4, "data": {"rules": [
             {"id": 1, "action": "permit", "prefix": "10.20.0.0/24"},
             {"id": 2, "action": "permit", "prefix": "192.0.2.0/24"},
             {"id": 3, "action": "permit", "prefix": "172.31.0.0/16", "le": 32}]}})  # fmt: skip
    apic = copy.deepcopy(tp.APIC)
    apic["routing"]["l3outs"][0]["route_control"] = {
        "enforce": ["export", "import"],
        "import": [{"prefix": "10.60.0.0/24", "epg": "EN_any"},
                   {"prefix": "172.20.0.0/16", "epg": "EN_any", "aggregate": True}]}  # fmt: skip
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    for name, platform, result in (("fw-01", "fortigate", fw), ("fw-02", "fortigate", tp.FW2),
                                   ("apic-01", "cisco_aci", apic)):  # fmt: skip
        tp._deliver(client, tid, tp._system(client, tid, name, platform), platform, result)
    sys = _ids(client, tid)

    def dev(sid):
        return {"kind": "fw_vdom", "value": "root", "source_system_id": sid}

    _zone(client, tid, "DC FW", [dev(sys["fw-01"]), {"kind": "prefix", "value": "10.20.0.0/16"},
                                 {"kind": "prefix", "value": "10.20.9.0/24", "exclude": True},
                                 {"kind": "prefix", "value": "203.0.113.0/24"}])  # fmt: skip
    lan = [dev(sys["fw-02"]), {"kind": "prefix", "value": "10.50.0.0/16"},
           {"kind": "prefix", "value": "10.60.0.0/24"}]  # fmt: skip
    _zone(client, tid, "Lan", lan, kind=lan_kind)
    _zone(client, tid, "DC ACI", [{"kind": "aci_vrf", "value": "TE:VRF1"},
                                  {"kind": "prefix", "value": "10.50.1.0/24"}])  # fmt: skip
    return tid, sys


def test_ausgangsfilter_aus_den_zonen_diesseits(client, session_factory):
    tid, _sys = _seed3(client)
    from app.models import TopoPeer
    from app.store import bgp_zones

    s = session_factory()
    p = s.query(TopoPeer).filter_by(peer_ip=FW02).one()
    r = bgp_zones.for_peer(s, tid, p, way="out")
    s.close()
    # der Nachbar (Lan) erreicht ueber die Firewall ihre Zone und das ACI dahinter
    assert r["way"] == "out" and r["neighbor_zone"]["name"] == "Lan"
    assert [z["name"] for z in r["zones"]] == ["DC FW", "DC ACI"] and not r["public_only"]
    (v4,) = r["lists"]
    entries = {(e["prefix"], e["action"]): e for e in v4["entries"]}
    assert set(entries) == {("10.20.9.0/24", "deny"), ("10.20.0.0/16", "permit"),
                            ("203.0.113.0/24", "permit"), ("10.50.1.0/24", "permit")}  # fmt: skip
    # PL_TO_FW02 sperrt die Zonen-Netze als Ganzes, erlaubt Netze ausserhalb der Zonen
    assert {"10.20.0.0/16", "10.50.1.0/24"} <= set(v4["missing"])
    assert [t["prefix"] for t in v4["too_open"]] == ["192.0.2.0/24", "172.31.0.0/16"]
    # heute angekuendigt: 10.20.0.0/24 (in der Zone) und 192.0.2.0/24 (ausserhalb)
    assert v4["announced_outside"] == ["192.0.2.0/24"] and v4["announced_outside_total"] == 1
    assert r["state"] == "differs" and not v4["open"]
    cli = v4["cli"]
    assert 'edit "PL_LAN_OUT"' in cli and cli.index("10.20.9.0/24") < cli.index("10.20.0.0/16")
    assert "prefix-list-out" not in cli  # Filter vorhanden: kein Binden vorgeschlagen

    # API mit Richtung, die Eingangsseite bleibt wie bisher
    params = {"tenant_id": tid, "way": "out"}
    got = client.get(f"/routing/bgp/peers/{p.id}/zones", params=params).json()
    assert got["way"] == "out" and got["lists"][0]["announced_outside"] == ["192.0.2.0/24"]
    inbound = client.get(f"/routing/bgp/peers/{p.id}/zones", params={"tenant_id": tid}).json()
    assert inbound["way"] == "in" and [z["name"] for z in inbound["zones"]] == ["Lan"]
    assert client.get(f"/routing/bgp/peers/{p.id}/zones",
                      params={"tenant_id": tid, "way": "x"}).status_code == 422  # fmt: skip

    # Befund je Nachbar
    found = client.get("/findings", params={"tenant_id": tid,
                                            "type": "bgp_zone_out_mismatch"}).json()  # fmt: skip
    assert len(found) == 1, found
    f = found[0]
    assert f["severity"] == "info" and f["subject_id"].endswith(f"→ {FW02}")
    assert (
        "zum Nachbarn 10.70.0.2: der Ausgangsfilter passt nicht zu den Zonen diesseits"
        in (f["message"])
    )
    assert "1 Routen ausserhalb der Zonen angekündigt: 192.0.2.0/24" in f["message"]
    assert "gesperrt: 10.20.0.0/16" in f["message"]


def test_an_einen_nachbarn_ausserhalb_nur_oeffentliche_netze(client, session_factory):
    tid, _sys = _seed3(client, lan_kind="external")
    from app.models import TopoPeer
    from app.store import bgp_zones

    s = session_factory()
    p = s.query(TopoPeer).filter_by(peer_ip=FW02).one()
    r = bgp_zones.for_peer(s, tid, p, way="out")
    s.close()
    assert r["public_only"]
    (v4,) = r["lists"]
    assert [e["prefix"] for e in v4["entries"]] == ["203.0.113.0/24"]


def test_aci_import_route_control_aus_den_zonen(client, session_factory):
    tid, _sys = _seed3(client)
    from app.models import TopoL3Out
    from app.store import bgp_zones

    s = session_factory()
    o = s.query(TopoL3Out).filter_by(name="L3O_fw").one()
    r = bgp_zones.for_l3out(s, tid, o)
    s.close()
    # VRF in „DC ACI“, Peer 10.255.0.1 = fw-01 („DC FW“), dahinter auch „Lan“
    assert r["home"]["name"] == "DC ACI" and r["neighbor_zone"]["name"] == "DC FW"
    assert [z["name"] for z in r["zones"]] == ["DC FW", "Lan"] and r["import_enforced"]
    verdicts = {e["prefix"]: e["verdict"] for e in r["entries"]}
    assert verdicts["10.60.0.0/24"] == "permit" and verdicts["10.20.0.0/16"] == "deny"
    assert set(r["missing"]) == {"10.20.0.0/16", "10.50.0.0/16", "203.0.113.0/24"}
    assert [t["prefix"] for t in r["too_open"]] == ["172.20.0.0/16"]
    assert r["state"] == "differs"
    assert "10.20.0.0/16" in r["proposal"] and "aggregate import" in r["proposal"]
    assert "Ausschlüsse" in r["proposal"] and "10.20.9.0/24" in r["proposal"]

    got = client.get(f"/routing/bgp/l3outs/{o.id}/zones", params={"tenant_id": tid}).json()
    assert got["l3out"] == "L3O_fw" and got["state"] == "differs"
    found = client.get("/findings", params={"tenant_id": tid,
                                            "type": "aci_import_zone_mismatch"}).json()  # fmt: skip
    assert len(found) == 1, found
    msg = found[0]["message"]
    assert msg.startswith("L3Out L3O_fw (VRF TE:VRF1): Import Route Control passt nicht")
    assert "importiert ausserhalb der Zonen: 172.20.0.0/16" in msg

    # ohne erzwungenen Import: der L3Out uebernimmt alles - eine Abweichung, nichts „fehlt“
    s = session_factory()
    o = s.query(TopoL3Out).filter_by(name="L3O_fw").one()
    o.route_control = {"enforce": ["export"], "import": [{"prefix": "10.60.0.0/24"}]}
    s.commit()
    r = bgp_zones.for_l3out(s, tid, o)
    s.close()
    assert not r["import_enforced"] and r["missing"] == [] and r["state"] == "differs"
