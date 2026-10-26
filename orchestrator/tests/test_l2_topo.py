"""L2-Sicht (0.64.0, ADR 0025 Nr. 9): Plan nach Ebenen, VLAN-Wege und Inseln, Ausfall eines
Geraets bzw. Kabels, Redundanz-Befunde und L2-Abschnitte in der Weg-Suche.

Aufbau wie `test_cabling` (sw-acc-01 dreifach an sw-core-01, sw-core-01 am ACI-Leaf, fw-08 an
sw-acc-01), dazu:
- sw-acc-02 mit einem einzigen Kabel an sw-core-01 (Trunk ohne VLAN 40), VLAN 40 an einem
  Access-Port von sw-acc-02 und als SVI auf sw-core-01 -> VLAN 40 getrennt;
- esx01 mit zwei Kabeln, beide an sw-acc-02;
- Po1 von sw-acc-01 mit einem dritten, nicht verbundenen Mitglied;
- fw-08 (FortiGate) mit VLAN-Subinterface `dmz` (VLAN 30) auf port5.
"""

import copy

import test_cabling as tc
from app.store import l2_topo

ADMIN, OP, TOKEN = tc.ADMIN, tc.OP, tc.TOKEN


def _switches():
    result = copy.deepcopy(tc.SWITCHES)
    inv = result["inventory"]
    inv["devices"].append({"name": "sw-acc-02", "role": "switch", "mgmt_ip": "10.0.0.12"})
    trunk40 = {"mode": "tagged", "untagged_vlan": 1, "native_vlan": 1}
    inv["interfaces"] += [
        tc._port("sw-acc-01", "Gi1/0/46", lag="Po1", status="notconnect", **tc.TRUNK),
        tc._port("sw-core-01", "Eth1/20", trunk_vlans="10,20,40", **trunk40),
        {"device": "sw-core-01", "name": "Vl40", "kind": "vlan", "ips": ["10.40.0.1/24"]},
        tc._port("sw-acc-02", "Gi1/0/1", trunk_vlans="10,20", **trunk40),
        tc._port("sw-acc-02", "Gi1/0/10", mode="access", untagged_vlan=40),
        tc._port("sw-acc-02", "Gi1/0/11", trunk_vlans="10,20", **trunk40),
        tc._port("sw-acc-02", "Gi1/0/12", trunk_vlans="10,20", **trunk40),
    ]
    inv["neighbors"] += [
        {"device": "sw-core-01", "port": "Eth1/20", "protocol": "cdp",
         "remote_name": "sw-acc-02", "remote_port": "GigabitEthernet1/0/1"},
        {"device": "sw-acc-02", "port": "Gi1/0/1", "protocol": "cdp",
         "remote_name": "sw-core-01", "remote_port": "Ethernet1/20"},
        {"device": "sw-acc-02", "port": "Gi1/0/11", "protocol": "cdp",
         "remote_name": "esx01.example.ch", "remote_port": "vmnic0",
         "remote_platform": "VMware ESX", "remote_caps": ["Switch"]},
        {"device": "sw-acc-02", "port": "Gi1/0/12", "protocol": "cdp",
         "remote_name": "esx01.example.ch", "remote_port": "vmnic1",
         "remote_platform": "VMware ESX", "remote_caps": ["Switch"]},
    ]  # fmt: skip
    inv["macs"] = [{"device": "sw-acc-02", "port": "Gi1/0/10", "mac": "a0:b1:c2:00:00:40",
                    "vlan": 40}]  # fmt: skip
    inv["arps"] = [{"device": "sw-core-01", "ip": "10.40.0.15", "mac": "a0:b1:c2:00:00:40",
                    "interface": "Vl40"}]  # fmt: skip
    return result


FORTIGATE = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "fw-08", "role": "firewall"}],
        "interfaces": [
            {"device": "fw-08", "name": "port5", "kind": "physical", "mode": "tagged",
             "tagged_vlans": [30]},
            {"device": "fw-08", "name": "dmz", "kind": "vlan", "mode": "tagged",
             "tagged_vlans": [30], "parent": "port5", "ips": ["10.30.0.1/24"]},
        ],
    },
}  # fmt: skip
VCENTER = {
    "status": "completed",
    "virtual": {"vms": [{"name": "vm01", "power_state": "poweredOn", "cluster": "c1",
                         "host": "esx01.example.ch", "nics": []}], "portgroups": []},
}  # fmt: skip


def _deliver(client, tid, sid, platform, result):
    req = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _setup(client):
    tid, sw = tc._setup(client)
    for name, platform, result in (("vc", "vmware", VCENTER), ("fw-08", "fortigate", FORTIGATE)):
        body = {"tenant_id": tid, "name": name, "platform": platform,
                "base_url": f"https://{name}.example.ch"}  # fmt: skip
        sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
        _deliver(client, tid, sid, platform, result)
    _deliver(client, tid, sw["id"], "cisco_switch", _switches())
    return tid, sw


def test_plan_ausfall_und_befunde(client, session_factory):
    tid, _sw = _setup(client)
    plan = client.get("/cabling/plan", params={"tenant_id": tid}).json()
    layer = {n["name"]: n["layer"] for n in plan["nodes"]}
    assert layer["sw-core-01"] == 0  # Kern: meiste weiterleitende Nachbarn
    assert layer["sw-acc-01"] == layer["sw-acc-02"] == layer["leaf-101"] == 1
    assert layer["fw-08"] == layer["esx01.example.ch"] == 2
    edge = next(e for e in plan["edges"] if {e["a"], e["b"]} == {"sw-acc-01", "sw-core-01"})
    assert edge["links"] == 3 and "Po1" in edge["lags"]
    only = client.get("/cabling/plan", params={"tenant_id": tid, "ends": "false"}).json()
    assert {n["name"] for n in only["nodes"]} == {"sw-core-01", "sw-acc-01", "sw-acc-02",
                                                   "leaf-101"}  # fmt: skip
    # VLAN 40: SVI auf dem Kern, Access-Port auf sw-acc-02, der Trunk dazwischen sperrt es
    v40 = client.get("/cabling/plan", params={"tenant_id": tid, "vlan": 40}).json()
    assert v40["vlan"]["present"] == ["sw-acc-02", "sw-core-01"]
    assert sorted(v40["vlan"]["islands"]) == [["sw-acc-02"], ["sw-core-01"]]
    e40 = next(e for e in v40["edges"] if {e["a"], e["b"]} == {"sw-acc-02", "sw-core-01"})
    assert e40["vlan"] == "no"
    e30 = client.get("/cabling/plan", params={"tenant_id": tid, "vlan": 30}).json()["edges"]
    acc = next(e for e in e30 if {e["a"], e["b"]} == {"sw-acc-01", "sw-core-01"})
    assert acc["vlan"] == "partial"  # Po1 ja, Gi1/0/48 und Gi1/0/5 nein

    # Ausfall: ohne den Kern zerfaellt das Netz; ohne das Kabel zu sw-acc-02 haengt es allein
    core = client.get("/cabling/failure", params={"tenant_id": tid, "device": "sw-core-01"})
    body = core.json()
    assert sorted(body["parts"]) == [["esx01.example.ch", "sw-acc-02"], ["fw-08", "sw-acc-01"],
                                     ["leaf-101"]]  # fmt: skip
    assert len(body["cut_off"]) == 2
    with session_factory() as s:
        topo = l2_topo.L2(s, tid)
        pair = {"sw-acc-02", "sw-core-01"}
        lid = next(lid for lid, lk in topo.cab.links.items()
                   if {lk.a.device.name, lk.b.device.name} == pair)  # fmt: skip
    cut = client.get("/cabling/failure", params={"tenant_id": tid, "link": lid}).json()
    assert cut["cut_off"] == [["esx01.example.ch", "sw-acc-02"]]
    assert client.get("/cabling/failure", params={"tenant_id": tid}).status_code == 422
    unknown = {"tenant_id": tid, "device": "gibt-es-nicht"}
    assert client.get("/cabling/failure", params=unknown).status_code == 404

    # Befunde
    found = client.get("/findings", params={"tenant_id": tid, "limit": 200}).json()
    items = found["items"] if isinstance(found, dict) else found
    by_type: dict[str, list] = {}
    for f in items:
        by_type.setdefault(f["type"], []).append(f)
    (single,) = by_type["l2_single_link"]
    assert "sw-acc-02 hängt mit einem Kabel an sw-core-01" in single["message"]
    switch = {f["detail"]["device"]: f for f in by_type["l2_single_switch"]}
    assert switch["esx01.example.ch"]["severity"] == "warning"
    assert switch["sw-acc-01"]["severity"] == "info"  # drei Kabel, alle zum Kern
    (point,) = by_type["l2_single_point"]
    assert "fällt sw-core-01 aus, zerfällt das Netz in 3 Teile" in point["message"]
    (lag,) = by_type["lag_degraded"]
    assert "sw-acc-01 Po1: 2 von 3 Mitgliedern verbunden (Gi1/0/46 notconnect)" in lag["message"]
    (split,) = by_type["vlan_split"]
    assert "VLAN 40 ist getrennt: sw-acc-02 | sw-core-01" in split["message"]
    assert "Trunks dazwischen" in split["hint"]
    # nicht fuer Kunden-Benutzer
    cust = client.post("/customers", json={"tenant_id": tid, "name": "K", "selectors": []},
                       headers=OP).json()  # fmt: skip
    who = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(cust["id"])}
    for path in ("/cabling/plan", "/cabling/failure?device=sw-core-01"):
        assert client.get(path, params={"tenant_id": tid}, headers=who).status_code == 403


def test_l2_in_der_weg_suche(client, session_factory):
    tid, _sw = _setup(client)
    with session_factory() as s:
        topo = l2_topo.L2(s, tid)
        assert topo.physical("fw-08", "dmz") == ({"port5"}, 30)
        seg = topo.between(["fw-08"], "dmz", ["leaf-101"], "eth1/10")
        assert seg is not None and seg.vlan == 30
        # VLAN 30 nur ueber Po1/Po10 (Gi1/0/48 und Gi1/0/5 sperren es)
        assert seg.text == ("fw-08 port5 → sw-acc-01 (Te1/1/1 → Po1) → sw-core-01 (Po10 → "
                            "Eth1/10) → leaf-101 eth1/10")  # fmt: skip
        assert seg.out()["switches"] == 3  # sw-acc-01, sw-core-01, leaf-101
        assert topo.between(["fw-08"], "gibt-es-nicht", ["leaf-101"]) is None
        # Weg-Suche: zwischen den Hops und bis zum Anschluss des Ziels (ARP + MAC-Tabelle)
        result = {"paths": [{"hops": [
            {"members": ["fw-08"], "egress": {"interface": "dmz"}},
            {"members": ["leaf-101"], "ingress": {"interface": "eth1/10"},
             "egress": {"interface": "eth1/10"}},
        ]}]}  # fmt: skip
        l2_topo.annotate(s, tid, result, "10.30.0.9", "10.40.0.15")
    hops = result["paths"][0]["hops"]
    assert hops[0]["l2"]["text"].startswith("fw-08 port5 → sw-acc-01")
    (dst,) = result["l2"]["dst"]
    assert (dst["device"], dst["port"], dst["vlan"]) == ("sw-acc-02", "Gi1/0/10", 40)
    assert hops[1]["l2"]["text"] == ("leaf-101 eth1/10 → sw-core-01 (Eth1/10 → Eth1/20) → "
                                     "sw-acc-02 (Gi1/0/1 → Gi1/0/10)")  # fmt: skip
    assert "src" not in result["l2"]  # Quelle in keiner MAC-Tabelle


def _switches_vpc():
    """sw-core-02 als vPC-Peer von sw-core-01 (Peer-Link Po1 ueber Eth1/49-50), vPC 10 nur auf
    sw-core-01, vPC 20 nur auf sw-core-02, vPC 30 down; sw-core-02 meldet den Peer als getrennt.
    sw-acc-01 zusaetzlich mit Gi1/0/45 an sw-core-02; Spanning Tree blockiert Po1 in VLAN 30."""
    result = _switches()
    inv = result["inventory"]
    inv["devices"].append({"name": "sw-core-02", "role": "switch", "mgmt_ip": "10.0.0.3"})
    peer = {"vpc_role": "primary", "vpc_peer": "peer adjacency formed ok",
            "vpc_keepalive": "peer is alive", "vpc_peer_link_status": "up"}  # fmt: skip
    for p in inv["interfaces"]:
        if (p["device"], p["name"]) == ("sw-acc-01", "Po1"):
            p["attrs"] = {"stp_blocked": "30"}
    inv["interfaces"] += [
        tc._port("sw-core-01", "Po1", kind="lag", attrs={"vpc_peer_link": "100", **peer},
                 **tc.TRUNK),
        tc._port("sw-core-01", "Eth1/49", lag="Po1", **tc.TRUNK),
        tc._port("sw-core-01", "Eth1/50", lag="Po1", **tc.TRUNK),
        tc._port("sw-core-01", "Po30", kind="lag", attrs={"vpc": "30", "vpc_status": "down*",
                                                          "vpc_consistency": "failed"}),
        tc._port("sw-core-02", "Po1", kind="lag",
                 attrs={"vpc_peer_link": "100", **peer, "vpc_role": "secondary",
                        "vpc_peer": "peer link is down"}, **tc.TRUNK),
        tc._port("sw-core-02", "Eth1/49", lag="Po1", **tc.TRUNK),
        tc._port("sw-core-02", "Eth1/50", lag="Po1", **tc.TRUNK),
        tc._port("sw-core-02", "Eth1/45", **tc.TRUNK),
        tc._port("sw-core-02", "Po20", kind="lag", attrs={"vpc": "20", "vpc_status": "up",
                                                          "vpc_consistency": "success"}),
        tc._port("sw-acc-01", "Gi1/0/45", **tc.TRUNK),
        tc._port("sw-acc-02", "Gi1/0/2", trunk_vlans="10,20", mode="tagged", native_vlan=1),
        tc._port("sw-core-01", "Eth1/21", trunk_vlans="10,20", mode="tagged", native_vlan=1),
    ]  # fmt: skip
    for a, b in (("Eth1/49", "Eth1/49"), ("Eth1/50", "Eth1/50")):
        inv["neighbors"] += [
            {"device": "sw-core-01", "port": a, "protocol": "cdp", "remote_name": "sw-core-02",
             "remote_port": f"Ethernet{b[3:]}"},
            {"device": "sw-core-02", "port": b, "protocol": "cdp", "remote_name": "sw-core-01",
             "remote_port": f"Ethernet{a[3:]}"},
        ]  # fmt: skip
    inv["neighbors"] += [
        {"device": "sw-acc-02", "port": "Gi1/0/2", "protocol": "cdp",
         "remote_name": "sw-core-01", "remote_port": "Ethernet1/21"},
        {"device": "sw-acc-01", "port": "Gi1/0/45", "protocol": "cdp",
         "remote_name": "sw-core-02", "remote_port": "Ethernet1/45"},
        {"device": "sw-core-02", "port": "Eth1/45", "protocol": "cdp",
         "remote_name": "sw-acc-01", "remote_port": "GigabitEthernet1/0/45"},
    ]  # fmt: skip
    return result


def test_vpc_stp_mehrere_ausfaelle_und_export(client, session_factory):
    tid, sw = tc._setup(client)
    for name, platform, result in (("vc", "vmware", VCENTER), ("fw-08", "fortigate", FORTIGATE)):
        body = {"tenant_id": tid, "name": name, "platform": platform,
                "base_url": f"https://{name}.example.ch"}  # fmt: skip
        sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
        _deliver(client, tid, sid, platform, result)
    _deliver(client, tid, sw["id"], "cisco_switch", _switches_vpc())

    plan = client.get("/cabling/plan", params={"tenant_id": tid, "vlan": 30}).json()
    assert plan["pairs"] == [{"a": "sw-core-01", "b": "sw-core-02", "domain": "100"}]
    by = {n["name"]: n for n in plan["nodes"]}
    assert by["sw-core-01"]["layer"] == by["sw-core-02"]["layer"] == 0  # Paar auf einer Ebene
    assert abs(by["sw-core-01"]["x"] - by["sw-core-02"]["x"]) == l2_topo.X_STEP  # nebeneinander
    acc = next(e for e in plan["edges"] if {e["a"], e["b"]} == {"sw-acc-01", "sw-core-01"})
    assert acc["vlan"] == "standby" and acc["stp"] == "30"  # Po1 blockiert, Rest sperrt VLAN 30
    with session_factory() as s:
        topo = l2_topo.L2(s, tid)
        # VLAN 30 ab fw-08: nicht ueber den blockierten Po1, sondern ueber sw-core-02
        seg = topo.between(["fw-08"], "dmz", ["leaf-101"])
        assert seg is not None
        assert [st.device.name for st in seg.steps] == ["fw-08", "sw-acc-01", "sw-core-02",
                                                        "sw-core-01", "leaf-101"]  # fmt: skip
        # blockiert Spanning Tree jeden Weg (Laeufe zu verschiedenen Zeiten): dann mit den
        # blockierten Kabeln, statt keinen Weg zu zeigen
        topo.stp_blocked = lambda end: {30}
        seg = topo.between(["fw-08"], "dmz", ["leaf-101"])
        assert seg is not None
        assert [st.device.name for st in seg.steps] == ["fw-08", "sw-acc-01", "sw-core-01",
                                                        "leaf-101"]  # fmt: skip
    # Ports: Mitglieder zeigen Spanning Tree bzw. Peer-Link ihres Port-Channels
    ports = {(p["device"], p["name"]): p for p in client.get(
        "/cabling/ports", params={"tenant_id": tid, "limit": 2000}).json()["items"]}  # fmt: skip
    assert ports[("sw-acc-01", "Gi1/0/47")]["stp_blocked"] == "30"
    assert ports[("sw-core-02", "Eth1/49")]["vpc_peer_link"] == "100"
    assert ports[("sw-core-02", "Eth1/45")]["stp_blocked"] is None
    # Ausfall beider Peers: Zugang, Leaf und sw-acc-02 getrennt
    both = client.get("/cabling/failure", params=[("tenant_id", tid), ("device", "sw-core-01"),
                                                  ("device", "sw-core-02")]).json()  # fmt: skip
    assert both["devices"] == ["sw-core-01", "sw-core-02"] and len(both["parts"]) == 3
    one = client.get("/cabling/failure", params={"tenant_id": tid, "device": "sw-core-02"}).json()
    assert one["cut_off"] == []  # sw-acc-01 haengt auch an sw-core-01
    # Befunde zum vPC-Paar
    found = client.get("/findings", params={"tenant_id": tid, "type": "vpc_mismatch"}).json()
    items = found["items"] if isinstance(found, dict) else found
    texts = sorted(f["message"] for f in items)
    assert any("vPC-Domäne 100: sw-core-02 meldet „peer link is down“" in t for t in texts)
    assert any("vPC 10 nur auf sw-core-01 (Po10), nicht auf sw-core-02" in t for t in texts)
    assert any("vPC 20 nur auf sw-core-02 (Po20), nicht auf sw-core-01" in t for t in texts)
    assert any("vPC 30 auf sw-core-01 (Po30): down*, Konsistenz failed" in t for t in texts)
    peer = next(f for f in items if "meldet" in f["message"])
    assert peer["severity"] == "error"
    single = client.get("/findings", params={"tenant_id": tid, "type": "l2_single_switch"}).json()
    single = single["items"] if isinstance(single, dict) else single
    assert not any(f["detail"]["device"] == "sw-acc-01" for f in single)  # jetzt an beiden Peers
    acc2 = next(f for f in single if f["detail"]["device"] == "sw-acc-02")
    assert "alle 2 Kabel von sw-acc-02 enden an sw-core-01 – sw-core-02 (vPC-Peer) nicht genutzt" \
        in acc2["message"]  # fmt: skip
    # Export
    r = client.get("/cabling/plan/export", params={"tenant_id": tid, "format": "svg"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "vPC-Domäne 100" in r.text and "sw-core-02" in r.text
    assert 'filename="verkabelung-' in r.headers["content-disposition"]
    d = client.get("/cabling/plan/export", params={"tenant_id": tid, "format": "drawio",
                                                   "vlan": 30})  # fmt: skip
    assert d.status_code == 200 and "<mxGraphModel" in d.text
    assert 'automato_device="sw-acc-01"' in d.text and "STP blockiert 30" in d.text
    assert "-vlan30.drawio" in d.headers["content-disposition"]
    pdf = {"tenant_id": tid, "format": "pdf"}
    assert client.get("/cabling/plan/export", params=pdf).status_code == 422
