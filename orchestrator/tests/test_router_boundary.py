# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Routing bis an die Firmengrenze (ADR 0032): gelesene Router und L3-Switches als Knoten im Weg
und in der Topologie, die Firmengrenze am Ausgang (eBGP zu fremdem AS, Anbindung, Default-Route
Richtung Provider), Kunde oben und Kunden-Benutzer, Verlauf.

Umgebung: fw-01 (nur root) mit Servernetz 10.20.0.0/24 und Default-Route zum Core-Router
rt-core-01; der Core leitet ins Internet ueber den Aussenrouter rt-edge-01 (beide in der Quelle
„Router Zürich“, Switches & Router). rt-edge-01 hat eBGP zu ISP A (AS 64500, aktiv) und ISP B
(AS 64511, Backup) und eine Route ins Partnernetz 172.16.0.0/16 ueber einen nicht gelesenen
Router ohne BGP.
"""

import copy

from test_paths import ADMIN, _deliver, _if, _r, _system

FW = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "fw-01", "role": "firewall"}],
        "interfaces": [_if("fw-01", "srv", "10.20.0.1/24", "server"),
                       _if("fw-01", "to-core", "10.0.0.2/30", "transit")],
    },
    "routing": {
        "contexts": [{"node": "fw-01", "context": "root"}],
        "routes": [
            _r("fw-01", "root", "10.20.0.0/24", [{"interface": "srv"}], "connected"),
            _r("fw-01", "root", "10.0.0.0/30", [{"interface": "to-core"}], "connected"),
            _r("fw-01", "root", "0.0.0.0/0", [{"ip": "10.0.0.1", "interface": "to-core"}]),
        ],
    },
}  # fmt: skip


def _l3(node, prefix, hops, protocol="static", **kw):
    return _r(node, node, prefix, hops, protocol, **kw)


def _local(node, ip, iface):
    return _l3(node, f"{ip}/32", [{"interface": iface}], "local", distance=0)


def _peer(node, ip, remote_as, state="Established", kind="ebgp", local_as=65001):
    return {"node": node, "context": node, "peer_ip": ip, "remote_as": remote_as, "state": state,
            "attrs": {"local_as": local_as, "type": kind}}  # fmt: skip


ROUTERS = {
    "status": "completed",
    "inventory": {"devices": [{"name": "rt-core-01", "role": "switch"},
                              {"name": "rt-edge-01", "role": "switch"}]},
    "routing": {
        "contexts": [{"node": "rt-core-01", "context": "rt-core-01", "kind": "l3"},
                     {"node": "rt-edge-01", "context": "rt-edge-01", "kind": "l3"}],
        "routes": [
            _l3("rt-core-01", "10.0.0.0/30", [{"interface": "Gi0/1"}], "connected"),
            _local("rt-core-01", "10.0.0.1", "Gi0/1"),
            _l3("rt-core-01", "10.0.1.0/30", [{"interface": "Gi0/2"}], "connected"),
            _local("rt-core-01", "10.0.1.1", "Gi0/2"),
            _l3("rt-core-01", "10.20.0.0/16", [{"ip": "10.0.0.2", "interface": "Gi0/1"}]),
            _l3("rt-core-01", "0.0.0.0/0", [{"ip": "10.0.1.2", "interface": "Gi0/2"}],
                distance=1),
            _l3("rt-edge-01", "10.0.1.0/30", [{"interface": "Gi0/1"}], "connected"),
            _local("rt-edge-01", "10.0.1.2", "Gi0/1"),
            _l3("rt-edge-01", "198.51.100.0/30", [{"interface": "Gi0/0"}], "connected"),
            _local("rt-edge-01", "198.51.100.2", "Gi0/0"),
            _l3("rt-edge-01", "198.51.100.8/30", [{"interface": "Gi0/3"}], "connected"),
            _local("rt-edge-01", "198.51.100.10", "Gi0/3"),
            _l3("rt-edge-01", "0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "Gi0/0"}],
                "bgp", distance=20),
            _l3("rt-edge-01", "0.0.0.0/0", [{"ip": "198.51.100.1"}], "bgp", table="bgp",
                best=True, attrs={"as_path": "64500"}),
            _l3("rt-edge-01", "0.0.0.0/0", [{"ip": "198.51.100.9"}], "bgp", table="bgp",
                best=False, attrs={"as_path": "64511 64500"}),
            _l3("rt-edge-01", "10.20.0.0/16", [{"ip": "10.0.1.1", "interface": "Gi0/1"}]),
            _l3("rt-edge-01", "172.16.0.0/16", [{"ip": "10.0.1.250", "interface": "Gi0/1"}]),
        ],
        "peers": [
            _peer("rt-edge-01", "198.51.100.1", 64500),
            _peer("rt-edge-01", "198.51.100.9", 64511),
            _peer("rt-edge-01", "10.0.1.1", 65001, kind="ibgp"),
            _peer("rt-core-01", "10.0.1.2", 65001, kind="ibgp"),
        ],
    },
}  # fmt: skip


def _routers(client, tid, result=ROUTERS) -> int:
    body = {"tenant_id": tid, "name": "Router Zürich", "platform": "cisco_switch",
            "read_scope": "10.255.0.1\n10.255.0.2", "username": "ro", "password": "x"}  # fmt: skip
    r = client.post("/systems", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    _deliver(client, tid, r.json()["id"], "cisco_switch", result)
    return r.json()["id"]


def _seed(client, routers=ROUTERS) -> tuple[int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    _deliver(client, tid, _system(client, tid, "fw-01", "fortigate"), "fortigate", FW)
    return tid, _routers(client, tid, routers)


def _trace(client, tid, **params) -> dict:
    r = client.get("/routing/trace", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_weg_durch_gelesene_router_bis_an_die_firmengrenze(client):
    tid, rid = _seed(client)
    out = _trace(client, tid, src="10.20.0.5", dst="203.0.113.50")
    active = [p for p in out["paths"] if p["kind"] == "active"]
    (path,) = active
    assert [(h["label"], h["kind"]) for h in path["hops"]] == [
        ("fw-01", "firewall"), ("rt-core-01", "l3"), ("rt-edge-01", "l3")]  # fmt: skip
    fw, core, edge = path["hops"]
    # Next-Hop-Besitzer ueber die eigenen Adressen der Router (local-Routen)
    assert (core["via"], core["ingress"]["interface"]) == ("Adresse des Nachbarn", "Gi0/1")
    assert (edge["ingress"]["interface"], edge["members"]) == ("Gi0/1", ["rt-edge-01"])
    assert path["end"]["kind"] == "external"
    # Firmengrenze: eBGP zum ISP mit fremdem AS am letzten Hop
    assert path["boundary"] == {
        "key": f"{rid}|rt-edge-01", "label": "rt-edge-01", "interface": "Gi0/0",
        "next_hop": "198.51.100.1", "reason": "ebgp", "as": 64500, "state": "Established",
        "text": "Firmengrenze: eBGP zu AS 64500 (198.51.100.1)",
    }  # fmt: skip
    assert edge["boundary"] == "ebgp" and "boundary" not in core
    # Backup-Weg ueber ISP B: ebenfalls an der Grenze, mit dessen AS (Interface aus dem Netz)
    (backup,) = (p for p in out["paths"] if p["kind"] == "backup")
    assert (backup["boundary"]["as"], backup["boundary"]["interface"]) == (64511, "Gi0/3")
    # Partnernetz ueber einen nicht gelesenen Router ohne BGP: Ende ohne Grenze
    (partner,) = _trace(client, tid, src="10.20.0.5", dst="172.16.1.1")["paths"]
    assert partner["end"]["kind"] == "unknown" and "boundary" not in partner
    assert "gehört keinem gelesenen Gerät" in partner["end"]["text"]
    # Startpunkt am Router; Rueckweg vom ISP-Netz zur Firewall
    back = _trace(client, tid, start=f"{rid}|rt-edge-01", dst="10.20.0.5")
    assert [h["label"] for h in back["paths"][0]["hops"]] == ["rt-edge-01", "rt-core-01", "fw-01"]
    assert back["paths"][0]["end"]["kind"] == "connected"
    # Englisch: Text der Grenze vom Server
    en = client.get("/routing/trace", params={"tenant_id": tid, "src": "10.20.0.5",
                                              "dst": "203.0.113.50"},
                    headers={"Accept-Language": "en"}).json()  # fmt: skip
    assert en["paths"][0]["boundary"]["text"] == "Company boundary: eBGP to AS 64500 (198.51.100.1)"


def test_grenze_default_route_und_anbindung(client):
    routers = copy.deepcopy(ROUTERS)
    routing = routers["routing"]
    # ohne BGP: statische Default-Route zum Provider (oeffentliche Adresse)
    routing["peers"] = [p for p in routing["peers"] if p["attrs"]["type"] == "ibgp"]
    routing["routes"] = [
        r
        for r in routing["routes"]
        if not (r["node"] == "rt-edge-01" and r["prefix"] == "0.0.0.0/0")
    ]
    routing["routes"].append(
        _l3("rt-edge-01", "0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "Gi0/0"}], distance=1)
    )
    tid, rid = _seed(client, routers)
    (path,) = _trace(client, tid, src="10.20.0.5", dst="203.0.113.50")["paths"]
    assert path["boundary"]["reason"] == "default" and "as" not in path["boundary"]
    assert path["boundary"]["text"] == (
        "Firmengrenze: Default-Route Richtung Provider über 198.51.100.1"
    )
    # Anbindung von Hand (MPLS) am nicht gelesenen Partner-Router: Grenze „Anbindung“
    body = {"tenant_id": tid, "name": "MPLS Partner", "kind": "mpls",
            "next_hops": ["10.0.1.250"], "prefixes": ["172.16.0.0/16"]}  # fmt: skip
    assert client.post("/routing/exits", json=body, headers=ADMIN).status_code in (200, 201)
    (mpls,) = _trace(client, tid, src="10.20.0.5", dst="172.16.1.1")["paths"]
    assert mpls["end"]["kind"] == "exit"
    assert (mpls["boundary"]["reason"], mpls["boundary"]["exit"]["kind"]) == ("exit", "mpls")
    assert mpls["boundary"]["text"] == "Firmengrenze: MPLS Partner"
    # Anbindung „WAN“ fuehrt nicht aus der Firma
    exit_id = next(e["id"] for e in client.get("/routing/exits", params={"tenant_id": tid}).json())
    client.put(f"/routing/exits/{exit_id}", json={**body, "kind": "wan"}, headers=ADMIN)
    (wan,) = _trace(client, tid, src="10.20.0.5", dst="172.16.1.1")["paths"]
    assert wan["end"]["kind"] == "exit" and "boundary" not in wan


def test_topologie_router_und_grenzknoten(client):
    tid, rid = _seed(client)
    model = client.get("/topology", params={"tenant_id": tid}).json()
    nodes = {n["id"]: n for n in model["nodes"]}
    core, edge = nodes[f"{rid}|rt-core-01"], nodes[f"{rid}|rt-edge-01"]
    assert (core["kind"], core["label"], edge["kind"]) == ("l3", "rt-core-01", "l3")
    edges = {(e["kind"], frozenset((e["a"], e["b"]))): e for e in model["edges"]}
    fw = next(n["id"] for n in model["nodes"] if n["label"] == "fw-01")
    # Transfernetze aus den Interface-Adressen bzw. den eigenen Adressen der Router
    assert edges[("transfer", frozenset((fw, core["id"])))]["network"] == "10.0.0.0/30"
    assert edges[("transfer", frozenset((core["id"], edge["id"])))]["network"] == "10.0.1.0/30"
    assert ("bgp", frozenset((core["id"], edge["id"]))) in edges
    # gelesene Router sind keine Gegenstelle mehr; aussen nur die wirklich nicht gelesenen
    assert "router:10.0.0.1" not in nodes and "router:10.0.1.2" not in nodes
    isp = nodes["router:198.51.100.1"]
    assert isp["boundary"] == {"reasons": ["ebgp"], "as": [64500]}
    assert nodes["router:198.51.100.9"]["boundary"]["as"] == [64511]
    assert "boundary" not in nodes["router:10.0.1.250"]  # Partner ohne BGP
    assert edge.get("at_boundary") is True and not core.get("at_boundary")
    up = edges[("uplink", frozenset((edge["id"], "router:198.51.100.1")))]
    assert up["boundary"] == {"reason": "ebgp", "as": 64500}
    assert model["summary"]["boundary"] == {"outside": 2, "inside": 1}
    assert model["summary"]["nodes"]["l3"] == 2
    # erkannte Anbindungen: der Default-Next-Hop der Firewall gehoert jetzt einem Router
    keys = {ip for s in client.get("/routing/exits/suggestions", params={"tenant_id": tid}).json()
            for ip in (r["ip"] for r in s["next_hops"])}  # fmt: skip
    assert "10.0.0.1" not in keys and {"198.51.100.1", "198.51.100.9"} <= keys
    # Routing-Uebersicht: Bezeichnung und Art der Router-Kontexte
    summary = client.get("/routing/summary", params={"tenant_id": tid}).json()
    rows = {c["context"]: (c["display"], c["label"], c["kind"]) for c in summary["contexts"]}
    assert rows["rt-edge-01"] == ("l3", "rt-edge-01", "l3")
    assert {n["label"] for n in summary["nodes"] if n["kind"] == "l3"} == {"rt-core-01",
                                                                         "rt-edge-01"}  # fmt: skip


def test_router_vrf_bezeichnung(client):
    routers = copy.deepcopy(ROUTERS)
    routers["routing"]["contexts"].append(
        {"node": "rt-edge-01", "context": "rt-edge-01:CUST-A", "kind": "l3"}
    )
    routers["routing"]["routes"].append(_r("rt-edge-01", "rt-edge-01:CUST-A", "10.50.0.0/24",
                                           [{"interface": "Gi0/4"}], "connected"))  # fmt: skip
    tid, rid = _seed(client, routers)
    labels = {
        n["key"]: n["label"]
        for n in client.get("/routing/summary", params={"tenant_id": tid}).json()["nodes"]
    }
    assert labels[f"{rid}|rt-edge-01:CUST-A"] == "rt-edge-01 · VRF CUST-A"
    (path,) = _trace(client, tid, start=f"{rid}|rt-edge-01:CUST-A", dst="10.50.0.9")["paths"]
    assert path["end"]["kind"] == "connected"


def test_kunde_oben_und_kunden_benutzer(client):
    routers = copy.deepcopy(ROUTERS)
    routers["routing"]["contexts"].append(
        {"node": "rt-edge-01", "context": "rt-edge-01:CUST-B", "kind": "l3"}
    )
    routers["routing"]["routes"] += [
        _r("rt-edge-01", "rt-edge-01:CUST-B", "10.99.0.0/24", [{"interface": "Gi0/5"}],
           "connected"),
        _r("rt-edge-01", "rt-edge-01:CUST-B", "0.0.0.0/0", [{"ip": "10.99.0.254"}]),
    ]  # fmt: skip
    tid, rid = _seed(client, routers)
    body = {"tenant_id": tid, "name": "Kunde A", "code": "KA",
            "selectors": [{"kind": "prefix", "value": "10.20.0.0/24"}]}  # fmt: skip
    r = client.post("/customers", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    summary = client.get("/routing/summary", params={"tenant_id": tid, "customer_id": cid}).json()
    marks = {c["context"]: c.get("shared") for c in summary["contexts"]}
    # Router, die ein Netz des Kunden routen: geteilt; die VRF nur mit anderem Netz und der
    # Default-Route gehoert nicht zum Ausschnitt
    assert marks["rt-core-01"] is True and marks["rt-edge-01"] is True
    assert "rt-edge-01:CUST-B" not in marks
    routes = client.get("/routing/routes", params={"tenant_id": tid, "customer_id": cid}).json()
    edge = {r["prefix"] for r in routes if r["context"] == "rt-edge-01"}
    assert "10.20.0.0/16" in edge and "172.16.0.0/16" not in edge  # nur Netze des Kunden
    model = client.get("/topology", params={"tenant_id": tid, "customer_id": cid}).json()
    assert {n["label"] for n in model["nodes"] if n["kind"] == "l3"} == {"rt-core-01",
                                                                       "rt-edge-01"}  # fmt: skip
    # Kunden-Benutzer: keine Routing-Endpunkte (Freigabeliste)
    user = {"X-Dev-Roles": "customer", "X-Dev-Customers": str(cid)}
    for path, params in (("/routing/trace", {"src": "10.20.0.5", "dst": "203.0.113.50"}),
                         ("/routing/summary", {}), ("/topology", {})):  # fmt: skip
        r = client.get(path, params={"tenant_id": tid, **params}, headers=user)
        assert r.status_code == 403, (path, r.text)


def test_verlauf_mit_router_kontexten(client):
    tid, rid = _seed(client)
    changed = copy.deepcopy(ROUTERS)
    for r in changed["routing"]["routes"]:
        if r["node"] == "rt-edge-01" and r["prefix"] == "172.16.0.0/16":
            r["next_hops"] = [{"ip": "10.0.1.251", "interface": "Gi0/1"}]
    _deliver(client, tid, rid, "cisco_switch", changed)
    items = client.get("/routing/changes", params={"tenant_id": tid,
                                                   "source_system_id": rid}).json()  # fmt: skip
    hit = [c for c in items if c.get("prefix") == "172.16.0.0/16" and c["change"] == "changed"]
    assert hit and hit[0]["context"] == "rt-edge-01"
    # Switches ohne L3-Geraet: leerer Routing-Stand ohne Ausgangsstand im Verlauf
    body = {"tenant_id": tid, "name": "Access", "platform": "cisco_switch",
            "read_scope": "10.255.1.1", "username": "ro", "password": "x"}  # fmt: skip
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    _deliver(client, tid, sid, "cisco_switch",
             {"status": "completed", "inventory": {"devices": [{"name": "sw-acc-01",
                                                               "role": "switch"}]},
              "routing": {}})  # fmt: skip
    empty = client.get("/routing/changes", params={"tenant_id": tid,
                                                   "source_system_id": sid}).json()  # fmt: skip
    assert empty == []


def test_hsrp_adresse_beider_router(client):
    """Gateway-Adresse (HSRP/VRRP bzw. VARP, `attrs.virtual`) auf beiden Routern: der Weg geht
    zu beiden, die Topologie meldet keine doppelte IP."""
    fw, routers = copy.deepcopy(FW), copy.deepcopy(ROUTERS)
    fw["inventory"]["interfaces"].append(_if("fw-01", "to-hsrp", "10.0.2.6/29", "transit"))
    fw["routing"]["routes"] += [
        _r("fw-01", "root", "10.0.2.0/29", [{"interface": "to-hsrp"}], "connected"),
        _r("fw-01", "root", "10.30.0.0/16", [{"ip": "10.0.2.1", "interface": "to-hsrp"}]),
    ]  # fmt: skip
    for node, iface, own in (("rt-core-01", "Vl30", "10.0.2.2"), ("rt-edge-01", "Vl30",
                                                                  "10.0.2.3")):  # fmt: skip
        routers["routing"]["routes"] += [
            _l3(node, "10.0.2.0/29", [{"interface": iface}], "connected"),
            _local(node, own, iface),
            _l3(node, "10.0.2.1/32", [{"interface": iface}], "local", distance=0,
                attrs={"virtual": True}),
        ]  # fmt: skip
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    _deliver(client, tid, _system(client, tid, "fw-01", "fortigate"), "fortigate", fw)
    _routers(client, tid, routers)
    out = _trace(client, tid, src="10.20.0.5", dst="10.30.0.9")
    routes = {tuple(h["label"] for h in p["hops"]) for p in out["paths"] if p["kind"] == "active"}
    assert routes == {("fw-01", "rt-core-01", "rt-edge-01"), ("fw-01", "rt-edge-01")}
    assert all(p["boundary"]["reason"] == "ebgp" for p in out["paths"])
    issues = client.get("/topology", params={"tenant_id": tid}).json()["issues"]
    assert not [i for i in issues if i["type"] == "topo_duplicate_ip"]


def test_kunden_doku_ohne_routernamen(session_factory, client):
    from app.store.customer_alias import Aliases

    tid, _rid = _seed(client)
    with session_factory() as session:
        aliases = Aliases(session, [tid])
        assert aliases.text("rt-edge-01 · VRF CUST-A") == "Router · VRF CUST-A"
        assert aliases.text(f"{_rid}|rt-core-01") == f"{_rid}|Router"
