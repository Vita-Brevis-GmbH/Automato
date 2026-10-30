"""VIP der FortiGate vor einem Virtual Service des NSX ALB (0.79.0, ADR 0027 Ergänzung).

Rückmeldung des Anwenders: zeigt das Ziel einer VIP (DNAT) der FortiGate auf die VIP des Load
Balancers, gehören beide zusammen - öffentliche IP → VIP der FortiGate → Virtual Service →
Server des Pools. Das gilt für die Firewall-Regeln (Suche, Detail), die Applikations-Doku, die
publizierten Dienste und die Weg-Suche.

Firewall fw-r wie in den Policy-Tests (wan 198.51.100.0/30, dmz 10.30.0.0/24, lan
10.10.0.0/24); die VIP der FortiGate 203.0.113.10 zeigt auf die VIP 10.30.0.50 des NSX ALB im
DMZ-Netz, die Server des Pools stehen im LAN.
"""

import copy

from test_avi import AVI, VIP, _deliver
from test_policy_check import ADMIN, FW, _policy

PUBLIC = "203.0.113.10"
OP = {"X-Dev-Roles": "operator"}


def _fw() -> dict:
    fw = copy.deepcopy(FW)
    fw["rules"] = [
        _policy(30, "inet-to-shop", "internet", "dmz", ["all"], [f"vip:V-EXT_shop={PUBLIC}"],
                ["tcp/443"]) | {"destination_refs": ["V-EXT_shop"]},
        # Service Engine (SNAT, im Netz der VIP) zum Server
        _policy(9, "se-to-web", "dmz", "lan", ["10.30.0.0/24"], ["10.10.0.20"], ["tcp/8080"]),
        *fw["rules"],
    ]  # fmt: skip
    fw["objects"] = [{"name": "V-EXT_shop", "category": "vip", "values": [PUBLIC], "vdom": "root"}]
    fw["nat_rules"] = [
        {"source_platform": "fortigate", "external_id": "V-EXT_shop", "name": "V-EXT_shop",
         "ext_ip": PUBLIC, "mapped_ip": VIP, "vdom": "root", "raw": {"extintf": "wan"}},
    ]  # fmt: skip
    return fw


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    _deliver(client, tid, client.post("/systems", json=body, headers=ADMIN).json()["id"],
             "fortigate", _fw())  # fmt: skip
    body = {"tenant_id": tid, "name": "avi-01", "platform": "avi", "base_url": "https://avi",
            "username": "ro", "password": "x", "read_scope": "admin"}  # fmt: skip
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    _deliver(client, tid, sid, "avi", AVI)  # danach: Regelindex des ganzen Mandanten
    return tid


def test_regeln_und_publizierte_dienste(client):
    tid = _seed(client)
    # Suche nach dem Server findet die Regel der FortiGate-VIP (VIP -> Load Balancer -> Server)
    found = client.get("/rules", params={"tenant_id": tid, "q": "10.10.0.21"}).json()
    assert "inet-to-shop" in {r["name"] for r in found}
    found = client.get("/rules", params={"tenant_id": tid, "q": "vs-web"}).json()
    assert "inet-to-shop" in {r["name"] for r in found}
    rule = next(r for r in found if r["name"] == "inet-to-shop")

    # Regel-Detail: die VIP mit dem Virtual Service dahinter
    detail = client.get(f"/rules/{rule['id']}").json()
    (vip,) = (n for n in detail["nat_entries"] if n["name"] == "V-EXT_shop")
    names = {lb["name"]: lb for lb in vip["balancers"]}
    assert set(names) == {"vs-web"}  # ohne Port-Weiterleitung: alle Ports der VIP
    lb = names["vs-web"]
    assert lb["source"] == "avi-01" and lb["pool"] == "pool-web" and lb["ext_ip"] == VIP
    assert "10.10.0.20:8080" in lb["servers"]


def test_applikations_doku_ueber_den_load_balancer(client):
    tid = _seed(client)
    body = {"name": "Shop", "members": [{"kind": "host", "ref": "10.10.0.20"}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    doc = client.get(f"/applications/{app['id']}/doc").json()
    rows = {r["Description"]: r for t in doc["tables"] if t["index"] == 2 for r in t["rows"]}
    assert "inet-to-shop" in rows, [t["index"] for t in doc["tables"]]
    row = rows["inet-to-shop"]
    # Kette in eigenen Spalten (0.84.0): VIP der FortiGate, Load Balancer (nur der Port der
    # Regel, 443 - nicht 80), Server mit dem Port im Pool
    assert row["VIP (Firewall)"] == f"V-EXT_shop: {PUBLIC} → {VIP}"
    assert row["Load Balancer"] == f"vs-web · {VIP}:443 · Pool pool-web"
    assert row["Destination"] == "10.10.0.20:8080"
    assert row["Source"] == "all → extern (Default-Route über internet)"
    (table,) = (t for t in doc["tables"] if t["index"] == 2)
    assert table["columns"][:6] == [
        "Source",
        "Externer DNS Name",
        "VIP (Firewall)",
        "Load Balancer",
        "Destination",
        "TCP/UDP",
    ]


def test_weg_von_aussen_ueber_vip_und_load_balancer(client):
    tid = _seed(client)
    params = {"tenant_id": tid, "src": "198.51.100.1", "dst": PUBLIC, "service": "tcp/443"}
    r = client.get("/routing/trace", params=params)
    assert r.status_code == 200, r.text
    (path,) = r.json()["paths"]
    front, back = path["hops"]
    # DNAT der FortiGate auf die VIP des Load Balancers, dort weiter zum Server
    assert front["nat"]["name"] == "V-EXT_shop" and front["nat"]["to"] == [VIP]
    assert front["nat"]["balancer"] == "vs-web"
    assert front["lb"]["name"] == "vs-web" and front["lb"]["to"] == ["10.10.0.20"]
    assert back["dst"] == "10.10.0.20" and back["via"].startswith("über Load Balancer vs-web")
    assert front["rules"]["text"] == "erlaubt durch Policy 30 „inet-to-shop“"
    assert back["rules"]["text"] == "erlaubt durch Policy 9 „se-to-web“"
    assert path["policy"]["verdict"] == "allow" and path["end"]["kind"] == "connected"


def test_vip_des_load_balancers_hinter_einem_router(client):
    """Liegt die VIP des Load Balancers hinter einem Router ohne gelesenes Geraet, geht der Weg
    trotzdem vom Load Balancer zum Server weiter (0.79.0) - statt „gehört keinem Gerät“."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    fw = _fw()
    behind_router = "10.60.0.50"
    fw["nat_rules"][0]["mapped_ip"] = behind_router
    fw["routing"]["routes"].append(
        {"node": "fw-r", "context": "root", "prefix": "10.60.0.0/24", "protocol": "static",
         "next_hops": [{"ip": "10.30.0.254", "interface": "dmz"}]}
    )  # fmt: skip
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    _deliver(client, tid, client.post("/systems", json=body, headers=ADMIN).json()["id"],
             "fortigate", fw)  # fmt: skip
    avi = copy.deepcopy(AVI)
    for n in avi["nat_rules"]:
        n["ext_ip"] = behind_router
    body = {"tenant_id": tid, "name": "avi-01", "platform": "avi", "base_url": "https://avi",
            "username": "ro", "password": "x", "read_scope": "admin"}  # fmt: skip
    _deliver(client, tid, client.post("/systems", json=body, headers=ADMIN).json()["id"],
             "avi", avi)  # fmt: skip
    params = {"tenant_id": tid, "src": "198.51.100.1", "dst": PUBLIC, "service": "tcp/443"}
    (path,) = client.get("/routing/trace", params=params).json()["paths"]
    front, back = path["hops"]
    assert front["egress"]["next_hop"] == "10.30.0.254" and front["lb"]["name"] == "vs-web"
    assert back["dst"] == "10.10.0.20" and path["end"]["kind"] == "connected"


def test_verbindung_ende_zu_ende_ueber_vip_und_load_balancer(client):
    """Verbindungen der Applikation (ADR 0021): Ziel ist die öffentliche IP der VIP - der Weg
    geht über DNAT und Load Balancer zum Server; Policy 30 entscheidet an der FortiGate."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    fw = _fw()
    fw["rules"][0]["sources"] = ["198.51.100.0/30"]  # Partnernetz statt any
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    _deliver(client, tid, client.post("/systems", json=body, headers=ADMIN).json()["id"],
             "fortigate", fw)  # fmt: skip
    body = {"tenant_id": tid, "name": "avi-01", "platform": "avi", "base_url": "https://avi",
            "username": "ro", "password": "x", "read_scope": "admin"}  # fmt: skip
    _deliver(client, tid, client.post("/systems", json=body, headers=ADMIN).json()["id"],
             "avi", AVI)  # fmt: skip
    body = {"name": "Shop", "members": [{"kind": "host", "ref": "10.10.0.20"}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    conns = client.get(f"/applications/{app['id']}/connections").json()["connections"]
    (conn,) = (c for c in conns if c["src"]["ip"] == "198.51.100.0/30")
    assert conn["dst"]["ip"] == PUBLIC and conn["dst"]["label"] == "10.10.0.20"
    front, back = conn["hops"]
    (check,) = front["checks"]
    assert check["nat"]["balancer"] == "vs-web" and front["lb"]["to"] == ["10.10.0.20"]
    assert check["rules"][0]["name"] == "inet-to-shop" and check["rules"][0]["decides"]
    assert back["checks"][0]["rules"][0]["name"] == "se-to-web"
    assert conn["verdict"] == "allow" and not conn["mismatch"], conn["text"]
    # Netzwerkplan (0.85.0): Load Balancer als eigener Knoten nach der FortiGate, der zweite
    # Durchgang durch dieselbe Firewall gehoert zum Load Balancer (Policy se-to-web im Kasten)
    plan = client.get(f"/applications/{app['id']}/connections").json()["plan"]
    nodes = {n["id"]: n for n in plan["nodes"]}
    lbn = nodes[f"lb:{VIP}"]
    assert lbn["kind"] == "lb" and lbn["lines"][0].startswith(f"vs-web {VIP}:443")
    assert any("se-to-web" in line for line in lbn["rule_lines"])
    fw = next(n for n in plan["nodes"] if n["kind"] == "firewall")
    assert f"VIP V-EXT_shop → {VIP}" in fw["lines"]


def test_weg_nennt_alle_server_mit_details(client):
    """0.114.1 (Anwender: „bei den VIPs den Zielserver, aber nur einer und nur die IP“): der Hop
    nennt alle Ziel-Server - je mit Port, Zustand, Gewicht, Health-Check und Objekt der Firewall -
    sowie Methode und Health-Check der VIP bzw. des Pools."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    fw = _fw()
    fw["objects"] += [
        {"name": "H_web01", "category": "address", "values": ["10.10.0.20"], "vdom": "root"},
        {"name": "H_web02", "category": "address", "values": ["10.10.0.21"], "vdom": "root"},
    ]  # fmt: skip
    fw["nat_rules"] = [
        {"source_platform": "fortigate", "external_id": "V-EXT_shop", "name": "V-EXT_shop",
         "ext_ip": PUBLIC, "ext_port": "443", "mapped_ip": "10.10.0.20", "mapped_port": "8080",
         "portforward": True, "vip_type": "server-load-balance", "vdom": "root",
         "targets": [{"id": 1, "ip": "10.10.0.20", "port": 8080, "status": "active", "weight": 1},
                     {"id": 2, "ip": "10.10.0.21", "port": 8080, "status": "standby",
                      "weight": 2, "monitor": ["HC_tcp"]}],
         "attrs": {"ldb-method": "round-robin", "monitor": [{"name": "HC_https"}]},
         "raw": {"extintf": "wan", "comment": "Shop extern"}},
    ]  # fmt: skip
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    _deliver(client, tid, sid, "fortigate", fw)

    params = {"tenant_id": tid, "src": "198.51.100.1", "dst": PUBLIC, "service": "tcp/443"}
    r = client.get("/routing/trace", params=params)
    assert r.status_code == 200, r.text
    nat = r.json()["paths"][0]["hops"][0]["nat"]
    assert nat["to"] == ["10.10.0.20", "10.10.0.21"]
    assert nat["method"] == "round-robin" and nat["health"] == ["HC_https"]
    assert nat["extintf"] == "wan" and nat["comment"] == "Shop extern"
    first, second = nat["servers"]
    assert first == {"ip": "10.10.0.20", "port": 8080, "status": "active", "weight": 1,
                     "object": ["H_web01"]}  # fmt: skip
    assert second["status"] == "standby" and second["weight"] == 2
    assert second["health"] == ["HC_tcp"] and second["object"] == ["H_web02"]


def test_load_balancer_mit_allen_servern_des_pools(client):
    """0.114.1: der Load Balancer im Hop nennt alle Server des Pools (auch deaktivierte) und die
    Methode."""
    tid = _seed(client)
    params = {"tenant_id": tid, "src": "198.51.100.1", "dst": PUBLIC, "service": "tcp/443"}
    front = client.get("/routing/trace", params=params).json()["paths"][0]["hops"][0]
    lb = front["lb"]
    assert lb["method"] == "LB_ALGORITHM_ROUND_ROBIN" and lb["servers"] == 2
    assert [(m["ip"], m["port"], m["status"]) for m in lb["members"]] == [
        ("10.10.0.20", 8080, "active"),
        ("10.10.0.21", 8080, "disable"),
    ]
    assert all(m["pool"] == "pool-web" for m in lb["members"])


def test_ziel_hinter_vip_und_load_balancer_ist_die_epg_der_server(client, session_factory):
    """0.117.0 (Anwender: „wenn ich ein V-EXT_ auflöse, nimmt er als Ziel nicht die VMs hinter dem
    Loadbalancer, sondern das Subnetz … Es sollte dann die EG wählen und diese darstellen“): Ziel
    und Zielzone kommen von den Servern am Ende des Wegs, nicht von der VIP bzw. dem Netz des
    Load Balancers; die Namenssuche nennt die Kette."""
    from app.models import InvSubnet, SourceSystem

    tid = _seed(client)
    s = session_factory()
    fw = s.query(SourceSystem).filter_by(tenant_id=tid, name="fw-r").one()
    s.add(InvSubnet(tenant_id=tid, source_system_id=fw.id, prefix="10.10.0.0/24",
                    context="epg", context_name="EG_web", aci_tenant="TE", ap="AP_shop",
                    epg="EG_web"))  # fmt: skip
    s.commit()
    s.close()

    def zone(name, net):
        body = {"tenant_id": tid, "name": name, "selectors": [{"kind": "prefix", "value": net}]}
        r = client.post("/zones", json=body, headers=OP)
        assert r.status_code == 201, r.text

    zone("Shop", "10.10.0.0/24")
    zone("DMZ", "10.30.0.0/24")  # Netz der VIP des Load Balancers
    # VIP des Load Balancers als Ziel (Treffer „V-EXT_shop → 10.30.0.50“)
    params = {"tenant_id": tid, "src": "10.30.0.10", "dst": VIP, "service": "tcp/443"}
    got = client.get("/routing/trace", params=params).json()
    target = got["target"]
    assert target["balancer"] == "vs-web" and target["ips"] == ["10.10.0.20", "10.10.0.21"]
    assert target["epgs"] == [{"epg": "TE|AP_shop|EG_web", "servers": 2}]
    assert [z["name"] for z in got["zones"]["dst"]] == ["Shop"]  # nicht DMZ
    # von aussen über die VIP der FortiGate: dieselbe EPG, mit der VIP
    params = {"tenant_id": tid, "src": "198.51.100.1", "dst": PUBLIC, "service": "tcp/443"}
    target = client.get("/routing/trace", params=params).json()["target"]
    assert target["vip"] == "V-EXT_shop" and target["balancer"] == "vs-web"
    assert target["epgs"][0]["epg"] == "TE|AP_shop|EG_web"
    # ohne VIP bzw. Load Balancer auf dem Weg: kein Ziel dahinter
    params = {"tenant_id": tid, "src": "198.51.100.1", "dst": "10.10.0.20"}
    assert "target" not in client.get("/routing/trace", params=params).json()

    # Namenssuche: die VIP selbst (externe IP) mit der Kette dahinter
    hits = client.get("/routing/resolve", params={"tenant_id": tid, "q": "V-EXT_shop"}).json()
    vip = next(h for h in hits["hits"] if h["ip"] == PUBLIC)
    assert vip["name"] == "V-EXT_shop"
    assert vip["detail"].endswith(f"→ {VIP} · Load Balancer vs-web → EPG TE|AP_shop|EG_web "
                                  "(2 Server)")  # fmt: skip
