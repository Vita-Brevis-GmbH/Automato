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
            "source_system_ids": [ids["fw-a"]], "interfaces": ["mpls"],
            "prefixes": ["10.60.0.0/16"],
            "routes": [{"prefix": "10.60.0.0/16", "target": f"{ids['fw-b']}|root"}]}  # fmt: skip
    assert client.post("/routing/exits", json={**mpls, "routes": [{"prefix": "10.60.0.0/16",
                       "target": "fw-b"}]}, headers=operator).status_code == 422  # fmt: skip
    mpls_id = client.post("/routing/exits", json=mpls, headers=operator).json()["id"]
    rows = client.get("/routing/exits", params={"tenant_id": tid}).json()
    assert [(x["name"], x["sources"]) for x in rows] == [("Internet", []), ("Kunde A", ["fw-a"])]
    assert client.post("/routing/exits", json={**mpls, "source_system_ids": [999]},
                       headers=operator).status_code == 422  # fmt: skip

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
    both = {**mpls, "name": "Beide", "source_system_ids": [ids["fw-a"], ids["fw-b"]]}
    client.post("/routing/exits", json=both, headers=operator)
    assert client.delete(f"/systems/{ids['fw-a']}", headers=ADMIN).status_code in (200, 204)
    rows = client.get("/routing/exits", params={"tenant_id": tid}).json()
    assert [(x["name"], x["source_system_ids"]) for x in rows] == [
        ("Beide", [ids["fw-b"]]),  # gilt noch fuer die andere Firewall
        ("Internet", []),
    ]


FWR = _fw(
    "fw-r",
    [
        _r("fw-r", "root", "10.10.0.0/24", [{"interface": "lan"}], "connected"),
        _r("fw-r", "root", "198.51.100.0/30", [{"interface": "wan1"}], "connected"),
        _r("fw-r", "root", "203.0.113.8/29", [{"interface": "wan2"}], "connected"),
        _r("fw-r", "root", "10.77.0.0/30", [{"interface": "to-fws"}], "connected"),
        _r("fw-r", "root", "10.66.0.0/29", [{"interface": "to-par"}], "connected"),
        _r("fw-r", "root", "0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan1"}], "bgp",
           distance=20),
        _r("fw-r", "root", "0.0.0.0/0", [{"ip": "198.51.100.1"}], "bgp", table="bgp",
           attrs={"as_path": "64500"}),
        _r("fw-r", "root", "0.0.0.0/0", [{"ip": "203.0.113.9"}], "bgp", table="bgp", best=False,
           attrs={"as_path": "64501 64502"}),
        _r("fw-r", "root", "0.0.0.0/0", [{"ip": "10.77.0.2", "interface": "to-fws"}],
           table="static", distance=250),  # schwebende Route ueber fw-s
        _r("fw-r", "root", "0.0.0.0/0", [{"ip": "10.77.0.9", "interface": "to-fws"}],
           table="static", distance=10, best=False),  # abgeschaltet: kein Backup
        _r("fw-r", "root", "10.88.0.0/16", [{"ip": "10.66.0.2", "interface": "to-par"},
                                            {"ip": "10.66.0.3", "interface": "to-par"}]),
    ],
    [_if("fw-r", "lan", "10.10.0.1/24"), _if("fw-r", "wan1", "198.51.100.2/30", "internet"),
     _if("fw-r", "wan2", "203.0.113.10/29", "internet"), _if("fw-r", "to-fws", "10.77.0.1/30")],
)  # fmt: skip
FWS = _fw(
    "fw-s",
    [
        _r("fw-s", "root", "10.77.0.0/30", [{"interface": "to-fwr"}], "connected"),
        _r("fw-s", "root", "0.0.0.0/0", [{"ip": "192.0.2.1", "interface": "wan"}]),
    ],
    [_if("fw-s", "to-fwr", "10.77.0.2/30")],
)


def _par(name: str, ip: str) -> dict:
    return _fw(
        name,
        [_r(name, "root", "10.88.0.0/24", [{"interface": "lan"}], "connected")],
        [_if(name, "to-fwr", ip)],
    )


def test_redundante_wege_mit_backup_und_verbund(client):
    """0.19.0: neben dem aktiven Weg die Backup-Wege (BGP-Pfad, schwebende statische Route)
    mit gleicher bzw. fast gleicher Laenge; ECMP ueber zwei Firewalls eines Verbunds."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    ids = {}
    for name, result in (("fw-r", FWR), ("fw-s", FWS), ("fw-p1", _par("fw-p1", "10.66.0.2/29")),
                         ("fw-p2", _par("fw-p2", "10.66.0.3/29"))):  # fmt: skip
        ids[name] = _system(client, tid, name, "fortigate")
        _deliver(client, tid, ids[name], "fortigate", result)

    out = _trace(client, tid, src="10.10.0.5", dst="198.18.0.10")
    kinds = [(p["kind"], _labels(p), p["end"]["kind"]) for p in out["paths"]]
    assert kinds == [
        ("active", ["fw-r"], "external"),
        ("backup", ["fw-r"], "external"),  # gleiche Laenge: zweiter Provider (BGP)
        ("backup", ["fw-r", "fw-s"], "external"),  # ein Hop mehr: schwebende Route
    ]
    bgp, floating = out["paths"][1], out["paths"][2]
    assert bgp["deviation"] == {
        "hop": 0,
        "label": "fw-r",
        "reason": "BGP-Backup (AS-Pfad 64501 64502)",
    }
    assert bgp["hops"][0]["egress"]["interface"] == "wan2"  # Interface aus dem Netz des Next-Hops
    assert floating["hops"][0]["backup"]["reason"] == "statische Backup-Route (Distanz 250)"
    assert all("10.77.0.9" not in str(p) for p in out["paths"])  # abgeschaltete Route
    assert [p["extra"] for p in out["paths"]] == [0, 0, 1]  # gleich lang bzw. ein Hop mehr
    strict = _trace(client, tid, src="10.10.0.5", dst="198.18.0.10", slack=0)
    assert [p["kind"] for p in strict["paths"]] == ["active", "backup"]
    assert "1 weitere Backup-Wege ausgeblendet" in strict["notes"][0]

    # ECMP auf zwei parallele Firewalls: redundant ueber den Verbund
    body = {"tenant_id": tid, "group": "Nord", "system_ids": [ids["fw-p1"], ids["fw-p2"]]}
    client.post("/systems/parallel-group", json=body, headers=ADMIN)
    out = _trace(client, tid, src="10.10.0.5", dst="10.88.0.7")
    assert [(_labels(p), p.get("parallel")) for p in out["paths"]] == [
        (["fw-r", "fw-p1"], ["Nord"]),
        (["fw-r", "fw-p2"], ["Nord"]),
    ]
    assert out["paths"][1]["hops"][1]["group"] == "Nord"


def _wan(name: str, own: int, to_x: bool) -> dict:
    """Firewall eines Verbunds an vier WAN-Routern mit BGP (zwei Interfaces in der Zone wan):
    10.60.0.0/16 aktiv ueber zwei Router (ECMP), die anderen zwei als BGP-Backup."""
    wan = [("198.18.10.1", "wan1"), ("198.18.10.2", "wan1"), ("198.18.20.1", "wan2"),
           ("198.18.20.2", "wan2")]  # fmt: skip
    routes = [
        _r(name, "root", "198.18.10.0/29", [{"interface": "wan1"}], "connected"),
        _r(name, "root", "198.18.20.0/29", [{"interface": "wan2"}], "connected"),
        _r(name, "root", "203.0.113.0/29", [{"interface": "inet"}], "connected"),
        _r(name, "root", "10.10.0.0/24", [{"interface": "lan"}], "connected"),
        _r(name, "root", "10.99.0.0/24", [{"interface": "mgmt"}], "connected", attrs=MGMT_ATTRS),
        _r(name, "root", "10.60.0.0/16", [{"ip": ip, "interface": i} for ip, i in wan[:2]], "bgp",
           distance=20),
        *(_r(name, "root", "10.60.0.0/16", [{"ip": ip}], "bgp", table="bgp", best=n < 2,
             attrs={"as_path": "65100"}) for n, (ip, _i) in enumerate(wan)),
        _r(name, "root", "0.0.0.0/0", [{"ip": "203.0.113.1", "interface": "inet"}]),
        _r(name, "root", "0.0.0.0/0", [{"ip": "10.99.0.1", "interface": "mgmt"}],
           attrs=MGMT_ATTRS),
    ]  # fmt: skip
    interfaces = [
        _if(name, "wan1", f"198.18.10.{own}/29", "wan"),
        _if(name, "wan2", f"198.18.20.{own}/29", "wan"),
        _if(name, "inet", f"203.0.113.{own}/29", "internet"),
        _if(name, "lan", "10.10.0.1/24"),
        _if(name, "mgmt", f"10.99.0.{own}/24"),
    ]
    if to_x:  # Next-Hop einer gelesenen Firewall: keine Anbindung
        routes.append(_r(name, "root", "10.90.0.0/16", [{"ip": "10.66.0.2", "interface": "to-x"}]))
        interfaces.append(_if(name, "to-x", "10.66.0.1/30"))
    result = _fw(name, routes, interfaces)
    result["routing"]["peers"] = [
        {"node": name, "context": "root", "peer_ip": ip, "remote_as": 65100, "state": "Established"}
        for ip, _i in wan
    ]
    return result


FWX = _fw(
    "fw-x",
    [
        _r("fw-x", "root", "10.66.0.0/30", [{"interface": "to-w"}], "connected"),
        _r("fw-x", "root", "198.18.30.0/30", [{"interface": "wan"}], "connected"),
        _r("fw-x", "root", "10.61.0.0/16", [{"ip": "198.18.30.1", "interface": "wan"}]),
    ],
    [_if("fw-x", "to-w", "10.66.0.2/30"), _if("fw-x", "wan", "198.18.30.2/30", "wan")],
)


def test_erkannte_anbindungen_mit_redundanz(client):
    """0.20.0: Router ohne gelesenes Geraet (Next-Hops, BGP-Nachbarn) werden erkannt und zu
    Anbindungen zusammengefasst - vier WAN-Router an beiden Firewalls eines Verbunds sind eine
    Anbindung mit zwei aktiven und zwei Backup-Routern; die Anbindung gilt fuer beide."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    ids = {}
    for name, result in (("fw-w1", _wan("fw-w1", 5, True)), ("fw-w2", _wan("fw-w2", 6, False)),
                         ("fw-x", FWX)):  # fmt: skip
        ids[name] = _system(client, tid, name, "fortigate")
        _deliver(client, tid, ids[name], "fortigate", result)
    body = {"tenant_id": tid, "group": "Nord", "system_ids": [ids["fw-w1"], ids["fw-w2"]]}
    client.post("/systems/parallel-group", json=body, headers=ADMIN)
    operator = {"X-Dev-Roles": "operator", "X-Dev-Tenants": str(tid)}

    def suggested() -> list[dict]:
        r = client.get("/routing/exits/suggestions", params={"tenant_id": tid})
        assert r.status_code == 200, r.text
        return r.json()

    rows = suggested()
    # nicht dabei: Management-Default-Route, Next-Hop einer gelesenen Firewall (fw-x)
    assert [(s["name"], s["kind"], s["sources"], [h["ip"] for h in s["next_hops"]])
            for s in rows] == [
        ("internet", "internet", ["fw-w1", "fw-w2"], ["203.0.113.1"]),
        ("wan", "wan", ["fw-w1", "fw-w2"],
         ["198.18.10.1", "198.18.10.2", "198.18.20.1", "198.18.20.2"]),
        ("wan", "wan", ["fw-x"], ["198.18.30.1"]),  # andere Firewall, kein Verbund: eigene
    ]  # fmt: skip
    wan = rows[1]
    assert wan["source_system_ids"] == sorted([ids["fw-w1"], ids["fw-w2"]])
    assert [(h["status"], h["remote_as"], h["state"]) for h in wan["next_hops"]] == [
        ("active", 65100, "Established"),
        ("active", 65100, "Established"),
        ("backup", 65100, "Established"),
        ("backup", 65100, "Established"),
    ]
    assert wan["redundancy"] == {"routers": 4, "active": 2, "backup": 2, "neighbor": 0, "ecmp": 2,
                                 "firewalls": 2}  # fmt: skip
    first = wan["next_hops"][0]
    assert [(u["source"], u["interface"], u["zone"]) for u in first["uses"]] == [
        ("fw-w1", "wan1", "wan"),
        ("fw-w2", "wan1", "wan"),
    ]
    assert wan["next_hops"][2]["uses"][0]["interface"] == "wan2"  # BGP-Pfad: Netz des Routers
    assert rows[0]["next_hops"][0]["default"] is True

    # ausblenden (0.21.0): fw-x ist keine Anbindung - nicht mehr vorgeschlagen, wieder anzeigen
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    hide = {"tenant_id": tid, "ips": ["198.18.30.1"]}
    assert client.post("/routing/exits/dismiss", json=hide, headers=viewer).status_code == 403
    r = client.post("/routing/exits/dismiss", json=hide, headers=operator)
    assert r.status_code == 200 and r.json() == {"ips": ["198.18.30.1"]}
    assert [s["sources"] for s in suggested()] == [["fw-w1", "fw-w2"], ["fw-w1", "fw-w2"]]
    hidden = client.get("/routing/exits/suggestions", params={"tenant_id": tid, "dismissed": True})
    assert [(s["sources"], s["dismissed"]) for s in hidden.json()][-1] == (["fw-x"], True)
    assert client.post("/routing/exits/dismiss", json=hide, headers=operator).json() == {"ips": []}
    undo = client.post("/routing/exits/dismiss/undo", json=hide, headers=operator)
    assert undo.json() == {"ips": ["198.18.30.1"]} and len(suggested()) == 3

    # wie bisher nur der erste Router: der Vorschlag bleibt mit den uebrigen, mit der Anbindung
    exit_ = {"tenant_id": tid, "name": "WAN Nord", "kind": "wan",
             "source_system_ids": [ids["fw-w1"]], "next_hops": ["198.18.10.1"]}  # fmt: skip
    exit_id = client.post("/routing/exits", json=exit_, headers=operator).json()["id"]
    wan = next(s for s in suggested() if s["key"] == "198.18.10.1")
    assert wan["exits"] == [{"id": exit_id, "name": "WAN Nord", "kind": "wan"}]
    assert [h["covered"] for h in wan["next_hops"]] == [False, False, False, False]  # fw-w2 fehlt

    # ueber die Zone: deckt alle vier ab, nennt aber nur einen - die anderen sind „erkannt“
    zone = {**exit_, "source_system_ids": [ids["fw-w1"], ids["fw-w2"]], "interfaces": ["wan"]}
    client.put(f"/routing/exits/{exit_id}", json=zone, headers=operator)
    (row,) = client.get("/routing/exits", params={"tenant_id": tid}).json()
    st = row["status"]
    assert [(r["ip"], r["status"], r["listed"]) for r in st["routers"]] == [
        ("198.18.10.1", "active", True),
        ("198.18.10.2", "active", False),
        ("198.18.20.1", "backup", False),
        ("198.18.20.2", "backup", False),
    ]
    assert st["missing"] == ["198.18.10.2", "198.18.20.1", "198.18.20.2"]
    assert st["source_system_ids"] == sorted([ids["fw-w1"], ids["fw-w2"]])
    assert all(s["key"] != "198.18.10.1" for s in suggested())  # ganz abgedeckt
    covered = client.get("/routing/exits/suggestions", params={"tenant_id": tid, "covered": True})
    assert any(s["covered"] and s["key"] == "198.18.10.1" for s in covered.json())

    # alle Router eingetragen: Weg ueber jeden Router endet an der Anbindung, alle gleich lang
    client.put(f"/routing/exits/{exit_id}", headers=operator,
               json={**zone, "next_hops": st["missing"] + ["198.18.10.1"]})  # fmt: skip
    out = _trace(client, tid, src="10.10.0.5", dst="10.60.1.1")
    ends = [(p["kind"], p["hops"][0]["label"], p["end"]["kind"], p["extra"]) for p in out["paths"]]
    assert sorted(ends) == sorted(
        [(k, fw, "exit", 0) for fw in ("fw-w1", "fw-w2") for k in ("active", "active")]
        + [("backup", fw, "exit", 0) for fw in ("fw-w1", "fw-w2") for _ in range(2)]
    )
    texts = {p["end"]["text"] for p in out["paths"]}
    assert "weiter über WAN Nord – Router 4 von 4" in texts  # 198.18.10.1 zuletzt eingetragen
    assert (
        client.get("/routing/exits", params={"tenant_id": tid}).json()[0]["status"]["missing"] == []
    )
