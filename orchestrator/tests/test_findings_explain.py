"""Findings sagen genau, was nicht stimmt (Satz + Hinweis + Detail), und der Abgleich
beruecksichtigt, dass das IPAM den Server ohne Praefix fuehrt (H_web01 <-> web01.example.ch)."""

from app.store.finding_text import explain
from test_reference_sources import TOKEN, _ddi, _run, _system, _tenant

DDI = [
    {"kind": "prefix", "value": "10.10.10.0/24", "description": "Server"},
    {"kind": "host", "value": "10.10.10.5", "dns_name": "web01.example.ch"},
    {"kind": "host", "value": "10.10.10.20", "dns_name": "app01.example.ch"},
    {"kind": "host", "value": "10.30.0.9", "dns_name": "db01.example.ch"},
    # gleiches erstes Label mit zwei IPs: ueber den Kurznamen nicht eindeutig
    {"kind": "host", "value": "10.10.10.31", "dns_name": "dup.example.ch"},
    {"kind": "host", "value": "10.10.10.32", "dns_name": "dup.other.ch"},
]

OBJECTS = [
    {"name": "H_web01", "category": "address", "obj_type": "ipmask", "values": ["10.10.10.5"]},
    {"name": "H_web03", "category": "address", "obj_type": "ipmask", "values": ["10.10.10.5"]},
    {"name": "H_db01", "category": "address", "obj_type": "ipmask", "values": ["172.16.5.9"]},
    {"name": "srv-x", "category": "address", "obj_type": "ipmask", "values": ["10.10.10.7"]},
]


def _rule(ext_id, name, sources, destinations, src_refs, dst_refs):
    return {
        "source_platform": "fortigate",
        "external_id": ext_id,
        "name": name,
        "action": "allow",
        "sources": sources,
        "destinations": destinations,
        "services": ["tcp/443"],
        "source_refs": src_refs,
        "destination_refs": dst_refs,
        "service_refs": ["HTTPS"],
    }


RULES = [
    _rule("1", "to-web", ["10.10.10.7"], ["10.10.10.5"], ["srv-x"], ["H_web01", "H_web03"]),
    _rule("2", "to-db", ["10.10.10.5"], ["172.16.5.9"], ["H_web01"], ["H_db01"]),
    # Endpunkte nur mit Namen (ohne aufgeloesten Wert): Abgleich ueber den Namen ohne Praefix
    _rule("3", "by-name", ["H_app01"], ["H_dup"], ["H_app01"], ["H_dup"]),
]


def _seed(client) -> int:
    tid = _tenant(client)
    ddi = _ddi(client, tid)
    _run(client, tid, ddi["id"], "infoblox", DDI)
    fw = _system(client, tid, name="fw-01", platform="fortigate", base_url="https://fw")
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    result = {"status": "completed", "rules": RULES, "objects": OBJECTS}
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.json()["status"] == "completed", r.text
    return tid


def _findings(client, tid, ftype) -> dict:
    r = client.get("/findings", params={"tenant_id": tid, "type": ftype, "status": "open"})
    assert r.status_code == 200, r.text
    return {f["subject_id"]: f for f in r.json()}


def test_name_ohne_praefix_im_ipam(client):
    tid = _seed(client)
    unmatched = _findings(client, tid, "unmatched_object")
    # H_app01 findet app01.example.ch (Praefix weg, erstes Label eindeutig) -> kein Befund
    assert "H_app01" not in unmatched
    # H_dup: zwei IPAM-Eintraege mit dem Kurznamen "dup" -> nicht eindeutig, bleibt offen
    assert "H_dup" in unmatched
    assert unmatched["H_dup"]["detail"]["server_name"] == "dup"
    assert unmatched["H_dup"]["message"] == (
        "Der Name „dup“ ist im IPAM (ddi-01) nicht eindeutig: dup.example.ch → 10.10.10.31, "
        "dup.other.ch → 10.10.10.32. Verwendet in 1 Regel (by-name)."
    )
    rule = next(
        x for x in client.get("/rules", params={"tenant_id": tid}).json() if x["name"] == "by-name"
    )
    detail = client.get(f"/rules/{rule['id']}").json()
    values = {e["name"]: e["value"] for e in detail["endpoints"]}
    assert values["H_app01"] == "10.10.10.20"

    # H_web01 heisst im IPAM web01.example.ch -> kein name_mismatch; H_web03 schon
    mismatch = _findings(client, tid, "name_mismatch")
    assert "H_web01" not in mismatch
    assert mismatch["H_web03"]["detail"]["compared"] == ["web03", "web01"]
    assert "web03 ≠ web01" in mismatch["H_web03"]["message"]

    # Suche ueber den IPAM-Namen (ohne Praefix, mit Domain) findet Regel und Objekt
    hits = client.get("/rules", params={"tenant_id": tid, "q": "web01.example.ch"}).json()
    assert sorted(x["name"] for x in hits) == ["to-db", "to-web"]
    objs = client.get("/objects", params={"tenant_id": tid, "q": "web01.example.ch"}).json()
    assert {o["name"] for o in objs["rows"]} == {"H_web01", "H_web03"}


def test_befund_sagt_was_nicht_stimmt(client):
    tid = _seed(client)
    f = _findings(client, tid, "unmatched_object")["172.16.5.9"]
    d = f["detail"]
    assert d["firewall_objects"] == ["H_db01"] and d["sources"] == ["fw-01"]
    assert d["rules"] == 1 and d["rule_names"] == ["to-db"]
    assert d["ipam_elsewhere"] == {
        "name": "db01.example.ch",
        "value": "10.30.0.9",
        "object": "H_db01",
    }
    msg = f["message"]
    assert "172.16.5.9" in msg and "ddi-01" in msg and "noch in einem bekannten Netz" in msg
    assert "db01.example.ch kennt das IPAM unter 10.30.0.9" in msg and "to-db" in msg
    assert "angleichen" in f["hint"]

    invalid = _findings(client, tid, "invalid_name")["srv-x"]
    assert "H_" in invalid["detail"]["prefixes"]
    assert invalid["message"].startswith("Adresse „srv-x“ auf fw-01 beginnt mit keinem Präfix")

    dup = _findings(client, tid, "duplicate_value")["10.10.10.5"]
    assert "in 2 Adressobjekten definiert: H_web01, H_web03" in dup["message"]

    summary = client.get("/findings/summary", params={"tenant_id": tid}).json()
    assert summary["by_type"]["unmatched_object"]["open"] == 2


def test_texte_ohne_detail_robust():
    for ftype in [
        "unmatched_object",
        "invalid_name",
        "name_type_mismatch",
        "duplicate_value",
        "unused_object",
        "name_mismatch",
        "subnet_not_in_ipam",
        "vlan_mismatch",
        "ambiguous_ip",
        "epg_no_endpoints",
        "kuenftiger_typ",
    ]:
        msg, hint = explain(ftype, "x", None)
        assert msg and isinstance(hint, str)
    msg, _ = explain(
        "subnet_not_in_ipam",
        "10.9.0.0/24",
        {
            "prefix": "10.9.0.0/24",
            "context": "bd",
            "context_name": "BD_web",
            "vrf": "PROD",
            "reference": "ddi-01",
            "source": "apic",
        },
    )
    assert msg == (
        "Das Subnetz 10.9.0.0/24 (BD BD_web VRF PROD) ist auf apic konfiguriert, im IPAM (ddi-01) "
        "aber nicht als Netz erfasst."
    )
