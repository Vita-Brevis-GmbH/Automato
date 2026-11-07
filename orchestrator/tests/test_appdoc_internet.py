"""Zugriffe ins Internet sind kein Zugriff auf die Applikation (0.114.1, Anwender zu 0.113:
„Zugriffe von intern ins Internet über die Internetfirewall wieder als Zugriff intern gezeigt
… irrelevant, wenn das ganze Internet zugreifen kann“).

Internet-Firewall mit SD-WAN: Ausgang ist die SD-WAN-Zone „WAN“ (Mitglied wan1), eine SD-WAN-
Regel leitet alles (dst all) über wan1. Die Regel „lan-to-inet“ (LAN → all, Ausgang WAN) erfasst
den Webserver in der DMZ nur über „all“ - sie gehört nicht in seine Doku. „lan-to-dmz-web“ nennt
den Server und bleibt.
"""

import test_policy_check as pc
from system_doc import system_list

OP = {"X-Dev-Roles": "operator"}

FW = {
    "status": "completed",
    "rules": [
        pc._policy(1, "lan-to-dmz-web", "lan", "dmz", ["10.10.0.0/24"], ["10.30.0.10"],
                   ["tcp/443"]),
        pc._policy(2, "lan-to-inet", "lan", "WAN", ["10.10.0.0/24"], ["all"], ["all"]),
    ],
    "inventory": {
        "devices": [{"name": "fw-r", "role": "firewall"}],
        "interfaces": [
            pc._iface("lan", "10.10.0.1/24", "lan"),
            pc._iface("dmz", "10.30.0.1/24", "dmz"),
            {"device": "fw-r", "name": "wan1", "ips": ["198.51.100.2/30"], "vdom": "root"},
        ],
    },
    "routing": {
        "contexts": [{"node": "fw-r", "context": "root"}],
        "routes": [
            pc._route("10.10.0.0/24", [{"interface": "lan"}], "connected"),
            pc._route("10.30.0.0/24", [{"interface": "dmz"}], "connected"),
            pc._route("198.51.100.0/30", [{"interface": "wan1"}], "connected"),
            pc._route("0.0.0.0/0", [{"ip": "198.51.100.1", "interface": "wan1"}]),
        ],
        "policy_routes": [
            {"node": "fw-r", "context": "root", "kind": "sdwan", "seq": 1, "name": "inet",
             "enabled": True, "match": {"dst": ["all"]}, "action": "manual",
             "output_device": ["wan1"],
             "attrs": {"zones": ["WAN"],
                       "members": [{"seq-num": 1, "interface": "wan1", "zone": "WAN"}]}},
        ],
    },
}  # fmt: skip


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=pc.ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=pc.ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=pc.TOKEN, json=FW)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    return tid


def test_zugriff_ins_internet_ist_kein_zugriff_auf_den_server(client):
    tid = _seed(client)
    web = next(s["name"] for s in system_list(client, tid) if s["ip"] == "10.30.0.10")
    body = {"name": "Web", "members": [{"kind": "host", "ref": web}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP).json()
    doc = client.get(f"/applications/{app['id']}/doc", params={"tenant_id": tid}).json()
    names = {r["id"]: r["name"] for r in doc["rules"]}
    shown = {names.get(h["rule_id"]) for h in doc["hits"]}
    assert "lan-to-dmz-web" in shown
    assert "lan-to-inet" not in shown, doc["hits"]
    rows = {r.get("Description") for t in doc["tables"] for r in t["rows"]}
    assert "lan-to-inet" not in rows
    assert doc["summary"]["to_internet"] == 1
