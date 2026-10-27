"""NAT / VIPs: Kette VIP der Internet-Firewall → VIP des Load Balancers → Server (0.95.0).

Rückmeldung des Anwenders (Test 2026-10-27): „Bei den NAT / VIPs sind die zusammengehörenden
VIPs der Internet Firewall und die des Loadbalancers noch nicht miteinander verknüpft … Ziel
muss sein, dass ich sehe, welche externe IP zu welchen Servern geht, egal ob es noch durch
einen Loadbalancer führt.“

Beispieldaten: fw-01 publiziert über VIPs (Port-Weiterleitung 443/80 in einer VIP-Gruppe,
Bereich 1:1, mehrere Werte in `mappedip`, FQDN-VIP) auf Virtual Services des NSX ALB avi-01;
V-EXT_mail geht direkt auf einen Server.
"""

from app.models import NatRule
from app.store import nat as nat_store
from test_avi import _deliver
from test_policy_check import ADMIN

LB = "10.30.0.50"


def _vip(name, ext, mapped, ext_port=None, mapped_port=None, **raw):
    forward = ext_port is not None
    mappedip = raw.pop("mappedip", [{"range": mapped}] if mapped else None)
    return {
        "source_platform": "fortigate", "external_id": name, "name": name, "ext_ip": ext,
        "ext_port": ext_port, "mapped_ip": mapped, "mapped_port": mapped_port,
        "protocol": "tcp" if forward else None, "portforward": forward,
        "vip_type": raw.get("type", "static-nat"), "family": "v4", "vdom": "root",
        "raw": {"name": name, "extintf": "wan1", "type": "static-nat", "mappedip": mappedip,
                **raw},
    }  # fmt: skip


def _vs(name, ip, port, servers, **attrs):
    return {
        "source_platform": "avi", "external_id": f"admin|{name}|{ip}|{port}", "name": name,
        "nat_type": "dnat_vip", "ext_ip": ip, "ext_port": str(port),
        "mapped_ip": servers[0][0], "mapped_port": str(servers[0][1]), "protocol": "tcp",
        "portforward": True, "vip_type": "server-load-balance", "family": "v4",
        "targets": [{"ip": s, "port": p, "status": "active", "pool": f"pool-{name}"}
                    for s, p in servers],
        "attrs": {"platform": "avi", "tenant": "admin", "pool": f"pool-{name}",
                  "snat": "service_engine", "oper_status": "OPER_UP", **attrs},
        "raw": {"tenant": "admin", "virtual_service": name},
    }  # fmt: skip


NAT = [
    _vip("V-EXT_shop-443", "203.0.113.10", LB, "443", "443"),
    _vip("V-EXT_shop-80", "203.0.113.10", LB, "80", "80"),
    # Bereich auf Bereich (1:1): .21 fuehrt auf 10.30.0.61, die VIP von vs-api
    _vip("V-EXT_api", "203.0.113.20-203.0.113.21", "10.30.0.60-10.30.0.61"),
    # mehrere Werte in mappedip: gespeichert ist nur der erste (ein Server), der zweite = LB
    _vip("V-EXT_multi", "203.0.113.40", "10.10.0.40",
         mappedip=[{"range": "10.10.0.40"}, {"range": "10.30.0.70"}]),
    # FQDN-VIP: mapped-addr = Adressobjekt mit dem FQDN des Virtual Service
    _vip("V-EXT_app", "203.0.113.50", None, type="fqdn", **{"mapped-addr": "FQDN_lb-app"}),
    # ohne Load Balancer: unverändert direkt zum Server
    _vip("V-EXT_mail", "203.0.113.30", "10.10.0.25"),
]  # fmt: skip
OBJECTS = [
    *({"name": n["name"], "category": "vip", "family": "v4", "obj_type": "static-nat",
       "vdom": "root", "values": [n["ext_ip"]]} for n in NAT),
    {"name": "VIPGRP_shop", "category": "address_group", "obj_type": "vipgrp", "family": "v4",
     "vdom": "root", "members": ["V-EXT_shop-443", "V-EXT_shop-80"], "values": ["203.0.113.10"]},
    {"name": "FQDN_lb-app", "category": "address", "obj_type": "fqdn", "family": "v4",
     "vdom": "root", "fqdn": "lb-app.example.ch", "values": ["fqdn:lb-app.example.ch"]},
    {"name": "H_web01", "category": "address", "obj_type": "ipmask", "family": "v4",
     "vdom": "root", "values": ["10.10.0.20"]},
]  # fmt: skip
RULES = [
    {
        "source_platform": "fortigate", "external_id": "30", "name": "inet-to-shop",
        "action": "allow", "vdom": "root", "family": "v4", "sources": ["all"],
        # Policy auf die VIP-Gruppe (Collector loest sie in die VIPs auf, 0.95.0)
        "destinations": ["vip:V-EXT_shop-443=203.0.113.10", "vip:V-EXT_shop-80=203.0.113.10"],
        "services": ["tcp/443", "tcp/80"], "source_refs": ["all"],
        "destination_refs": ["VIPGRP_shop"], "service_refs": ["HTTPS", "HTTP"],
    },
]  # fmt: skip
AVI = {
    "status": "completed",
    "nat_rules": [
        _vs("vs-shop", LB, 443, [("10.10.0.20", 8443)]),
        _vs("vs-shop", LB, 80, [("10.10.0.20", 8080)]),
        _vs("vs-api", "10.30.0.61", 443, [("10.10.0.30", 8443), ("10.10.0.31", 8443)]),
        _vs("vs-multi", "10.30.0.70", 443, [("10.10.0.41", 443)]),
        _vs("vs-app", "10.30.0.80", 443, [("10.10.0.50", 8443)], fqdn=["lb-app.example.ch"]),
    ],
}


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-01", "platform": "fortigate", "base_url": "https://x"}
    fw = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    result = {"status": "completed", "rules": RULES, "objects": OBJECTS, "nat_rules": NAT}
    _deliver(client, tid, fw, "fortigate", result)
    body = {"tenant_id": tid, "name": "avi-01", "platform": "avi", "base_url": "https://avi",
            "username": "ro", "password": "x", "read_scope": "admin"}  # fmt: skip
    _deliver(client, tid, client.post("/systems", json=body, headers=ADMIN).json()["id"],
             "avi", AVI)  # fmt: skip
    return tid


def _rows(client, tid, **params) -> list[dict]:
    r = client.get("/nat", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _by(rows) -> dict:
    return {(n["name"], n["ext_port"]): n for n in rows}


def test_kette_vip_firewall_load_balancer_server(client):
    tid = _seed(client)
    rows = _by(_rows(client, tid))
    shop = rows[("V-EXT_shop-443", "443")]
    # Port-Weiterleitung: nur der Virtual Service auf dem weitergeleiteten Port
    (chain,) = shop["chain"]
    assert (chain["ext_ip"], chain["ext_port"], chain["vip_ip"], chain["vip_port"]) == (
        "203.0.113.10", "443", LB, "443")  # fmt: skip
    assert (chain["balancer"], chain["source"], chain["pool"]) == ("vs-shop", "avi-01",
                                                                    "pool-vs-shop")  # fmt: skip
    assert [(s["ip"], s["port"], s["names"]) for s in chain["servers"]] == [
        ("10.10.0.20", "8443", ["H_web01"])]  # fmt: skip
    # Ziel-Server: ueber den Load Balancer; die VIP-Gruppe und ihre Regel
    assert [(s["ip"], s["port"], s["via"]) for s in shop["dest_servers"]] == [
        ("10.10.0.20", "8443", "vs-shop")]  # fmt: skip
    assert shop["groups"] == ["VIPGRP_shop"] and shop["rule_count"] == 1
    assert shop["source"] == "fw-01"
    http = rows[("V-EXT_shop-80", "80")]["chain"]
    assert [c["balancer"] + ":" + c["vip_port"] for c in http] == ["vs-shop:80"]

    # umgekehrt am Virtual Service: die VIP davor mit externer IP, die Regel der VIP zaehlt
    vs = rows[("vs-shop", "443")]
    assert [(f["name"], f["ext_ip"], f["ext_port"], f["source"]) for f in vs["fronted_by"]] == [
        ("V-EXT_shop-443", "203.0.113.10", "443", "fw-01")]  # fmt: skip
    assert vs["rule_count"] == 1 and vs["chain"] == []
    assert [(s["ip"], s["port"]) for s in vs["dest_servers"]] == [("10.10.0.20", "8443")]

    # Bereich 1:1 - die passende externe Adresse; mehrere Werte in mappedip; FQDN-VIP
    (api,) = rows[("V-EXT_api", None)]["chain"]
    assert (api["ext_ip"], api["balancer"]) == ("203.0.113.21", "vs-api")
    assert [s["ip"] for s in api["servers"]] == ["10.10.0.30", "10.10.0.31"]
    assert [c["balancer"] for c in rows[("V-EXT_multi", None)]["chain"]] == ["vs-multi"]
    (app,) = rows[("V-EXT_app", None)]["chain"]
    assert (app["balancer"], app["via"], app["ext_port"]) == ("vs-app", "fqdn", "443")
    assert rows[("vs-api", "443")]["fronted_by"][0]["ext_ip"] == "203.0.113.21"


def test_vip_ohne_load_balancer_unveraendert(client):
    tid = _seed(client)
    mail = _by(_rows(client, tid))[("V-EXT_mail", None)]
    assert mail["chain"] == [] and mail["fronted_by"] == [] and mail["groups"] == []
    assert mail["mapped_ip"] == "10.10.0.25"
    assert [(s["ip"], s["port"], s["via"]) for s in mail["dest_servers"]] == [
        ("10.10.0.25", None, None)]  # fmt: skip
    # SNAT-Pools haben keine Ziel-Server; alles Bisherige bleibt
    assert mail["rule_count"] == 0 and mail["interface"] == "wan1"


def test_suche_findet_die_kette(client):
    tid = _seed(client)

    def names(q):
        return {n["name"] for n in _rows(client, tid, q=q)}

    # Server hinter dem Load Balancer: VIP der Firewall und Virtual Service
    assert names("10.10.0.20") == {"V-EXT_shop-443", "V-EXT_shop-80", "vs-shop"}
    assert names("H_web01") >= {"V-EXT_shop-443", "vs-shop"}  # Firewall-Objekt des Servers
    # externe IP (auch im Bereich): VIP der Firewall und der Virtual Service dahinter
    assert names("203.0.113.21") == {"V-EXT_api", "vs-api"}
    assert names("vs-api") == {"V-EXT_api", "vs-api"}
    assert names("VIPGRP_shop") == {"V-EXT_shop-443", "V-EXT_shop-80"}
    assert "V-EXT_mail" in names("10.10.0.25")

    # Detail: Kette, Regeln (auch am Virtual Service), VM-Suche ueber die Server dahinter
    shop = next(n for n in _rows(client, tid, q="V-EXT_shop-443"))
    detail = client.get(f"/nat/{shop['id']}").json()
    assert detail["chain"][0]["balancer"] == "vs-shop"
    assert [r["name"] for r in detail["rules"]] == ["inet-to-shop"]
    vs = next(n for n in _rows(client, tid, q="vs-shop") if n["name"] == "vs-shop")
    assert [r["name"] for r in client.get(f"/nat/{vs['id']}").json()["rules"]] == ["inet-to-shop"]
    # Regel-Liste: Filter nat= auf den Virtual Service (Regelindex)
    rules = client.get("/rules", params={"tenant_id": tid, "nat": "vs-shop"}).json()
    assert [r["name"] for r in rules] == ["inet-to-shop"]
    # Regel-Detail: die VIPs der Gruppe mit dem Load Balancer dahinter
    rule = client.get(f"/rules/{rules[0]['id']}").json()
    vips = {n["name"]: n for n in rule["nat_entries"]}
    assert set(vips) == {"V-EXT_shop-443", "V-EXT_shop-80"}
    assert {b["name"] for b in vips["V-EXT_shop-443"]["balancers"]} == {"vs-shop"}


def _nat(i, **kw) -> NatRule:
    base = {"id": i, "tenant_id": 1, "source_system_id": 1, "external_id": str(i),
            "name": f"n{i}", "nat_type": "dnat_vip", "family": "v4", "raw": {}}  # fmt: skip
    return NatRule(**{**base, **kw})


def _lb(i, ip, port):
    return _nat(i, source_system_id=2, ext_ip=ip, ext_port=port, attrs={"platform": "avi"},
                targets=[{"ip": "10.10.0.9", "port": 80}])  # fmt: skip


def test_balancers_bereiche_ports_virtual_server_ipv6():
    lbs = [_lb(10, LB, "443"), _lb(11, LB, "444"), _lb(12, "2001:db8::50", "443")]
    # Port-Bereich mit Versatz: extern 8443-8444 -> intern 443-444
    fwd = _nat(1, ext_ip="203.0.113.1", ext_port="8443-8444", mapped_ip="10.30.0.50",
               mapped_port="443-444", portforward=True)  # fmt: skip
    # Virtual Server der FortiGate mit der VIP des Load Balancers als Real Server
    slb = _nat(2, ext_ip="203.0.113.2", ext_port="443", vip_type="server-load-balance",
               targets=[{"ip": "10.30.0.50", "port": 444}])  # fmt: skip
    # IPv6 ungekuerzt geschrieben
    v6 = _nat(3, ext_ip="2001:db8:1::1", mapped_ip="2001:0db8:0000::0050", family="v6")
    b = nat_store.Balancers([*lbs, fwd, slb, v6])
    assert [(x.lb.id, x.ext_port) for x in b.links(fwd)] == [(10, "8443"), (11, "8444")]
    assert [(x.lb.id, x.ext_port) for x in b.links(slb)] == [(11, "443")]
    assert [x.lb.id for x in b.links(v6)] == [12]
    assert [x.vip.id for x in b.fronts(lbs[1])] == [1, 2]
    assert b.links(lbs[0]) == []  # der Virtual Service selbst hat keine Kette
    # SNAT-Pool bzw. ohne Ziel: nichts
    assert b.links(_nat(4, nat_type="snat_pool", ext_ip="10.30.0.50")) == []
    assert b.links(_nat(5, ext_ip="203.0.113.5")) == []
