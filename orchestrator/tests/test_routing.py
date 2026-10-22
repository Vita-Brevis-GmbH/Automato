# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Routing, BGP, HA und L3Out (ADR 0019 Stufe 2): speichern je Lauf, abfragen je Kontext.

Eine FortiGate mit zwei VDOMs (ECMP-Default, angeschlossenes Netz, Backup als statische Route
und als BGP-Pfad, Policy-Route, HA mit Session-Sync) und ein APIC mit L3Out und Routen der
Border-Leafs. Die Weg-Abfrage liefert je Kontext die wirksame Route (laengstes Praefix) und
die Policy-Routen, die vorgehen.
"""

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _route(context, prefix, hops, table="rib", protocol="static", **kw):
    return {"node": "fw-01", "context": context, "prefix": prefix, "table": table,
            "protocol": protocol, "next_hops": hops, **kw}  # fmt: skip


FW = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "fw-01", "role": "firewall"}],
        "interfaces": [
            {"device": "fw-01", "name": "wan1", "zone": "internet", "vdom": "root"},
            {"device": "fw-01", "name": "wan2", "zone": "internet", "vdom": "root"},
            {"device": "fw-01", "name": "srv", "zone": "server", "vdom": "root"},
        ],
    },
    "routing": {
        "contexts": [
            {"node": "fw-01", "context": "root"},
            {"node": "fw-01", "context": "kunde-a", "missing": ["BGP-Pfade IPv4 (HTTP 403)"]},
        ],
        "routes": [
            _route("root", "0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan1"},
                                         {"ip": "198.51.100.5", "interface": "wan2"}],
                   protocol="bgp", distance=20),
            _route("root", "10.20.0.0/24", [{"interface": "srv"}], protocol="connected"),
            _route("root", "10.99.0.0/16", [{"ip": "10.20.0.254", "interface": "srv"}],
                   table="static", distance=10),
            _route("root", "10.99.0.0/16", [{"ip": "10.30.0.254", "interface": "dmz"}],
                   table="static", distance=20, best=False),
            _route("root", "0.0.0.0/0", [{"ip": "198.51.100.1"}], table="bgp", protocol="bgp",
                   attrs={"as_path": "64500"}),
            _route("root", "0.0.0.0/0", [{"ip": "198.51.100.9"}], table="bgp", protocol="bgp",
                   best=False, attrs={"as_path": "64500 64501"}),
            _route("kunde-a", "0.0.0.0/0", [{"ip": "10.255.1.1", "interface": "vl-kunde"}]),
            _route("root", "2001:db8::/32", [{"ip": "2001:db8::1", "interface": "wan1"}]),
        ],
        "peers": [{"node": "fw-01", "context": "root", "peer_ip": "198.51.100.1",
                   "remote_as": 64500, "state": "Established"}],
        "ha": [{"node": "fw-01", "mode": "a-p", "group_name": "cluster-a", "group_id": 7,
                "members": [{"hostname": "fw-a", "role": "primary"},
                            {"hostname": "fw-b", "role": "secondary"}],
                "session_pickup": True,
                "sync_peers": [{"peerip": "10.0.0.2", "syncvd": ["root"]}]}],
        "policy_routes": [
            {"node": "fw-01", "context": "root", "seq": 5, "match": {"dst": ["10.20.0.0/24"]},
             "gateway": "10.20.0.253", "output_device": ["srv"]},
            {"node": "fw-01", "context": "root", "seq": 6, "match": {"dst": ["192.0.2.0/24"]}},
            {"node": "fw-01", "context": "root", "kind": "sdwan", "seq": 1, "name": "office",
             "match": {"dstaddr": ["N_office"]}, "output_device": ["wan2", "wan1"]},
        ],
    },
}  # fmt: skip

APIC = {
    "status": "completed",
    "routing": {
        "contexts": [{"node": "leaf-101", "context": "TE:VRF1", "kind": "vrf"}],
        "routes": [{"node": "leaf-101", "context": "TE:VRF1", "prefix": "0.0.0.0/0",
                    "protocol": "bgp", "distance": 20,
                    "next_hops": [{"ip": "10.255.0.1", "interface": "vlan5"},
                                  {"ip": "10.255.0.9", "interface": "vlan6"}]}],
        "peers": [{"node": "leaf-101", "context": "TE:VRF1", "peer_ip": "10.255.0.1",
                   "remote_as": 65010, "state": "established"}],
        "l3outs": [{"tenant": "TE", "name": "L3O_inet", "dn": "uni/tn-TE/out-L3O_inet",
                    "vrf": "TE:VRF1", "nodes": [{"pod": "1", "node": "101"}],
                    "paths": [{"path": "topology/pod-1/protpaths-101-102/pathep-[vpc_fw]",
                               "nodes": ["101", "102"], "kind": "ext-svi", "vlan": 3001}],
                    "peers": [{"ip": "10.255.0.1", "asn": 65010}], "ext_epgs": ["EN_inet"]}],
    },
}  # fmt: skip


def _deliver(client, tid, sid, platform, result):
    run = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _system(client, tid, name, platform):
    body = {"tenant_id": tid, "name": name, "platform": platform, "base_url": "https://x"}
    return client.post("/systems", json=body, headers=ADMIN).json()["id"]


def _seed(client) -> tuple[int, int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    fw = _system(client, tid, "fw-01", "fortigate")
    apic = _system(client, tid, "apic-01", "cisco_aci")
    _deliver(client, tid, fw, "fortigate", FW)
    _deliver(client, tid, apic, "cisco_aci", APIC)
    return tid, fw, apic


def test_routing_speichern_und_abfragen(client):
    tid, fw, apic = _seed(client)
    p = {"tenant_id": tid}
    summary = client.get("/routing/summary", params=p).json()
    rows = {(c["node"], c["context"]): c for c in summary["contexts"]}
    assert rows[("fw-01", "root")]["counts"] == {
        "rib": 3,
        "static": 2,
        "bgp": 2,
        "peers": 1,
        "policy_routes": 3,
    }
    assert rows[("fw-01", "kunde-a")]["missing"] == ["BGP-Pfade IPv4 (HTTP 403)"]
    assert (
        rows[("leaf-101", "TE:VRF1")]["kind"] == "vrf"
        and rows[("leaf-101", "TE:VRF1")]["source"] == "apic-01"
    )
    assert (summary["ha"], summary["l3outs"]) == (1, 1)

    # Routen mit der IP: laengstes Praefix zuerst; Next-Hops mit Zone aus dem Inventar
    r = client.get("/routing/routes", params={**p, "ip": "10.20.0.5", "table": "rib"})
    assert [(x["node"], x["context"], x["prefix"]) for x in r.json()] == [
        ("fw-01", "root", "10.20.0.0/24"),
        ("fw-01", "kunde-a", "0.0.0.0/0"),
        ("fw-01", "root", "0.0.0.0/0"),
        ("leaf-101", "TE:VRF1", "0.0.0.0/0"),
    ]
    assert r.headers["X-Total-Count"] == "4"  # dazu die Defaults von kunde-a und leaf-101
    default = next(x for x in r.json() if x["context"] == "root" and x["prefix"] == "0.0.0.0/0")
    assert [(h["interface"], h.get("zone")) for h in default["next_hops"]] == [
        ("wan1", "internet"),
        ("wan2", "internet"),
    ]
    bgp = client.get("/routing/routes", params={**p, "table": "bgp"}).json()
    assert [(x["best"], x["attrs"]["as_path"]) for x in bgp] == [
        (True, "64500"),
        (False, "64500 64501"),  # Backup-Weg
    ]
    static = client.get("/routing/routes", params={**p, "q": "10.30.0.254"}).json()
    assert [(x["prefix"], x["best"], x["distance"]) for x in static] == [
        ("10.99.0.0/16", False, 20)
    ]
    v6 = client.get("/routing/routes", params={**p, "ip": "2001:db8::5"}).json()
    assert [x["prefix"] for x in v6] == ["2001:db8::/32"]

    # Weg-Abfrage je Kontext: wirksame Route und vorgehende Policy-Routen
    found = client.get("/routing/lookup", params={**p, "ip": "10.20.0.5"}).json()
    look = {(x["node"], x["context"]): x for x in found}
    root = look[("fw-01", "root")]
    assert root["route"]["prefix"] == "10.20.0.0/24" and root["route"]["protocol"] == "connected"
    assert [(x["kind"], x["seq"], x["certain"]) for x in root["policy_routes"]] == [
        ("policy", 5, True),  # Ziel 10.20.0.0/24 passt sicher
        ("sdwan", 1, False),  # Ziel nur ueber Objekte bestimmt: moeglich
    ]
    assert look[("fw-01", "kunde-a")]["route"]["next_hops"] == [
        {"ip": "10.255.1.1", "interface": "vl-kunde"}
    ]
    assert look[("fw-01", "kunde-a")]["missing"] == ["BGP-Pfade IPv4 (HTTP 403)"]
    assert len(look[("leaf-101", "TE:VRF1")]["route"]["next_hops"]) == 2  # ECMP
    assert client.get("/routing/lookup", params={**p, "ip": "kein-ip"}).status_code == 422

    (ha,) = client.get("/routing/ha", params=p).json()
    assert (ha["mode"], ha["group_name"], ha["source"]) == ("a-p", "cluster-a", "fw-01")
    assert [m["role"] for m in ha["members"]] == ["primary", "secondary"]
    assert ha["sync_peers"] == [{"peerip": "10.0.0.2", "syncvd": ["root"], "node": None}]
    peers = client.get("/routing/peers", params=p).json()
    assert {(x["node"], x["peer_ip"], x["state"]) for x in peers} == {
        ("fw-01", "198.51.100.1", "Established"),
        ("leaf-101", "10.255.0.1", "established"),
    }
    (l3,) = client.get("/routing/l3outs", params=p).json()
    assert (l3["aci_tenant"], l3["name"], l3["vrf"]) == ("TE", "L3O_inet", "TE:VRF1")
    assert l3["paths"][0]["nodes"] == ["101", "102"]
    pol = client.get("/routing/policy-routes", params={**p, "source_system_id": fw}).json()
    assert [(x["kind"], x["seq"]) for x in pol] == [("policy", 5), ("policy", 6), ("sdwan", 1)]

    # neuer Lauf ersetzt den Stand; ohne Routing (aeltere Collectors) bleibt er
    _deliver(client, tid, fw, "fortigate", FW)
    assert len(client.get("/routing/routes", params={**p, "source_system_id": fw}).json()) == 8
    _deliver(client, tid, fw, "fortigate", {"status": "completed"})
    assert len(client.get("/routing/routes", params={**p, "source_system_id": fw}).json()) == 8
    empty = {"status": "completed", "routing": {}}
    _deliver(client, tid, apic, "cisco_aci", empty)
    assert client.get("/routing/l3outs", params=p).json() == []


def test_routing_mandant_und_loeschen(client):
    tid, fw, _ = _seed(client)
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(other)}
    r = client.get("/routing/routes", params={"tenant_id": tid}, headers=viewer)
    assert r.status_code == 403
    assert client.get("/routing/summary").status_code == 422  # Mandant noetig

    assert client.delete(f"/systems/{fw}", headers=ADMIN).status_code in (200, 204)
    p = {"tenant_id": tid}
    assert {c["node"] for c in client.get("/routing/summary", params=p).json()["contexts"]} == {
        "leaf-101"
    }
    assert client.get("/routing/ha", params=p).json() == []
