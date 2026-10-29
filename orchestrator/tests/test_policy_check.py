# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Regeln auf dem Weg (0.22.0, ADR 0019 Stufe 3): je FortiGate-Hop die Policies, die auf Ein-
und Ausgang (Interface bzw. Zone), Quelle, Ziel und Dienst passen - die erste sichere
entscheidet; moegliche davor (Dienst, FQDN, Benutzer) machen das Urteil „abhaengig“.

Eine Firewall mit LAN, DMZ und Internet, Policies in dieser Reihenfolge:
1. lan -> dmz Web (tcp/443) erlauben   2. lan -> dmz alles sperren
3. lan -> internet FQDN (tcp/443)      4. lan -> internet alles fuer eine Benutzergruppe
5. any -> any sperren, abgeschaltet
6. internet -> dmz Web (tcp/443) erlauben
7. lan -> internet das DMZ-Netz (tcp/22) - falscher Ausgang, kann nie greifen
"""

from ipaddress import ip_address

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
        _policy(6, "inet-to-web", "internet", "dmz", ["all"], ["10.30.0.10"], ["tcp/443"]),
        _policy(
            7,
            "lan-to-dmz-falsch",
            "lan",
            "internet",
            ["10.10.0.0/24"],
            ["10.30.0.0/24"],
            ["tcp/22"],
        ),
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
        ("6", ["Eingang"]),
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
    [_policy(20, "edge-to-app", "transit", "server", ["10.70.0.100/31"], ["10.40.0.80"],
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
    # Pool overload mit zwei IPs (0.47.0): die Quelle danach ist der ganze Bereich
    assert edge["snat"] == {"pool": "IPP_e", "to": "10.70.0.100/31", "sure": False,
                            "type": "overload", "range": "10.70.0.100-10.70.0.101"}  # fmt: skip
    assert inner["rules"]["text"] == "erlaubt durch Policy 20 „edge-to-app“"
    assert path["policy"] == {"verdict": "allow", "text": "erlaubt an allen 2 Firewalls"}

    # anderer Eingang: die VIP gilt nur an wan - kein NAT
    nodes = client.get("/routing/summary", params={"tenant_id": tid}).json()["nodes"]
    start = next(n["key"] for n in nodes if n["label"] == "fw-e")
    other = {**params, "src": "10.70.0.9", "start": start}
    paths = client.get("/routing/trace", params=other).json()["paths"]
    assert paths and all("nat" not in p["hops"][0] for p in paths)
    assert paths[0]["hops"][0]["ingress"]["interface"] == "to-i"


def test_sperre_zuoberst_zeigt_die_policies_dahinter(client, session_factory):
    """0.78.4 (Rueckmeldung des Anwenders): eine Sperre zuoberst („Default Block …“) entscheidet
    - die Weg-Suche zeigt trotzdem die passenden Policies dahinter (nicht erreicht) und nennt
    die erlaubende; die Applikations-Doku rechnet ohne sie."""
    from app.store import policy_check

    fw = {**FW, "rules": [
        _policy(9, "Default Block Darknet", "lan", "dmz", ["all"], ["10.30.0.0/24"],
                ["tcp/443", "tcp/445"], "deny"),
        *FW["rules"],
    ]}  # fmt: skip
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=fw)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text

    rules, policy = _hop(client, tid, src="10.10.0.5", dst="10.30.0.10", service="tcp/443")
    assert rules["verdict"] == "deny" and policy["verdict"] == "deny"
    (first,) = rules["rules"]
    assert (first["external_id"], first["decides"], first["dst"]) == ("9", True, "10.30.0.0/24")
    # dahinter: Policy 1 erlaubt, Policy 2 sperrt - nicht erreicht
    assert [(e["external_id"], e["action"], e["after"], e["decides"]) for e in rules["after"]] == [
        ("1", "allow", True, False), ("2", "deny", True, False)]  # fmt: skip
    assert rules["text"] == (
        "gesperrt durch Policy 9 „Default Block Darknet“ – dahinter würde Policy 1 "
        "„lan-to-dmz-web“ erlauben (nicht erreicht)"
    )
    # anderer Dienst: die Sperre passt nicht, Policy 2 entscheidet wie bisher
    rules, _ = _hop(client, tid, src="10.10.0.5", dst="10.30.0.10", service="tcp/22")
    assert [e["external_id"] for e in rules["rules"]] == ["2"] and "after" not in rules

    # ohne die Weg-Suche (Applikations-Doku, Abgleich): keine Policies dahinter
    with session_factory() as session:
        checks = policy_check.Checks.load(session, tid)
        assert checks.after == 0
        hop = {"ingress": {"interface": "lan", "zone": "lan"},
               "egress": {"interface": "dmz", "zone": "dmz"}}  # fmt: skip
        result = checks.checker.check(sid, "root", hop["ingress"], hop["egress"],
                                      ip_address("10.10.0.5"), ip_address("10.30.0.10"),
                                      ("tcp", 443))  # fmt: skip
        assert result["verdict"] == "deny" and "after" not in result
        assert result["text"] == "gesperrt durch Policy 9 „Default Block Darknet“"


GEO = _fw(
    "fw-g",
    [_policy(1, "block-geo", "any", "any", ["all"], ["geo:G_RU"], ["all"], "deny"),
     _policy(2, "block-isdb", "any", "any", ["all"], ["isdb:Botnet-C&C.Server"], ["all"], "deny",
             **{"internet-service": "enable",
                "internet-service-name": [{"name": "Botnet-C&C.Server"}]}),
     _policy(3, "block-custom", "any", "internet", ["all"], ["isdb:Eigener-Dienst"], ["all"],
             "deny", **{"internet-service": "enable",
                        "internet-service-custom": [{"name": "Eigener-Dienst"}]}),
     _policy(4, "lan-to-dmz", "lan", "dmz", ["all"], ["10.30.0.0/24"], ["tcp/443"]),
     _policy(5, "lan-to-inet", "lan", "internet", ["all"], ["all"], ["all"])],
    [_iface("lan", "10.10.0.1/24", "lan"), _iface("dmz", "10.30.0.1/24", "dmz"),
     _iface("wan", "198.51.100.2/30", "internet")],
    [_route("10.10.0.0/24", [{"interface": "lan"}], "connected"),
     _route("10.30.0.0/24", [{"interface": "dmz"}], "connected"),
     _route("198.51.100.0/30", [{"interface": "wan"}], "connected"),
     _route("10.50.0.0/16", [{"ip": "10.10.0.254", "interface": "lan"}]),
     _route("0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan"}])],
    objects=[{"name": "G_RU", "category": "address", "obj_type": "geography", "values": [],
              "vdom": "root"}],
)  # fmt: skip


def test_abhaengig_mit_grund_geo_isdb_und_eingang_nach_der_route(client):
    """0.112.0 (Anwender: „abhängig“, obwohl die Regeln offen sind): Land und Internet Service
    treffen keine private Adresse; der Grund steht im Urteil des Wegs; am gewählten Start ohne
    angeschlossene Quelle ist der Eingang das Interface der Route zur Quelle."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-g", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=GEO)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text

    # privates Ziel: Geo- und ISDB-Sperre treffen es nicht - Policy 4 entscheidet sicher
    rules, policy = _hop(client, tid, src="10.10.0.5", dst="10.30.0.10", service="tcp/443")
    assert rules["text"] == "erlaubt durch Policy 4 „lan-to-dmz“"
    assert [x["external_id"] for x in rules["rules"]] == ["4"]
    assert policy == {"verdict": "allow", "text": "erlaubt"}

    # öffentliches Ziel: Land und Internet Service möglich - abhängig, mit Grund im Weg
    rules, policy = _hop(client, tid, src="10.10.0.5", dst="203.0.113.10", service="tcp/443")
    assert [(x["external_id"], x["unsure"]) for x in rules["rules"]] == [
        ("1", ["Ziel"]), ("2", ["Ziel"]), ("3", ["Ziel"]), ("5", [])]  # fmt: skip
    assert policy == {
        "verdict": "depends",
        "text": "abhängig an fw-g (Policy 1 „block-geo“ kann vor Policy 5 „lan-to-inet“ greifen "
        "– abhängig von: Ziel)",
    }
    en = client.get("/routing/trace", headers={"Accept-Language": "en"},
                    params={"tenant_id": tid, "src": "10.10.0.5", "dst": "203.0.113.10",
                            "service": "tcp/443"}).json()  # fmt: skip
    assert en["paths"][0]["policy"]["text"].startswith("depends at fw-g (Policy 1 “block-geo”")

    # eigener Internet Service: kann private Adressen enthalten - bleibt möglich
    rules, _ = _hop(client, tid, src="10.10.0.5", dst="10.99.0.5", service="tcp/443")
    assert [(x["external_id"], x["unsure"]) for x in rules["rules"]] == [("3", ["Ziel"]), ("5", [])]

    # Start gewählt, Quelle nicht angeschlossen: Eingang lan nach der Route zur Quelle
    nodes = client.get("/routing/summary", params={"tenant_id": tid}).json()["nodes"]
    params = {"tenant_id": tid, "start": nodes[0]["key"], "src": "10.50.0.5",
              "dst": "10.30.0.10", "service": "tcp/443"}  # fmt: skip
    (path,) = client.get("/routing/trace", params=params).json()["paths"]
    (hop,) = path["hops"]
    assert hop["ingress"]["interface"] == "lan"
    assert hop["via"] == "gewählt, Eingang lan nach der Route zur Quelle"
    assert hop["rules"]["text"] == "erlaubt durch Policy 4 „lan-to-dmz“"


def test_regel_in_der_weg_suche(client):
    """0.112.0 (Anwender: Knopf Weg-Suche bei Regeln und in der Doku): Quelle, Ziel und Dienst
    einer Regel für `#wege?rule=<id>` - ohne any, Dienste wie in der Weg-Suche."""
    tid = _seed(client)
    rules = {r["name"]: r["id"] for r in client.get("/rules", params={"tenant_id": tid}).json()}
    web = client.get(f"/rules/{rules['lan-to-dmz-web']}/path-search").json()
    assert web == {
        "rule_id": rules["lan-to-dmz-web"], "name": "lan-to-dmz-web",
        "src": [{"value": "10.10.0.0/24", "name": None}],
        "dst": [{"value": "10.30.0.10", "name": None}],
        "services": ["tcp/443"], "src_exit": None, "start": None,
    }  # fmt: skip
    deny = client.get(f"/rules/{rules['lan-to-dmz-deny']}/path-search").json()
    assert (deny["src"], deny["dst"], deny["services"]) == ([], [], [])  # any, alle Dienste
    assert client.get("/rules/999999/path-search").status_code == 404

    # 0.113.1 (Anwender: „hier jeweils die Quelle mindestens angeben“): Quelle any -> Start an
    # der Firewall der Regel bzw. die Anbindung an ihrem Eingang; der Weg geht bis zum Ziel
    sid = client.get("/systems", params={"tenant_id": tid}).json()[0]["id"]
    inet = client.get(f"/rules/{rules['inet-to-web']}/path-search").json()
    assert (inet["src"], inet["src_exit"], inet["start"]) == ([], None, f"{sid}|root")
    assert inet["ports"] == ["internet"]
    # mit dem Eingang der Regel am Start: Policy 6 sicher, nicht „abhaengig (Eingang)“
    params = {"tenant_id": tid, "start": inet["start"], "start_port": "internet",
              "dst": "10.30.0.10", "service": "tcp/443"}  # fmt: skip
    (path,) = client.get("/routing/trace", params=params).json()["paths"]
    assert path["policy"]["verdict"] == "allow"
    body = {"tenant_id": tid, "name": "Internet", "kind": "internet", "interfaces": ["internet"]}
    exit_id = client.post("/routing/exits", json=body, headers=ADMIN).json()["id"]
    inet = client.get(f"/rules/{rules['inet-to-web']}/path-search").json()
    assert inet["src_exit"] == {"ref": f"exit:{exit_id}@{sid}", "name": "Internet",
                                "kind": "internet", "ports": ["internet"]}  # fmt: skip
    params = {"tenant_id": tid, "src_exit": inet["src_exit"]["ref"], "dst": "10.30.0.10",
              "service": "tcp/443"}  # fmt: skip
    trace = client.get("/routing/trace", params=params).json()
    (path,) = trace["paths"]
    assert path["policy"]["verdict"] == "allow"
