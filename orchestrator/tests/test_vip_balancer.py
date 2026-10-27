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

    # Publizierte Dienste: Kette in beide Richtungen, Regeln der VIP davor zaehlen mit
    rows = client.get("/published-services", params={"tenant_id": tid}).json()
    shop = next(r for r in rows if r["name"] == "V-EXT_shop")
    assert {b["name"] for b in shop["balancers"]} == {"vs-web"}
    web = next(r for r in rows if r["name"] == "vs-web" and r["ext_port"] == "443")
    assert [f["name"] for f in web["fronted_by"]] == ["V-EXT_shop"]
    assert web["fronted_by"][0]["ext_ip"] == PUBLIC and web["rule_count"] >= 1
    one = client.get("/published-services/vs-web", params={"tenant_id": tid}).json()
    assert "inet-to-shop" in {r["name"] for r in one["rules"]}


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
