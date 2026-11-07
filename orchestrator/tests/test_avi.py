"""NSX Advanced Load Balancer (Avi) als lesende Quelle (0.77.0, ADR 0027): Quellsystem, Einlesen
der Virtual Services als publizierte Dienste, NAT-Detail und der Weg ueber den Load Balancer.

Firewall fw-r wie in den Policy-Tests (lan 10.10.0.0/24, dmz 10.30.0.0/24, wan); die VIP des
Virtual Service liegt im DMZ-Netz, die Server des Pools im LAN.
"""

import copy

from test_policy_check import ADMIN, FW, TOKEN, _policy

VIP = "10.30.0.50"


def _vs(name, port, servers, snat="service_engine", **attrs):
    return {
        "source_platform": "avi",
        "external_id": f"admin|{name}|{VIP}|{port}",
        "name": name,
        "nat_type": "dnat_vip",
        "ext_ip": VIP,
        "ext_port": str(port),
        "mapped_ip": servers[0][0],
        "mapped_port": str(servers[0][1]),
        "protocol": "tcp",
        "portforward": True,
        "vip_type": "server-load-balance",
        "family": "v4",
        "targets": [{"ip": ip, "port": p, "status": st, "pool": "pool-web"}
                    for ip, p, st in servers],  # fmt: skip
        "attrs": {"platform": "avi", "tenant": "admin", "pool": "pool-web", "snat": snat,
                  "lb_algorithm": "LB_ALGORITHM_ROUND_ROBIN", "oper_status": "OPER_UP", **attrs},
        "raw": {"tenant": "admin", "virtual_service": name},
    }  # fmt: skip


AVI = {
    "status": "completed",
    "nat_rules": [
        _vs("vs-web", 443, [("10.10.0.20", 8080, "active"), ("10.10.0.21", 8080, "disable")]),
        _vs("vs-web", 80, [("10.10.0.20", 8080, "active")]),
    ],
}


def _deliver(client, tid, sid, platform, result):
    run = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    if platform == "avi":  # 0.78.5: Spalte „Einträge“ = publizierte Dienste, nicht 0
        assert client.get(f"/jobs/{run_id}").json()["rule_count"] == len(result["nat_rules"])


def _seed(client) -> tuple[int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    fw = copy.deepcopy(FW)
    fw["rules"] = [
        _policy(8, "lan-to-vip", "lan", "dmz", ["10.10.0.0/24"], [VIP], ["tcp/443"]),
        # Service Engine (SNAT, im Netz der VIP) zum Server
        _policy(9, "se-to-web", "dmz", "lan", ["10.30.0.0/24"], ["10.10.0.20"], ["tcp/8080"]),
        *fw["rules"],
    ]
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    _deliver(client, tid, client.post("/systems", json=body, headers=ADMIN).json()["id"],
             "fortigate", fw)  # fmt: skip
    body = {"tenant_id": tid, "name": "avi-01", "platform": "avi", "base_url": "https://avi",
            "username": "ro", "password": "x", "read_scope": "admin, kunde-a, admin"}  # fmt: skip
    r = client.post("/systems", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    assert r.json()["read_scope"] == "admin,kunde-a" and r.json()["has_credentials"]
    sid = r.json()["id"]
    _deliver(client, tid, sid, "avi", AVI)
    return tid, sid


def test_quellsystem_und_publizierte_dienste(client):
    tid, sid = _seed(client)
    bad = {"tenant_id": tid, "name": "avi-02", "platform": "avi", "read_scope": "a;b"}
    assert client.post("/systems", json=bad, headers=ADMIN).status_code == 422

    nats = client.get("/nat", params={"tenant_id": tid}).json()
    nat = next(n for n in nats if n["name"] == "vs-web" and n["ext_port"] == "443")
    assert nat["source_system_id"] == sid
    detail = client.get(f"/nat/{nat['id']}", params={"tenant_id": tid}).json()
    assert detail["settings"]["Pool"] == "pool-web"
    assert detail["settings"]["Quelle zum Server"] == "Service Engine (SNAT)"
    assert detail["settings"]["Avi-Tenant"] == "admin"


def test_weg_ueber_den_load_balancer(client):
    tid, _ = _seed(client)
    params = {"tenant_id": tid, "src": "10.10.0.5", "dst": VIP, "service": "tcp/443"}
    r = client.get("/routing/trace", params=params)
    assert r.status_code == 200, r.text
    (path,) = r.json()["paths"]
    front, back = path["hops"]
    # Abschnitt 1: Client -> VIP (im DMZ-Netz); Abschnitt 2 am selben Knoten zum Server
    assert front["lb"]["name"] == "vs-web" and front["lb"]["to"] == ["10.10.0.20"]
    assert front["lb"]["server_port"] == 8080 and front["lb"]["source"] == "avi-01"
    assert front["egress"]["interface"] == "dmz" and back["ingress"]["interface"] == "dmz"
    assert back["dst"] == "10.10.0.20" and back["via"].startswith("über Load Balancer vs-web")
    assert path["end"]["kind"] == "connected"
    # Regeln: Policy 8 fuer den Client, Policy 9 fuer die Service Engine (Quelle = VIP-Netz)
    assert front["rules"]["text"] == "erlaubt durch Policy 8 „lan-to-vip“"
    assert back["rules"]["text"] == "erlaubt durch Policy 9 „se-to-web“"
    assert path["policy"]["verdict"] == "allow"

    # anderer Port: Virtual Service auf Port 80; ohne passenden Port kein Load Balancer
    other = client.get("/routing/trace", params={**params, "service": "tcp/22"}).json()
    assert all("lb" not in h for p in other["paths"] for h in p["hops"])


def test_client_ip_bleibt(client):
    tid, sid = _seed(client)
    avi = copy.deepcopy(AVI)
    for n in avi["nat_rules"]:
        n["attrs"]["snat"] = "client"
    _deliver(client, tid, sid, "avi", avi)
    params = {"tenant_id": tid, "src": "10.10.0.5", "dst": VIP, "service": "tcp/443"}
    (path,) = client.get("/routing/trace", params=params).json()["paths"]
    # Quelle bleibt der Client: Policy 9 (nur aus dem DMZ-Netz) greift nicht
    assert path["hops"][1]["rules"]["verdict"] == "implicit_deny"
    assert path["policy"]["verdict"] in ("deny", "implicit_deny")
