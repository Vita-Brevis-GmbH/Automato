"""Parallele Firewalls (Verbund, 0.18.0): gleiche Regeln zusammenfassen, Abweichungen melden.

fw-a und fw-b (zwei Cluster mit Session-Sync) tragen dieselben Policies 1-3; Policy 4 hat auf
fw-b ein anderes Ziel, Policy 5 fehlt auf fw-b, das Objekt H_web01 hat dort eine andere IP.
"""

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _rule(ext_id: str, name: str, dst: str = "H_web01", uuid: str = "a") -> dict:
    raw = {
        "policyid": int(ext_id),
        "name": name,
        "uuid": f"{uuid}-{ext_id}",  # je Firewall verschieden - zaehlt nicht
        "status": "enable",
        "action": "accept",
        "srcintf": [{"name": "lan"}],
        "dstintf": [{"name": "dmz"}],
        "srcaddr": [{"name": "N_clients"}],
        "dstaddr": [{"name": dst}],
        "service": [{"name": "HTTPS"}],
        "schedule": "always",
        "nat": "disable",
        "comments": f"auf {uuid}",  # Kommentare zaehlen nicht
    }
    return {
        "source_platform": "fortigate",
        "external_id": ext_id,
        "name": name,
        "action": "allow",
        "sources": ["10.1.0.0/24"],
        "destinations": ["10.2.0.5/32"],
        "services": ["tcp/443"],
        "src_zone": "lan",
        "dst_zone": "dmz",
        "source_refs": ["N_clients"],
        "destination_refs": [dst],
        "service_refs": ["HTTPS"],
        "family": "v4",
        "vdom": "root",
        "raw": raw,
    }


def _objects(web_ip: str) -> list[dict]:
    return [
        {"name": "N_clients", "category": "address", "family": "v4", "values": ["10.1.0.0/24"],
         "vdom": "root"},
        {"name": "H_web01", "category": "address", "family": "v4", "values": [web_ip],
         "vdom": "root"},
        {"name": "H_web02", "category": "address", "family": "v4", "values": ["10.2.0.6/32"],
         "vdom": "root"},
        {"name": "HTTPS", "category": "service", "values": ["tcp/443"], "vdom": "root"},
    ]  # fmt: skip


FWA = {
    "status": "completed",
    "objects": _objects("10.2.0.5/32"),
    "rules": [_rule("1", "web"), _rule("2", "api"), _rule("3", "mail"), _rule("4", "db"),
              _rule("5", "nur-a")],
    "inventory": {"devices": [{"name": "fw-a", "role": "firewall"}],
                  "interfaces": [{"device": "fw-a", "name": "sync", "ips": ["10.0.9.1/30"],
                                  "vdom": "root"}]},
    "routing": {"contexts": [{"node": "fw-a", "context": "root"}],
                "ha": [{"node": "fw-a", "mode": "a-p", "sync_peers": [{"peerip": "10.0.9.2"}]}]},
}  # fmt: skip
FWB = {
    "status": "completed",
    "objects": _objects("10.2.0.99/32"),
    "rules": [_rule("1", "web", uuid="b"), _rule("2", "api", uuid="b"),
              _rule("3", "mail", uuid="b"), _rule("4", "db", dst="H_web02", uuid="b")],
    "inventory": {"devices": [{"name": "fw-b", "role": "firewall"}],
                  "interfaces": [{"device": "fw-b", "name": "sync", "ips": ["10.0.9.2/30"],
                                  "vdom": "root"}]},
    "routing": {"contexts": [{"node": "fw-b", "context": "root"}]},
}  # fmt: skip


def _deliver(client, tid, sid, result):
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.json()["status"] == "completed", r.text


def _seed(client) -> tuple[int, int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    ids = []
    for name, result in (("fw-a", FWA), ("fw-b", FWB)):
        body = {"tenant_id": tid, "name": name, "platform": "fortigate", "base_url": "https://x"}
        sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
        _deliver(client, tid, sid, result)
        ids.append(sid)
    return tid, ids[0], ids[1]


def _rules(client, **params) -> list[dict]:
    return client.get("/rules", params=params).json()


def test_vorschlag_und_zusammenfassen(client):
    tid, fwa, fwb = _seed(client)
    assert len(_rules(client, tenant_id=tid)) == 9  # noch kein Verbund: alles doppelt

    (s,) = client.get("/systems/parallel-suggestions", params={"tenant_id": tid}).json()
    assert s["names"] == ["fw-a", "fw-b"] and s["group"] == "fw-a + fw-b"
    assert s["reasons"] == ["Session-Sync (FGSP)", "3 von 4 Policies gleich"]

    body = {"tenant_id": tid, "group": "Cluster Nord", "system_ids": [fwa, fwb]}
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    assert client.post("/systems/parallel-group", json=body, headers=viewer).status_code == 403
    r = client.post("/systems/parallel-group", json=body, headers=ADMIN)
    assert [x["parallel_group"] for x in r.json()] == ["Cluster Nord", "Cluster Nord"]
    assert client.get("/systems/parallel-suggestions", params={"tenant_id": tid}).json() == []

    # gleiche Policies einmal (fuehrend fw-a), abweichende beide, fehlende nur auf fw-a
    rows = _rules(client, tenant_id=tid)
    got = sorted((r["name"], r["source_system_id"]) for r in rows)
    assert got == [("api", fwa), ("db", fwa), ("db", fwb), ("mail", fwa), ("nur-a", fwa),
                   ("web", fwa)]  # fmt: skip
    web = next(r for r in rows if r["name"] == "web")
    assert web["parallel"] == {"group": "Cluster Nord", "same": ["fw-b"], "differs": [],
                               "missing": []}  # fmt: skip
    db_b = next(r for r in rows if r["name"] == "db" and r["source_system_id"] == fwb)
    assert db_b["parallel"]["fields"] == ["Ziel"] and db_b["parallel"]["lead"] == "fw-a"
    nur_a = next(r for r in rows if r["name"] == "nur-a")
    assert nur_a["parallel"]["missing"] == ["fw-b"]
    # mit Filter auf eine Firewall: deren Regeln vollstaendig
    assert len(_rules(client, tenant_id=tid, source_system_id=fwb)) == 4
    assert len(_rules(client, tenant_id=tid, twins=True)) == 9
    assert {r["name"] for r in _rules(client, tenant_id=tid, parallel="differs")} == {"db"}
    assert [r["name"] for r in _rules(client, tenant_id=tid, parallel="missing")] == ["nur-a"]
    summary = client.get("/rules/summary", params={"tenant_id": tid}).json()
    assert summary["total"] == 6  # Zwillinge zaehlen nicht doppelt

    detail = client.get(f"/rules/{web['id']}").json()
    assert [t["source"] for t in detail["twins"]] == ["fw-b"]

    # Befunde: abweichende Policy, fehlende Policy, anders definiertes Objekt
    findings = client.get("/findings", params={"tenant_id": tid}).json()
    by_type = {}
    for f in findings:
        by_type.setdefault(f["type"], []).append(f)
    (mismatch,) = by_type["parallel_rule_mismatch"]
    assert mismatch["source_system_id"] == fwb
    assert (
        "weicht von der parallelen Firewall fw-a (Verbund Cluster Nord) ab: Ziel"
        in (mismatch["message"])
    )
    assert "h_web01 ≠ h_web02" in mismatch["message"]
    (missing,) = by_type["parallel_rule_missing"]
    assert "fehlt auf fw-b" in missing["message"]
    (obj,) = by_type["parallel_object_mismatch"]
    assert obj["subject_id"] == "H_web01"
    assert "10.2.0.99/32 ≠ 10.2.0.5/32" in obj["message"]

    # neuer Lauf einer Firewall: Zusammenfassung bleibt; Verbund aufloesen: wieder doppelt
    _deliver(client, tid, fwb, FWB)
    assert len(_rules(client, tenant_id=tid)) == 6
    body = {"tenant_id": tid, "group": "Cluster Nord", "system_ids": []}
    assert client.post("/systems/parallel-group", json=body, headers=ADMIN).status_code == 200
    assert len(_rules(client, tenant_id=tid)) == 9
    types = {f["type"] for f in client.get("/findings", params={"tenant_id": tid}).json()}
    assert not types & {"parallel_rule_mismatch", "parallel_rule_missing"}


def test_verbund_ueber_das_quellsystem(client):
    tid, fwa, fwb = _seed(client)
    for sid in (fwa, fwb):
        r = client.patch(f"/systems/{sid}", json={"parallel_group": "Nord"}, headers=ADMIN)
        assert r.status_code == 200 and r.json()["parallel_group"] == "Nord"
    assert len(_rules(client, tenant_id=tid)) == 6
    client.patch(f"/systems/{fwb}", json={"parallel_group": ""}, headers=ADMIN)
    assert len(_rules(client, tenant_id=tid)) == 9
