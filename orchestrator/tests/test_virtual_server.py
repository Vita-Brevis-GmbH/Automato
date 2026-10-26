"""FortiGate Virtual Server (VIP-Typ server-load-balance): Real Server, Lastverteilung und
Health-Checks in NAT-Sicht, Suche und Regeln (Dev-Befund 0.10.0)."""

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}
VS = "V-EXT_shop.example.ch"

OBJECTS = [
    {"name": "H_shop01", "category": "address", "family": "v4", "values": ["10.10.30.11"]},
    {"name": "H_shop02", "category": "address", "family": "v4", "values": ["10.10.30.12"]},
    {"name": VS, "category": "vip", "family": "v4", "obj_type": "server-load-balance",
     "values": ["203.0.113.20", "10.10.30.11", "10.10.30.12"]},
]  # fmt: skip

RULES = [
    {
        "source_platform": "fortigate",
        "external_id": "1",
        "name": "shop-in",
        "action": "allow",
        "sources": ["all"],
        "destinations": [f"vip:{VS}=203.0.113.20"],
        "services": ["tcp/443"],
        "source_refs": ["all"],
        "destination_refs": [VS],
        "service_refs": ["HTTPS"],
        "family": "v4",
    }
]

NAT = [
    {
        "source_platform": "fortigate",
        "external_id": VS,
        "name": VS,
        "ext_ip": "203.0.113.20",
        "ext_port": "443",
        "mapped_ip": "10.10.30.11",
        "mapped_port": "8443",
        "protocol": "https",
        "vip_type": "server-load-balance",
        "family": "v4",
        "targets": [
            {"id": 1, "ip": "10.10.30.11", "port": 8443, "status": "active", "weight": 1},
            {
                "id": 2,
                "ip": "10.10.30.12",
                "port": 8443,
                "status": "standby",
                "weight": 2,
                "monitor": ["HC_tcp"],
            },
        ],  # fmt: skip
        "attrs": {
            "server-type": "https",
            "ldb-method": "round-robin",
            "monitor": [{"name": "HC_https", "type": "https", "port": 8443}],
        },
        "raw": {"name": VS, "type": "server-load-balance", "extintf": "wan1"},
    }
]


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-01", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    result = {"status": "completed", "rules": RULES, "objects": OBJECTS, "nat_rules": NAT}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    return tid


def test_virtual_server_real_server_suche_und_regeln(client):
    tid = _seed(client)
    (vs,) = client.get("/nat", params={"tenant_id": tid}).json()
    assert vs["vip_type"] == "server-load-balance" and vs["rule_count"] == 1
    assert [(t["ip"], t["status"], t["servers"]) for t in vs["targets"]] == [
        ("10.10.30.11", "active", ["H_shop01"]),
        ("10.10.30.12", "standby", ["H_shop02"]),
    ]
    assert vs["targets"][1]["monitor"] == ["HC_tcp"]
    assert vs["attrs"]["ldb-method"] == "round-robin"

    # Suche ueber Real Server, Methode und Health-Check
    for q in ("10.10.30.12", "H_shop02", "round-robin", "HC_https", "virtual server"):
        found = client.get("/nat", params={"tenant_id": tid, "q": q}).json()
        assert [n["name"] for n in found] == [VS], q

    detail = client.get(f"/nat/{vs['id']}").json()
    assert [r["name"] for r in detail["rules"]] == ["shop-in"]
    assert detail["attrs"]["monitor"][0]["name"] == "HC_https"

    # Regel trifft auch ueber den zweiten Real Server (DNAT-Ziel)
    hits = client.get("/rules", params={"tenant_id": tid, "q": "10.10.30.12"}).json()
    assert [r["name"] for r in hits] == ["shop-in"]
    assert hits[0]["nat"]["vips"] == [VS]
