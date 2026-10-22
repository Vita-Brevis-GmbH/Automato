# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Regeln auf dem Weg (0.22.0, ADR 0019 Stufe 3): je FortiGate-Hop die Policies, die auf Ein-
und Ausgang (Interface bzw. Zone), Quelle, Ziel und Dienst passen - die erste sichere
entscheidet; moegliche davor (Dienst, FQDN, Benutzer) machen das Urteil „abhaengig“.

Eine Firewall mit LAN, DMZ und Internet, Policies in dieser Reihenfolge:
1. lan -> dmz Web (tcp/443) erlauben   2. lan -> dmz alles sperren
3. lan -> internet FQDN (tcp/443)      4. lan -> internet alles fuer eine Benutzergruppe
5. any -> any sperren, abgeschaltet
"""

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _route(prefix, hops, protocol="static"):
    return {"node": "fw-r", "context": "root", "prefix": prefix, "protocol": protocol,
            "next_hops": hops}  # fmt: skip


def _iface(name, ip, zone):
    return {"device": "fw-r", "name": name, "ips": [ip], "zone": zone, "vdom": "root"}


def _policy(pid, name, src_if, dst_if, src, dst, services, action="allow", **raw):
    return {
        "source_platform": "fortigate",
        "external_id": str(pid),
        "name": name,
        "action": action,
        "sources": src,
        "destinations": dst,
        "services": services,
        "src_zone": src_if,
        "dst_zone": dst_if,
        "vdom": "root",
        "family": "v4",
        "raw": {"policyid": pid, "srcintf": [{"name": src_if}], "dstintf": [{"name": dst_if}],
                "status": "enable", "schedule": "always", **raw},
    }  # fmt: skip


FW = {
    "status": "completed",
    "rules": [
        _policy(1, "lan-to-dmz-web", "lan", "dmz", ["10.10.0.0/24"], ["10.30.0.10"], ["tcp/443"]),
        _policy(2, "lan-to-dmz-deny", "lan", "dmz", ["all"], ["all"], ["all"], "deny"),
        _policy(
            3,
            "lan-to-update",
            "lan",
            "internet",
            ["10.10.0.0/24"],
            ["fqdn:update.example.ch"],
            ["tcp/443"],
        ),
        _policy(
            4,
            "lan-to-inet-users",
            "lan",
            "internet",
            ["all"],
            ["all"],
            ["all"],
            groups=[{"name": "G_staff"}],
        ),
        _policy(5, "block-all", "any", "any", ["all"], ["all"], ["all"], "deny", status="disable"),
    ],  # fmt: skip
    "inventory": {
        "devices": [{"name": "fw-r", "role": "firewall"}],
        "interfaces": [
            _iface("lan", "10.10.0.1/24", "lan"),
            _iface("dmz", "10.30.0.1/24", "dmz"),
            _iface("wan", "198.51.100.2/30", "internet"),
        ],
    },
    "routing": {
        "contexts": [{"node": "fw-r", "context": "root"}],
        "routes": [
            _route("10.10.0.0/24", [{"interface": "lan"}], "connected"),
            _route("10.30.0.0/24", [{"interface": "dmz"}], "connected"),
            _route("198.51.100.0/30", [{"interface": "wan"}], "connected"),
            _route("0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan"}]),
        ],
    },
}


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=FW)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    return tid


def _hop(client, tid, **params) -> tuple[dict, dict]:
    r = client.get("/routing/trace", params={"tenant_id": tid, **params})
    assert r.status_code == 200, r.text
    (path,) = r.json()["paths"]
    (hop,) = path["hops"]
    return hop["rules"], path["policy"]


def test_regeln_auf_dem_weg(client):
    tid = _seed(client)
    web = {"src": "10.10.0.5", "dst": "10.30.0.10"}

    # Dienst passt: Policy 1 entscheidet
    rules, policy = _hop(client, tid, **web, service="tcp/443")
    assert rules["verdict"] == "allow"
    assert rules["text"] == "erlaubt durch Policy 1 „lan-to-dmz-web“"
    (first,) = rules["rules"]
    assert (first["decides"], first["src"], first["dst"]) == (True, "10.10.0.0/24", "10.30.0.10")
    assert policy == {"verdict": "allow", "text": "erlaubt"}

    # anderer Dienst: Policy 1 passt nicht, Policy 2 sperrt
    rules, policy = _hop(client, tid, **web, service="tcp/22")
    assert (rules["verdict"], [r["external_id"] for r in rules["rules"]]) == ("deny", ["2"])
    assert policy == {"verdict": "deny", "text": "gesperrt an fw-r"}

    # ohne Dienst: Policy 1 nur möglich, Policy 2 sperrt sicher - abhängig vom Dienst
    rules, policy = _hop(client, tid, **web)
    assert rules["verdict"] == "depends"
    assert rules["text"] == (
        "Policy 1 „lan-to-dmz-web“ kann vor Policy 2 „lan-to-dmz-deny“ greifen – abhängig von: "
        "Dienst"
    )
    assert [(r["external_id"], r["unsure"]) for r in rules["rules"]] == [
        ("1", ["Dienst"]),
        ("2", []),
    ]
    assert policy["verdict"] == "depends"

    # ins Internet: FQDN und Benutzergruppe nicht prüfbar, keine sichere Policy
    rules, _ = _hop(client, tid, src="10.10.0.5", dst="203.0.113.10", service="tcp/443")
    assert rules["verdict"] == "depends"
    assert [(r["external_id"], r["unsure"]) for r in rules["rules"]] == [
        ("3", ["Ziel"]),
        ("4", ["Benutzer/Gruppen"]),
    ]
    assert rules["text"].startswith("nur mögliche Policies, sonst implizit gesperrt")

    # DMZ -> LAN: keine Policy (die abgeschaltete zählt nicht) - implizit gesperrt
    rules, policy = _hop(client, tid, src="10.30.0.10", dst="10.10.0.5")
    assert rules == {
        "verdict": "implicit_deny",
        "rules": [],
        "text": "keine passende Policy – implizit gesperrt",
    }
    assert policy["text"] == "gesperrt an fw-r"

    # Startpunkt ohne Quelle: Eingang und Quelle unbekannt - nur möglich
    nodes = client.get("/routing/summary", params={"tenant_id": tid}).json()["nodes"]
    rules, _ = _hop(client, tid, start=nodes[0]["key"], dst="10.30.0.10", service="tcp/443")
    assert [(r["external_id"], r["unsure"]) for r in rules["rules"]] == [
        ("1", ["Eingang", "Quelle"]),
        ("2", ["Eingang"]),
    ]
    p = {"tenant_id": tid, **web, "service": "http"}
    assert client.get("/routing/trace", params=p).status_code == 422


def _fw(name, rules, interfaces, routes, objects=(), nat=()):
    return {
        "status": "completed",
        "rules": rules,
        "objects": list(objects),
        "nat_rules": list(nat),
        "inventory": {"devices": [{"name": name, "role": "firewall"}],
                      "interfaces": [{**i, "device": name} for i in interfaces]},
        "routing": {"contexts": [{"node": name, "context": "root"}],
                    "routes": [{**r, "node": name} for r in routes]},
    }  # fmt: skip


EDGE = _fw(
    "fw-e",
    [_policy(10, "inet-to-app", "internet", "transit", ["all"], ["vip:VIP_app=198.51.100.80"],
             ["tcp/8443"], nat="enable", ippool="enable", poolname=[{"name": "IPP_e"}])
     | {"destination_refs": ["VIP_app"]}],
    [_iface("wan", "203.0.113.1/24", "internet"), _iface("to-i", "10.70.0.1/24", "transit")],
    [_route("203.0.113.0/24", [{"interface": "wan"}], "connected"),
     _route("10.70.0.0/24", [{"interface": "to-i"}], "connected"),
     _route("10.40.0.0/24", [{"ip": "10.70.0.2", "interface": "to-i"}])],
    objects=[{"name": "VIP_app", "category": "vip", "values": ["198.51.100.80"], "vdom": "root"}],
    nat=[{"source_platform": "fortigate", "external_id": "VIP_app", "name": "VIP_app",
          "ext_ip": "198.51.100.80", "mapped_ip": "10.40.0.80", "ext_port": "443",
          "mapped_port": "8443", "portforward": True, "vdom": "root",
          "raw": {"extintf": "wan"}},
         {"source_platform": "fortigate", "external_id": "IPP_e", "name": "IPP_e",
          "nat_type": "snat_pool", "ext_ip": "10.70.0.100-10.70.0.101", "vdom": "root"}],
)  # fmt: skip
INNER = _fw(
    "fw-i",
    [_policy(20, "edge-to-app", "transit", "server", ["10.70.0.100"], ["10.40.0.80"],
             ["tcp/8443"])],
    [_iface("to-e", "10.70.0.2/24", "transit"), _iface("srv", "10.40.0.1/24", "server")],
    [_route("10.70.0.0/24", [{"interface": "to-e"}], "connected"),
     _route("10.40.0.0/24", [{"interface": "srv"}], "connected")],
)  # fmt: skip


def test_nat_auf_dem_weg(client):
    """0.23.0: Die VIP der äusseren Firewall schickt den Weg zur internen IP (mit internem
    Port); die SNAT-Adresse des IP-Pools ist die Quelle für die innere Firewall."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    for name, result in (("fw-e", EDGE), ("fw-i", INNER)):
        body = {"tenant_id": tid, "name": name, "platform": "fortigate", "base_url": "https://x"}
        sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
        run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
        run_id = client.post("/jobs/collect", json=run).json()["run_id"]
        r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
        assert r.status_code == 200 and r.json()["status"] == "completed", r.text

    params = {"tenant_id": tid, "src": "203.0.113.50", "dst": "198.51.100.80", "service": "tcp/443"}
    (path,) = client.get("/routing/trace", params=params).json()["paths"]
    edge, inner = path["hops"]
    assert [h["label"] for h in path["hops"]] == ["fw-e", "fw-i"]
    assert edge["nat"] | {"id": 0} == {
        "name": "VIP_app",
        "id": 0,
        "ext": "198.51.100.80",
        "to": ["10.40.0.80"],
        "type": None,
        "ports": ["443", "8443"],
    }
    assert "dst" not in edge and inner["dst"] == "10.40.0.80"
    assert path["end"]["kind"] == "connected"
    # VIP-Policy mit internem Port, SNAT auf den Pool, innen nur die Pool-Adresse erlaubt
    assert edge["rules"]["text"] == "erlaubt durch Policy 10 „inet-to-app“"
    assert edge["snat"] == {"pool": "IPP_e", "to": "10.70.0.100"}
    assert inner["rules"]["text"] == "erlaubt durch Policy 20 „edge-to-app“"
    assert path["policy"] == {"verdict": "allow", "text": "erlaubt an allen 2 Firewalls"}

    # anderer Eingang: die VIP gilt nur an wan - kein NAT
    nodes = client.get("/routing/summary", params={"tenant_id": tid}).json()["nodes"]
    start = next(n["key"] for n in nodes if n["label"] == "fw-e")
    other = {**params, "src": "10.70.0.9", "start": start}
    paths = client.get("/routing/trace", params=other).json()["paths"]
    assert paths and all("nat" not in p["hops"][0] for p in paths)
    assert paths[0]["hops"][0]["ingress"]["interface"] == "to-i"
