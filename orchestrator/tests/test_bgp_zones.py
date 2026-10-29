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
