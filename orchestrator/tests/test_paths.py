# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Weg Hop fuer Hop (ADR 0019 Stufe 3, erste Fassung) und Bezeichnungen der Kontexte.

Topologie: fw-01 (VDOMs root und kunde-a) mit Servernetz, Transfernetz zur ACI (L3Out-SVI im
vPC), Transfernetz zu fw-02 (nur root = „die Firewall“) und Default-Route ueber zwei
Provider (ECMP). Im ACI ein EPG-Subnetz. Namen aus dem DDI: Hosts und Transfernetze.
"""

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _r(node, context, prefix, hops, protocol="static", **kw):
    return {"node": node, "context": context, "prefix": prefix, "protocol": protocol,
            "next_hops": hops, **kw}  # fmt: skip


def _if(device, name, ip, zone=None, vdom="root"):
    return {"device": device, "name": name, "ips": [ip], "zone": zone, "vdom": vdom}


FW1 = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "fw-01", "role": "firewall"}],
        "interfaces": [
            _if("fw-01", "srv", "10.20.0.1/24", "server"),
            _if("fw-01", "to-aci", "10.255.0.1/29", "aci"),
            _if("fw-01", "to-fw02", "10.70.0.1/30", "transit"),
            _if("fw-01", "wan1", "198.51.100.2/30", "internet"),
            _if("fw-01", "wan2", "198.51.100.6/30", "internet"),
            _if("fw-01", "vl-kunde", "10.255.1.2/30", vdom="kunde-a"),
        ],
    },
    "routing": {
        "contexts": [{"node": "fw-01", "context": "root"}, {"node": "fw-01", "context": "kunde-a"}],
        "routes": [
            _r("fw-01", "root", "10.20.0.0/24", [{"interface": "srv"}], "connected"),
            _r("fw-01", "root", "10.255.0.0/29", [{"interface": "to-aci"}], "connected"),
            _r("fw-01", "root", "10.70.0.0/30", [{"interface": "to-fw02"}], "connected"),
            _r("fw-01", "root", "10.50.0.0/16", [{"ip": "10.255.0.2", "interface": "to-aci"}],
               "bgp", distance=20),
            _r("fw-01", "root", "10.60.0.0/24", [{"ip": "10.70.0.2", "interface": "to-fw02"}]),
            _r("fw-01", "root", "0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan1"},
                                              {"ip": "198.51.100.5", "interface": "wan2"}],
               "bgp", distance=20),
            _r("fw-01", "kunde-a", "0.0.0.0/0", [{"ip": "10.255.1.1", "interface": "vl-kunde"}]),
        ],
        "peers": [
            {"node": "fw-01", "context": "root", "peer_ip": "198.51.100.1", "remote_as": 64500,
             "state": "Established"},
            {"node": "fw-01", "context": "root", "peer_ip": "10.255.0.2", "remote_as": 65010,
             "state": "Established"},
        ],
        "policy_routes": [
            {"node": "fw-01", "context": "root", "seq": 1, "gateway": "198.51.100.5",
             "output_device": ["wan2"],
             "match": {"src": ["10.20.0.0/24"], "dst": ["192.0.2.0/24"]}},
        ],
    },
}  # fmt: skip

FW2 = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "fw-02", "role": "firewall"}],
        "interfaces": [
            _if("fw-02", "to-fw01", "10.70.0.2/30", "transit"),
            _if("fw-02", "lan", "10.60.0.1/24", "lan"),
        ],
    },
    "routing": {
        "contexts": [{"node": "fw-02", "context": "root"}],
        "routes": [
            _r("fw-02", "root", "10.60.0.0/24", [{"interface": "lan"}], "connected"),
            _r("fw-02", "root", "10.70.0.0/30", [{"interface": "to-fw01"}], "connected"),
            _r("fw-02", "root", "10.20.0.0/16", [{"ip": "10.70.0.1", "interface": "to-fw01"}]),
        ],
    },
}  # fmt: skip

ACI_HOP = [{"ip": "10.255.0.1", "interface": "vlan-3001"}]
APIC = {
    "status": "completed",
    "inventory": {
        "devices": [
            {"name": "leaf-101", "role": "leaf", "node_id": "101"},
            {"name": "leaf-102", "role": "leaf", "node_id": "102"},
        ],
        "subnets": [
            {"prefix": "10.50.1.0/24", "gateway": "10.50.1.1/24", "vrf": "TE:VRF1",
             "context": "epg", "context_name": "EPG_web", "aci_tenant": "TE", "ap": "AP1",
             "epg": "EPG_web"},
        ],
    },
    "routing": {
        "contexts": [
            {"node": "leaf-101", "context": "TE:VRF1", "kind": "vrf"},
            {"node": "leaf-102", "context": "TE:VRF1", "kind": "vrf"},
        ],
        "routes": [
            _r("leaf-101", "TE:VRF1", "10.20.0.0/16", ACI_HOP, "bgp", distance=20),
            _r("leaf-102", "TE:VRF1", "10.20.0.0/16", ACI_HOP, "bgp", distance=20),
            # BD-Subnetz: pervasive Route zum Spine-Proxy - das Ziel liegt im Fabric
            _r("leaf-101", "TE:VRF1", "10.50.1.0/24", [{"ip": "10.0.72.64"}]),
        ],
        "l3outs": [
            {"tenant": "TE", "name": "L3O_fw", "dn": "uni/tn-TE/out-L3O_fw", "vrf": "TE:VRF1",
             "nodes": [{"pod": "1", "node": "101",
                        "static_routes": [{"prefix": "10.99.0.0/16",
                                           "next_hops": ["10.255.0.1"]}]}],
             "paths": [{"path": "topology/pod-1/protpaths-101-102/pathep-[vpc_fw]",
                        "nodes": ["101", "102"], "interface": "vpc_fw", "kind": "ext-svi",
                        "vlan": 3001,
                        "ips": [{"side": "A", "ip": "10.255.0.2/29"},
                                {"side": "B", "ip": "10.255.0.3/29"}]}],
             "peers": [{"ip": "10.255.0.1", "asn": 65000}], "ext_epgs": ["EN_any"]},
        ],
    },
}  # fmt: skip

DDI = [
    {"kind": "host", "value": "198.51.100.1", "dns_name": "isp-a-gw.example.ch"},
    {"kind": "prefix", "value": "198.51.100.4/30", "description": "Transfer ISP B"},
    {"kind": "prefix", "value": "10.255.0.0/29", "description": "Transfer ACI"},
    {"kind": "host", "value": "10.255.0.1", "dns_name": "fw-01-aci.example.ch"},
    {"kind": "host", "value": "10.20.0.5", "dns_name": "app01.example.ch"},
]


def _run(client, tid, sid, platform) -> str:
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    return client.post("/jobs/collect", json=body).json()["run_id"]


def _deliver(client, tid, sid, platform, result):
    run_id = _run(client, tid, sid, platform)
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _system(client, tid, name, platform, **extra):
    body = {"tenant_id": tid, "name": name, "platform": platform, "base_url": "https://x", **extra}
    r = client.post("/systems", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    for name, platform, result in (
        ("fw-01", "fortigate", FW1),
        ("fw-02", "fortigate", FW2),
        ("apic-01", "cisco_aci", APIC),
    ):
        _deliver(client, tid, _system(client, tid, name, platform), platform, result)
    ddi = _system(client, tid, "ddi", "infoblox", username="u", password="p")
    run_id = _run(client, tid, ddi, "infoblox")
    client.post(f"/internal/collector-runs/{run_id}/references", headers=TOKEN,
                json={"records": DDI})  # fmt: skip
    done = {"status": "completed", "reference_count": len(DDI)}
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=done)
    return tid


def _trace(client, tid, **params) -> dict:
    r = client.get("/routing/trace", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _labels(path) -> list[str]:
    return [h["label"] for h in path["hops"]]


def test_bezeichnungen_firewall_vdom_vrf(client):
    tid = _seed(client)
    summary = client.get("/routing/summary", params={"tenant_id": tid}).json()
    rows = {(c["source"], c["node"], c["context"]): (c["display"], c["label"])
            for c in summary["contexts"]}  # fmt: skip
    assert rows[("fw-01", "fw-01", "root")] == ("vdom", "fw-01 · VDOM root")
    assert rows[("fw-01", "fw-01", "kunde-a")] == ("vdom", "fw-01 · VDOM kunde-a")
    assert rows[("fw-02", "fw-02", "root")] == ("firewall", "fw-02")  # nur root: die Firewall
    assert rows[("apic-01", "leaf-101", "TE:VRF1")] == ("vrf", "leaf-101 · VRF TE:VRF1")
    nodes = {n["label"]: n for n in summary["nodes"]}
    assert sorted(nodes) == [
        "apic-01 · VRF TE:VRF1",  # alle Border-Leafs der VRF = ein Knoten
        "fw-01 · VDOM kunde-a",
        "fw-01 · VDOM root",
        "fw-02",
    ]
    assert nodes["apic-01 · VRF TE:VRF1"]["members"] == ["leaf-101", "leaf-102"]
    assert nodes["fw-02"]["kind"] == "firewall"

    # VDOMs je Quellsystem: die Oberflaeche zeigt „VDOM root“ nur bei Firewalls mit VDOMs
    systems = {x["name"]: x["vdoms"] for x in client.get(f"/systems?tenant_id={tid}").json()}
    assert (systems["fw-01"], systems["fw-02"], systems["apic-01"]) == (
        ["kunde-a", "root"],
        ["root"],
        [],
    )

    routes = client.get("/routing/routes", params={"tenant_id": tid, "ip": "10.20.0.5"}).json()
    assert {r["label"] for r in routes} >= {"fw-02", "fw-01 · VDOM root"}


def test_nachbarn_mit_namen_aus_dem_ipam(client):
    tid = _seed(client)
    p = {"tenant_id": tid}
    peers = {x["peer_ip"]: x for x in client.get("/routing/peers", params=p).json()}
    assert peers["198.51.100.1"]["peer"] == {"names": ["isp-a-gw.example.ch"]}
    assert peers["198.51.100.1"]["label"] == "fw-01 · VDOM root"
    # ohne Namen: das Netz aus dem IPAM; der Nachbar ist der gelesene L3Out
    assert peers["10.255.0.2"]["peer"] == {"network": "Transfer ACI (10.255.0.0/29)"}
    assert peers["10.255.0.2"]["peer_node"] == "apic-01 · VRF TE:VRF1"

    (l3,) = client.get("/routing/l3outs", params=p).json()
    assert l3["peers"] == [{"ip": "10.255.0.1", "asn": 65000, "names": ["fw-01-aci.example.ch"],
                            "node": "fw-01 · VDOM root"}]  # fmt: skip
    assert l3["nodes"][0]["name"] == "leaf-101"
    assert l3["nodes"][0]["static_routes"][0]["resolved"] == {
        "10.255.0.1": {"names": ["fw-01-aci.example.ch"]}
    }
    assert l3["paths"][0]["node_names"] == ["leaf-101", "leaf-102"]

    found = client.get("/routing/routes", params={**p, "ip": "203.0.113.9", "table": "rib"}).json()
    default = next(r for r in found if r["label"] == "fw-01 · VDOM root")
    assert [(h["ip"], h.get("names"), h.get("network")) for h in default["next_hops"]] == [
        ("198.51.100.1", ["isp-a-gw.example.ch"], None),
        ("198.51.100.5", None, "Transfer ISP B (198.51.100.4/30)"),
    ]


def test_weg_von_der_quelle_ins_fabric(client):
    tid = _seed(client)
    out = _trace(client, tid, src="10.20.0.5", dst="10.50.1.10")
    assert [(s["label"], s["via"]) for s in out["start"]] == [
        ("fw-01 · VDOM root", "Quelle angeschlossen")
    ]
    (path,) = out["paths"]
    assert _labels(path) == ["fw-01 · VDOM root", "apic-01 · VRF TE:VRF1"]
    fw, aci = path["hops"]
    assert fw["ingress"] == {"interface": "srv", "zone": "server"}
    assert fw["route"]["prefix"] == "10.50.0.0/16"
    assert fw["egress"] == {
        "interface": "to-aci",
        "zone": "aci",
        "next_hop": "10.255.0.2",
        "network": "Transfer ACI (10.255.0.0/29)",
    }
    assert (aci["via"], aci["ingress"]["interface"]) == ("Adresse des Nachbarn", "vpc_fw")
    assert path["end"] == {
        "kind": "fabric",
        "text": "Ziel im Fabric: EPG TE|AP1|EPG_web (10.50.1.0/24)",
    }

    # Gegenrichtung: Quelle im EPG-Subnetz, ueber den L3Out zur Firewall
    back = _trace(client, tid, src="10.50.1.10", dst="10.20.0.5")
    (path,) = back["paths"]
    assert back["start"][0]["via"] == "Quelle im Fabric: EPG TE|AP1|EPG_web (10.50.1.0/24)"
    assert _labels(path) == ["apic-01 · VRF TE:VRF1", "fw-01 · VDOM root"]
    assert path["hops"][0]["egress"]["names"] == ["fw-01-aci.example.ch"]
    assert path["hops"][1]["ingress"] == {"interface": "to-aci", "zone": "aci"}
    assert path["end"]["text"] == "Ziel angeschlossen an fw-01 · VDOM root (srv)"


def test_weg_ecmp_nach_extern_und_policy_route(client):
    tid = _seed(client)
    out = _trace(client, tid, src="10.20.0.5", dst="203.0.113.10")
    assert [(p["hops"][-1]["egress"]["interface"], p["end"]["kind"]) for p in out["paths"]] == [
        ("wan1", "external"),
        ("wan2", "external"),
    ]
    assert "isp-a-gw.example.ch" in out["paths"][0]["end"]["text"]
    assert "Transfer ISP B (198.51.100.4/30)" in out["paths"][1]["end"]["text"]

    # Policy-Route passt sicher (Quelle und Ziel als Subnetz) und geht der RIB vor
    pbr = _trace(client, tid, src="10.20.0.5", dst="192.0.2.10")
    (path,) = pbr["paths"]
    assert path["hops"][0]["policy"]["seq"] == 1
    assert path["hops"][0]["egress"]["interface"] == "wan2"
    assert path["end"]["kind"] == "unknown"
    # ohne Quelle nur „moeglich“: dann gilt die Default-Route
    start = f"{path['hops'][0]['source_system_id']}|root"
    loose = _trace(client, tid, start=start, dst="192.0.2.10")
    assert [p["end"]["kind"] for p in loose["paths"]] == ["external", "external"]
    assert loose["paths"][0]["hops"][0]["policy_routes"][0]["certain"] is False


def test_weg_ab_gewaehltem_startpunkt(client):
    tid = _seed(client)
    nodes = client.get("/routing/summary", params={"tenant_id": tid}).json()["nodes"]
    fw2 = next(n for n in nodes if n["label"] == "fw-02")
    out = _trace(client, tid, start=fw2["key"], dst="10.20.0.5")
    assert out["start"][0]["via"] == "gewählt"
    (path,) = out["paths"]
    assert _labels(path) == ["fw-02", "fw-01 · VDOM root"]
    assert path["hops"][0]["egress"]["interface"] == "to-fw01"
    assert path["hops"][1]["ingress"] == {"interface": "to-fw02", "zone": "transit"}
    assert path["end"]["kind"] == "connected"
    # mit Quelle: Eingang am Startpunkt aus dem angeschlossenen Netz
    with_src = _trace(client, tid, start=fw2["key"], src="10.60.0.7", dst="10.20.0.5")
    assert with_src["paths"][0]["hops"][0]["ingress"] == {"interface": "lan", "zone": "lan"}

    # VDOM ohne Route zum Ziel, unbekannter Startpunkt, ohne Quelle und Start
    kunde = next(n for n in nodes if n["label"] == "fw-01 · VDOM kunde-a")
    other = _trace(client, tid, start=kunde["key"], dst="10.20.0.5")
    assert other["paths"][0]["end"]["kind"] == "external"  # Default ohne gelesenen Nachbarn
    assert _trace(client, tid, start="999|root", dst="10.20.0.5")["notes"] == [
        "Startpunkt unbekannt – neu einlesen bzw. einen anderen wählen"
    ]
    lost = _trace(client, tid, src="172.16.0.1", dst="10.20.0.5")
    assert lost["paths"] == [] and "Kein gelesenes Gerät" in lost["notes"][0]
    p = {"tenant_id": tid, "dst": "10.20.0.5"}
    assert client.get("/routing/trace", params=p).status_code == 422
    assert client.get("/routing/trace", params={**p, "src": "x"}).status_code == 422


def _fw(name, routes, interfaces):
    return {
        "status": "completed",
        "inventory": {"devices": [{"name": name, "role": "firewall"}], "interfaces": interfaces},
        "routing": {"contexts": [{"node": name, "context": "root"}], "routes": routes},
    }


MGMT_ATTRS = {"vrf": 10, "mgmt": True}
FWA = _fw(
    "fw-a",
    [
        _r("fw-a", "root", "203.0.113.0/29", [{"interface": "wan"}], "connected"),
        _r("fw-a", "root", "0.0.0.0/0", [{"ip": "203.0.113.1", "interface": "wan"}]),
        _r("fw-a", "root", "10.99.0.0/24", [{"interface": "mgmt"}], "connected", attrs=MGMT_ATTRS),
        _r("fw-a", "root", "0.0.0.0/0", [{"ip": "10.99.0.1", "interface": "mgmt"}],
           attrs=MGMT_ATTRS),
        _r("fw-a", "root", "10.60.0.0/16", [{"ip": "10.80.0.1", "interface": "mpls"}]),
    ],
    [_if("fw-a", "wan", "203.0.113.2/29", "internet"), _if("fw-a", "mgmt", "10.99.0.2/24"),
     _if("fw-a", "mpls", "10.80.0.2/30", "wan")],
)  # fmt: skip
FWB = _fw(
    "fw-b",
    [
        _r("fw-b", "root", "203.0.113.0/29", [{"interface": "wan"}], "connected"),
        _r("fw-b", "root", "0.0.0.0/0", [{"ip": "203.0.113.1", "interface": "wan"}]),
        _r("fw-b", "root", "10.60.0.0/24", [{"interface": "lan"}], "connected"),
    ],
    [_if("fw-b", "wan", "203.0.113.3/29", "internet")],
)


def test_kein_raten_ueber_gemeinsames_netz_und_vrf_management(client):
    """Dev-Befund 0.17.0: zwei Firewalls am selben Aussenrouter ergaben Schleifen (fw-a ->
    „Transfernetz“ -> fw-b -> fw-a), die Management-Default-Route zaehlte als Weg."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    for name, result in (("fw-a", FWA), ("fw-b", FWB)):
        _deliver(client, tid, _system(client, tid, name, "fortigate"), "fortigate", result)
    nodes = {n["label"]: n["key"] for n in client.get(
        "/routing/summary", params={"tenant_id": tid}).json()["nodes"]}  # fmt: skip

    out = _trace(client, tid, start=nodes["fw-a"], dst="192.0.2.77")
    (path,) = out["paths"]  # nur ueber wan, nicht ueber mgmt, keine Schleife ueber fw-b
    assert _labels(path) == ["fw-a"]
    assert path["hops"][0]["egress"]["interface"] == "wan"
    assert path["end"]["kind"] == "external"

    # Quelle im Management-Netz: Weg in der Management-Tabelle
    mgmt = _trace(client, tid, src="10.99.0.50", dst="192.0.2.77")
    (path,) = mgmt["paths"]
    assert path["hops"][0]["vrf"] == "mgmt"
    assert path["hops"][0]["egress"]["next_hop"] == "10.99.0.1"

    rows = client.get("/routing/lookup", params={"tenant_id": tid, "ip": "192.0.2.77"}).json()
    fwa = [(r["vrf"], r["route"]["next_hops"][0]["ip"]) for r in rows if r["node"] == "fw-a"]
    assert fwa == [(None, "203.0.113.1"), ("mgmt", "10.99.0.1")]


def test_anbindungen_nach_aussen(client):
    """0.18.0: Anbindungen von Hand - der Weg endet am Internet bzw. geht ueber MPLS nach der
    manuellen Route bei der Firewall des anderen Standorts weiter."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    ids = {}
    for name, result in (("fw-a", FWA), ("fw-b", FWB)):
        ids[name] = _system(client, tid, name, "fortigate")
        _deliver(client, tid, ids[name], "fortigate", result)
    operator = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    inet = {"tenant_id": tid, "name": "Internet", "kind": "internet", "next_hops": ["203.0.113.1"]}
    assert client.post("/routing/exits", json=inet, headers=viewer).status_code == 403
    assert client.post("/routing/exits", json={**inet, "next_hops": []},
                       headers=operator).status_code == 422  # fmt: skip
    created = client.post("/routing/exits", json=inet, headers=operator)
    assert created.status_code == 201, created.text
    mpls = {"tenant_id": tid, "name": "Kunde A", "kind": "mpls",
            "source_system_id": ids["fw-a"], "interfaces": ["mpls"],
            "prefixes": ["10.60.0.0/16"],
            "routes": [{"prefix": "10.60.0.0/16", "target": f"{ids['fw-b']}|root"}]}  # fmt: skip
    assert client.post("/routing/exits", json={**mpls, "routes": [{"prefix": "10.60.0.0/16",
                       "target": "fw-b"}]}, headers=operator).status_code == 422  # fmt: skip
    mpls_id = client.post("/routing/exits", json=mpls, headers=operator).json()["id"]
    rows = client.get("/routing/exits", params={"tenant_id": tid}).json()
    assert [(x["name"], x["source"]) for x in rows] == [("Internet", None), ("Kunde A", "fw-a")]

    start = f"{ids['fw-a']}|root"
    (path,) = _trace(client, tid, start=start, dst="192.0.2.77")["paths"]
    assert path["end"] == {"kind": "exit", "text": "weiter über Internet"}
    assert path["hops"][0]["egress"]["exit"]["kind"] == "internet"

    (path,) = _trace(client, tid, start=start, dst="10.60.0.5")["paths"]
    assert _labels(path) == ["fw-a", "fw-b"]
    assert path["hops"][1]["via"] == "über MPLS Kunde A (manuelle Route)"
    assert path["end"]["kind"] == "connected"

    # ohne manuelle Route endet der Weg an der Anbindung, mit Hinweis auf die Netze
    body = {**mpls, "routes": [], "prefixes": ["10.70.0.0/16"]}
    assert client.put(f"/routing/exits/{mpls_id}", json=body, headers=operator).status_code == 200
    (path,) = _trace(client, tid, start=start, dst="10.60.0.5")["paths"]
    assert path["end"]["text"] == (
        "weiter über MPLS Kunde A – Ziel liegt nicht in den Netzen der Anbindung"
    )
    assert client.delete(f"/routing/exits/{mpls_id}", headers=operator).status_code == 204
    (path,) = _trace(client, tid, start=start, dst="10.60.0.5")["paths"]
    assert path["end"]["kind"] == "unknown"
    # Quellsystem geloescht: seine Anbindungen mit
    client.post("/routing/exits", json=mpls, headers=operator)
    assert client.delete(f"/systems/{ids['fw-a']}", headers=ADMIN).status_code in (200, 204)
    assert [x["name"] for x in client.get("/routing/exits", params={"tenant_id": tid}).json()] == [
        "Internet"
    ]
