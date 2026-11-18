# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Gegenprüfung (Phase 3, Fable) der Weg-Suche (ADR 0070) und der Regel-Zuordnung (ADR 0071).

Unabhängig von Phase 1 und 2 geprüft: tut der Code, was die ADRs behaupten, und entspricht es dem
echten Gerät (Herstellerdoku; Belege im Bericht der Prüfung, hier je Test die Kurzfassung)?

- **Stichproben S1–S3** des Anwenders als Muster (anonymisiert): bestanden = wie echt.
- **Abweichungen P-1 … P-10** als `xfail(strict=True)`: jeder Test verlangt das Verhalten des
  echten Geräts; wird eine Abweichung korrigiert, schlägt der Test als „unerwartet bestanden“ an
  und die Markierung kann weg. Zu P-6, P-7 und P-10 liest der Collector die Einstellung noch
  nicht bzw. wertet der Orchestrator sie nicht aus - die Tests nennen den vorgeschlagenen
  Schlüssel in `topo_context.settings` (wie `strict_src_check` für `strict-src-check`).

Beispiele anonymisiert: RFC 1918, Dokumentationsnetze (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24, 2001:db8::/32), „Kunde A“.
"""

import copy

import pytest
import test_aci_check as ac
import test_aci_relevance as ar
import test_fable_regeln as fr
import test_fable_wege as fw
import test_paths as tp
import test_policy_check as pc
from test_fable_wege import (
    REACHED,
    _active,
    _ctx,
    _firewall,
    _if,
    _l3,
    _labels,
    _local,
    _r,
    _routers,
    _tenant,
    _trace,
)

# gelesene Einstellungen des VDOMs (`system settings`): Reverse Path nach der Vorgabe, Policy-NAT
READ = {"asymroute": False, "strict_src_check": False, "central_nat": False}


# -- Bausteine der Stichproben: ACI-Tenants mit L3Out zur Firewall ----------------------------


def _vrf_routes(vrf, routes):
    return [
        tp._r(leaf, vrf, prefix, hops, proto, distance=dist)
        for leaf in ("leaf-101", "leaf-102")
        for prefix, hops, proto, dist in routes
    ]


def _l3out(tenant, name, vrf, vlan, a, b, peer, ext):
    return {"tenant": tenant, "name": name, "dn": f"uni/tn-{tenant}/out-{name}", "vrf": vrf,
            "nodes": [{"pod": "1", "node": "101"}, {"pod": "1", "node": "102"}],
            "paths": [{"path": f"topology/pod-1/protpaths-101-102/pathep-[vpc_{name}]",
                       "nodes": ["101", "102"], "interface": f"vpc_{name}", "kind": "ext-svi",
                       "vlan": vlan, "ips": [{"side": "A", "ip": a}, {"side": "B", "ip": b}]}],
            "peers": [{"ip": peer, "asn": 65000}], "ext_epgs": ext}  # fmt: skip


def _epg(tenant, name, gw):
    return {"name": name, "dn": f"uni/tn-{tenant}/ap-AP1/epg-{name}", "tenant": tenant,
            "ap": "AP1", "bd": f"BD_{name}", "vrf": "VRF1", "bd_subnets": [gw]}  # fmt: skip


def _ext(tenant, name, l3out, subnets):
    return {"name": name, "kind": "external", "dn": f"uni/tn-{tenant}/out-{l3out}/instP-{name}",
            "tenant": tenant, "l3out": l3out, "vrf": "VRF1", "subnets": subnets,
            "subnet_scopes": {}}  # fmt: skip


def _ct(tenant, name, src, dst, services):
    ct = f"uni/tn-{tenant}/brc-{name}"
    return {"source_platform": "cisco_aci", "external_id": f"{ct}/SJ_1", "name": name,
            "action": "allow", "sources": [f"epg:{src}"], "destinations": [f"epg:{dst}"],
            "services": services, "aci_scope": "context",
            "raw": {"dn": ct, "name": name, "scope": "context"}}  # fmt: skip


def _subnet(tenant, prefix, gw, epg):
    return {"prefix": prefix, "gateway": gw, "vrf": f"{tenant}:VRF1", "context": "epg",
            "context_name": epg, "aci_tenant": tenant, "ap": "AP1", "epg": epg}  # fmt: skip


S1_APIC = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "leaf-101", "role": "leaf", "node_id": "101"},
                    {"name": "leaf-102", "role": "leaf", "node_id": "102"}],
        "subnets": [_subnet("T1", "10.41.6.0/24", "10.41.6.1/24", "EPG_app1"),
                    _subnet("T2", "10.42.8.0/24", "10.42.8.1/24", "EPG_app2")],
    },
    "routing": {
        "contexts": [{"node": leaf, "context": vrf, "kind": "vrf"}
                     for leaf in ("leaf-101", "leaf-102") for vrf in ("T1:VRF1", "T2:VRF1")],
        "routes": [
            *_vrf_routes("T1:VRF1", [
                ("10.41.6.0/24", [{"ip": "10.0.72.64"}], "static", 1),
                ("10.42.0.0/16", [{"ip": "10.250.1.1", "interface": "vlan-3001"}], "bgp", 20),
                ("10.90.0.0/16", [{"ip": "10.250.1.1", "interface": "vlan-3001"}], "bgp", 20),
            ]),
            *_vrf_routes("T2:VRF1", [
                ("10.42.8.0/24", [{"ip": "10.0.72.65"}], "static", 1),
                ("10.41.0.0/16", [{"ip": "10.250.2.1", "interface": "vlan-3002"}], "bgp", 20),
            ]),
        ],
        "l3outs": [
            _l3out("T1", "L3O_fw1", "T1:VRF1", 3001, "10.250.1.2/29", "10.250.1.3/29",
                   "10.250.1.1", ["EN_t2", "EN_wan"]),
            _l3out("T2", "L3O_fw2", "T2:VRF1", 3002, "10.250.2.2/29", "10.250.2.3/29",
                   "10.250.2.1", ["EN_t1"]),
        ],
    },
    "epgs": [
        _epg("T1", "EPG_app1", "10.41.6.1/24"),
        _epg("T2", "EPG_app2", "10.42.8.1/24"),
        _ext("T1", "EN_t2", "L3O_fw1", ["10.42.0.0/16"]),
        _ext("T1", "EN_wan", "L3O_fw1", ["10.90.0.0/16"]),
        _ext("T2", "EN_t1", "L3O_fw2", ["10.41.0.0/16"]),
    ],
    "rules": [
        _ct("T2", "CT_to_t1", "uni/tn-T2/ap-AP1/epg-EPG_app2", "uni/tn-T2/out-L3O_fw2/instP-EN_t1",
            ["tcp/443"]),
        _ct("T1", "CT_from_t2", "uni/tn-T1/out-L3O_fw1/instP-EN_t2",
            "uni/tn-T1/ap-AP1/epg-EPG_app1", ["tcp/443"]),
        _ct("T1", "CT_to_wan", "uni/tn-T1/ap-AP1/epg-EPG_app1",
            "uni/tn-T1/out-L3O_fw1/instP-EN_wan", ["tcp/443"]),
    ],
}  # fmt: skip

S1_RULE = pc._policy(1, "t2-to-t1-web", "any", "any", ["10.42.8.0/24"], ["10.41.6.25"],
                     ["tcp/443"])  # fmt: skip


def _fw_a(extra_ifs=(), extra_routes=(), rules=(S1_RULE,)):
    return pc._fw("fw-a", list(rules), [
        {"name": "t1", "ips": ["10.250.1.1/29"], "zone": "tenant-1", "vdom": "root"},
        {"name": "t2", "ips": ["10.250.2.1/29"], "zone": "tenant-2", "vdom": "root"},
        {"name": "to-fw", "ips": ["10.250.9.1/30"], "zone": "transit", "vdom": "root"},
        *extra_ifs,
    ], [
        {"context": "root", **r} for r in (
            {"prefix": "10.250.1.0/29", "protocol": "connected",
             "next_hops": [{"interface": "t1"}]},
            {"prefix": "10.250.2.0/29", "protocol": "connected",
             "next_hops": [{"interface": "t2"}]},
            {"prefix": "10.250.9.0/30", "protocol": "connected",
             "next_hops": [{"interface": "to-fw"}]},
            {"prefix": "10.41.0.0/16", "protocol": "bgp", "distance": 20,
             "next_hops": [{"ip": "10.250.1.2", "interface": "t1"}]},
            {"prefix": "10.42.0.0/16", "protocol": "bgp", "distance": 20,
             "next_hops": [{"ip": "10.250.2.2", "interface": "t2"}]},
            *extra_routes,
        )
    ])  # fmt: skip


S1_FW_VPN = pc._fw("fw-vpn", [S1_RULE], [
    {"name": "to-fw-a", "ips": ["10.250.9.2/30"], "zone": "transit", "vdom": "root"},
], [
    {"context": "root", "prefix": "10.250.9.0/30", "protocol": "connected",
     "next_hops": [{"interface": "to-fw-a"}]},
    {"context": "root", "prefix": "10.40.0.0/14", "protocol": "static",
     "next_hops": [{"ip": "10.250.9.1", "interface": "to-fw-a"}]},
])  # fmt: skip


def _seed_all(client, systems) -> tuple[int, dict[str, int]]:
    tid = _tenant(client)
    ids = {}
    for name, platform, result in systems:
        ids[name] = tp._system(client, tid, name, platform)
        tp._deliver(client, tid, ids[name], platform, result)
    return tid, ids


# -- S1: Tenant 2 → ACI → fw-a → ACI → Tenant 1; fw-vpn mit denselben Regeln abseits -----------


def test_s1_weg_ueber_fw_a_und_doku_ohne_fw_vpn(client):
    """Quelle in ACI-Tenant 2, Ziel in Tenant 1, dazwischen der Cluster fw-a. `fw-vpn` hängt am
    Cluster, kennt beide Netze über ihn und hat dieselbe Regel - sie liegt nicht auf dem Weg:
    der Weg geht VRF T2 → fw-a → VRF T1 (Contracts und Policy erlauben), die Doku des Servers
    zeigt nur die Regel von fw-a."""
    tid, ids = _seed_all(client, [("apic-01", "cisco_aci", S1_APIC),
                                  ("fw-a", "fortigate", _fw_a()),
                                  ("fw-vpn", "fortigate", S1_FW_VPN)])  # fmt: skip
    out = _trace(client, tid, src="10.42.8.14", dst="10.41.6.25", service="tcp/443")
    (path,) = _active(out)
    assert _labels(path) == ["apic-01 · VRF T2:VRF1", "fw-a", "apic-01 · VRF T1:VRF1"]
    assert path["end"]["kind"] == "fabric" and path["policy"]["verdict"] == "allow"
    assert [h["rules"]["verdict"] for h in path["hops"]] == ["allow", "allow", "allow"]
    assert "uncertain" not in out
    doc = fr._doc(client, tid, "10.41.6.25")
    by_id = {r["id"]: r for r in doc["rules"]}
    assert [by_id[h["rule_id"]]["source_system_id"] for h in doc["hits"]] == [ids["fw-a"]]
    counted = {x["key"]: x["count"] for x in doc["summary"]["filtered"]}
    assert counted.get("off_path_specific") == 1  # die Regel von fw-vpn: gezählt, nicht gezeigt


# -- S2: Tenant 1 → fw-a → MPLS-Router (nicht gelesen) → anderer Standort ----------------------

S2_RULE = pc._policy(2, "t1-to-standort", "any", "any", ["10.41.6.30"], ["10.90.7.25"],
                     ["tcp/443"])  # fmt: skip
S2_MPLS = [{"ip": "10.250.5.2", "interface": "mpls"}, {"ip": "10.250.5.3", "interface": "mpls"}]


def _s2(client):
    fwa = _fw_a(
        extra_ifs=[{"name": "mpls", "ips": ["10.250.5.1/29"], "zone": "mpls", "vdom": "root"}],
        extra_routes=[
            {"prefix": "10.250.5.0/29", "protocol": "connected",
             "next_hops": [{"interface": "mpls"}]},
            {"prefix": "10.90.0.0/16", "protocol": "bgp", "distance": 20, "next_hops": S2_MPLS},
            {"prefix": "0.0.0.0/0", "protocol": "static",
             "next_hops": [{"ip": "10.250.9.2", "interface": "to-fw"}]},
        ], rules=(S1_RULE, S2_RULE))  # fmt: skip
    inet = pc._fw("fw-inet", [S2_RULE], [
        {"name": "to-fw-a", "ips": ["10.250.9.2/30"], "zone": "transit", "vdom": "root"},
        {"name": "wan1", "ips": ["198.51.100.2/30"], "zone": "internet", "vdom": "root"},
    ], [
        {"context": "root", "prefix": "10.250.9.0/30", "protocol": "connected",
         "next_hops": [{"interface": "to-fw-a"}]},
        {"context": "root", "prefix": "198.51.100.0/30", "protocol": "connected",
         "next_hops": [{"interface": "wan1"}]},
        {"context": "root", "prefix": "10.40.0.0/14", "protocol": "static",
         "next_hops": [{"ip": "10.250.9.1", "interface": "to-fw-a"}]},
        {"context": "root", "prefix": "0.0.0.0/0", "protocol": "static",
         "next_hops": [{"ip": "198.51.100.1", "interface": "wan1"}]},
    ])  # fmt: skip
    return _seed_all(
        client,
        [
            ("apic-01", "cisco_aci", S1_APIC),
            ("fw-a", "fortigate", fwa),
            ("fw-inet", "fortigate", inet),
        ],
    )


def test_s2_weg_endet_am_mpls_uebergang_nicht_an_der_internet_firewall(client, session_factory):
    """fw-a kennt den anderen Standort per BGP über zwei MPLS-Router (nicht gelesen), fw-inet
    nur über die Default-Route. Der Weg geht VRF T1 → fw-a und endet sichtbar am MPLS-Übergang
    (nicht erreicht) - nie über fw-inet, auch nicht in den Verbindungen der Applikation."""
    tid, _ids = _s2(client)
    out = _trace(client, tid, src="10.41.6.30", dst="10.90.7.25", service="tcp/443")
    found = _active(out)
    assert all(_labels(p) == ["apic-01 · VRF T1:VRF1", "fw-a"] for p in found)
    assert sorted(p["hops"][-1]["egress"]["next_hop"] for p in found) == ["10.250.5.2",
                                                                          "10.250.5.3"]  # fmt: skip
    assert {p["end"]["kind"] for p in found} == {"unknown"}
    assert all(p["end"]["kind"] not in REACHED for p in found)
    res, _start = fw._conn(session_factory, tid, "10.41.6.30", "10.90.7.25", [])
    assert res["paths"] and all("fw-inet" not in _labels(p) for p in res["paths"])


@pytest.mark.xfail(
    strict=True,
    reason="P-4: endet der Weg an einer nicht gelesenen Grenze, "
    "bleiben in der Doku die Regeln aller Firewalls stehen (nur markiert „Weg "
    "nicht geprüft“) - auch die der Internet-Firewall abseits des Wegs; die "
    "Firewall auf dem belegten Teil des Wegs ist ebenfalls „nicht geprüft“",
)
def test_p4_s2_doku_zeigt_nicht_die_regel_der_internet_firewall(client):
    """S2 in der Doku: der belegte Teil des Wegs (VRF T1 → fw-a → MPLS) zeigt, dass fw-a auf dem
    Weg liegt und fw-inet nicht (sie kennt das private Ziel nur über die Default-Route ins
    Internet). Erwartet: die Regel von fw-a, nicht die gleichlautende von fw-inet."""
    tid, ids = _s2(client)
    doc = fr._doc(client, tid, "10.41.6.30")
    by_id = {r["id"]: r for r in doc["rules"]}
    systems = sorted(by_id[h["rule_id"]]["source_system_id"] for h in doc["hits"])
    assert systems == [ids["fw-a"]], systems


# -- S3: Internet → WAN-Router (HSRP) → Firewall-VIP → ACI → NSX ALB → ACI → Server ------------

S3_LB = "10.30.0.50"
S3_APIC = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "leaf-101", "role": "leaf", "node_id": "101"},
                    {"name": "leaf-102", "role": "leaf", "node_id": "102"}],
        "subnets": [_subnet("T1", "10.30.0.0/24", "10.30.0.1/24", "EPG_lb"),
                    _subnet("T1", "10.30.10.0/24", "10.30.10.1/24", "EPG_srv1"),
                    _subnet("T1", "10.30.11.0/24", "10.30.11.1/24", "EPG_srv2")],
    },
    "routing": {
        "contexts": [{"node": leaf, "context": "T1:VRF1", "kind": "vrf"}
                     for leaf in ("leaf-101", "leaf-102")],
        "routes": _vrf_routes("T1:VRF1", [
            ("10.30.0.0/24", [{"ip": "10.0.72.64"}], "static", 1),
            ("10.30.10.0/24", [{"ip": "10.0.72.64"}], "static", 1),
            ("10.30.11.0/24", [{"ip": "10.0.72.64"}], "static", 1),
            ("0.0.0.0/0", [{"ip": "10.255.0.1", "interface": "vlan-3001"}], "bgp", 20),
        ]),
        "l3outs": [_l3out("T1", "L3O_fw", "T1:VRF1", 3001, "10.255.0.2/29", "10.255.0.3/29",
                          "10.255.0.1", ["EN_inet"])],
    },
    "epgs": [
        _epg("T1", "EPG_lb", "10.30.0.1/24"),
        _epg("T1", "EPG_srv1", "10.30.10.1/24"),
        _epg("T1", "EPG_srv2", "10.30.11.1/24"),
        _ext("T1", "EN_inet", "L3O_fw", ["0.0.0.0/0"]),
    ],
    "rules": [
        _ct("T1", "CT_inet_lb", "uni/tn-T1/out-L3O_fw/instP-EN_inet",
            "uni/tn-T1/ap-AP1/epg-EPG_lb", ["tcp/443", "tcp/80"]),
        _ct("T1", "CT_lb_srv1", "uni/tn-T1/ap-AP1/epg-EPG_lb", "uni/tn-T1/ap-AP1/epg-EPG_srv1",
            ["tcp/8080"]),
        _ct("T1", "CT_lb_srv2", "uni/tn-T1/ap-AP1/epg-EPG_lb", "uni/tn-T1/ap-AP1/epg-EPG_srv2",
            ["tcp/8080"]),
    ],
}  # fmt: skip


def _s3_vip(name, port):
    return {"source_platform": "fortigate", "external_id": name, "name": name,
            "nat_type": "dnat_vip", "ext_ip": "203.0.113.10", "mapped_ip": S3_LB,
            "portforward": True, "protocol": "tcp", "ext_port": port, "mapped_port": port,
            "vdom": "root", "raw": {"extintf": "wan1"}}  # fmt: skip


def _s3_vs(name, servers, port, **attrs):
    return {"source_platform": "avi", "external_id": f"admin|{name}|{S3_LB}|{port}",
            "name": name, "nat_type": "dnat_vip", "ext_ip": S3_LB, "ext_port": str(port),
            "mapped_ip": servers[0] if servers else None,
            "mapped_port": "8080" if servers else None, "protocol": "tcp", "portforward": True,
            "vip_type": "server-load-balance", "family": "v4",
            "targets": [{"ip": ip, "port": 8080, "status": "active", "pool": "pool-shop"}
                        for ip in servers],
            "attrs": {"platform": "avi", "tenant": "admin", "snat": "service_engine",
                      "oper_status": "OPER_UP", **attrs},
            "raw": {"tenant": "admin", "virtual_service": name}}  # fmt: skip


def _s3(client, services=None, apic=None):
    tid = _tenant(client)

    def wan(name, own, state):
        return [
            _l3(name, "198.51.100.0/29", [{"interface": "Gi0/1"}], "connected", distance=0),
            _local(name, own, "Gi0/1"),
            _l3(name, "198.51.100.1/32", [{"interface": "Gi0/1"}], "local", distance=0,
                attrs={"virtual": True, "state": state}),
            _l3(name, "203.0.113.0/28", [{"ip": "198.51.100.4", "interface": "Gi0/1"}],
                distance=1),
            _l3(name, "0.0.0.0/0", [{"ip": "192.0.2.1", "interface": "Gi0/0"}], "bgp",
                distance=20),
        ]  # fmt: skip

    rid = _routers(client, tid, ["rt-wan-01", "rt-wan-02"],
                   wan("rt-wan-01", "198.51.100.2", "active")
                   + wan("rt-wan-02", "198.51.100.3", "standby"), name="WAN")  # fmt: skip
    rules = [
        pc._policy(1, "inet-to-shop", "wan1", "to-aci", ["all"],
                   ["vip:V-EXT_shop-https=203.0.113.10"], ["tcp/443"]),
        pc._policy(2, "inet-to-shop-http", "wan1", "to-aci", ["all"],
                   ["vip:V-EXT_shop-http=203.0.113.10"], ["tcp/80"]),
    ]  # fmt: skip
    fwr = pc._fw("fw-01", rules, [
        {"name": "wan1", "ips": ["198.51.100.4/29"], "zone": "internet", "vdom": "root"},
        {"name": "to-aci", "ips": ["10.255.0.1/29"], "zone": "aci", "vdom": "root"},
    ], [
        {"context": "root", "prefix": "198.51.100.0/29", "protocol": "connected",
         "next_hops": [{"interface": "wan1"}]},
        {"context": "root", "prefix": "10.255.0.0/29", "protocol": "connected",
         "next_hops": [{"interface": "to-aci"}]},
        {"context": "root", "prefix": "10.30.0.0/16", "protocol": "bgp", "distance": 20,
         "next_hops": [{"ip": "10.255.0.2", "interface": "to-aci"},
                       {"ip": "10.255.0.3", "interface": "to-aci"}]},
        {"context": "root", "prefix": "0.0.0.0/0", "protocol": "static",
         "next_hops": [{"ip": "198.51.100.1", "interface": "wan1"}]},
    ], nat=[_s3_vip("V-EXT_shop-https", "443"), _s3_vip("V-EXT_shop-http", "80")])  # fmt: skip
    fwr["routing"]["contexts"] = _ctx("fw-01", **READ)
    for name, platform, result in (
        ("fw-01", "fortigate", fwr),
        ("apic-01", "cisco_aci", apic or S3_APIC),
    ):
        tp._deliver(client, tid, tp._system(client, tid, name, platform), platform, result)
    avi = fw._system(client, tid, "alb-01", "avi", username="ro", password="x")
    services = services or [
        _s3_vs("vs-shop", ["10.30.10.20", "10.30.11.20"], 443),
        _s3_vs("vs-shop", [], 80, redirect="https"),
    ]
    fw._deliver(client, tid, avi, "avi", {"status": "completed", "nat_rules": services})
    return tid, rid


def _s3_trace(client, tid, rid, service):
    return _trace(client, tid, src="192.0.2.50", dst="203.0.113.10", start=f"{rid}|rt-wan-01",
                  service=service)  # fmt: skip


def test_s3_dnat_load_balancer_und_umleitung(client, session_factory):
    """Internet → rt-wan → fw-01 (VIP 203.0.113.10 → VIP des Load Balancers) → ACI → Virtual
    Service → Server (443 → 443 → 8080); Port 80 endet am Load Balancer (Umleitung). Von aussen
    beginnt der Weg an beiden WAN-Routern (HSRP gilt nur für die Richtung nach aussen), der
    Rückweg geht über dieselbe Firewall."""
    tid, rid = _s3(client)
    out = _s3_trace(client, tid, rid, "tcp/443")
    found = _active(out)
    assert found and "uncertain" not in out
    for p in found:
        assert _labels(p) == ["rt-wan-01", "fw-01", "apic-01 · VRF T1:VRF1",
                              "apic-01 · VRF T1:VRF1"]  # fmt: skip
        assert p["hops"][1]["nat"]["name"] == "V-EXT_shop-https"
        assert p["hops"][2]["lb"]["name"] == "vs-shop"
        assert p["hops"][2]["lb"]["to"] == ["10.30.10.20", "10.30.11.20"]
        assert p["end"]["kind"] == "fabric" and p["return"]["state"] == "symmetric"
    assert [x["service"] for x in out["service_chain"]] == ["tcp/443", "tcp/443", "tcp/8080"]
    http = _s3_trace(client, tid, rid, "tcp/80")
    for p in _active(http):
        assert _labels(p) == ["rt-wan-01", "fw-01", "apic-01 · VRF T1:VRF1"]
        assert p["hops"][1]["nat"]["name"] == "V-EXT_shop-http"
        assert p["end"]["kind"] == "redirect" and p["hops"][2]["lb"]["redirect"] == "https"
    res, start = fw._conn(session_factory, tid, "192.0.2.50", "203.0.113.10", [])
    assert start == "rt-wan-01, rt-wan-02"
    assert {_labels(p)[0] for p in res["paths"]} == {"rt-wan-01", "rt-wan-02"}


@pytest.mark.xfail(
    strict=True,
    reason="P-5: Server des Pools in verschiedenen EPGs derselben "
    "ACI-VRF - der Weg zum zweiten Server fällt still weg (`paths._unique`: "
    "gleiche Hops ohne Ausgang gelten als derselbe Weg), nicht gezählt",
)
def test_p5_s3_weg_zu_jedem_server_des_pools_im_fabric(client):
    """S3: der Load Balancer verteilt auf alle Mitglieder - der Weg (mit den Contracts je EPG)
    ist bis zu jedem Server zu prüfen, auch wenn beide in derselben VRF liegen."""
    tid, rid = _s3(client)
    out = _s3_trace(client, tid, rid, "tcp/443")
    servers = {t for p in _active(out) for h in p["hops"]
               for t in (h.get("lb") or {}).get("targets", [])}  # fmt: skip
    assert servers == {"10.30.10.20", "10.30.11.20"}, servers


@pytest.mark.xfail(
    strict=True,
    reason="P-5: der Contract zum zweiten Server (andere EPG) wird "
    "nie geprüft - fehlt er, steht der Fluss trotzdem als „erlaubt“ da",
)
def test_p5_s3_fehlender_contract_zum_zweiten_server_ist_nicht_erlaubt(client):
    apic = copy.deepcopy(S3_APIC)
    apic["rules"] = [r for r in apic["rules"] if r["name"] != "CT_lb_srv2"]
    tid, rid = _s3(client, apic=apic)
    out = _s3_trace(client, tid, rid, "tcp/443")
    assert any(p["policy"]["verdict"] != "allow" for p in _active(out))


# -- P-1: Reverse Path - Route gleicher Distanz mit höherer Priorität --------------------------


def _wan_router(client, tid):
    _routers(client, tid, ["rt-wan2"], [
        _l3("rt-wan2", "203.0.113.0/24", [{"interface": "Gi0/1"}], "connected", distance=0),
        _local("rt-wan2", "203.0.113.1", "Gi0/1"),
        _l3("rt-wan2", "198.51.100.4/30", [{"interface": "Gi0/2"}], "connected", distance=0),
        _local("rt-wan2", "198.51.100.5", "Gi0/2"),
        _l3("rt-wan2", "10.20.0.0/24", [{"ip": "198.51.100.6", "interface": "Gi0/2"}], distance=1),
    ])  # fmt: skip


def _dual_wan(client, tid, second: dict):
    """fw-01 mit zwei Default-Routen gleicher Distanz: wan1 (Priorität 1) und wan2 (`second`)."""
    _wan_router(client, tid)
    _firewall(client, tid, "fw-01", [
        _if("fw-01", "lan", "10.20.0.1/24", "lan"),
        _if("fw-01", "wan1", "198.51.100.2/30", "internet"),
        _if("fw-01", "wan2", "198.51.100.6/30", "internet"),
    ], [
        _r("fw-01", "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
        _r("fw-01", "root", "198.51.100.0/30", [{"interface": "wan1"}], "connected"),
        _r("fw-01", "root", "198.51.100.4/30", [{"interface": "wan2"}], "connected"),
        _r("fw-01", "root", "0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan1"}],
           distance=10, attrs={"priority": 1}),
        _r("fw-01", "root", "0.0.0.0/0", [{"ip": "198.51.100.5", "interface": "wan2"}],
           distance=10, **second),
    ], contexts=_ctx("fw-01", **READ))  # fmt: skip
    (path,) = _active(_trace(client, tid, src="203.0.113.50", dst="10.20.0.5"))
    assert _labels(path) == ["rt-wan2", "fw-01"]
    assert path["hops"][1]["ingress"]["interface"] == "wan2"
    return path


def test_reverse_path_besteht_mit_ecmp_ueber_den_eingang(client):
    """Gegenprobe (richtig): zweite Default-Route gleicher Distanz und Priorität (ECMP) - der
    Reverse Path am Eingang wan2 besteht."""
    path = _dual_wan(client, _tenant(client), {"attrs": {"priority": 1}})
    assert path["end"]["kind"] == "connected", path["end"]


@pytest.mark.xfail(
    strict=True,
    reason="P-1: Reverse Path kennt nur die beste Route - eine Route "
    "gleicher Distanz mit höherer Priorität (vom Collector `best=False`) steht "
    "auf der FortiGate in der Routing-Tabelle und erfüllt den Reverse Path "
    "(feasible); die Weg-Suche meldet „verworfen“",
)
def test_p1_reverse_path_besteht_mit_route_hoeherer_prioritaet(client):
    """Dual-WAN wie von Fortinet empfohlen: zwei Default-Routen gleicher Distanz, wan2 mit
    höherer Priorität (nicht bevorzugt, aber aktiv). Verkehr von aussen an wan2 besteht den
    Reverse Path (feasible path: mindestens eine aktive Route zur Quelle über den Eingang)."""
    path = _dual_wan(client, _tenant(client), {"attrs": {"priority": 10}, "best": False})
    assert path["end"]["kind"] == "connected", path["end"]


# -- P-2: VIP mit Filter (Quelle bzw. Eingang) -------------------------------------------------


def _vip_fw(client, tid, raw: dict) -> int:
    vip = {"source_platform": "fortigate", "external_id": "V-EXT_web", "name": "V-EXT_web",
           "nat_type": "dnat_vip", "ext_ip": "198.51.100.80", "mapped_ip": "10.1.1.10",
           "vdom": "root", "raw": raw}  # fmt: skip
    return _firewall(client, tid, "fw-01", [
        _if("fw-01", "lan", "10.20.0.1/24", "lan"),
        _if("fw-01", "dmz1", "10.1.1.1/24", "dmz"),
        _if("fw-01", "wan2", "198.51.100.6/30", "internet"),
    ], [
        _r("fw-01", "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
        _r("fw-01", "root", "10.1.1.0/24", [{"interface": "dmz1"}], "connected"),
        _r("fw-01", "root", "198.51.100.4/30", [{"interface": "wan2"}], "connected"),
        _r("fw-01", "root", "0.0.0.0/0", [{"ip": "198.51.100.5", "interface": "wan2"}]),
    ], nat_rules=[vip],
        rules=[fw._policy(1, "wan2", "dmz1", "all", "vip:V-EXT_web=198.51.100.80")],
        contexts=_ctx("fw-01", **READ))  # fmt: skip


def test_vip_mit_quell_filter_schreibt_fuer_passende_quelle_um(client):
    """Gegenprobe (richtig): die Quelle liegt im `src-filter` der VIP - DNAT."""
    tid = _tenant(client)
    sid = _vip_fw(client, tid, {"extintf": "wan2", "src-filter": [{"range": "192.0.2.0/24"}]})
    out = _trace(client, tid, src="192.0.2.7", dst="198.51.100.80", start=f"{sid}|root",
                 service="tcp/443")  # fmt: skip
    assert _active(out)[0]["hops"][0]["nat"]["name"] == "V-EXT_web"


@pytest.mark.xfail(
    strict=True,
    reason="P-2: `src-filter` der VIP wird nicht ausgewertet - die "
    "Weg-Suche schreibt auch für Quellen ausserhalb des Filters um (DNAT)",
)
def test_p2_vip_mit_quell_filter_kein_dnat_fuer_andere_quellen(client):
    """FortiOS: mit Quell-Filter trifft der Verkehr die VIP nur, wenn auch die Quelle passt."""
    tid = _tenant(client)
    sid = _vip_fw(client, tid, {"extintf": "wan2", "src-filter": [{"range": "192.0.2.0/24"}]})
    out = _trace(client, tid, src="203.0.113.50", dst="198.51.100.80", start=f"{sid}|root",
                 service="tcp/443")  # fmt: skip
    hop = _active(out)[0]["hops"][0]
    assert not hop.get("nat"), hop.get("nat")


@pytest.mark.xfail(
    strict=True,
    reason="P-2: `srcintf-filter` der VIP wird nicht ausgewertet - "
    "DNAT auch für Verkehr, der an einem anderen Interface ankommt",
)
def test_p2_vip_mit_eingangs_filter_kein_dnat_an_anderem_eingang(client):
    """VIP mit `extintf any` und `srcintf-filter wan2`: Verkehr aus dem LAN zur externen Adresse
    wird nicht umgeschrieben (er geht zur Adresse selbst, hier Richtung Internet)."""
    tid = _tenant(client)
    _vip_fw(client, tid, {"extintf": "any", "srcintf-filter": [{"interface-name": "wan2"}]})
    out = _trace(client, tid, src="10.20.0.5", dst="198.51.100.80", service="tcp/443")
    hop = _active(out)[0]["hops"][0]
    assert hop["ingress"]["interface"] == "lan"
    assert not hop.get("nat"), hop.get("nat")


# -- P-3: ACI Preferred Group - Nichtmitglieder ------------------------------------------------


def _aci_seed(client, extra_rules) -> int:
    apic = copy.deepcopy(ac.APIC)
    apic["rules"] += extra_rules
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=tp.ADMIN).json()["id"]
    for name, platform, result in (("fw-01", "fortigate", ac.FW1), ("apic-01", "cisco_aci", apic)):
        tp._deliver(client, tid, tp._system(client, tid, name, platform), platform, result)
    return tid


def _pref_group(members: list[str]) -> dict:
    refs = [f"epg:{m}" for m in members]
    return {"source_platform": "cisco_aci", "external_id": f"{ac.ANY}/prefgr",
            "name": "Preferred Group VRF1", "action": "allow", "sources": refs,
            "destinations": refs, "services": ["ip/any"], "aci_scope": "context",
            "raw": {"kind": "preferred_group", "dn": ac.ANY,
                    "name": "Preferred Group VRF1"}}  # fmt: skip


def test_aci_vzany_contract_mit_filter_gewinnt_gegen_preferred_group(client):
    """Gegenprobe (richtig): vzAny ↔ vzAny mit einem bestimmten Filter (Priorität 17) gewinnt
    gegen die Sperre für Nichtmitglieder der Preferred Group (18/19)."""
    tid = _aci_seed(client, [_pref_group([ar.WEB]),
                             ac._rule("CT_all", [f"epg:{ac.ANY}"], [f"epg:{ac.ANY}"],
                                      ["tcp/8080"])])  # fmt: skip
    rules, _policy, _out = ac._aci(client, tid, src="10.50.1.10", dst="10.50.2.20",
                                   service="tcp/8080")  # fmt: skip
    assert rules["verdict"] == "allow", rules


@pytest.mark.xfail(
    strict=True,
    reason="P-3: Preferred Group eingeschaltet - die Zoning-Rules, "
    "die Nichtmitglieder sperren (Priorität 18/19), fehlen im Vorrang; ein "
    "Contract vzAny ↔ vzAny mit dem Filter „alles“ (Priorität 21) gilt als "
    "erlaubt, im Fabric gewinnt die Sperre",
)
def test_p3_aci_preferred_group_sperrt_nichtmitglied_vor_vzany_permit_any(client):
    """VRF mit Preferred Group (Mitglied: EPG_web); EPG_db ist kein Mitglied. vzAny bietet und
    nutzt einen Contract mit `ip/any` (Zoning-Rule any_any_any, Priorität 21). Für das
    Nichtmitglied legt das Fabric Sperren mit Priorität 18/19 an - kleinere Zahl gewinnt."""
    tid = _aci_seed(client, [_pref_group([ar.WEB]),
                             ac._rule("CT_all", [f"epg:{ac.ANY}"], [f"epg:{ac.ANY}"],
                                      ["ip/any"])])  # fmt: skip
    rules, _policy, _out = ac._aci(client, tid, src="10.50.1.10", dst="10.50.2.20",
                                   service="tcp/8080")  # fmt: skip
    assert rules["verdict"] in ("deny", "implicit_deny"), rules


# -- P-6 / P-7: Verkehr, den die FortiGate ohne Policy weiterleitet ---------------------------


@pytest.mark.xfail(
    strict=True,
    reason="P-6: `intrazone allow` der Zone wird nicht gelesen "
    "(vorgeschlagen: `settings.intrazone_allow`) - Verkehr zwischen zwei "
    "Interfaces derselben Zone gilt als „implizit gesperrt“",
)
def test_p6_zone_mit_intrazone_allow_braucht_keine_policy(client):
    """FortiOS: mit `set intrazone allow` geht Verkehr zwischen den Interfaces der Zone ohne
    Policy durch."""
    tid = _tenant(client)
    _firewall(client, tid, "fw-01", [
        _if("fw-01", "dmz1", "10.1.1.1/24", "dmz"),
        _if("fw-01", "dmz2", "10.1.2.1/24", "dmz"),
        _if("fw-01", "lan", "10.20.0.1/24", "lan"),
    ], [
        _r("fw-01", "root", "10.1.1.0/24", [{"interface": "dmz1"}], "connected"),
        _r("fw-01", "root", "10.1.2.0/24", [{"interface": "dmz2"}], "connected"),
        _r("fw-01", "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
    ], rules=[fw._policy(1, "lan", "dmz", "all", "all")],
        contexts=_ctx("fw-01", **READ, intrazone_allow=["dmz"]))  # fmt: skip
    (path,) = _active(_trace(client, tid, src="10.1.1.5", dst="10.1.2.5", service="tcp/443"))
    assert path["hops"][0]["rules"]["verdict"] == "allow", path["hops"][0]["rules"]


@pytest.mark.xfail(
    strict=True,
    reason="P-7: `allow-traffic-redirect` (system global) wird nicht "
    "gelesen (vorgeschlagen: `settings.allow_traffic_redirect`) - Verkehr, der "
    "am selben Interface ein- und ausgeht, gilt als „implizit gesperrt“",
)
def test_p7_gleiches_interface_mit_traffic_redirect_braucht_keine_policy(client):
    """FortiOS mit `allow-traffic-redirect enable` (Vorgabe bis 7.4.9/7.6.4): Quelle im Netz des
    Interfaces, Route zum Ziel über dasselbe Interface - weitergeleitet ohne Policy."""
    tid = _tenant(client)
    _firewall(client, tid, "fw-01", [
        _if("fw-01", "lan", "10.20.0.1/24", "lan"),
        _if("fw-01", "dmz1", "10.1.1.1/24", "dmz"),
    ], [
        _r("fw-01", "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
        _r("fw-01", "root", "10.1.1.0/24", [{"interface": "dmz1"}], "connected"),
        _r("fw-01", "root", "10.99.0.0/24", [{"ip": "10.20.0.254", "interface": "lan"}]),
    ], rules=[fw._policy(1, "lan", "dmz", "all", "all")],
        contexts=_ctx("fw-01", **READ, allow_traffic_redirect=True))  # fmt: skip
    (path,) = _active(_trace(client, tid, src="10.20.0.5", dst="10.99.0.5", service="tcp/443"))
    hop = path["hops"][0]
    assert hop["ingress"]["interface"] == hop["egress"]["interface"] == "lan"
    assert hop["rules"]["verdict"] == "allow", hop["rules"]


# -- P-8 / P-9: NSX ALB ------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="P-8: Pool Group - der Weg geht als aktiver Weg auch zu "
    "den Servern des Pools mit tieferer Priorität (Avi: nur, wenn der Pool mit "
    "der höchsten Priorität ausfällt)",
)
def test_p8_pool_group_nur_der_pool_mit_der_hoechsten_prioritaet_ist_aktiv(client):
    vs = fw._vs("vs-shop", ["10.10.0.20", "10.11.0.20"])
    vs["attrs"]["pool_group"] = "pg-shop"
    vs["targets"][0] |= {"priority": "10", "pool": "pool-a"}
    vs["targets"][1] |= {"priority": "5", "pool": "pool-b"}
    tid, sid = fw._lb_setup(client, [vs])
    out = _trace(client, tid, dst="203.0.113.10", start=f"{sid}|root", service="tcp/443")
    assert fw._ends(out) == {("srv1", "connected")}, fw._ends(out)


@pytest.mark.xfail(
    strict=True,
    reason="P-9: abgeschalteter Virtual Service (`enabled` bzw. "
    "`traffic_enabled` false) - die VIP antwortet nicht, der Weg gilt trotzdem "
    "als erreicht bis zum Server",
)
def test_p9_abgeschalteter_virtual_service_ist_nicht_erreicht(client):
    vs = fw._vs("vs-shop", ["10.10.0.20"])
    vs["attrs"] |= {"enabled": False, "traffic_enabled": False, "oper_status": "OPER_DISABLED"}
    tid, sid = fw._lb_setup(client, [vs])
    out = _trace(client, tid, dst="203.0.113.10", start=f"{sid}|root", service="tcp/443")
    assert all(p["end"]["kind"] not in REACHED for p in _active(out)), fw._ends(out)


# -- P-10: Reverse Path für IPv6 ---------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="P-10: für IPv6 gilt `asymroute6` (vom Collector gelesen) "
    "- die Weg-Suche wertet auch für IPv6 nur `asymroute` aus",
)
def test_p10_reverse_path_ipv6_nach_asymroute6(client):
    """`asymroute enable`, `asymroute6 disable`: für IPv6 prüft die FortiGate den Reverse Path."""
    tid = _tenant(client)
    _routers(client, tid, ["rt-01"], [
        _l3("rt-01", "2001:db8:1::/64", [{"interface": "Gi0/1"}], "connected", distance=0),
        _l3("rt-01", "2001:db8:1::1/128", [{"interface": "Gi0/1"}], "local", distance=0),
        _l3("rt-01", "2001:db8:12::/64", [{"interface": "Gi0/2"}], "connected", distance=0),
        _l3("rt-01", "2001:db8:12::1/128", [{"interface": "Gi0/2"}], "local", distance=0),
        _l3("rt-01", "2001:db8:20::/64", [{"ip": "2001:db8:12::2", "interface": "Gi0/2"}],
            distance=1),
    ])  # fmt: skip
    _firewall(client, tid, "fw-01", [
        _if("fw-01", "lan", "2001:db8:20::1/64", "lan"),
        _if("fw-01", "port2", "2001:db8:12::2/64", "transit"),
    ], [
        _r("fw-01", "root", "2001:db8:20::/64", [{"interface": "lan"}], "connected"),
        _r("fw-01", "root", "2001:db8:12::/64", [{"interface": "port2"}], "connected"),
        _r("fw-01", "root", "2001:db8:1::/64", [{"ip": "2001:db8:13::1", "interface": "port3"}]),
    ], contexts=_ctx("fw-01", asymroute=True, asymroute6=False,
                     strict_src_check=False))  # fmt: skip
    (path,) = _active(_trace(client, tid, src="2001:db8:1::5", dst="2001:db8:20::5"))
    assert _labels(path) == ["rt-01", "fw-01"]
    assert path["end"]["kind"] == "dropped", path["end"]
