# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Topologie-Modell (0.50.0, ADR 0019): Knoten, Kanten mit Herkunft und Plausibilitaet.

Grundlage ist die Topologie der Weg-Suche (test_paths): fw-01 (VDOMs root und kunde-a) mit
Transfernetz zur ACI (L3Out-SVI im vPC) und zu fw-02, Default-Route ueber zwei Provider.
"""

import copy

from test_paths import ADMIN, APIC, FW1, FW2, _deliver, _r, _seed, _system


def _model(client, tid, **params) -> dict:
    r = client.get("/topology", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_knoten_und_kanten(client):
    tid = _seed(client)
    model = _model(client, tid)
    nodes = {n["id"]: n for n in model["nodes"]}
    by_label = {n["label"]: n for n in model["nodes"]}
    assert {"fw-01 · VDOM root", "fw-01 · VDOM kunde-a", "fw-02", "apic-01 · VRF TE:VRF1"} <= set(
        by_label
    )
    assert by_label["apic-01 · VRF TE:VRF1"]["members"] == ["leaf-101", "leaf-102"]
    root, fw2 = by_label["fw-01 · VDOM root"]["id"], by_label["fw-02"]["id"]
    aci = by_label["apic-01 · VRF TE:VRF1"]["id"]
    edges = {(e["kind"], frozenset((e["a"], e["b"]))): e for e in model["edges"]}

    # Transfernetze: gemeinsames Netz (Interface-IP bzw. L3Out-SVI), mit den Routen darueber
    to_fw2 = edges[("transfer", frozenset((root, fw2)))]
    assert to_fw2["network"] == "10.70.0.0/30" and to_fw2["origin"] == "derived"
    assert {v["address"] for v in to_fw2["via"]} == {"10.70.0.1/30", "10.70.0.2/30"}
    way = "a>b" if to_fw2["a"] == root else "b>a"
    assert to_fw2["routes"]["active"][way] == 1  # 10.60.0.0/24 ueber fw-02
    to_aci = edges[("transfer", frozenset((root, aci)))]
    assert to_aci["network"] == "10.255.0.0/29"
    assert sum(to_aci["routes"]["active"].values()) >= 2  # BGP hin, Routen der Leafs zurueck
    # BGP zwischen zwei gelesenen Knoten
    bgp = edges[("bgp", frozenset((root, aci)))]
    assert bgp["sessions"][0]["remote_as"] == 65010

    # Provider: Router ohne gelesenes Geraet, aktiv mit Default-Route (ECMP), AS und Name
    up = [e for e in model["edges"] if e["kind"] == "uplink" and e["a"] == root]
    routers = {r["ip"]: r for e in up for r in e["routers"]}
    assert routers["198.51.100.1"]["status"] == "active" and routers["198.51.100.1"]["default"]
    isp = nodes["router:198.51.100.1"]
    assert isp["kind"] == "router" and isp["remote_as"] == 64500
    assert isp["label"] == "isp-a-gw.example.ch"
    # ACI: Fabric-Adressen (Spine-Proxy) sind keine Router
    assert "router:10.0.72.64" not in nodes
    assert model["summary"]["nodes"]["router"] == 3  # zwei Provider + Gegenstelle kunde-a
    assert model["issues"] == []

    # ein Knoten mit seinen Nachbarn
    near = _model(client, tid, node=fw2)
    assert {n["id"] for n in near["nodes"]} == {fw2, root}
    assert client.get("/topology", params={"tenant_id": tid, "node": "9|x"}).status_code == 404


def test_anbindung_von_hand_und_verbund(client):
    tid = _seed(client)
    systems = {s["name"]: s["id"] for s in client.get(f"/systems?tenant_id={tid}").json()}
    body = {"tenant_id": tid, "name": "Internet", "kind": "internet",
            "next_hops": ["198.51.100.1", "198.51.100.5"], "prefixes": ["0.0.0.0/0"]}  # fmt: skip
    assert client.post("/routing/exits", json=body, headers=ADMIN).status_code in (200, 201)
    model = _model(client, tid)
    (exit_node,) = (n for n in model["nodes"] if n["kind"] == "exit")
    assert exit_node["label"] == "Internet" and exit_node["origin"] == "manual"
    up = [e for e in model["edges"] if e["kind"] == "uplink" and e["b"] == exit_node["id"]]
    assert {r["ip"] for e in up for r in e["routers"]} == {"198.51.100.1", "198.51.100.5"}
    assert "router:198.51.100.1" not in {n["id"] for n in model["nodes"]}

    group = {"tenant_id": tid, "group": "DC", "system_ids": [systems["fw-01"], systems["fw-02"]]}
    assert client.post("/systems/parallel-group", json=group, headers=ADMIN).status_code == 200
    par = [e for e in _model(client, tid)["edges"] if e["kind"] == "parallel"]
    assert par and par[0]["origin"] == "confirmed"


def test_plausibilitaet(client):
    """Doppelte IP und abweichende Maske im selben Netz, Schleife, statische Route ohne
    Wirkung, BGP-Sitzung nicht aufgebaut - als Befunde nach dem Lauf."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    fw1, fw2 = copy.deepcopy(FW1), copy.deepcopy(FW2)
    # fw-02: Maske /29 statt /30 auf dem Transfernetz; zusaetzlich die IP von fw-01 im LAN
    fw2["inventory"]["interfaces"][0]["ips"] = ["10.70.0.2/29"]
    fw2["inventory"]["interfaces"].append(
        {"device": "fw-02", "name": "dup", "ips": ["10.20.0.1/24"], "vdom": "root"}
    )
    # Schleife: fw-02 leitet 10.60.0.0/24 nicht mehr selbst aus, sondern zurueck zu fw-01
    fw2["routing"]["routes"][0] = _r("fw-02", "root", "10.60.0.0/24",
                                     [{"ip": "10.70.0.1", "interface": "to-fw01"}])  # fmt: skip
    # statische Route ohne Wirkung und ein BGP-Nachbar, der nicht aufgebaut ist
    fw1["routing"]["routes"].append(_r("fw-01", "root", "192.0.2.0/24",
                                       [{"ip": "10.20.0.254", "interface": "srv"}],
                                       table="static"))  # fmt: skip
    fw1["routing"]["peers"][0]["state"] = "Active"
    for name, platform, result in (("fw-01", "fortigate", fw1), ("fw-02", "fortigate", fw2),
                                   ("apic-01", "cisco_aci", APIC)):  # fmt: skip
        _deliver(client, tid, _system(client, tid, name, platform), platform, result)

    found = {}
    for f in client.get("/findings", params={"tenant_id": tid, "page_size": 200}).json():
        if f["type"].startswith("topo_"):
            found.setdefault(f["type"], []).append(f)
    assert set(found) == {"topo_duplicate_ip", "topo_mask_mismatch", "topo_route_loop",
                          "topo_static_unused", "topo_bgp_down"}  # fmt: skip
    (dup,) = found["topo_duplicate_ip"]
    assert dup["subject_id"] == "10.20.0.1" and "mehrfach vergeben" in dup["message"]
    (mask,) = found["topo_mask_mismatch"]
    assert mask["subject_id"] == "10.70.0.0/29"
    (loop,) = found["topo_route_loop"]
    assert loop["severity"] == "error" and "10.60.0.0/24" in loop["message"]
    (static,) = found["topo_static_unused"]
    assert static["detail"]["prefix"] == "192.0.2.0/24" and static["subject_kind"] == "node"
    (bgp,) = found["topo_bgp_down"]
    assert bgp["detail"]["peer"] == "198.51.100.1" and "Active" in bgp["message"]
    # dieselben Befunde im Modell, je Knoten gezaehlt
    model = _model(client, tid)
    assert model["summary"]["issues"]["topo_route_loop"] == 1
    fw1_root = next(n for n in model["nodes"] if n["label"] == "fw-01 · VDOM root")
    assert fw1_root["issues"] >= 3


def test_nicht_fuer_kunden_benutzer(client):
    tid = _seed(client)
    headers = {"X-Dev-Roles": "customer", "X-Dev-Customers": "1"}
    assert client.get("/topology", params={"tenant_id": tid}, headers=headers).status_code == 403
