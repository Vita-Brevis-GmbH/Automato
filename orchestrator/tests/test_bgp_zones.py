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


def _seed(client, fw: dict | None = None) -> tuple[int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    systems = (("fw-01", "fortigate", fw or _fw1()), ("fw-02", "fortigate", tp.FW2),
               ("apic-01", "cisco_aci", tp.APIC))  # fmt: skip
    for name, platform, result in systems:
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


def test_begruendung_je_abweichung(client, session_factory):
    """0.127.0 (Anwender: „Bei BGP Findings sehe ich sehr viele Filter, welche nicht passen
    wuerden, aber ich sehe keine Details, weshalb“): je Abweichung Netz, Zone und entscheidende
    Regel (bzw. keine passt, mit den erlaubten Teilen darin), je zu offener Regel die Regel,
    dazu woher die Zonen kommen - in der Ansicht und im Befund."""
    fw = _fw1()
    fw["routing"]["bgp"][1]["data"]["rules"].append(
        {"id": 4, "action": "permit", "prefix": "10.99.1.0/24"})  # fmt: skip
    tid, _fw = _seed(client, fw)
    from app.models import TopoPeer
    from app.store import bgp_zones

    s = session_factory()
    p = s.query(TopoPeer).filter_by(peer_ip=FW02).one()
    r = bgp_zones.for_peer(s, tid, p)
    s.close()
    (v4,) = r["lists"]
    why = {(w["kind"], w["prefix"]): w for w in v4["reasons"]}
    miss = why[("missing", "10.99.0.0/16")]
    assert miss["zones"] == ["Lan"]
    assert miss["by"] == [{"kind": "prefix_list", "filter": "PL_FW02", "verdict": "deny",
                           "rule": None}]  # fmt: skip
    assert miss["parts"] == ["10.99.1.0/24"]  # nur ein Teil ist erlaubt
    wide = why[("too_open", "172.16.0.0/12")]
    assert (wide["filter"], wide["rule"], wide["zones"]) == (
        "PL_FW02", "3 permit 172.16.0.0/12 le 32", [])  # fmt: skip
    ok = next(e for e in v4["entries"] if e["prefix"] == "10.50.0.0/16")
    assert [d["rule"] for d in ok["decided"]] == ["2 permit 10.50.0.0/16 le 24"]
    chain = r["chain"]
    assert (chain["home"], chain["neighbor"]) == ("DC FW", "Lan")
    assert chain.get("device") or chain.get("network")
    assert [(z["name"], z["hops"]) for z in chain["zones"]] == [("Lan", 1)]

    (f,) = client.get("/findings", params={"tenant_id": tid, "type": "bgp_zone_mismatch"}).json()
    assert "gesperrt: 10.99.0.0/16 (Lan)" in f["message"]
    assert "172.16.0.0/12 (PL_FW02; keine Zone)" in f["message"]
    assert "Firewall in Zone DC FW, Nachbar in Zone Lan" in f["message"]
    kinds = [(w["kind"], w["prefix"]) for w in f["detail"]["why"]]
    assert ("missing", "10.99.0.0/16") in kinds and ("too_open", "172.16.0.0/12") in kinds
    assert f["detail"]["chain"]["neighbor"] == "Lan"
    en = client.get("/findings", params={"tenant_id": tid, "type": "bgp_zone_mismatch"},
                    headers={"Accept-Language": "en"}).json()[0]  # fmt: skip
    assert "Firewall in zone DC FW, neighbor in zone Lan" in en["message"]


def test_session_sync_kein_uebergang(client, session_factory):
    """0.130.0 (Anwender: „Es zeigt mir nun auch andere Zonen an, wo es nicht möglich sein soll.
    Zonen, welche in anderen Tenants oder Session Links“): fw-02 synchronisiert die Sessions
    ueber 10.0.9.0/30 mit fw-03 (Zone „Kunde B“, FGSP). Das Sync-Netz ist kein Uebergang - Kunde
    B liegt nicht hinter Lan - und kein Netz der Zone; je Zone der Weg mit dem Netz."""
    fw2 = copy.deepcopy(tp.FW2)
    fw2["inventory"]["interfaces"].append(tp._if("fw-02", "sync", "10.0.9.1/30"))
    fw2["routing"]["ha"] = [{"node": "fw-02", "mode": "a-p",
                             "sync_peers": [{"peerip": "10.0.9.2"}]}]  # fmt: skip
    fw3 = {"status": "completed",
           "inventory": {"devices": [{"name": "fw-03", "role": "firewall"}],
                         "interfaces": [tp._if("fw-03", "sync", "10.0.9.2/30"),
                                        tp._if("fw-03", "lan", "10.61.0.1/24")]},
           "routing": {"contexts": [{"node": "fw-03", "context": "root"}]}}  # fmt: skip
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    for name, result in (("fw-01", _fw1()), ("fw-02", fw2), ("fw-03", fw3)):
        tp._deliver(client, tid, tp._system(client, tid, name, "fortigate"), "fortigate", result)
    sys = _ids(client, tid)

    def dev(sid):
        return {"kind": "fw_vdom", "value": "root", "source_system_id": sid}

    _zone(client, tid, "DC FW", [dev(sys["fw-01"])])
    lan = _zone(client, tid, "Lan", [dev(sys["fw-02"]),
                                     {"kind": "prefix", "value": "10.50.0.0/16"}])  # fmt: skip
    _zone(client, tid, "Kunde B", [dev(sys["fw-03"])])
    from app.models import TopoPeer
    from app.store import bgp_zones, zone_struct
    from app.store import zones as zone_store

    s = session_factory()
    st = zone_struct.build(s, tid, zone_store.load(s, tid))
    assert [x["network"] for x in st.sync_links] == ["10.0.9.0/30"]
    assert not any(i["network"] == "10.0.9.0/30" for t in st.transitions.values()
                   for i in t.networks)  # fmt: skip
    assert "10.0.9.0/30" not in {str(n) for n in st.nets(lan)[1]}
    p = s.query(TopoPeer).filter_by(peer_ip=FW02).one()
    r = bgp_zones.for_peer(s, tid, p)
    s.close()
    assert [z["name"] for z in r["zones"]] == ["Lan"]  # ohne Kunde B
    (v4,) = r["lists"]
    assert "10.0.9.0/30" not in {e["prefix"] for e in v4["entries"]}
    # Uebergang zum Nachbarn: zuerst das Netz mit seiner Adresse (dazu 10.255.0.0/29: Lan nur
    # hinter dem Router des ACI)
    assert r["chain"]["zones"][0]["path"] == [
        {"zone": "DC FW", "network": None},
        {"zone": "Lan", "network": "10.70.0.0/30", "more": 1},
    ]


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
