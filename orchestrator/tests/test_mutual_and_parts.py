"""0.79.1 (Rückmeldung des Anwenders):

1. Contract mit EPGs als Consumer und Provider zugleich: Befund `aci_contract_both_sides`, und
   die Applikations-Doku zeigt die eigene EPG nicht als Gegenstelle (bisher: Quelle die Server,
   Ziel die EPG selbst).
2. Verbindungen Ende zu Ende zu einem Netz: die Route, die das ganze Netz enthält (oft 0/0),
   gilt nicht für seine genauer gerouteten Teile - diese sind eigene Verbindungen mit ihrem
   Weg (z. B. über einen anderen L3Out bzw. ein anderes Interface).
"""

import copy

from test_policy_check import ADMIN, FW, TOKEN, _policy
from test_vmware import ACI, BACKUP, WEB, _deliver, _seed

OP = {"X-Dev-Roles": "operator"}


def _aci() -> dict:
    aci = copy.deepcopy(ACI)
    aci["rules"].append(
        {"source_platform": "cisco_aci", "external_id": "uni/tn-TE_ka/brc-CT_peer/SJ",
         "name": "CT_peer:SJ", "action": "allow", "sources": [f"epg:{WEB}", f"epg:{BACKUP}"],
         "destinations": [f"epg:{WEB}", f"epg:{BACKUP}"], "services": ["tcp/22"],
         "aci_scope": "context",
         "raw": {"tenant": "TE_ka", "kind": "contract", "dn": "uni/tn-TE_ka/brc-CT_peer"}},
    )  # fmt: skip
    return aci


def test_consumer_und_provider_zugleich(client):
    ids = _seed(client)
    tid = ids["tid"]
    _deliver(client, tid, ids["apic"], "cisco_aci", _aci())

    found = client.get("/findings", params={"tenant_id": tid, "type": "aci_contract_both_sides"})
    assert found.status_code == 200, found.text
    (f,) = found.json()
    assert f["subject_id"] == "TE_ka/CT_peer" and f["severity"] == "warning"
    assert set(f["detail"]["epgs"]) == {"TE_ka/AP_007/EG_web01", "TE_ka/AP_007/EG_backup"}
    assert "Consumer und Provider zugleich" in f["message"]
    # CT_app (web -> db) nur in eine Richtung: kein Befund

    body = {"name": "Webshop", "members": [{"kind": "ap", "ref": "TE_ka/AP_007"}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    doc = client.get(f"/applications/{app['id']}/doc").json()
    rows = [r for t in doc["tables"] for r in t["rows"] if r["Description"].startswith("CT_peer")]
    assert rows, [t["index"] for t in doc["tables"]]
    for r in rows:  # nie die eigene EPG als Gegenstelle
        own = r["Server Name"].split("/")[-1].split()[0]
        assert own not in (r.get("Target Systems") or ""), r
    peer = {x["id"] for x in doc["rules"] if (x.get("name") or "").startswith("CT_peer")}
    hits = [h for h in doc["hits"] if h["rule_id"] in peer]
    assert hits and all("Consumer und Provider" in h["via"] for h in hits)
    assert any(x["type"] == "aci_contract_both_sides" for x in doc["findings"])


def test_netz_als_ziel_genauer_geroutete_teile(client):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    fw = copy.deepcopy(FW)
    # Server im LAN darf in alle privaten Netze: das ganze 10/8 geht ueber die Default-Route,
    # das DMZ-Netz darin genauer ueber dmz
    fw["rules"] = [
        _policy(40, "lan-to-private", "lan", "any", ["10.10.0.5"], ["10.0.0.0/8"],
                ["tcp/443"]) | {"destination_refs": ["N_private"]},
        *fw["rules"],
    ]  # fmt: skip
    fw["objects"] = [{"name": "N_private", "category": "address", "values": ["10.0.0.0/8"]}]
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=fw)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text

    body = {"name": "Server", "members": [{"kind": "host", "ref": "10.10.0.5"}]}
    r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert r.status_code == 201, r.text
    app = r.json()
    conns = client.get(f"/applications/{app['id']}/connections").json()["connections"]
    whole = next(c for c in conns if c["dst"]["ip"] == "10.0.0.0/8")
    assert "10.30.0.0/24" in whole["parts"]
    assert whole["end"]["kind"] == "external"  # das ganze Netz: ueber die Default-Route
    part = next(c for c in conns if c.get("part_of") == whole["key"]
                and c["dst"]["ip"] == "10.30.0.0/24")  # fmt: skip
    assert part["dst"]["label"].endswith("› 10.30.0.0/24") and part["service"] == "tcp/443"
    assert part["end"]["kind"] == "connected"  # der Teil: genauer ueber dmz
    assert part["hops"][0]["checks"][0]["egress"] == "dmz"
