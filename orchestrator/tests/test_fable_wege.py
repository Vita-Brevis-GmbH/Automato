# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Weg-Suche nach echtem Geräteverhalten (Prüfung A „Wege“, Fable; ADR 0070).

Jeder Test verlangt das Verhalten des echten Geräts (Herstellerdoku im Bericht der Prüfung bzw.
in ADR 0070). Phase 1 hielt die Abweichungen als `xfail(strict=True)` fest; seit dem neuen Kern
der Weg-Suche (Forwarding-Modell `store/forwarding.py`, Stufen je Hersteller
`store/path_stages.py`) bestehen sie. Beispiele anonymisiert: RFC 1918, Dokumentationsnetze,
`example.ch`.

Nummern wie im Bericht (A-1 …). Die Normalisierung der Collectors steht in
`collectors/tests/test_fable_wege.py`. Am Ende: was Phase 2 dazu entschieden hat (Central NAT,
Reverse Path mit Einstellungen, gezählte Unsicherheiten, Start nur wo Verkehr ankommt).
"""

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}
# Ende, das die Weg-Suche als „Ziel erreicht“ zählt (`paths.REACHED`)
REACHED = ("connected", "fabric", "exit", "external", "vpn", "redirect")


def _r(node, context, prefix, hops, protocol="static", **kw):
    return {"node": node, "context": context, "prefix": prefix, "protocol": protocol,
            "next_hops": hops, **kw}  # fmt: skip


def _l3(node, prefix, hops, protocol="static", **kw):
    return _r(node, node, prefix, hops, protocol, **kw)


def _local(node, ip, iface):
    return _l3(node, f"{ip}/32", [{"interface": iface}], "local", distance=0)


def _if(device, name, ip, zone=None, vdom="root"):
    return {"device": device, "name": name, "ips": [ip], "zone": zone, "vdom": vdom}


def _tenant(client) -> int:
    return client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]


def _system(client, tid, name, platform, **extra) -> int:
    body = {"tenant_id": tid, "name": name, "platform": platform, "base_url": "https://x", **extra}
    r = client.post("/systems", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _deliver(client, tid, sid, platform, result) -> None:
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _firewall(client, tid, name, interfaces, routes, nat_rules=None, rules=None, **routing) -> int:
    sid = _system(client, tid, name, "fortigate")
    result = {
        "status": "completed",
        "rules": rules or [],
        "nat_rules": nat_rules or [],
        "inventory": {"devices": [{"name": name, "role": "firewall"}], "interfaces": interfaces},
        "routing": {"contexts": [{"node": name, "context": "root"}], "routes": routes, **routing},
    }
    _deliver(client, tid, sid, "fortigate", result)
    return sid


def _policy(pid, src_if, dst_if, src, dst, service="tcp/443"):
    return {"source_platform": "fortigate", "external_id": str(pid), "name": f"p{pid}",
            "action": "allow", "sources": [src], "destinations": [dst], "services": [service],
            "src_zone": src_if, "dst_zone": dst_if, "vdom": "root", "family": "v4",
            "raw": {"policyid": pid, "srcintf": [{"name": src_if}], "dstintf": [{"name": dst_if}],
                    "status": "enable", "schedule": "always"}}  # fmt: skip


def _routers(client, tid, names, routes, name="Router") -> int:
    sid = _system(client, tid, name, "cisco_switch", read_scope="10.255.255.1", username="ro",
                  password="x")  # fmt: skip
    result = {
        "status": "completed",
        "inventory": {"devices": [{"name": n, "role": "switch"} for n in names]},
        "routing": {"contexts": [{"node": n, "context": n, "kind": "l3"} for n in names],
                    "routes": routes},
    }  # fmt: skip
    _deliver(client, tid, sid, "cisco_switch", result)
    return sid


def _trace(client, tid, **params) -> dict:
    r = client.get("/routing/trace", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _labels(path) -> list[str]:
    return [h["label"] for h in path["hops"]]


def _active(out) -> list[dict]:
    return [p for p in out["paths"] if p.get("kind") != "backup"]


# -- A-1: Null-Route --------------------------------------------------------------------------


def test_a1_null_route_verwirft(client):
    tid = _tenant(client)
    _routers(client, tid, ["rt-01"], [
        _l3("rt-01", "10.1.0.0/24", [{"interface": "Gi0/1"}], "connected", distance=0),
        _local("rt-01", "10.1.0.1", "Gi0/1"),
        _l3("rt-01", "10.99.0.0/16", [{"interface": "Null0"}], distance=1),
    ])  # fmt: skip
    out = _trace(client, tid, src="10.1.0.5", dst="10.99.1.1")
    (path,) = _active(out)
    assert path["end"]["kind"] not in REACHED, path["end"]


# -- A-2: statische Route nur mit Interface (Punkt-zu-Punkt) ----------------------------------


def test_a2_route_ueber_tunnel_interface_ist_nicht_angeschlossen(client):
    tid = _tenant(client)
    _routers(client, tid, ["rt-01"], [
        _l3("rt-01", "10.1.0.0/24", [{"interface": "Gi0/1"}], "connected", distance=0),
        _local("rt-01", "10.1.0.1", "Gi0/1"),
        _l3("rt-01", "172.16.0.0/30", [{"interface": "Tu0"}], "connected", distance=0),
        _local("rt-01", "172.16.0.1", "Tu0"),
        _l3("rt-01", "10.9.0.0/16", [{"interface": "Tu0"}], distance=1),
    ])  # fmt: skip
    out = _trace(client, tid, src="10.1.0.5", dst="10.9.1.1")
    (path,) = _active(out)
    assert path["end"]["kind"] != "connected", path["end"]


# -- A-3: Policy-Route mit Protokoll und Port -------------------------------------------------

FW_IFS = [
    _if("fw-01", "lan", "10.20.0.1/24", "lan"),
    _if("fw-01", "wan1", "198.51.100.2/30", "internet"),
    _if("fw-01", "wan2", "198.51.100.6/30", "internet"),
]
FW_ROUTES = [
    _r("fw-01", "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
    _r("fw-01", "root", "198.51.100.0/30", [{"interface": "wan1"}], "connected"),
    _r("fw-01", "root", "198.51.100.4/30", [{"interface": "wan2"}], "connected"),
    _r("fw-01", "root", "0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan1"}],
       distance=10),
]  # fmt: skip


def _egress(path) -> str | None:
    return (path["hops"][0].get("egress") or {}).get("interface")


def test_a3_policy_route_nur_fuer_ihr_protokoll_und_ihren_port(client):
    tid = _tenant(client)
    _firewall(client, tid, "fw-01", FW_IFS, FW_ROUTES, policy_routes=[
        {"node": "fw-01", "context": "root", "seq": 1, "gateway": "198.51.100.5",
         "output_device": ["wan2"], "action": "permit",
         "match": {"input_device": ["lan"], "protocol": 6, "ports": [443, 443]}},
    ])  # fmt: skip
    web = _trace(client, tid, src="10.20.0.5", dst="203.0.113.9", service="tcp/443")
    assert _egress(_active(web)[0]) == "wan2"  # passt: Policy-Route
    ssh = _trace(client, tid, src="10.20.0.5", dst="203.0.113.9", service="tcp/22")
    assert _egress(_active(ssh)[0]) == "wan1"  # passt nicht: Routing-Tabelle


# -- A-4: Policy-Route mit `action deny` (Stop Policy Routing) --------------------------------


def test_a4_policy_route_deny_beendet_das_policy_routing(client):
    tid = _tenant(client)
    match = {"input_device": ["lan"], "src": ["10.20.0.0/24"], "dst": ["203.0.113.0/24"]}
    _firewall(client, tid, "fw-01", FW_IFS, FW_ROUTES, policy_routes=[
        {"node": "fw-01", "context": "root", "seq": 1, "action": "deny", "match": match},
        {"node": "fw-01", "context": "root", "seq": 2, "action": "permit", "match": match,
         "gateway": "198.51.100.5", "output_device": ["wan2"]},
    ])  # fmt: skip
    out = _trace(client, tid, src="10.20.0.5", dst="203.0.113.9")
    assert _egress(_active(out)[0]) == "wan1"  # Routing-Tabelle, nicht Regel 2


# -- A-5: Reihenfolge der Policy-Routen = Liste, nicht seq-num --------------------------------


def test_a5_policy_routen_in_der_reihenfolge_der_liste(client):
    tid = _tenant(client)
    match = {"input_device": ["lan"], "src": ["10.20.0.0/24"]}
    _firewall(client, tid, "fw-01", FW_IFS, [
        *FW_ROUTES,
        _r("fw-01", "root", "10.30.0.0/24", [{"interface": "dmz"}], "connected"),
    ], policy_routes=[
        # mit `move 5 before 2` nach oben geschoben: die Liste kommt in dieser Reihenfolge
        {"node": "fw-01", "context": "root", "seq": 5, "action": "permit", "match": match,
         "gateway": "198.51.100.5", "output_device": ["wan2"]},
        {"node": "fw-01", "context": "root", "seq": 2, "action": "permit", "match": match,
         "gateway": "198.51.100.1", "output_device": ["wan1"]},
    ])  # fmt: skip
    out = _trace(client, tid, src="10.20.0.5", dst="203.0.113.9")
    assert _egress(_active(out)[0]) == "wan2"


# -- A-6: Reverse Path Forwarding der FortiGate -----------------------------------------------


def test_a6_fortigate_verwirft_ohne_route_zur_quelle_ueber_den_eingang(client):
    tid = _tenant(client)
    _routers(client, tid, ["rt-01"], [
        _l3("rt-01", "10.1.0.0/24", [{"interface": "Gi0/1"}], "connected", distance=0),
        _local("rt-01", "10.1.0.1", "Gi0/1"),
        _l3("rt-01", "10.0.12.0/30", [{"interface": "Gi0/2"}], "connected", distance=0),
        _local("rt-01", "10.0.12.1", "Gi0/2"),
        _l3("rt-01", "10.20.0.0/24", [{"ip": "10.0.12.2", "interface": "Gi0/2"}], distance=1),
    ])  # fmt: skip
    _firewall(client, tid, "fw-01", [
        _if("fw-01", "lan", "10.20.0.1/24", "lan"),
        _if("fw-01", "port2", "10.0.12.2/30", "transit"),
        _if("fw-01", "port3", "10.0.13.2/30", "transit"),
    ], [
        _r("fw-01", "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
        _r("fw-01", "root", "10.0.12.0/30", [{"interface": "port2"}], "connected"),
        _r("fw-01", "root", "10.0.13.0/30", [{"interface": "port3"}], "connected"),
        # die Quelle kennt die Firewall nur ueber port3 - an port2 kommt ihr Verkehr aber an
        _r("fw-01", "root", "10.1.0.0/24", [{"ip": "10.0.13.1", "interface": "port3"}]),
    ])  # fmt: skip
    out = _trace(client, tid, src="10.1.0.5", dst="10.20.0.5")
    (path,) = _active(out)
    assert _labels(path) == ["rt-01", "fw-01"]
    assert path["hops"][1]["ingress"]["interface"] == "port2"
    assert path["end"]["kind"] not in REACHED, path["end"]


# -- A-7: rekursiver Next-Hop (iBGP ueber Loopback) -------------------------------------------


def test_a7_rekursiver_next_hop_geht_ueber_den_router_dazwischen(client):
    tid = _tenant(client)
    _routers(client, tid, ["rt-01", "rt-02", "rt-03"], [
        _l3("rt-01", "10.1.0.0/24", [{"interface": "Gi0/1"}], "connected", distance=0),
        _local("rt-01", "10.1.0.1", "Gi0/1"),
        _l3("rt-01", "10.0.12.0/30", [{"interface": "Gi0/2"}], "connected", distance=0),
        _local("rt-01", "10.0.12.1", "Gi0/2"),
        # iBGP: Next-Hop ist die Loopback von rt-03, erreichbar per OSPF ueber rt-02
        _l3("rt-01", "10.50.0.0/16", [{"ip": "10.255.0.3"}], "bgp", distance=200),
        _l3("rt-01", "10.255.0.3/32", [{"ip": "10.0.12.2", "interface": "Gi0/2"}], "ospf",
            distance=110),
        _l3("rt-02", "10.0.12.0/30", [{"interface": "Gi0/1"}], "connected", distance=0),
        _local("rt-02", "10.0.12.2", "Gi0/1"),
        _l3("rt-02", "10.0.23.0/30", [{"interface": "Gi0/2"}], "connected", distance=0),
        _local("rt-02", "10.0.23.1", "Gi0/2"),
        _l3("rt-02", "10.50.0.0/16", [{"ip": "10.0.23.2", "interface": "Gi0/2"}], "ospf",
            distance=110),
        _l3("rt-03", "10.0.23.0/30", [{"interface": "Gi0/1"}], "connected", distance=0),
        _local("rt-03", "10.0.23.2", "Gi0/1"),
        _local("rt-03", "10.255.0.3", "Lo0"),
        _l3("rt-03", "10.50.0.0/16", [{"interface": "Gi0/2"}], "connected", distance=0),
    ])  # fmt: skip
    out = _trace(client, tid, src="10.1.0.5", dst="10.50.1.1")
    (path,) = _active(out)
    assert _labels(path) == ["rt-01", "rt-02", "rt-03"]


# -- A-8: VIP mit Port-Weiterleitung ----------------------------------------------------------


def _vip(name, ext_port, mapped_ip, mapped_port):
    return {"source_platform": "fortigate", "external_id": name, "name": name,
            "nat_type": "dnat_vip", "ext_ip": "203.0.113.10", "mapped_ip": mapped_ip,
            "portforward": True, "protocol": "tcp", "ext_port": ext_port,
            "mapped_port": mapped_port, "vdom": "root"}  # fmt: skip


VIP_IFS = [
    _if("fw-01", "wan1", "198.51.100.2/30", "internet"),
    _if("fw-01", "dmz1", "10.1.1.1/24", "dmz"),
    _if("fw-01", "dmz2", "10.1.2.1/24", "dmz"),
]
VIP_ROUTES = [
    _r("fw-01", "root", "198.51.100.0/30", [{"interface": "wan1"}], "connected"),
    _r("fw-01", "root", "10.1.1.0/24", [{"interface": "dmz1"}], "connected"),
    _r("fw-01", "root", "10.1.2.0/24", [{"interface": "dmz2"}], "connected"),
    _r("fw-01", "root", "0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan1"}]),
]


VIP_RULES = [
    _policy(1, "wan1", "dmz1", "all", "vip:V-EXT_a-http=203.0.113.10", "tcp/8080"),
    _policy(2, "wan1", "dmz2", "all", "vip:V-EXT_b-https=203.0.113.10", "tcp/8443"),
]


def test_a8_vip_mit_port_weiterleitung_nur_fuer_ihren_port(client):
    tid = _tenant(client)
    sid = _firewall(client, tid, "fw-01", VIP_IFS, VIP_ROUTES, rules=VIP_RULES, nat_rules=[
        _vip("V-EXT_a-http", "80", "10.1.1.10", "8080"),
        _vip("V-EXT_b-https", "443", "10.1.2.20", "8443"),
    ])  # fmt: skip
    out = _trace(client, tid, dst="203.0.113.10", start=f"{sid}|root", service="tcp/443")
    hop = _active(out)[0]["hops"][0]
    assert hop["nat"]["name"] == "V-EXT_b-https"
    assert hop["egress"]["interface"] == "dmz2"


def test_a8_vip_mit_port_weiterleitung_kein_dnat_fuer_andere_ports(client):
    tid = _tenant(client)
    sid = _firewall(client, tid, "fw-01", VIP_IFS, VIP_ROUTES, rules=VIP_RULES[1:], nat_rules=[
        _vip("V-EXT_b-https", "443", "10.1.2.20", "8443"),
    ])  # fmt: skip
    out = _trace(client, tid, dst="203.0.113.10", start=f"{sid}|root", service="tcp/22")
    hop = _active(out)[0]["hops"][0]
    assert not hop.get("nat"), hop.get("nat")


# -- A-9: VIP in keiner Policy (Central NAT aus) ----------------------------------------------


def test_a9_vip_ohne_policy_macht_kein_dnat(client):
    tid = _tenant(client)
    sid = _firewall(client, tid, "fw-01", VIP_IFS, VIP_ROUTES, nat_rules=[
        {"source_platform": "fortigate", "external_id": "V-EXT_alt", "name": "V-EXT_alt",
         "nat_type": "dnat_vip", "ext_ip": "203.0.113.10", "mapped_ip": "10.1.1.10",
         "vdom": "root"},
    ])  # fmt: skip
    out = _trace(client, tid, dst="203.0.113.10", start=f"{sid}|root", service="tcp/443")
    hop = _active(out)[0]["hops"][0]
    assert not hop.get("nat"), hop.get("nat")


# -- A-10: IPsec - Phase-2-Selektoren ---------------------------------------------------------


def _vpn_pair(client, tid) -> None:
    def tunnel(node, name, gw, src, dst):
        return {
            "node": node,
            "context": "root",
            "name": name,
            "kind": "static",
            "interface": "wan1",
            "remote_gw": gw,
            "ike_version": 2,
            "status": "up",
            "phase2": [{"name": f"{name}-p2", "src": [src], "dst": [dst], "status": "up"}],
        }

    def tun_if(device, name):
        return {"device": device, "name": name, "kind": "tunnel", "vdom": "root"}

    _firewall(client, tid, "fw-a", [
        _if("fw-a", "lan", "10.10.0.1/16", "lan"),
        _if("fw-a", "wan1", "203.0.113.1/30", "internet"),
        tun_if("fw-a", "vpn-b"),
    ], [
        _r("fw-a", "root", "10.10.0.0/16", [{"interface": "lan"}], "connected"),
        _r("fw-a", "root", "203.0.113.0/30", [{"interface": "wan1"}], "connected"),
        _r("fw-a", "root", "10.20.0.0/16", [{"interface": "vpn-b"}]),
        _r("fw-a", "root", "10.30.0.0/16", [{"interface": "vpn-b"}]),
    ], vpn=[tunnel("fw-a", "vpn-b", "198.51.100.2", "10.10.0.0/24", "10.20.0.0/16")])  # fmt: skip
    _firewall(client, tid, "fw-b", [
        _if("fw-b", "lan", "10.20.0.1/16", "lan"),
        _if("fw-b", "lan2", "10.30.0.1/16", "lan"),
        _if("fw-b", "wan1", "198.51.100.2/30", "internet"),
        tun_if("fw-b", "vpn-a"),
    ], [
        _r("fw-b", "root", "10.20.0.0/16", [{"interface": "lan"}], "connected"),
        _r("fw-b", "root", "10.30.0.0/16", [{"interface": "lan2"}], "connected"),
        _r("fw-b", "root", "198.51.100.0/30", [{"interface": "wan1"}], "connected"),
        _r("fw-b", "root", "10.10.0.0/16", [{"interface": "vpn-a"}]),
    ], vpn=[tunnel("fw-b", "vpn-a", "203.0.113.1", "10.20.0.0/16", "10.10.0.0/24")])  # fmt: skip


def test_a10_ipsec_ziel_in_keinem_selektor_wird_verworfen(client):
    tid = _tenant(client)
    _vpn_pair(client, tid)
    ok = _trace(client, tid, src="10.10.0.5", dst="10.20.0.5")
    assert _labels(_active(ok)[0]) == ["fw-a", "fw-b"]  # gedeckt: bis zum Ziel
    assert _active(ok)[0]["end"]["kind"] in REACHED
    out = _trace(client, tid, src="10.10.0.5", dst="10.30.0.5")
    assert _active(out)[0]["end"]["kind"] not in REACHED, _active(out)[0]["end"]


def test_a10_ipsec_quelle_ausserhalb_des_lokalen_selektors_wird_verworfen(client):
    tid = _tenant(client)
    _vpn_pair(client, tid)
    out = _trace(client, tid, src="10.10.9.5", dst="10.20.0.5")  # Selektor nur 10.10.0.0/24
    assert _active(out)[0]["end"]["kind"] not in REACHED, _active(out)[0]["end"]


# -- A-13: Rückweg mit ECMP über zwei unabhängige Firewalls -----------------------------------


def _fw_pair_member(client, tid, name, last):
    _firewall(client, tid, name, [
        _if(name, "port1", f"10.0.1.{last}/29", "west"),
        _if(name, "port2", f"10.0.2.{last}/29", "east"),
    ], [
        _r(name, "root", "10.0.1.0/29", [{"interface": "port1"}], "connected"),
        _r(name, "root", "10.0.2.0/29", [{"interface": "port2"}], "connected"),
        _r(name, "root", "10.1.0.0/24", [{"ip": "10.0.1.1", "interface": "port1"}]),
        _r(name, "root", "10.2.0.0/24", [{"ip": "10.0.2.1", "interface": "port2"}]),
    ])  # fmt: skip


def test_a13_rueckweg_mit_ecmp_ueber_andere_firewall_ist_nicht_symmetrisch(client):
    """Zwei Firewalls ohne Verbund bzw. Session-Sync, davor und dahinter je ein Router mit ECMP
    über beide: die Antwort einer Sitzung über fw-a kann der Router über fw-b schicken - fw-b
    kennt die Sitzung nicht und verwirft sie."""
    tid = _tenant(client)
    _routers(client, tid, ["rt-01", "rt-02"], [
        _l3("rt-01", "10.1.0.0/24", [{"interface": "Gi0/1"}], "connected", distance=0),
        _local("rt-01", "10.1.0.1", "Gi0/1"),
        _l3("rt-01", "10.0.1.0/29", [{"interface": "Gi0/2"}], "connected", distance=0),
        _local("rt-01", "10.0.1.1", "Gi0/2"),
        _l3("rt-01", "10.2.0.0/24", [{"ip": "10.0.1.2", "interface": "Gi0/2"},
                                     {"ip": "10.0.1.3", "interface": "Gi0/2"}], "ospf",
            distance=110),
        _l3("rt-02", "10.2.0.0/24", [{"interface": "Gi0/1"}], "connected", distance=0),
        _local("rt-02", "10.2.0.1", "Gi0/1"),
        _l3("rt-02", "10.0.2.0/29", [{"interface": "Gi0/2"}], "connected", distance=0),
        _local("rt-02", "10.0.2.1", "Gi0/2"),
        _l3("rt-02", "10.1.0.0/24", [{"ip": "10.0.2.2", "interface": "Gi0/2"},
                                     {"ip": "10.0.2.3", "interface": "Gi0/2"}], "ospf",
            distance=110),
    ])  # fmt: skip
    _fw_pair_member(client, tid, "fw-a", 2)
    _fw_pair_member(client, tid, "fw-b", 3)
    out = _trace(client, tid, src="10.1.0.5", dst="10.2.0.5")
    found = _active(out)
    assert sorted(_labels(p)[1] for p in found) == ["fw-a", "fw-b"]
    assert all(p["return"]["state"] != "symmetric" for p in found), [p["return"] for p in found]


# -- Stichproben des Anwenders als Muster (synthetisch nachgebaut, Fall 1 und Fall 2) ---------
#
# Muster: zwei ACI-Tenants, dazwischen der FortiGate-Cluster fw-a (ein Quellsystem); daneben
# eine weitere Firewall (fw-vpn bzw. fw-inet), die am Cluster haengt und dieselben Regeln hat.
# Die VRFs der Tenants haben keinen gelesenen Routing-Kontext (z. B. L3Out in einem anderen
# Tenant) - die Quelle haengt damit an keinem gelesenen Knoten.


def _aci_subnets(client, tid) -> None:
    sid = _system(client, tid, "apic-01", "cisco_aci")
    subnets = [
        {"prefix": f"10.4{t}.{net}.0/24", "gateway": f"10.4{t}.{net}.1/24",
         "vrf": f"tenant-{t}:VRF1", "context": "epg", "context_name": "EPG_app",
         "aci_tenant": f"tenant-{t}", "ap": "AP1", "epg": "EPG_app"}
        for t, net in ((1, 6), (2, 8))
    ]  # fmt: skip
    _deliver(client, tid, sid, "cisco_aci", {
        "status": "completed",
        "inventory": {"devices": [{"name": "leaf-101", "role": "leaf", "node_id": "101"}],
                      "subnets": subnets},
        "routing": {"contexts": [], "routes": []},
    })  # fmt: skip


def _cluster(client, tid, extra_routes=(), extra_ifs=()) -> int:
    return _firewall(client, tid, "fw-a", [
        _if("fw-a", "t1", "10.250.1.1/29", "tenant-1"),
        _if("fw-a", "t2", "10.250.2.1/29", "tenant-2"),
        _if("fw-a", "to-fw", "10.250.9.1/30", "transit"),
        *extra_ifs,
    ], [
        _r("fw-a", "root", "10.250.1.0/29", [{"interface": "t1"}], "connected"),
        _r("fw-a", "root", "10.250.2.0/29", [{"interface": "t2"}], "connected"),
        _r("fw-a", "root", "10.250.9.0/30", [{"interface": "to-fw"}], "connected"),
        _r("fw-a", "root", "10.41.0.0/16", [{"ip": "10.250.1.2", "interface": "t1"}]),
        _r("fw-a", "root", "10.42.0.0/16", [{"ip": "10.250.2.2", "interface": "t2"}]),
        *extra_routes,
    ])  # fmt: skip


def _conn(session_factory, tid, src, dst, starts):
    from app.store import app_paths, paths, policy_check

    with session_factory() as session:
        graph = paths.Graph(session, tid)
        checks = policy_check.Checks.load(session, tid)
        return app_paths.connection_trace(session, tid, graph, checks, src, dst, ("tcp", 443),
                                          starts)  # fmt: skip


def test_a14_fall1_weg_beginnt_nicht_auf_firewall_abseits_des_wegs(client, session_factory):
    """Echt: tenant-2 → Cluster fw-a → tenant-1. fw-vpn haengt am Cluster, hat denselben
    Regelsatz, liegt aber nicht auf dem Weg - sie darf nicht als Hop (mit ihren Regeln)
    erscheinen, nur weil sie eine passende Regel hat."""
    tid = _tenant(client)
    _aci_subnets(client, tid)
    fwa = _cluster(client, tid)
    vpn = _firewall(client, tid, "fw-vpn", [
        _if("fw-vpn", "to-fw-a", "10.250.9.2/30", "transit"),
    ], [
        _r("fw-vpn", "root", "10.250.9.0/30", [{"interface": "to-fw-a"}], "connected"),
        _r("fw-vpn", "root", "10.40.0.0/14", [{"ip": "10.250.9.1", "interface": "to-fw-a"}]),
    ])  # fmt: skip
    res, _start = _conn(session_factory, tid, "10.42.8.14", "10.41.6.25",
                        [f"{vpn}|root", f"{fwa}|root"])  # fmt: skip
    for p in res["paths"]:
        assert "fw-vpn" not in _labels(p), _labels(p)


def test_a15_fall2_privates_ziel_nicht_ueber_die_default_route_der_internet_firewall(
        client, session_factory):  # fmt: skip
    """Echt: tenant-1 → Cluster fw-a → zwei MPLS-Router (nicht gelesen; die Route kennt der
    Cluster per BGP aus seiner Routing-Tabelle). Die Internet-Firewall hat nur die
    Default-Route und eine passende Regel - der Weg geht nicht ueber sie."""
    tid = _tenant(client)
    _aci_subnets(client, tid)
    mpls = [{"ip": "10.250.5.2", "interface": "mpls"}, {"ip": "10.250.5.3", "interface": "mpls"}]
    fwa = _cluster(client, tid, extra_ifs=[_if("fw-a", "mpls", "10.250.5.1/29", "mpls")],
                   extra_routes=[
        _r("fw-a", "root", "10.250.5.0/29", [{"interface": "mpls"}], "connected"),
        _r("fw-a", "root", "10.90.0.0/16", mpls, "bgp", distance=20),
        _r("fw-a", "root", "0.0.0.0/0", [{"ip": "10.250.9.2", "interface": "to-fw"}]),
    ])  # fmt: skip
    inet = _firewall(client, tid, "fw-inet", [
        _if("fw-inet", "to-fw-a", "10.250.9.2/30", "transit"),
        _if("fw-inet", "wan1", "198.51.100.2/30", "internet"),
    ], [
        _r("fw-inet", "root", "10.250.9.0/30", [{"interface": "to-fw-a"}], "connected"),
        _r("fw-inet", "root", "198.51.100.0/30", [{"interface": "wan1"}], "connected"),
        _r("fw-inet", "root", "10.40.0.0/14", [{"ip": "10.250.9.1", "interface": "to-fw-a"}]),
        _r("fw-inet", "root", "0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan1"}]),
    ])  # fmt: skip
    res, _start = _conn(session_factory, tid, "10.41.6.30", "10.90.7.25",
                        [f"{inet}|root", f"{fwa}|root"])  # fmt: skip
    assert res["paths"]
    for p in res["paths"]:
        assert "fw-inet" not in _labels(p), (_labels(p), p["end"])


def test_fall2_cluster_mit_bgp_route_endet_sichtbar_am_mpls_router(client):
    """Gegenprobe (heute richtig): gelesen wird die wirksame Routing-Tabelle (auch BGP), nicht
    nur die Konfiguration - ab dem Cluster geht der Weg ueber die gelernte Route zu den
    MPLS-Routern und endet dort sichtbar („gehört keinem gelesenen Gerät“), nicht ueber die
    Default-Route."""
    tid = _tenant(client)
    mpls = [{"ip": "10.250.5.2", "interface": "mpls"}, {"ip": "10.250.5.3", "interface": "mpls"}]
    fwa = _cluster(client, tid, extra_ifs=[_if("fw-a", "mpls", "10.250.5.1/29", "mpls")],
                   extra_routes=[
        _r("fw-a", "root", "10.250.5.0/29", [{"interface": "mpls"}], "connected"),
        _r("fw-a", "root", "10.90.0.0/16", mpls, "bgp", distance=20),
        _r("fw-a", "root", "0.0.0.0/0", [{"ip": "10.250.9.2", "interface": "to-fw"}]),
    ])  # fmt: skip
    out = _trace(client, tid, src="10.41.6.30", dst="10.90.7.25", start=f"{fwa}|root")
    found = _active(out)
    assert sorted(p["hops"][0]["egress"]["next_hop"] for p in found) == ["10.250.5.2",
                                                                         "10.250.5.3"]  # fmt: skip
    assert {p["end"]["kind"] for p in found} == {"unknown"}
    assert all("gehört keinem gelesenen Gerät" in p["end"]["text"] for p in found)


# -- Stichprobe Fall 3 (Muster, synthetisch): Internet → Firewall-VIP → Load Balancer → Server -
#
# fw-01: wan1, dmz (Netz der VIP des Load Balancers), srv1/srv2 (Server des Pools). Die VIP der
# Firewall 203.0.113.10:443 zeigt auf die VIP des Virtual Service 10.30.0.50:443, dieser auf die
# Server des Pools (Port 8080).

LB_VIP = "10.30.0.50"
LB_IFS = [
    _if("fw-01", "wan1", "198.51.100.2/30", "internet"),
    _if("fw-01", "dmz", "10.30.0.1/24", "dmz"),
    _if("fw-01", "srv1", "10.10.0.1/24", "server"),
    _if("fw-01", "srv2", "10.11.0.1/24", "server"),
]
LB_ROUTES = [
    _r("fw-01", "root", "198.51.100.0/30", [{"interface": "wan1"}], "connected"),
    _r("fw-01", "root", "10.30.0.0/24", [{"interface": "dmz"}], "connected"),
    _r("fw-01", "root", "10.10.0.0/24", [{"interface": "srv1"}], "connected"),
    _r("fw-01", "root", "10.11.0.0/24", [{"interface": "srv2"}], "connected"),
    _r("fw-01", "root", "0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan1"}]),
]
LB_FW_VIP = {"source_platform": "fortigate", "external_id": "V-EXT_shop", "name": "V-EXT_shop",
             "nat_type": "dnat_vip", "ext_ip": "203.0.113.10", "mapped_ip": LB_VIP,
             "portforward": True, "protocol": "tcp", "ext_port": "443", "mapped_port": "443",
             "vdom": "root"}  # fmt: skip
LB_RULES = [_policy(1, "wan1", "dmz", "all", "vip:V-EXT_shop=203.0.113.10", "tcp/443")]


def _vs(name, servers, port=443):
    return {"source_platform": "avi", "external_id": f"admin|{name}|{LB_VIP}|{port}",
            "name": name, "nat_type": "dnat_vip", "ext_ip": LB_VIP, "ext_port": str(port),
            "mapped_ip": servers[0] if servers else None,
            "mapped_port": "8080" if servers else None, "protocol": "tcp", "portforward": True,
            "vip_type": "server-load-balance", "family": "v4",
            "targets": [{"ip": ip, "port": 8080, "status": "active", "pool": f"pool-{name}"}
                        for ip in servers],
            "attrs": {"platform": "avi", "tenant": "admin", "snat": "service_engine",
                      "oper_status": "OPER_UP"},
            "raw": {"tenant": "admin", "virtual_service": name}}  # fmt: skip


def _lb_setup(client, services) -> tuple[int, int]:
    tid = _tenant(client)
    sid = _firewall(client, tid, "fw-01", LB_IFS, LB_ROUTES, nat_rules=[LB_FW_VIP],
                    rules=LB_RULES)  # fmt: skip
    avi = _system(client, tid, "alb-01", "avi", username="ro", password="x")
    _deliver(client, tid, avi, "avi", {"status": "completed", "nat_rules": services})
    return tid, sid


def _ends(out) -> set[tuple[str | None, str]]:
    """Je aktivem Weg: (Ausgang am letzten Hop, Art des Endes)."""
    return {((p["hops"][-1].get("egress") or {}).get("interface"), p["end"]["kind"])
            for p in _active(out)}  # fmt: skip


def test_fall3_zwei_dnat_hintereinander_mit_port_umsetzung(client):
    """Gegenprobe (heute richtig): VIP der Firewall → VIP des Load Balancers → Server des
    Pools, Ports 443 → 443 → 8080 entlang des Wegs."""
    tid, sid = _lb_setup(client, [_vs("vs-shop", ["10.10.0.20"])])
    out = _trace(client, tid, dst="203.0.113.10", start=f"{sid}|root", service="tcp/443")
    (path,) = _active(out)
    assert path["hops"][0]["nat"]["name"] == "V-EXT_shop"
    assert [h["lb"]["name"] for h in path["hops"] if h.get("lb")] == ["vs-shop"]
    assert _ends(out) == {("srv1", "connected")}
    assert [x["service"] for x in out["service_chain"]] == ["tcp/443", "tcp/443", "tcp/8080"]


def test_a16_fall3_sni_parent_ohne_pool_weg_geht_zum_pool_des_child(client):
    """Mehrere Virtual Services auf derselben VIP und demselben Port (SNI-Hosting: der Parent
    hat keinen Pool, die Children je einen): der Load Balancer verteilt nach dem Hostnamen auf
    die Pools der Children - der Weg endet nicht am Load Balancer."""
    tid, sid = _lb_setup(client, [_vs("vs-a-parent", []), _vs("vs-b-child", ["10.10.0.20"])])
    out = _trace(client, tid, dst="203.0.113.10", start=f"{sid}|root", service="tcp/443")
    assert ("srv1", "connected") in _ends(out), [p["end"] for p in _active(out)]


def test_a17_fall3_weg_zu_jedem_server_des_pools(client):
    """Server des Pools in verschiedenen Netzen: der Load Balancer verteilt auf alle - der Weg
    (und die Regeln je Hop) ist bis zu jedem Server zu pruefen, nicht nur zum ersten."""
    tid, sid = _lb_setup(client, [_vs("vs-shop", ["10.10.0.20", "10.11.0.20"])])
    out = _trace(client, tid, dst="203.0.113.10", start=f"{sid}|root", service="tcp/443")
    assert _ends(out) == {("srv1", "connected"), ("srv2", "connected")}


# -- Phase 2 (ADR 0070): Entscheide des Anwenders und des Projekts ------------------------------


def _ctx(name, **settings):
    return [{"node": name, "context": "root", "settings": settings}]


def test_a6_reverse_path_nach_den_einstellungen_des_vdoms(client):
    """Reverse Path hängt an `asymroute`, `src-check` und `strict-src-check`: gelesen =
    „verworfen“ bzw. kein Reverse Path; nicht gelesen = „nicht geprüft“, gezählt - nie still."""

    def seed(name, **settings):
        tid = client.post("/tenants", json={"name": name}, headers=ADMIN).json()["id"]
        _routers(client, tid, ["rt-01"], [
            _l3("rt-01", "10.1.0.0/24", [{"interface": "Gi0/1"}], "connected", distance=0),
            _local("rt-01", "10.1.0.1", "Gi0/1"),
            _l3("rt-01", "10.0.12.0/30", [{"interface": "Gi0/2"}], "connected", distance=0),
            _local("rt-01", "10.0.12.1", "Gi0/2"),
            _l3("rt-01", "10.20.0.0/24", [{"ip": "10.0.12.2", "interface": "Gi0/2"}],
                distance=1),
        ])  # fmt: skip
        extra = {"contexts": _ctx("fw-01", **settings)} if settings else {}
        _firewall(client, tid, "fw-01", [
            _if("fw-01", "lan", "10.20.0.1/24", "lan"),
            _if("fw-01", "port2", "10.0.12.2/30", "transit"),
        ], [
            _r("fw-01", "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
            _r("fw-01", "root", "10.0.12.0/30", [{"interface": "port2"}], "connected"),
            _r("fw-01", "root", "10.1.0.0/24", [{"ip": "10.0.13.1", "interface": "port3"}]),
        ], **extra)  # fmt: skip
        return _trace(client, tid, src="10.1.0.5", dst="10.20.0.5")

    unread = seed("Kunde A")
    assert _active(unread)[0]["end"]["kind"] == "unchecked"
    assert unread["uncertain"] == {"rpf": 1}
    assert "asymroute und src-check sind nicht gelesen" in _active(unread)[0]["end"]["text"]
    read = seed("Kunde B", asymroute=False, strict_src_check=False)
    (path,) = _active(read)
    assert path["end"]["kind"] == "dropped" and path["hops"][1]["dropped"]["stage"] == "rpf"
    assert "uncertain" not in read
    assert "rules" not in path["hops"][1]  # verworfen vor der Policy
    asym = seed("Kunde C", asymroute=True, strict_src_check=False)
    assert _active(asym)[0]["end"]["kind"] == "connected"
    off = seed("Kunde D", asymroute=False, strict_src_check=False, src_check_off=["port2"])
    assert _active(off)[0]["end"]["kind"] == "connected"


def test_a15_default_route_privat_nicht_geprueft_oeffentlich_extern(client):
    """Privates Ziel über die Default-Route an ein nicht gelesenes Gerät: Grenze „nicht geprüft
    (Default-Route)“, gezählt - nie „erreicht“. Öffentliches Ziel: weiter „extern“."""
    tid = _tenant(client)
    _firewall(client, tid, "fw-01", FW_IFS, FW_ROUTES)
    private = _trace(client, tid, src="10.20.0.5", dst="10.99.0.5")
    (path,) = _active(private)
    assert path["end"]["kind"] == "unchecked" and path["end"]["kind"] not in REACHED
    assert path["end"]["text"].startswith("nicht geprüft (Default-Route)")
    assert private["uncertain"] == {"default": 1}
    public = _trace(client, tid, src="10.20.0.5", dst="203.0.113.9")
    assert _active(public)[0]["end"]["kind"] == "external" and "uncertain" not in public


def test_a9_central_nat_vip_ohne_policy_und_central_snat(client):
    """Central NAT (je VDOM `system settings`): die VIP wirkt ohne Policy, das SNAT kommt aus
    der Tabelle `central-snat-map` (erste passende Zeile), nicht aus der Policy."""
    tid = _tenant(client)
    vip = {"source_platform": "fortigate", "external_id": "V-EXT_web", "name": "V-EXT_web",
           "nat_type": "dnat_vip", "ext_ip": "203.0.113.10", "mapped_ip": "10.1.1.10",
           "vdom": "root"}  # fmt: skip
    pool = {"source_platform": "fortigate", "external_id": "IPP_out", "name": "IPP_out",
            "nat_type": "snat_pool", "ext_ip": "198.51.100.77", "family": "v4",
            "attrs": {"type": "overload"}, "vdom": "root", "raw": {"type": "overload"}}  # fmt: skip
    snat = [
        {"id": 1, "srcintf": ["dmz1"], "dstintf": ["wan1"], "orig_addr": ["all"],
         "dst_addr": ["all"], "nat": "enable", "nat_ippool": ["IPP_out"]},
    ]  # fmt: skip
    sid = _firewall(client, tid, "fw-01", VIP_IFS, VIP_ROUTES, nat_rules=[vip, pool],
                    rules=[_policy(1, "dmz1", "wan1", "all", "all", "ALL")],
                    contexts=_ctx("fw-01", central_nat=True, asymroute=False,
                                  strict_src_check=False, central_snat=snat))  # fmt: skip
    out = _trace(client, tid, dst="203.0.113.10", start=f"{sid}|root", service="tcp/443")
    hop = _active(out)[0]["hops"][0]
    assert hop["nat"]["name"] == "V-EXT_web" and hop["egress"]["interface"] == "dmz1"
    back = _trace(client, tid, src="10.1.1.10", dst="203.0.113.99", service="tcp/443")
    hop = _active(back)[0]["hops"][0]
    assert hop["central_snat"] == {"rule": 1, "sure": True}
    assert (hop["snat"]["pool"], hop["snat"]["to"]) == ("IPP_out", "198.51.100.77")
    assert "uncertain" not in back


def test_central_nat_tabelle_nicht_gelesen_ist_nat_nicht_geprueft(client):
    """Central NAT an, die Tabelle des Central SNAT aber nicht lesbar: sichtbar „NAT nicht
    geprüft“ und gezählt - kein SNAT aus der Policy angenommen."""
    tid = _tenant(client)
    _firewall(client, tid, "fw-01", VIP_IFS, VIP_ROUTES,
              rules=[_policy(1, "dmz1", "wan1", "all", "all", "ALL")],
              contexts=_ctx("fw-01", central_nat=True, asymroute=False,
                            strict_src_check=False))  # fmt: skip
    out = _trace(client, tid, src="10.1.1.10", dst="203.0.113.99", service="tcp/443")
    assert out["uncertain"] == {"nat": 1}
    assert "NAT nicht geprüft an fw-01: Central SNAT nicht gelesen" in out["notes"]
    assert "snat" not in _active(out)[0]["hops"][0]


def test_a12_hsrp_standby_nimmt_den_verkehr_nicht_an(client):
    """Virtuelle Gateway-Adresse (HSRP) auf zwei Routern: den Verkehr nimmt der aktive an."""
    tid = _tenant(client)

    def core(name, own, state):
        return [
            _l3(name, "10.0.0.0/24", [{"interface": "Vl10"}], "connected", distance=0),
            _local(name, own, "Vl10"),
            _l3(name, "10.0.0.1/32", [{"interface": "Vl10"}], "local", distance=0,
                attrs={"virtual": True, "state": state}),
            _l3(name, "10.50.0.0/24", [{"interface": "Vl50"}], "connected", distance=0),
        ]  # fmt: skip

    _routers(client, tid, ["sw-core-01", "sw-core-02"],
             core("sw-core-01", "10.0.0.2", "standby") + core("sw-core-02", "10.0.0.3",
                                                              "active"))  # fmt: skip
    _firewall(client, tid, "fw-01", [
        _if("fw-01", "lan", "10.20.0.1/24", "lan"),
        _if("fw-01", "core", "10.0.0.10/24", "core"),
    ], [
        _r("fw-01", "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
        _r("fw-01", "root", "10.0.0.0/24", [{"interface": "core"}], "connected"),
        _r("fw-01", "root", "10.50.0.0/24", [{"ip": "10.0.0.1", "interface": "core"}]),
    ])  # fmt: skip
    out = _trace(client, tid, src="10.20.0.5", dst="10.50.0.5")
    assert [_labels(p) for p in _active(out)] == [["fw-01", "sw-core-02"]]


def test_a18_umleitung_endet_am_load_balancer(client):
    """Port 80 mit Umleitung HTTP → HTTPS (`attrs.redirect`, vom Collector): der Fluss endet am
    Load Balancer - „erreicht“ (er antwortet selbst), kein Weg zum Pool."""
    redirect = _vs("vs-shop", [], port=80)
    redirect["attrs"]["redirect"] = "https"
    tid, sid = _lb_setup(client, [redirect, _vs("vs-shop", ["10.10.0.20"])])
    out = _trace(client, tid, src="10.11.0.7", dst=LB_VIP, service="tcp/80")
    (path,) = _active(out)
    assert path["end"]["kind"] == "redirect" and path["end"]["kind"] in REACHED
    assert _labels(path) == ["fw-01"] and path["hops"][0]["lb"]["redirect"] == "https"
    https = _trace(client, tid, src="10.11.0.7", dst=LB_VIP, service="tcp/443")
    assert _ends(https) == {("srv1", "connected")}


def test_a17_server_derselben_klasse_sind_ein_weg(client):
    """Mitglieder des Pools im selben Netz (gleiche Route, gleiche Adressklasse der Policies):
    ein Weg mit allen Servern, nicht je Server einer - gerechnet je Äquivalenzklasse."""
    tid, sid = _lb_setup(client, [_vs("vs-shop", ["10.10.0.20", "10.10.0.21", "10.11.0.20"])])
    out = _trace(client, tid, dst="203.0.113.10", start=f"{sid}|root", service="tcp/443")
    found = {tuple(h["lb"]["targets"]) for p in _active(out) for h in p["hops"] if h.get("lb")}
    assert found == {("10.10.0.20", "10.10.0.21"), ("10.11.0.20",)}
    assert "uncertain" not in out


def test_virtual_server_der_fortigate_zu_jedem_real_server(client):
    """Virtual Server (VIP `server-load-balance`) der FortiGate: zu jedem Real Server."""
    tid = _tenant(client)
    vs = {"source_platform": "fortigate", "external_id": "VS_web", "name": "VS_web",
          "nat_type": "dnat_vip", "ext_ip": "203.0.113.10", "mapped_ip": "10.1.1.10",
          "portforward": True, "protocol": "tcp", "ext_port": "443", "mapped_port": "443",
          "vip_type": "server-load-balance", "vdom": "root",
          "targets": [{"ip": "10.1.1.10", "port": 443, "status": "active"},
                      {"ip": "10.1.2.20", "port": 443, "status": "active"}]}  # fmt: skip
    sid = _firewall(client, tid, "fw-01", VIP_IFS, VIP_ROUTES, nat_rules=[vs], rules=[
        _policy(1, "wan1", "dmz1", "all", "vip:VS_web=203.0.113.10", "tcp/443")])  # fmt: skip
    out = _trace(client, tid, dst="203.0.113.10", start=f"{sid}|root", service="tcp/443")
    assert {(p["hops"][0]["egress"]["interface"], p["end"]["kind"]) for p in _active(out)} == {
        ("dmz1", "connected"), ("dmz2", "connected")}  # fmt: skip


def test_a3_policy_route_ohne_dienst_ist_nur_moeglich_und_gezaehlt(client):
    """Policy-Route mit Protokoll und Port, Weg ohne Dienst: nicht sicher - der Weg folgt der
    Routing-Tabelle, die Unsicherheit ist sichtbar und gezählt (ADR 0048)."""
    tid = _tenant(client)
    _firewall(client, tid, "fw-01", FW_IFS, FW_ROUTES, policy_routes=[
        {"node": "fw-01", "context": "root", "seq": 1, "gateway": "198.51.100.5",
         "output_device": ["wan2"], "action": "permit",
         "match": {"input_device": ["lan"], "protocol": 6, "ports": [443, 443]}},
    ])  # fmt: skip
    out = _trace(client, tid, src="10.20.0.5", dst="203.0.113.9")
    assert _egress(_active(out)[0]) == "wan1"
    assert out["uncertain"] == {"policy_route": 1}
    assert any("passt nur möglicherweise" in n for n in out["notes"])


def test_sdwan_regel_ohne_route_ueber_ein_mitglied_wird_uebergangen(client):
    """FortiOS übergeht eine SD-WAN-Regel, wenn kein Mitglied eine Route zum Ziel hat."""
    tid = _tenant(client)
    rule = {"node": "fw-01", "context": "root", "kind": "sdwan", "seq": 1, "name": "inet",
            "output_device": ["wan2", "wan1"], "match": {"dst": ["all"], "src": ["all"]},
            "attrs": {"members": [{"interface": "wan1", "gateway": "198.51.100.1"},
                                  {"interface": "wan2", "gateway": "198.51.100.5"}]}}  # fmt: skip
    _firewall(client, tid, "fw-01", FW_IFS, [
        *FW_ROUTES,
        _r("fw-01", "root", "10.30.0.0/24", [{"ip": "10.20.0.254", "interface": "lan"}]),
    ], policy_routes=[rule])  # fmt: skip
    inside = _trace(client, tid, src="10.20.0.5", dst="10.30.0.9")
    hop = _active(inside)[0]["hops"][0]
    assert hop["egress"]["interface"] == "lan" and "policy" not in hop
    # nur wan1 hat eine Route (Default): die Regel greift mit diesem Mitglied
    out = _trace(client, tid, src="10.20.0.5", dst="203.0.113.9")
    hop = _active(out)[0]["hops"][0]
    assert hop["policy"]["name"] == "inet" and hop["egress"]["interface"] == "wan1"
    assert hop["egress"]["next_hop"] == "198.51.100.1"


def test_a14_start_unbekannt_wird_gezaehlt_statt_geraten(client, session_factory):
    """Ohne Knoten, an dem der Verkehr der Quelle ankommt: „Start unbekannt“, gezählt - kein
    Rückfall auf das Gerät einer Regel."""
    tid = _tenant(client)
    fw = _firewall(client, tid, "fw-01", FW_IFS, FW_ROUTES)
    res, start = _conn(session_factory, tid, "10.77.0.5", "10.20.0.5", [f"{fw}|root"])
    assert res["paths"] == [] and start is None
    assert res["uncertain"] == {"start": 1}
    assert any(n.startswith("Start unbekannt") for n in res["notes"])


def test_a14_start_am_rand_wo_die_route_zur_quelle_das_gelesene_netz_verlaesst(
        client, session_factory):  # fmt: skip
    """Fall 1 (Muster): die Quelle hängt an keinem gelesenen Knoten, der Cluster kennt sie über
    einen nicht gelesenen Nachbarn - dort kommt ihr Verkehr an (Eingang = Interface der Route)."""
    tid = _tenant(client)
    _aci_subnets(client, tid)
    fwa = _cluster(client, tid)
    res, start = _conn(session_factory, tid, "10.42.8.14", "10.41.6.25", [])
    (path,) = res["paths"]
    assert start == "fw-a" and _labels(path) == ["fw-a"]
    assert path["hops"][0]["ingress"]["interface"] == "t2"
    assert path["hops"][0]["egress"]["interface"] == "t1"
    assert fwa


def test_mehr_als_16_wege_gekuerzt_und_gezaehlt(client):
    """Die Kürzung bei 16 Wegen steht nicht nur im Hinweis, sie wird gezählt (ADR 0048)."""
    tid = _tenant(client)
    hops = [{"ip": f"10.0.12.{i}", "interface": "Gi0/2"} for i in range(2, 22)]
    _routers(client, tid, ["rt-01"], [
        _l3("rt-01", "10.1.0.0/24", [{"interface": "Gi0/1"}], "connected", distance=0),
        _local("rt-01", "10.1.0.1", "Gi0/1"),
        _l3("rt-01", "10.0.12.0/24", [{"interface": "Gi0/2"}], "connected", distance=0),
        _l3("rt-01", "10.50.0.0/16", hops, "ospf", distance=110),
    ])  # fmt: skip
    out = _trace(client, tid, src="10.1.0.5", dst="10.50.1.1")
    # dazu der Rückweg: nur für die ersten 8 Wege geprüft - ebenfalls gezählt
    assert len(_active(out)) == 16 and out["uncertain"] == {"truncated": 4, "return": 8}
    assert "mehr als 16 Wege – gekürzt" in out["notes"]


def test_a14_start_unbekannt_macht_die_applikation_unvollstaendig_geprueft(client):
    """In den Verbindungen einer Applikation: ohne Start kein geratener Weg - die Verbindung ist
    „Start unbekannt“, die Applikation unvollständig geprüft (Befund `app_coverage`)."""
    tid = _tenant(client)
    _firewall(client, tid, "fw-01", FW_IFS, FW_ROUTES,
              rules=[_policy(7, "wan1", "lan", "10.77.0.5", "10.20.0.5")])  # fmt: skip
    body = {"name": "Web", "members": [{"kind": "host", "ref": "10.20.0.5"}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body,
                      headers={"X-Dev-Roles": "operator"})  # fmt: skip
    assert app.status_code == 201, app.text
    out = client.get(f"/applications/{app.json()['id']}/connections").json()
    (conn,) = out["connections"]
    assert conn["hops"] == [] and conn["uncertain"] == {"start": 1}
    assert "Start unbekannt" in conn["text"]
    assert out["coverage"]["complete"] is False
    assert out["coverage"]["cuts"]["start"] == {"rules": 1, "missing": 1}
    assert any("ohne bekannten Start" in n for n in out["notes"])


# -- Zusammenspiel Weg-Suche (ADR 0070) und Abgleich je Hop (ADR 0071) ------------------------


def test_central_nat_der_weg_suche_erreicht_den_abgleich(client):
    """Central NAT je VDOM aus der Weg-Suche steht am Hop (`central_nat`) und entscheidet im
    Abgleich: mit Central NAT trifft eine Policy mit Ziel `all` die VIP sicher, ohne gelesene
    NAT (Policy-NAT) bleibt sie nur „möglich“ (ADR 0071)."""
    vip = {"source_platform": "fortigate", "external_id": "V-EXT_web", "name": "V-EXT_web",
           "nat_type": "dnat_vip", "ext_ip": "203.0.113.10", "mapped_ip": "10.1.1.10",
           "vdom": "root"}  # fmt: skip

    def seed(name, central):
        tid = client.post("/tenants", json={"name": name}, headers=ADMIN).json()["id"]
        rules = [_policy(1, "wan1", "dmz1", "all", "all")]
        if not central:  # Policy-NAT: die VIP wirkt nur, wenn sie in einer Policy steht -
            # dann entscheidet die VIP-Policy, die Policy mit `all` davor ist nur „möglich“
            rules.append(_policy(2, "wan1", "dmz1", "all", "vip:V-EXT_web=203.0.113.10"))
        sid = _firewall(client, tid, "fw-01", VIP_IFS, VIP_ROUTES, nat_rules=[vip], rules=rules,
                        contexts=_ctx("fw-01", central_nat=central, asymroute=True,
                                      central_snat=[]))  # fmt: skip
        out = _trace(client, tid, src="198.51.100.1", dst="203.0.113.10", start=f"{sid}|root",
                     service="tcp/443")  # fmt: skip
        return _active(out)[0]["hops"][0]

    hop = seed("Kunde A", central=True)
    assert hop["nat"]["name"] == "V-EXT_web" and hop["central_nat"] is True
    assert hop["rules"]["verdict"] == "allow" and hop["rules"]["rules"][0]["name"] == "p1"
    hop = seed("Kunde B", central=False)
    assert hop["nat"]["name"] == "V-EXT_web" and hop["central_nat"] is False
    first, second = hop["rules"]["rules"]
    assert first["decides"] is False and first["unsure"] == ["Ziel (VIP ohne VIP-Objekt)"]
    assert second["name"] == "p2" and hop["rules"]["verdict"] == "allow"
