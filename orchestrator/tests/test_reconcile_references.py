"""Abgleich gegen die Referenzquellen + Vergleich DDI ↔ NetBox + Objekt-/Inventar-Ansicht."""

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _tenant(client) -> int:
    return client.post("/tenants", json={"name": "kunde-a"}, headers=ADMIN).json()["id"]


def _system(client, tid, **body) -> int:
    r = client.post("/systems", json={"tenant_id": tid, **body}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _collect(client, tid, sid, platform) -> str:
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    return client.post("/jobs/collect", json=body).json()["run_id"]


def _refs(client, tid, sid, platform, records):
    run_id = _collect(client, tid, sid, platform)
    client.post(
        f"/internal/collector-runs/{run_id}/references", headers=TOKEN, json={"records": records}
    )
    result = {"status": "completed", "reference_count": len(records)}
    assert (
        client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result).json()[
            "status"
        ]
        == "completed"
    )


def _result(client, tid, sid, platform, result):
    run_id = _collect(client, tid, sid, platform)
    r = client.post(
        f"/internal/collector-runs/{run_id}/result",
        headers=TOKEN,
        json={"status": "completed", **result},
    )
    assert r.json()["status"] == "completed", r.text


DDI = [
    {"kind": "prefix", "value": "10.20.0.0/24", "vlan_vid": 120, "site": "RZ1"},
    {"kind": "prefix", "value": "10.30.0.0/24"},
    {"kind": "host", "value": "10.20.0.5", "dns_name": "app01.example.ch"},
    {"kind": "host", "value": "10.20.0.6", "dns_name": "web02.example.ch"},
    {"kind": "host", "value": "10.20.0.7", "dns_name": "db01.example.ch"},
]
NETBOX = [
    {"kind": "prefix", "value": "10.20.0.0/24", "url": "http://nb/ipam/prefixes/1/"},
    {"kind": "ip_address", "value": "10.20.0.5", "dns_name": "app01.example.ch"},
    {"kind": "ip_address", "value": "10.20.0.6", "dns_name": "web-alt.example.ch"},
    {"kind": "ip_address", "value": "10.20.0.99", "dns_name": "nur-netbox.example.ch"},
]
FW = {
    "rules": [
        {
            "source_platform": "fortigate",
            "external_id": "1",
            "action": "allow",
            "destinations": ["10.20.0.5", "10.20.0.6"],
            "destination_refs": ["H_web01", "H_web02.example.ch"],
            "services": ["tcp/443"],
        }
    ],
    "objects": [
        {"name": "H_web01", "category": "address", "obj_type": "ipmask", "values": ["10.20.0.5"]},
        {
            "name": "H_web02.example.ch",
            "category": "address",
            "obj_type": "ipmask",
            "values": ["10.20.0.6"],
        },
    ],
}
ACI = {
    "inventory": {
        "subnets": [
            {
                "prefix": "10.20.0.0/24",
                "gateway": "10.20.0.1/24",
                "vlan": 110,
                "context": "bd",
                "context_name": "BD_app",
                "epg": "EG_web",
            },
            {
                "prefix": "10.99.0.0/24",
                "gateway": "10.99.0.1/24",
                "context": "bd",
                "context_name": "BD_neu",
            },
        ]
    }
}


def _setup(client) -> dict:
    tid = _tenant(client)
    ids = {
        "tid": tid,
        "fw": _system(client, tid, name="fw", platform="fortigate"),
        "apic": _system(client, tid, name="apic", platform="cisco_aci"),
        "ddi": _system(client, tid, name="ddi", platform="infoblox", username="u", password="p"),
        "nb": _system(client, tid, name="netbox", platform="netbox", token="t"),
    }
    _refs(client, tid, ids["ddi"], "infoblox", DDI)
    _refs(client, tid, ids["nb"], "netbox", NETBOX)
    _result(client, tid, ids["fw"], "fortigate", FW)
    _result(client, tid, ids["apic"], "cisco_aci", ACI)
    return ids


def _findings(client, tid) -> dict[str, set]:
    out: dict[str, set] = {}
    for f in client.get(f"/findings?tenant_id={tid}").json():
        out.setdefault(f["type"], set()).add(f["subject_id"])
    return out


def test_name_subnetz_vlan_befunde(client):
    ids = _setup(client)
    f = _findings(client, ids["tid"])
    assert f["name_mismatch"] == {"H_web01"}  # DNS-Name app01, Objekt heisst web01
    assert "H_web02.example.ch" not in f.get("name_mismatch", set())
    assert f["subnet_not_in_ipam"] == {"10.99.0.0/24"}
    assert f["vlan_mismatch"] == {"10.20.0.0/24"}  # ACI VLAN 110, DDI VLAN 120
    assert "unmatched_object" not in f


def test_vergleich_ddi_netbox(client):
    ids = _setup(client)
    tid = ids["tid"]
    summary = client.get(f"/reconcile/summary?tenant_id={tid}").json()
    ipam = summary["ipam"]
    assert (
        ipam["available"]
        and ipam["primary"]["name"] == "ddi"
        and ipam["enrichment"]["name"] == "netbox"
    )
    assert ipam["summary"] == {"missing": 2, "different": 1, "only_enrichment": 1, "equal": 2}
    assert summary["findings"]["name_mismatch"] == {"open": 1, "total": 1}

    rows = client.get(f"/reconcile/ipam?tenant_id={tid}").json()["rows"]
    assert [(r["status"], r["kind"], r["value"]) for r in rows] == [
        ("missing", "prefix", "10.30.0.0/24"),
        ("missing", "ip", "10.20.0.7"),
        ("different", "ip", "10.20.0.6"),
        ("only_enrichment", "ip", "10.20.0.99"),
    ]
    diff = rows[2]
    assert (
        diff["primary"]["dns_name"] == "web02.example.ch"
        and diff["enrichment"]["dns_name"] == "web-alt.example.ch"
    )
    only = client.get(f"/reconcile/ipam?tenant_id={tid}&status=only_enrichment").json()
    assert only["total"] == 1
    assert client.get(f"/reconcile/ipam?tenant_id={tid}&q=db01").json()["total"] == 1
    assert client.get(f"/reconcile/ipam?tenant_id={tid}&status=bogus").status_code == 422


def test_abgleich_ausloesen_nur_operator(client):
    ids = _setup(client)
    tid = ids["tid"]
    assert (
        client.post(f"/reconcile?tenant_id={tid}", headers={"X-Dev-Roles": "viewer"}).status_code
        == 403
    )
    r = client.post(f"/reconcile?tenant_id={tid}", headers={"X-Dev-Roles": "operator"})
    assert r.status_code == 200 and r.json()["findings"]["subnet_not_in_ipam"] == 1


def test_objekt_und_inventar_ansicht(client):
    ids = _setup(client)
    tid = ids["tid"]
    data = client.get(f"/objects?tenant_id={tid}").json()
    objs = {o["name"]: o for o in data["rows"]}
    web = objs["H_web01"]
    assert web["used_in_rules"] == 1 and web["source"] == "fw" and "name_mismatch" in web["issues"]
    ref = web["values"][0]["reference"]
    assert (ref["dns_name"], ref["prefix"], ref["vlan"]) == (
        "app01.example.ch",
        "10.20.0.0/24",
        120,
    )
    assert web["values"][0]["enrichment"]["source"] == "netbox"
    only = client.get(f"/objects?tenant_id={tid}&issues_only=true").json()
    assert {o["name"] for o in only["rows"]} == {"H_web01"}
    assert client.get(f"/objects?tenant_id={tid}&q=web02").json()["total"] == 1

    subnets = {s["prefix"]: s for s in client.get(f"/inventory/subnets?tenant_id={tid}").json()}
    assert subnets["10.20.0.0/24"]["ipam"] == {
        "source": "ddi",
        "found": True,
        "vlan": 120,
        "description": None,
        "site": "RZ1",
        "url": None,
    }
    assert subnets["10.20.0.0/24"]["enrichment"]["url"] == "http://nb/ipam/prefixes/1/"
    assert subnets["10.99.0.0/24"]["ipam"]["found"] is False
    assert subnets["10.99.0.0/24"]["source"] == "apic"
    assert client.get("/objects").status_code == 422  # Mandant noetig
