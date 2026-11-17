"""Spanning Tree (1.19.0, Anwender 2026-11-17 Prioritaet 5): Einspielen je Lauf, Baeume je VLAN
mit Root und Soll-Root, Befunde (Root nicht am Soll bzw. nur gemeldet, unerwartet blockiert,
BPDU-Guard, gemischte Modi, viele Topologieaenderungen) positiv und negativ, API mit Soll und
Schwelle (operator, Audit) und der Datenstand (Teil wirkt nicht auf Doku und Wege).

Aufbau wie `test_cabling`: sw-acc-01 ueber Po1 (Gi1/0/47-48) an sw-core-01, Gi1/0/5 an Eth1/3,
Te1/1/1 an fw-08 (FortiGate laut LLDP), dazu ein Switch einer Plattform ohne Spanning Tree.
"""

import copy
from datetime import UTC, datetime, timedelta

import test_cabling as tc
from app.models import AuditLog, DataChange, Finding, StpDevice, StpInstance
from app.store import data_state, stp, stp_findings
from sqlalchemy import select

ADMIN, OP, TOKEN = tc.ADMIN, tc.OP, tc.TOKEN
VIEWER = {"X-Dev-Roles": "viewer"}
CORE_MAC, ACC_MAC = "52:54:00:aa:00:99", "7c:21:0e:00:00:01"


def _p(port, role, state, cost=4, ptype="P2p", inc=None):
    return {"port": port, "role": role, "state": state, "cost": cost, "priority": "128.1",
            "type": ptype, "inconsistent": inc}  # fmt: skip


def _inst(device, vlan, ports, root=False, root_mac=CORE_MAC, tc_count=None, tc_from=None,
          protocol="rstp", mst=None):  # fmt: skip
    bridge = CORE_MAC if device == "sw-core-01" else ACC_MAC
    return {"device": device, "instance": f"MST{mst}" if mst is not None else f"VLAN{vlan:04d}",
            "vlan": None if mst is not None else vlan, "mst": mst,
            "vlans": "1-4094" if mst is not None else None, "protocol": protocol,
            "root_priority": 24576 + (vlan or 0), "root_mac": bridge if root else root_mac,
            "root_cost": 0 if root else 3, "root_port": None if root else "Po1", "is_root": root,
            "bridge_priority": 32768 + (vlan or 0), "bridge_mac": bridge,
            "tc_count": tc_count, "tc_ago": 300 if tc_count is not None else None,
            "tc_from": tc_from, "ports": ports}  # fmt: skip


def _switches(core_mode="rapid-pvst", tc_count=5):
    result = copy.deepcopy(tc.SWITCHES)
    inv = result["inventory"]
    fw = next(n for n in inv["neighbors"] if n["remote_name"] == "fw-08")
    fw["remote_description"] = "FortiGate-200F v7.2.8,build1639"
    fw["remote_caps"] = ["R"]
    inv["devices"].append({"name": "sw-cbs-01", "role": "switch", "mgmt_ip": "10.0.0.13"})
    core_proto = "mstp" if core_mode == "mst" else "rstp"
    inv["stp_devices"] = [
        {"device": "sw-acc-01", "status": "read", "mode": "rapid-pvst",
         "errdisabled": [{"port": "Gi1/0/2", "reason": "bpduguard"},
                         {"port": "Gi1/0/9", "reason": "psecure-violation", "vlans": "20"}]},
        {"device": "sw-core-01", "status": "read", "mode": core_mode},
        {"device": "sw-cbs-01", "status": "unsupported"},
    ]  # fmt: skip
    if core_mode == "mst":
        core = [_inst("sw-core-01", None, [_p("Po10", "designated", "forwarding",
                                              ptype="P2p Bound(PVST)")],
                      root=True, protocol=core_proto, mst=0)]  # fmt: skip
    else:
        core = [_inst("sw-core-01", v, [_p("Po10", "designated", "forwarding"),
                                        _p("Eth1/3", "designated", "forwarding")], root=True)
                for v in (10, 20, 30)]  # fmt: skip
    inv["stp"] = core + [
        _inst("sw-acc-01", 10, [_p("Po1", "root", "forwarding"),
                                _p("Gi1/0/2", "designated", "broken", inc="ROOT_Inc")]),
        # Reserve zum Kern: erwartet (Gegenstelle ein Switch)
        _inst("sw-acc-01", 20, [_p("Po1", "root", "forwarding"),
                                _p("Gi1/0/5", "alternate", "blocking")]),
        # zur Firewall blockiert: unerwartet; viele Aenderungen von dort
        _inst("sw-acc-01", 30, [_p("Po1", "root", "forwarding"),
                                _p("Te1/1/1", "alternate", "blocking")],
              tc_count=tc_count, tc_from="Te1/1/1"),
        # nur auf sw-acc-01: Root ist der Access-Switch
        _inst("sw-acc-01", 40, [_p("Gi1/0/5", "designated", "forwarding")], root=True),
    ]  # fmt: skip
    return result


def _deliver(client, tid, sid, result):
    req = {"platform": "cisco_switch", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _setup(client, **kw):
    tid, sw = tc._setup(client)
    _deliver(client, tid, sw["id"], _switches(**kw))
    return tid, sw


def _findings(client, tid, ftype):
    r = client.get("/findings", params={"tenant_id": tid, "type": ftype, "limit": 500})
    assert r.status_code == 200, r.text
    return {f["subject_id"]: f for f in r.json()}


def test_einspielen_baeume_und_ansicht(client, session_factory):
    tid, sw = _setup(client)
    s = session_factory()
    devices = {d.device: d for d in s.scalars(select(StpDevice))}
    assert {k: d.status for k, d in devices.items()} == {
        "sw-acc-01": "read", "sw-core-01": "read", "sw-cbs-01": "unsupported"}  # fmt: skip
    assert len(list(s.scalars(select(StpInstance)))) == 7
    s.close()

    ov = client.get("/stp/overview", params={"tenant_id": tid}).json()
    sm = ov["summary"]
    assert sm["status"] == {"read": 2, "off": 0, "unsupported": 1, "unreadable": 0}
    assert sm["unsupported"] == ["sw-cbs-01"]  # gezaehlt, nicht still
    assert (sm["trees"], sm["blocked"], sm["unexpected"]) == (4, 3, 2)
    assert (sm["errdisabled"], sm["bpdu_guard"], sm["tc_per_day"]) == (2, 1, stp.TC_PER_DAY)
    acc = next(d for d in ov["devices"] if d["device"] == "sw-acc-01")
    assert acc["root_of"] == ["VLAN0040"] and acc["blocked"] == 3
    assert [e["bpdu_guard"] for e in acc["errdisabled"]] == [True, False]

    trees = client.get("/stp/trees", params={"tenant_id": tid}).json()
    assert [t["label"] for t in trees] == ["VLAN 10", "VLAN 20", "VLAN 30", "VLAN 40"]
    v30 = next(t for t in trees if t["number"] == 30)
    assert v30["root"]["names"] == ["sw-core-01"] and v30["root"]["mac"] == CORE_MAC
    assert (v30["switches"], v30["blocked"], v30["unexpected"], v30["target_state"]) == (
        2, 1, 1, "none")  # fmt: skip
    only = client.get("/stp/trees", params={"tenant_id": tid, "only": "problems"}).json()
    assert [t["number"] for t in only] == [10, 30]  # Schutzfunktion bzw. Firewall
    assert [t["number"] for t in client.get(
        "/stp/trees", params={"tenant_id": tid, "vlan": 20}).json()] == [20]  # fmt: skip
    assert client.get("/stp/trees", params={"tenant_id": tid, "q": "sw-cbs"}).json() == []

    detail = client.get("/stp/tree", params={"tenant_id": tid, "key": v30["key"]}).json()
    acc30 = next(m for m in detail["members"] if m["device"] == "sw-acc-01")
    te = next(p for p in acc30["ports"] if p["port"] == "Te1/1/1")
    assert te["blocked"] and te["unexpected"] == ["no_bridge"]
    assert te["neighbors"] == [{"device": "fw-08", "port": "port5", "kind": "firewall",
                                "read": False}]  # fmt: skip
    edges = {(e["from"], e["kind"], e["to"]) for e in detail["edges"]}
    assert ("sw-acc-01", "root", "sw-core-01") in edges  # Root-Port ueber Po1 (Mitglieder)
    assert ("sw-acc-01", "blocked", "fw-08") in edges
    v20 = client.get("/stp/tree", params={"tenant_id": tid, "key": trees[1]["key"]}).json()
    gi5 = next(p for m in v20["members"] for p in m["ports"] if p["port"] == "Gi1/0/5")
    assert gi5["blocked"] and gi5["unexpected"] == []  # Reserve zum Kern: erwartet
    missing = client.get("/stp/tree", params={"tenant_id": tid, "key": "vlan|99|-"})
    assert missing.status_code == 404
    # nur Betreuer (Infrastruktur), nicht Kunden-Benutzer
    user = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": "1"}
    assert client.get("/stp/overview", params={"tenant_id": tid}, headers=user).status_code == 403

    # Lauf ohne Spanning Tree (aelterer Agent): der Stand bleibt
    plain = copy.deepcopy(tc.SWITCHES)
    _deliver(client, tid, sw["id"], plain)
    s = session_factory()
    assert len(list(s.scalars(select(StpInstance)))) == 7
    s.close()


def test_befunde_ports_bpdu_guard_und_root_ohne_soll(client):
    tid, _ = _setup(client)
    blocked = _findings(client, tid, "stp_port_blocked")
    assert set(blocked) == {"sw-acc-01|Te1/1/1", "sw-acc-01|Gi1/0/2"}  # nicht Gi1/0/5 (Reserve)
    te = blocked["sw-acc-01|Te1/1/1"]
    assert te["detail"]["reasons"] == ["no_bridge"] and te["detail"]["instances"] == "VLAN 30"
    assert te["severity"] == "warning" and "fw-08 (Firewall)" in te["message"]
    assert blocked["sw-acc-01|Gi1/0/2"]["detail"]["reasons"] == ["guard"]
    bpdu = _findings(client, tid, "stp_bpdu_guard")
    assert list(bpdu) == ["sw-acc-01|Gi1/0/2"]  # psecure-violation nur in der Ansicht
    assert "BPDU-Guard" in bpdu["sw-acc-01|Gi1/0/2"]["message"]
    info = _findings(client, tid, "stp_root_info")
    assert set(info) == {"stp-root|sw-core-01", "stp-root|sw-acc-01"}
    core = info["stp-root|sw-core-01"]
    assert core["severity"] == "info" and core["detail"]["vlans"] == "10,20,30"
    assert core["message"].startswith("Root ist sw-core-01 für VLAN 10,20,30")
    assert _findings(client, tid, "stp_root_mismatch") == {}
    assert _findings(client, tid, "stp_mode_mixed") == {}  # beide Rapid-PVST
    assert _findings(client, tid, "stp_topology_changes") == {}  # erst ab zwei Laeufen
    # auf Englisch
    r = client.get("/findings", params={"tenant_id": tid, "type": "stp_port_blocked"},
                   headers={"Accept-Language": "en"})  # fmt: skip
    assert any("peer is not a switch" in f["message"] for f in r.json())


def test_soll_root_api_und_befunde(client, session_factory):
    tid, sw = _setup(client)
    # zwei Standorte: ohne Angabe am Quellsystem gehoeren die Switches zu keinem
    zrh, bsl = (client.post(f"/tenants/{tid}/sites", json={"code": code, "name": name},
                            headers=ADMIN).json()
                for code, name in (("chzrh1", "Zürich"), ("chbsl1", "Basel")))  # fmt: skip
    body = {"tenant_id": tid, "root": "sw-core-01", "backup": "sw-acc-01"}
    assert client.put("/stp/targets", json=body, headers=VIEWER).status_code == 403
    r = client.put("/stp/targets", json=body, headers=OP)
    assert r.status_code == 200, r.text
    target = r.json()
    assert (target["root_known"], target["backup_known"], target["vlans"]) == (True, True, None)
    # VLAN 40 hat den Ersatz-Root als Root: Hinweis; die uebrigen sind am Soll
    assert _findings(client, tid, "stp_root_info") == {}
    mism = _findings(client, tid, "stp_root_mismatch")
    (only,) = mism.values()
    assert only["severity"] == "info" and only["detail"]["state"] == "backup"
    assert "Ersatz-Root sw-acc-01" in only["message"]

    # genauerer Eintrag fuer VLAN 40 mit anderem Soll: Warnung
    r = client.put("/stp/targets", json={"tenant_id": tid, "vlans": "40, 41-42",
                                         "root": "sw-dist-09"}, headers=OP)  # fmt: skip
    assert r.status_code == 200 and r.json()["vlans"] == "40-42" and not r.json()["root_known"]
    v40 = _findings(client, tid, "stp_root_mismatch")
    (warn,) = v40.values()
    assert warn["severity"] == "warning" and warn["detail"]["target_root"] == "sw-dist-09"
    assert warn["message"].startswith("Root für VLAN 40 ist sw-acc-01, Soll ist sw-dist-09")
    trees = client.get("/stp/trees", params={"tenant_id": tid}).json()
    assert {t["number"]: t["target_state"] for t in trees} == {10: "ok", 20: "ok", 30: "ok",
                                                               40: "mismatch"}  # fmt: skip
    # gleicher Bereich noch einmal: derselbe Eintrag (geaendert, nicht doppelt)
    again = client.put("/stp/targets", json={"tenant_id": tid, "vlans": "40-42",
                                             "root": "sw-acc-01"}, headers=OP).json()  # fmt: skip
    assert again["id"] == r.json()["id"]
    assert _findings(client, tid, "stp_root_mismatch") != v40
    # Standort: gilt erst, wenn die Switches an diesem Standort stehen (Quellsystem)
    site_t = client.put("/stp/targets", json={"tenant_id": tid, "site_id": zrh["id"],
                                              "root": "sw-acc-01"}, headers=OP)  # fmt: skip
    assert site_t.status_code == 200 and site_t.json()["site"] == "chzrh1 · Zürich"
    states = {t["number"]: t["target_state"]
              for t in client.get("/stp/trees", params={"tenant_id": tid}).json()}  # fmt: skip
    assert states[10] == "ok"
    assert client.patch(f"/systems/{sw['id']}", json={"site_id": zrh["id"]},
                        headers=ADMIN).status_code == 200  # fmt: skip
    trees = client.get("/stp/trees", params={"tenant_id": tid}).json()
    # Standort vor VLAN-Bereich: Soll sw-acc-01 ohne Ersatz
    states = {t["number"]: t["target_state"] for t in trees}
    assert states == {10: "mismatch", 20: "mismatch", 30: "mismatch", 40: "ok"}
    assert trees[0]["sites"] == ["chzrh1 · Zürich"] and trees[0]["target"]["site_id"] == zrh["id"]
    assert client.get("/stp/trees", params={"tenant_id": tid, "site_id": bsl["id"]}).json() == []
    # ungueltig: VLANs, Standort eines anderen Mandanten, Ersatz = Root
    for bad in ({"vlans": "10-x"}, {"vlans": "5000"}, {"site_id": 999999},
                {"backup": "SW-CORE-01.example.ch"}):  # fmt: skip
        r = client.put("/stp/targets", json={"tenant_id": tid, "root": "sw-core-01", **bad},
                       headers=OP)  # fmt: skip
        assert r.status_code == 422, (bad, r.text)
    listed = client.get("/stp/targets", params={"tenant_id": tid}).json()
    assert len(listed) == 3
    for t in listed:
        assert client.delete(f"/stp/targets/{t['id']}", headers=OP).status_code == 204
    assert client.delete(f"/stp/targets/{listed[0]['id']}", headers=OP).status_code == 404
    # ohne Soll wieder nur gemeldet
    assert _findings(client, tid, "stp_root_mismatch") == {}
    assert len(_findings(client, tid, "stp_root_info")) == 2
    s = session_factory()
    actions = [a.action for a in s.scalars(select(AuditLog)) if a.action.startswith("stp_")]
    s.close()
    assert actions.count("stp_target.created") == 3 and actions.count("stp_target.deleted") == 3
    assert actions.count("stp_target.changed") == 1


def test_gemischte_modi_im_l2_bereich(client):
    tid, _ = _setup(client, core_mode="mst")
    mixed = _findings(client, tid, "stp_mode_mixed")
    (f,) = mixed.values()
    assert f["detail"]["modes"] == {"mst": ["sw-core-01"], "rapid-pvst": ["sw-acc-01"]}
    assert f["detail"]["boundary"] == [{"device": "sw-core-01", "port": "Po10",
                                        "type": "Bound(PVST)"}]  # fmt: skip
    assert "Gemischte Spanning-Tree-Modi" in f["message"]
    assert "MST: sw-core-01" in f["message"]


def test_viele_topologieaenderungen_und_schwelle(client, session_factory):
    tid, sw = _setup(client, tc_count=5)
    # zweiter Lauf drei Stunden spaeter mit 35 Aenderungen mehr
    s = session_factory()
    row = s.scalars(
        select(StpInstance).where(
            StpInstance.instance == "VLAN0030", StpInstance.device == "sw-acc-01"
        )
    ).one()
    earlier = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    row.tc_history = [[earlier, 5]]
    s.commit()
    s.close()
    _deliver(client, tid, sw["id"], _switches(tc_count=40))
    found = _findings(client, tid, "stp_topology_changes")
    (f,) = found.values()
    assert f["subject_id"] == "stp-tc|sw-acc-01"
    item = f["detail"]["instances"][0]
    assert (item["instance"], item["delta"], item["per_day"], item["from"]) == (
        "VLAN0030", 35, 35.0, "Te1/1/1")  # fmt: skip
    assert "Te1/1/1" in f["message"]
    # Schwelle hoeher (operator, Audit): kein Befund mehr; Vorgabe wieder: Befund
    assert client.put("/stp/settings", json={"tenant_id": tid, "tc_per_day": 50},
                      headers=VIEWER).status_code == 403  # fmt: skip
    r = client.put("/stp/settings", json={"tenant_id": tid, "tc_per_day": 50}, headers=OP)
    assert r.json() == {"tc_per_day": 50, "tc_default": stp.TC_PER_DAY}
    assert _findings(client, tid, "stp_topology_changes") == {}
    sm = client.get("/stp/overview", params={"tenant_id": tid}).json()["summary"]
    assert sm["tc_per_day"] == 50
    assert client.put("/stp/settings", json={"tenant_id": tid, "tc_per_day": 0},
                      headers=OP).status_code == 422  # fmt: skip
    client.put("/stp/settings", json={"tenant_id": tid, "tc_per_day": None}, headers=OP)
    assert len(_findings(client, tid, "stp_topology_changes")) == 1
    # Neustart (Zaehler kleiner): Verlauf beginnt neu, keine Aussage
    _deliver(client, tid, sw["id"], _switches(tc_count=2))
    assert _findings(client, tid, "stp_topology_changes") == {}


def test_zeitfenster_der_topologieaenderungen():
    now = datetime.now(UTC)

    def window(points):
        inst = StpInstance(tc_history=[[(now - timedelta(hours=h)).isoformat(), c]
                                       for h, c in points])  # fmt: skip
        return stp.Model.tc_window(inst)

    assert window([(0, 5)]) is None  # ein Stand: keine Aussage
    assert window([(0.5, 5), (0, 50)]) is None  # zu kurz
    assert window([(2, 5), (0, 8)]) == {"delta": 3, "hours": 2.0, "per_day": 3.0}
    # Laeufe eine Woche auseinander: auf 24 h umgerechnet
    assert window([(168, 0), (0, 70)])["per_day"] == 10.0
    # mehr als 24 h Verlauf: Basis ist der juengste Stand vor 24 h
    assert window([(48, 0), (30, 100), (20, 150), (0, 160)])["delta"] == 60
    assert window([(5, 50), (0, 10)]) is None  # zurueckgesetzt
    hist = stp._history([[(now - timedelta(days=9)).isoformat(), 1],
                         [(now - timedelta(hours=1)).isoformat(), 3]], 7, now)  # fmt: skip
    assert [c for _, c in hist] == [3, 7]  # alter Eintrag weg
    assert stp._history(hist, 2, now) == [[now.isoformat(), 2]]  # Neustart


def test_datenstand_wirkt_nicht_auf_doku_und_wege(client, session_factory):
    assert data_state.scope("inventory.stp") == data_state.NONE
    assert data_state.scope("inventory.stp_devices") == data_state.NONE
    tid, sw = _setup(client, tc_count=5)
    _deliver(client, tid, sw["id"], _switches(tc_count=9))  # nur der Spanning Tree anders
    s = session_factory()
    last = s.scalars(select(DataChange).order_by(DataChange.seq.desc())).first()
    assert last.parts == ["inventory.stp"] and not last.all and not last.devices
    s.close()


def test_loeschen_des_quellsystems_und_pruefung_aus(client, session_factory):
    tid, sw = _setup(client)
    assert client.put("/checks/stp_blockiert", params={"tenant_id": tid},
                      json={"enabled": False}, headers=ADMIN).status_code == 200  # fmt: skip
    client.put("/stp/targets", json={"tenant_id": tid, "root": "sw-core-01"}, headers=OP)
    assert _findings(client, tid, "stp_port_blocked") == {}  # ausgeschaltet: Befunde weg
    s = session_factory()
    assert stp_findings.refresh(s, tid) > 0
    s.commit()
    s.close()
    r = client.delete(f"/systems/{sw['id']}", headers=ADMIN)
    assert r.status_code in (200, 204), r.text
    s = session_factory()
    assert list(s.scalars(select(StpInstance))) == [] and list(s.scalars(select(StpDevice))) == []
    assert not [f for f in s.scalars(select(Finding)) if f.type.value == "stp_bpdu_guard"]
    s.close()
