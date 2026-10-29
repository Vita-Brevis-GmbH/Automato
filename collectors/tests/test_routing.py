# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Routing, BGP, HA und L3Out lesen (ADR 0019 Stufe 2) - nur lesend, ohne Secrets.

FortiGate je VDOM: wirksame Tabelle (ECMP), alle BGP-Pfade (auch Backup), Nachbarn, statische
Routen (auch inaktive), Policy-Routen, SD-WAN, HA-Cluster und Session-Sync (FGSP). ACI: L3Outs
aus dem Tenant-Baum (Border-Leafs, vPC-SVI, Peers, statische Routen) und die Routen der
Border-Leafs je VRF.
"""

import json

import httpx

from collectors.cisco_aci import routing as aci
from collectors.cisco_aci.client import ApicClient
from collectors.fortigate import routing as fgt
from collectors.fortigate.client import FortiGateClient

SECRET = "ENC geheimes-ha-passwort"
BGP_SECRET = "ENC geheimes-bgp-passwort"

MONITOR = {
    "router/ipv4": [
        {"ip_mask": "0.0.0.0/0", "type": "bgp", "distance": 20, "metric": 0,
         "gateway": "198.51.100.1", "interface": "wan1"},
        {"ip_mask": "0.0.0.0/0", "type": "bgp", "distance": 20, "metric": 0,
         "gateway": "198.51.100.5", "interface": "wan2"},
        {"ip_mask": "10.20.0.0/24", "type": "connect", "distance": 0, "gateway": "0.0.0.0",
         "interface": "srv"},
    ],
    "router/ipv6": [{"ip_mask": "::/0", "type": "static", "distance": 10, "gateway": "2001:db8::1",
                     "interface": "wan1"}],
    "router/bgp/paths": [
        {"nlri_prefix": "0.0.0.0", "nlri_prefix_len": 0, "next_hop": "198.51.100.1",
         "learned_from": "198.51.100.1", "is_best": True, "as_path": "64500", "local_pref": 100},
        {"nlri_prefix": "0.0.0.0", "nlri_prefix_len": 0, "next_hop": "198.51.100.9",
         "learned_from": "198.51.100.9", "is_best": False, "as_path": "64500 64501",
         "local_pref": 50},
    ],
    "router/bgp/neighbors": [{"neighbor_ip": "198.51.100.1", "local_ip": "198.51.100.2",
                              "remote_as": 64500, "state": "Established", "type": "ipv4"}],
    "system/ha-peer": [
        {"hostname": "fw-a", "serial_no": "FG1", "priority": 200, "master": True},
        {"hostname": "fw-b", "serial_no": "FG2", "priority": 100, "master": False},
    ],
}  # fmt: skip

CMDB = {
    "router/static": [
        {"seq-num": 1, "dst": "10.99.0.0 255.255.0.0", "gateway": "10.20.0.254", "device": "srv",
         "distance": 10, "status": "enable"},
        {"seq-num": 2, "dst": "10.99.0.0 255.255.0.0", "gateway": "10.30.0.254", "device": "dmz",
         "distance": 20, "status": "disable", "comment": "Backup"},
    ],
    "router/policy": [
        {"seq-num": 5, "input-device": [{"name": "srv"}],
         "src": [{"subnet": "10.20.0.0 255.255.255.0"}], "dst": [{"subnet": "0.0.0.0 0.0.0.0"}],
         "protocol": 6, "start-port": 443, "end-port": 443, "gateway": "198.51.100.5",
         "output-device": "wan2", "status": "enable"},
    ],
    "system/sdwan": {
        "status": "enable",
        "members": [{"seq-num": 1, "interface": "wan1", "gateway": "198.51.100.1"},
                    {"seq-num": 2, "interface": "wan2", "gateway": "198.51.100.5"}],
        "service": [{"id": 1, "name": "office", "mode": "priority", "dst": [{"name": "N_office"}],
                     "priority-members": [{"seq-num": 2}, {"seq-num": 1}], "start-port": 1,
                     "end-port": 65535}],
    },
    "system/ha": {"mode": "a-p", "group-name": "cluster-a", "group-id": 7, "password": SECRET,
                  "session-pickup": "enable", "priority": 200},
    "system/standalone-cluster": {
        "standalone-group-id": 1, "group-member-id": 0, "psksecret": SECRET,
        "cluster-peer": [{"sync-id": 1, "peervd": "root", "peerip": "10.0.0.2",
                          "syncvd": [{"name": "root"}], "secondary-add-ipsec-routes": "enable"}],
    },
}  # fmt: skip


def _fgt(denied: set[str] = frozenset(), seen: list | None = None) -> FortiGateClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # nur lesend
        path = request.url.path
        if seen is not None:
            seen.append((path, dict(request.url.params)))
        for prefix, table in (("/api/v2/monitor/", MONITOR), ("/api/v2/cmdb/", CMDB)):
            if path.startswith(prefix):
                key = path.removeprefix(prefix)
                if key in denied:
                    return httpx.Response(403, json={})
                if key == "system/global":
                    return httpx.Response(200, json={"results": {"hostname": "fw-a"}})
                if key == "system/vdom":
                    return httpx.Response(200, json={"results": [{"name": "root"}]})
                if key in table:
                    return httpx.Response(200, json={"results": table[key]})
                if prefix == "/api/v2/cmdb/":  # Policies, Objekte usw.: leer
                    return httpx.Response(200, json={"results": []})
        return httpx.Response(404, json={})

    return FortiGateClient(
        "https://fw", "t", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_fortigate_routing_ha_und_keine_secrets():
    d = _fgt().gather()
    inv = fgt.normalize(d["vdoms"], d["ha"], "fw-a")
    assert [(c.node, c.context, c.missing) for c in inv.contexts] == [("fw-a", "root", [])]

    rib = {(r.prefix, r.protocol): r for r in inv.routes if r.table == "rib"}
    default = rib[("0.0.0.0/0", "bgp")]  # ECMP: ein Eintrag, zwei Next-Hops
    assert [(h.ip, h.interface) for h in default.next_hops] == [
        ("198.51.100.1", "wan1"),
        ("198.51.100.5", "wan2"),
    ]
    assert rib[("10.20.0.0/24", "connected")].next_hops[0].ip is None  # direkt angeschlossen
    assert rib[("::/0", "static")].next_hops[0].ip == "2001:db8::1"

    bgp = [r for r in inv.routes if r.table == "bgp"]
    assert [(r.best, r.next_hops[0].ip, r.attrs["as_path"]) for r in bgp] == [
        (True, "198.51.100.1", "64500"),
        (False, "198.51.100.9", "64500 64501"),  # Backup-Weg, gerade nicht aktiv
    ]
    static = [r for r in inv.routes if r.table == "static"]
    assert [(r.prefix, r.best, r.distance, r.next_hops[0].interface) for r in static] == [
        ("10.99.0.0/16", True, 10, "srv"),
        ("10.99.0.0/16", False, 20, "dmz"),
    ]
    (peer,) = inv.peers
    assert (peer.peer_ip, peer.remote_as, peer.state) == ("198.51.100.1", 64500, "Established")

    policy = {p.kind: p for p in inv.policy_routes}
    assert policy["policy"].match == {
        "input_device": ["srv"],
        "src": ["10.20.0.0/24"],
        "dst": ["0.0.0.0/0"],
        "protocol": 6,
        "ports": [443, 443],
    }
    assert (policy["policy"].gateway, policy["policy"].output_device) == ("198.51.100.5", ["wan2"])
    assert policy["sdwan"].output_device == ["wan2", "wan1"]  # Vorrang der Mitglieder
    assert "ports" not in policy["sdwan"].match  # 1-65535 = alle

    (ha,) = inv.ha
    assert (ha.mode, ha.group_name, ha.group_id, ha.session_pickup) == ("a-p", "cluster-a", 7, True)
    assert [(m["hostname"], m["role"]) for m in ha.members] == [
        ("fw-a", "primary"),
        ("fw-b", "secondary"),
    ]
    assert ha.sync_peers[0]["peerip"] == "10.0.0.2" and ha.sync_peers[0]["syncvd"] == ["root"]
    assert SECRET not in json.dumps(inv.model_dump())  # Passwort und PSK nie uebernommen
    assert "psksecret" not in json.dumps(inv.model_dump())


BGP_CFG = {
    "router/bgp": {
        "as": "65001", "router-id": "10.0.0.1",
        "neighbor": [
            {"ip": "198.51.100.1", "remote-as": "64500", "password": BGP_SECRET,
             "route-map-in": "RM_ISP_IN", "prefix-list-out": "PL_DC", "description": "ISP A"},
            {"ip": "198.51.100.13", "remote-as": "64502", "shutdown": "enable"},
        ],
        "neighbor-group": [{"name": "G_SITES", "remote-as": "65100", "prefix-list-in": "PL_SITES"}],
        "neighbor-range": [{"id": 1, "prefix": "10.60.0.0 255.255.0.0",
                            "neighbor-group": "G_SITES"}],
        "network": [{"id": 1, "prefix": "10.20.0.0 255.255.0.0", "route-map": ""}],
        "aggregate-address": [{"id": 1, "prefix": "10.20.0.0 255.255.0.0",
                               "summary-only": "enable"}],
        "redistribute": [{"name": "connected", "status": "enable", "route-map": "RM_CONN"},
                         {"name": "ospf", "status": "disable"}],
    },
    "router/prefix-list": [
        {"name": "PL_DC", "rule": [{"id": 1, "action": "permit",
                                    "prefix": "10.20.0.0 255.255.0.0", "ge": 0, "le": 24}]},
        {"name": "PL_SITES", "rule": [{"id": 2, "action": "deny", "prefix": "any"},
                                      {"id": 1, "action": "permit",
                                       "prefix": "10.60.0.0 255.255.0.0", "ge": 0, "le": 0}]},
    ],
    "router/prefix-list6": [{"name": "PL6", "rule": [{"id": 1, "action": "permit",
                                                      "prefix6": "2001:db8::/32", "le": 48}]}],
    "router/route-map": [
        {"name": "RM_ISP_IN", "rule": [
            {"id": 1, "action": "deny", "match-ip-address": "ACL_BOGONS"},
            {"id": 2, "action": "permit", "match-community": "", "set-local-preference": 200,
             "set-community": [{"community": "65001:100"}]},
        ]},
    ],
    "router/access-list": [{"name": "ACL_BOGONS", "rule": [
        {"id": 1, "action": "permit", "prefix": "10.0.0.0 255.0.0.0", "exact-match": "disable"},
    ]}],
}  # fmt: skip


def test_fortigate_bgp_filter_und_nachbar_konfiguration(monkeypatch):
    """0.113.0 (ADR 0041): BGP-Prozess, Filter je Nachbar (auch ueber Nachbar-Gruppe und
    -Bereich), Prefix-/Access-Listen und Route-Maps - nur lesend, ohne Passwort."""
    for key, value in BGP_CFG.items():
        monkeypatch.setitem(CMDB, key, value)
    site = {"neighbor_ip": "10.60.1.1", "remote_as": 65100, "state": "Established"}
    monitor = [*MONITOR["router/bgp/neighbors"], site]
    monkeypatch.setitem(MONITOR, "router/bgp/neighbors", monitor)
    d = _fgt().gather()
    inv = fgt.normalize(d["vdoms"], d["ha"], "fw-a")
    assert inv.contexts[0].missing == []
    peers = {p.peer_ip: p for p in inv.peers}
    isp = peers["198.51.100.1"].attrs["config"]
    assert isp["filters"] == {"in": {"route_map": "RM_ISP_IN"}, "out": {"prefix_list": "PL_DC"}}
    assert (isp["remote_as"], isp["description"]) == ("64500", "ISP A")
    site = peers["10.60.1.1"].attrs["config"]  # dynamischer Nachbar: Filter der Gruppe
    assert (site["group"], site["filters"]) == ("G_SITES", {"in": {"prefix_list": "PL_SITES"}})
    by = {(b.kind, b.name): b for b in inv.bgp}
    process = by[("bgp", "bgp")].data
    # konfiguriert, nicht im Monitor: nur im BGP-Prozess, kein Nachbar ohne Zustand
    assert "198.51.100.13" not in peers and process["neighbors"]["198.51.100.13"]["shutdown"]
    assert (process["as"], process["router_id"]) == ("65001", "10.0.0.1")
    assert process["networks"] == [{"prefix": "10.20.0.0/16"}]
    assert process["aggregates"] == [{"prefix": "10.20.0.0/16", "summary_only": True}]
    assert process["redistribute"] == [{"name": "connected", "route_map": "RM_CONN"}]
    sites = by[("prefix_list", "PL_SITES")]
    assert sites.family == 4 and [(r["id"], r["action"], r["prefix"], r["ge"], r["le"])
                                  for r in sites.data["rules"]] == [
        (1, "permit", "10.60.0.0/16", None, None), (2, "deny", "any", None, None)]  # fmt: skip
    assert by[("prefix_list", "PL_DC")].data["rules"][0]["le"] == 24
    assert by[("prefix_list", "PL6")].family == 6
    rm = by[("route_map", "RM_ISP_IN")].data["rules"]
    assert rm[0]["match"] == {"ip-address": "ACL_BOGONS"} and rm[1]["match"] == {}
    assert rm[1]["set"] == {"local-preference": 200, "community": ["65001:100"]}
    acl = by[("access_list", "ACL_BOGONS")].data["rules"][0]
    assert (acl["prefix"], acl["exact"]) == ("10.0.0.0/8", False)
    assert BGP_SECRET not in json.dumps(inv.model_dump())  # Passwort des Nachbarn nie


def test_fortigate_routing_ohne_recht_steht_in_der_meldung():
    import collectors.fortigate.tasks as t

    d = _fgt(denied={"router/bgp/paths", "system/ha"}).gather()
    inv = fgt.normalize(d["vdoms"], d["ha"], "fw-a")
    assert inv.contexts[0].missing == ["BGP-Pfade IPv4 (HTTP 403)"]
    assert not [r for r in inv.routes if r.table == "bgp"]
    assert t._vdom_message(d) == "Routing nicht lesbar: BGP-Pfade IPv4 (HTTP 403); HA ha (HTTP 403)"


def test_monitor_liste_seitenweise_ohne_endlosschleife(monkeypatch):
    """Kuerzt die FortiGate auf eine Seite, liest der Client mit start/count weiter; liefert sie
    trotz start dasselbe (Parameter unbekannt), bricht er ab."""
    import collectors.fortigate.client as client_mod

    monkeypatch.setattr(client_mod, "MONITOR_PAGE", 2)
    routes = [
        {"ip_mask": f"10.0.{i}.0/24", "type": "static", "gateway": "10.9.9.9"} for i in range(5)
    ]

    def paged(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start", 0))
        count = int(request.url.params.get("count", 2))
        return httpx.Response(200, json={"results": routes[start : start + count]})

    c = FortiGateClient(
        "https://fw", "t", client=httpx.Client(transport=httpx.MockTransport(paged))
    )
    assert len(c._monitor_rows("router/ipv4")) == 5

    calls = []

    def ignores(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params.get("start"))
        return httpx.Response(200, json={"results": routes[:2]})

    c = FortiGateClient(
        "https://fw", "t", client=httpx.Client(transport=httpx.MockTransport(ignores))
    )
    assert len(c._monitor_rows("router/ipv4")) == 2 and calls == [None, "2"]


# -- ACI ----------------------------------------------------------------------------------

BGP_PW = "bgp-peer-passwort"


def _mo(cls: str, children: list | None = None, **attrs) -> dict:
    return {cls: {"attributes": attrs, "children": children or []}}


EXPORT = {
    "imdata": [
        _mo("fvTenant", name="TE", children=[
            _mo("fvCtx", name="VRF1"),
            _mo("l3extOut", name="L3O_inet", enforceRtctrl="export,import", children=[
                _mo("l3extRsEctx", tnFvCtxName="VRF1"),
                _mo("l3extInstP", name="EN_inet", children=[
                    _mo("l3extSubnet", ip="0.0.0.0/0", scope="import-rtctrl,import-security",
                        aggregate="import-rtctrl"),
                    _mo("l3extSubnet", ip="10.20.0.0/16", scope="export-rtctrl"),
                    _mo("l3extSubnet", ip="10.99.0.0/16", scope="import-security"),
                ]),
                _mo("l3extLNodeP", name="NP_border", children=[
                    _mo("l3extRsNodeL3OutAtt", tDn="topology/pod-1/node-101", rtrId="10.255.255.1",
                        children=[_mo("ipRouteP", ip="0.0.0.0/0", pref="1", children=[
                            _mo("ipNexthopP", nhAddr="10.255.0.1")])]),
                    _mo("l3extRsNodeL3OutAtt", tDn="topology/pod-1/node-102", rtrId="10.255.255.2"),
                    _mo("l3extLIfP", name="IP_svi", children=[
                        _mo("l3extRsPathL3OutAtt",
                            tDn="topology/pod-1/protpaths-101-102/pathep-[vpc_fw]",
                            ifInstT="ext-svi", encap="vlan-3001", addr="0.0.0.0", mtu="9000",
                            children=[
                                _mo("l3extMember", side="A", addr="10.255.0.2/29"),
                                _mo("l3extMember", side="B", addr="10.255.0.3/29"),
                                _mo("bgpPeerP", addr="10.255.0.1", password=BGP_PW, children=[
                                    _mo("bgpAsP", asn="65010")]),
                            ]),
                    ]),
                ]),
            ]),
        ]),
        _mo("fvTenant", name="TE_b", children=[
            _mo("l3extOut", name="L3O_shared", children=[_mo("l3extRsEctx", tnFvCtxName="VRF_s")]),
        ]),
    ]
}  # fmt: skip

DOM = "topology/pod-1/node-101/sys/uribv4/dom-TE:VRF1/db-rt"
PEER = "topology/pod-1/node-101/sys/bgp/inst/dom-TE:VRF1/peer-[10.255.0.1/32]"
BORDER = {
    ("1", "101", "TE:VRF1"): {
        "missing": [],
        "rib4": [
            _mo("uribv4Route", dn=f"{DOM}/rt-[0.0.0.0/0]", prefix="0.0.0.0/0"),
            _mo("uribv4Nexthop", dn=f"{DOM}/rt-[0.0.0.0/0]/nh-[bgp-65000]-[10.255.0.1/32]-[vlan5]",
                addr="10.255.0.1/32", owner="bgp-65000", pref="20", metric="0", routeType="ebgp",
                **{"if": "vlan5"}),
            _mo("uribv4Nexthop", dn=f"{DOM}/rt-[0.0.0.0/0]/nh-[bgp-65000]-[10.255.0.9/32]-[vlan6]",
                addr="10.255.0.9/32", owner="bgp-65000", pref="20", metric="0", **{"if": "vlan6"}),
            _mo("uribv4Route", dn=f"{DOM}/rt-[10.20.0.0/24]", prefix="10.20.0.0/24"),
            _mo("uribv4Nexthop", dn=f"{DOM}/rt-[10.20.0.0/24]/nh-[direct]-[10.20.0.1/24]",
                addr="10.20.0.1/24", owner="direct", pref="0", **{"if": "vlan10"}),
        ],
        "bgp": [
            _mo("bgpPeer", dn=PEER, addr="10.255.0.1/32", asn="65010"),
            _mo("bgpPeerEntry",
                dn=f"{PEER}/ent-[10.255.0.1]",
                addr="10.255.0.1", operSt="established", type="ebgp"),
        ],
    },
}  # fmt: skip
FABRIC = {"fabricNode": [_mo("fabricNode", id="101", name="leaf-101")]}


def test_aci_l3out_border_leafs_und_routen():
    inv = aci.normalize(EXPORT, FABRIC, BORDER)
    l3 = {x.name: x for x in inv.l3outs}
    out = l3["L3O_inet"]
    assert (out.tenant, out.vrf, out.ext_epgs) == ("TE", "TE:VRF1", ["EN_inet"])
    assert [(n["node"], n["router_id"]) for n in out.nodes] == [
        ("101", "10.255.255.1"),
        ("102", "10.255.255.2"),
    ]
    assert out.nodes[0]["static_routes"] == [
        {"prefix": "0.0.0.0/0", "pref": "1", "next_hops": ["10.255.0.1"]}
    ]
    (path,) = out.paths
    assert (path["nodes"], path["interface"], path["kind"], path["vlan"]) == (
        ["101", "102"],
        "vpc_fw",
        "ext-svi",
        3001,
    )
    assert path["ips"] == [
        {"side": "A", "ip": "10.255.0.2/29"},
        {"side": "B", "ip": "10.255.0.3/29"},
    ]
    assert out.peers == [{"ip": "10.255.0.1", "asn": 65010, "path": path["path"]}]
    assert l3["L3O_shared"].vrf == "common:VRF_s"  # VRF nicht im eigenen Tenant
    # Route Control (0.113.0): erzwungen je Richtung, Subnetze mit export-/import-rtctrl
    assert out.route_control == {
        "enforce": ["export", "import"],
        "export": [{"prefix": "10.20.0.0/16", "epg": "EN_inet"}],
        "import": [{"prefix": "0.0.0.0/0", "epg": "EN_inet", "aggregate": True}],
    }
    assert l3["L3O_shared"].route_control == {"enforce": ["export"]}  # Vorgabe des APIC
    assert BGP_PW not in json.dumps(inv.model_dump())

    assert aci.border_contexts(inv.l3outs) == [("1", "101", "TE:VRF1"), ("1", "102", "TE:VRF1")]
    routes = {r.prefix: r for r in inv.routes}
    default = routes["0.0.0.0/0"]
    assert (default.node, default.context, default.protocol, default.distance) == (
        "leaf-101",
        "TE:VRF1",
        "bgp",
        20,
    )
    assert [(h.ip, h.interface) for h in default.next_hops] == [
        ("10.255.0.1", "vlan5"),
        ("10.255.0.9", "vlan6"),
    ]  # ECMP
    assert routes["10.20.0.0/24"].protocol == "connected"
    (peer,) = inv.peers
    assert (peer.node, peer.peer_ip, peer.remote_as, peer.state) == (
        "leaf-101",
        "10.255.0.1",
        65010,
        "established",
    )


def test_apic_border_routing_nur_lesend_und_fehlende_rechte():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200, json={"imdata": [{"aaaLogin": {"attributes": {"token": "x"}}}]}
            )
        seen.append((request.url.path, request.url.params.get("target-subtree-class")))
        if "uribv6" in request.url.path:
            return httpx.Response(403, json={"imdata": []})
        return httpx.Response(200, json={"imdata": BORDER[("1", "101", "TE:VRF1")]["rib4"]})

    client = ApicClient(
        "https://apic", "ro", "pw", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with client:
        data = client.border_routing([("1", "101", "TE:VRF1")])
    assert seen[0] == (
        "/api/node/mo/topology/pod-1/node-101/sys/uribv4/dom-TE:VRF1.json",
        "uribv4Route,uribv4Nexthop",
    )
    entry = data[("1", "101", "TE:VRF1")]
    assert entry["missing"] == ["Routing-Tabelle IPv6 (HTTP 403)"] and entry["rib6"] == []
    assert len(entry["rib4"]) == 5


def test_fortigate_vrf_und_management_getrennt():
    """Dev-Befund 0.17.0: die Default-Route der Management-VRF lief als ECMP mit der
    Daten-Default-Route zusammen - jetzt je VRF, Management-Ports gekennzeichnet."""
    interfaces = [
        {"name": "wan1", "vrf": 0},
        {"name": "mgmt", "vrf": 10, "dedicated-to": "management"},
        {"name": "port9", "vrf": 10, "secondaryip": [{"ip": "10.9.0.2 255.255.255.0"}]},
    ]
    rows = [
        {"ip_mask": "0.0.0.0/0", "type": "static", "distance": 10, "gateway": "198.51.100.1",
         "interface": "wan1", "vrf": 0},
        {"ip_mask": "0.0.0.0/0", "type": "static", "distance": 10, "gateway": "10.99.0.1",
         "interface": "mgmt"},  # ohne vrf in der Antwort: VRF vom Interface
        {"ip_mask": "10.9.0.0/24", "type": "connect", "interface": "port9", "vrf": 10},
    ]  # fmt: skip
    routes = fgt.rib(rows, "fw-01", "root", fgt.ports(interfaces))
    by = {(r.prefix, r.attrs.get("vrf", 0)): r for r in routes}
    assert len(routes) == 3  # keine ECMP aus zwei VRFs
    assert by[("0.0.0.0/0", 0)].next_hops[0].ip == "198.51.100.1"
    assert "mgmt" not in by[("0.0.0.0/0", 0)].attrs
    assert by[("0.0.0.0/0", 10)].attrs == {"vrf": 10, "mgmt": True}
    assert by[("10.9.0.0/24", 10)].protocol == "connected"
    static = fgt.static([{"dst": "0.0.0.0 0.0.0.0", "device": "mgmt", "gateway": "10.99.0.1"}],
                        "fw-01", "root", fgt.ports(interfaces))  # fmt: skip
    assert static[0].attrs == {"vrf": 10, "mgmt": True}

    from collectors.fortigate import normalize as norm

    inv = norm.normalize_inventory(
        [{"name": "port9", "ip": "10.8.0.1 255.255.255.0", **interfaces[2]}], [], {}, "fw-01"
    )
    assert inv.interfaces[0].ips == ["10.8.0.1/24", "10.9.0.2/24"]  # Sekundaer-IP dabei
