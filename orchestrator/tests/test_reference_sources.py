"""Referenzquellen Infoblox/NetBox als Quellsysteme (ADR 0010)."""

from app.models import ReferenceRecordRow, SourceSystem

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _tenant(client) -> int:
    return client.post("/tenants", json={"name": "kunde-a"}, headers=ADMIN).json()["id"]


def _agent(client, tid) -> tuple[int, str]:
    a = client.post("/agents", json={"name": "agent-a", "tenant_id": tid}, headers=ADMIN).json()
    return a["id"], a["token"]


def _system(client, tid, **body) -> dict:
    r = client.post("/systems", json={"tenant_id": tid, **body}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


def _ddi(client, tid, agent_id=None) -> dict:
    return _system(
        client,
        tid,
        name="ddi-01",
        platform="infoblox",
        base_url="https://ddi.kunde.lan",
        agent_id=agent_id,
        username="ro-automato",
        password="ddi-geheim",
    )


def _netbox(client, tid) -> dict:
    return _system(
        client,
        tid,
        name="netbox",
        platform="netbox",
        base_url="http://netbox:8080",
        token="nb-lesen",
        write_token="nb-schreiben",
    )


REC = [
    {
        "kind": "prefix",
        "value": "10.20.0.0/24",
        "vlan_vid": 110,
        "site": "RZ1",
        "description": "Web",
    },
    {"kind": "host", "value": "10.20.0.5", "dns_name": "web01.example.ch"},
    {"kind": "ptr_record", "value": "10.20.0.5", "dns_name": "web01.example.ch"},
]


def _run(client, tid, sid, platform, records, batches=1, count=None) -> dict:
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    size = max(1, len(records) // batches)
    for i in range(0, len(records), size):
        r = client.post(
            f"/internal/collector-runs/{run_id}/references",
            headers=TOKEN,
            json={"records": records[i : i + size]},
        )
        assert r.status_code == 204, r.text
    total = len(records) if count is None else count
    result = {"status": "completed", "reference_count": total}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200, r.text
    return r.json()


def test_rollen_und_zugangsdaten(client):
    tid = _tenant(client)
    agent_id, token = _agent(client, tid)
    ddi = _ddi(client, tid, agent_id)
    nb = _netbox(client, tid)
    assert ddi["ref_role"] == "primary" and nb["ref_role"] == "enrichment"
    assert nb["has_write_token"] is True and ddi["has_write_token"] is False
    assert "nb-schreiben" not in str(client.get("/systems").json())

    # NetBox fuehrend setzen -> DDI wird zur Anreicherung (nur ein fuehrendes IPAM)
    client.patch(f"/systems/{nb['id']}", json={"ref_role": "primary"}, headers=ADMIN)
    roles = {s["name"]: s["ref_role"] for s in client.get(f"/systems?tenant_id={tid}").json()}
    assert roles == {"ddi-01": "enrichment", "netbox": "primary"}

    # Agent bekommt Benutzer/Passwort der DDI ...
    body = {"platform": "infoblox", "tenant_id": tid, "source_system_id": ddi["id"]}
    client.post("/jobs/collect", json=body)
    got = client.get("/internal/agent/next-run", headers={"X-Agent-Token": token}).json()["run"]
    assert (got["target"]["username"], got["target"]["password"]) == ("ro-automato", "ddi-geheim")
    # ... das NetBox-Schreib-Token geht nie an einen Agenten
    client.patch(f"/systems/{nb['id']}", json={"agent_id": agent_id}, headers=ADMIN)
    targets = client.get("/internal/agent/targets", headers={"X-Agent-Token": token}).json()
    assert "nb-schreiben" not in str(targets) and any(t["token"] == "nb-lesen" for t in targets)

    bad = {"tenant_id": tid, "name": "fw", "platform": "fortigate", "ref_role": "primary"}
    assert client.post("/systems", json=bad, headers=ADMIN).status_code == 422
    bad = {"tenant_id": tid, "name": "fw", "platform": "fortigate", "write_token": "x"}
    assert client.post("/systems", json=bad, headers=ADMIN).status_code == 422


def test_teil_upload_und_ersetzen_des_stands(client, session_factory):
    tid = _tenant(client)
    ddi = _ddi(client, tid)
    ack = _run(client, tid, ddi["id"], "infoblox", REC, batches=3)
    assert ack["status"] == "completed"
    system = next(s for s in client.get(f"/systems?tenant_id={tid}").json() if s["id"] == ddi["id"])
    assert system["reference_count"] == 3 and system["reference_at"]

    _run(client, tid, ddi["id"], "infoblox", REC[:1])  # neuer Stand ersetzt den alten
    s = session_factory()
    assert s.query(ReferenceRecordRow).count() == 1
    first_run = s.get(SourceSystem, ddi["id"]).reference_run_id
    s.close()

    ack = _run(client, tid, ddi["id"], "infoblox", REC, count=99)  # unvollstaendig
    assert ack["status"] == "failed" and "unvollstaendig" in ack["message"]
    s = session_factory()
    assert s.query(ReferenceRecordRow).count() == 1  # verworfen, alter Stand bleibt
    assert s.get(SourceSystem, ddi["id"]).reference_run_id == first_run
    s.close()

    fw = _system(client, tid, name="fw", platform="fortigate")
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(
        f"/internal/collector-runs/{run_id}/references", headers=TOKEN, json={"records": REC}
    )
    assert r.status_code == 422  # keine Referenzquelle


FW_RESULT = {
    "status": "completed",
    "rules": [
        {
            "source_platform": "fortigate",
            "external_id": "1",
            "name": "to-web",
            "action": "allow",
            "sources": ["all"],
            "destinations": ["10.20.0.5"],
            "services": ["tcp/443"],
            "destination_refs": ["H_web01"],
        }
    ],
    "objects": [
        {"name": "H_web01", "category": "address", "obj_type": "ipmask", "values": ["10.20.0.5"]}
    ],
}


def test_abgleich_mit_referenzdaten_unabhaengig_von_der_reihenfolge(client):
    tid = _tenant(client)
    fw = _system(client, tid, name="fw", platform="fortigate")
    ddi = _ddi(client, tid)
    nb = _netbox(client, tid)

    # zuerst die Firewall (noch ohne Referenzstand) ...
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=FW_RESULT)
    # ... dann DDI und NetBox - der Abgleich verknuepft nachtraeglich
    _run(client, tid, ddi["id"], "infoblox", REC)
    _run(
        client,
        tid,
        nb["id"],
        "netbox",
        [
            {
                "kind": "ip_address",
                "value": "10.20.0.5",
                "dns_name": "web01.example.ch",
                "tenant": "Kunde A",
                "url": "http://netbox:8080/ipam/ip-addresses/7/",
            }
        ],
    )

    (rule,) = client.get(f"/rules?tenant_id={tid}").json()
    detail = client.get(f"/rules/{rule['id']}").json()
    value = detail["refs"][0]["values"][0]
    assert value["match_status"] == "matched" and value["source_of_record"] == "infoblox"
    ref = value["reference"]
    assert (
        ref["source"],
        ref["kind"],
        ref["dns_name"],
        ref["prefix"],
        ref["vlan"],
        ref["site"],
    ) == (
        "ddi-01",
        "host",
        "web01.example.ch",
        "10.20.0.0/24",
        110,
        "RZ1",
    )
    enr = value["enrichment"]
    assert (enr["source"], enr["tenant"], enr["url"]) == (
        "netbox",
        "Kunde A",
        "http://netbox:8080/ipam/ip-addresses/7/",
    )
    assert not [
        f
        for f in client.get(f"/findings?tenant_id={tid}").json()
        if f["type"] == "unmatched_object"
    ]


def test_netbox_nur_mit_schreib_token_kann_nicht_eingelesen_werden(client):
    tid = _tenant(client)
    nb = _system(client, tid, name="netbox", platform="netbox", write_token="nb-schreiben")
    assert nb["has_credentials"] is False and nb["has_write_token"] is True
    body = {"platform": "netbox", "tenant_id": tid, "source_system_id": nb["id"]}
    r = client.post("/jobs/collect", json=body)
    assert r.status_code == 422 and "read token" in r.json()["detail"]
    # mit Lese-Token geht es
    client.patch(f"/systems/{nb['id']}", json={"token": "nb-lesen"}, headers=ADMIN)
    assert client.post("/jobs/collect", json=body).status_code == 202
    ddi = _system(client, tid, name="ddi", platform="infoblox")
    body = {"platform": "infoblox", "tenant_id": tid, "source_system_id": ddi["id"]}
    assert client.post("/jobs/collect", json=body).status_code == 422
