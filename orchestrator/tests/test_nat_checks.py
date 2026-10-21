"""NAT / VIPs mit Korrektheitspruefung (Dev-Befund 0.14.0): Befunde des VIP- bzw. Pool-Objekts
wie in der Objekt-Ansicht (Namenskonvention, unbenutzt) und ein internes Ziel, das im
fuehrenden IPAM fehlt; Filter "nur Abweichungen"; im Detail Link aufs Objekt."""

from test_reference_sources import TOKEN, _system
from test_search_nat import VIP, _seed

BAD = "webextern"  # kein Praefix der Konvention, von keiner Regel genutzt, Ziel nicht im IPAM
FW2 = {
    "status": "completed",
    "objects": [{"name": BAD, "category": "vip", "values": ["203.0.113.20"]}],
    "nat_rules": [
        {"source_platform": "fortigate", "external_id": BAD, "name": BAD,
         "ext_ip": "203.0.113.20", "mapped_ip": "10.99.0.9", "raw": {"type": "static-nat"}},
    ],
}  # fmt: skip


def _with_bad_vip(client) -> int:
    tid = _seed(client)
    fw2 = _system(client, tid, name="fw2", platform="fortigate", base_url="https://fw2")
    body = {"platform": "fortigate", "tenant_id": tid, "source_system_id": fw2["id"]}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=FW2)
    assert r.json()["status"] == "completed", r.text
    return tid


def test_pruefung_und_filter_nur_abweichungen(client):
    tid = _with_bad_vip(client)
    rows = {n["name"]: n for n in client.get("/nat", params={"tenant_id": tid}).json()}
    assert "mapped_not_in_ipam" not in rows[VIP]["issues"]  # 10.10.10.5 steht in der DDI
    assert "unused_object" not in rows[VIP]["issues"]  # von internet-to-www genutzt
    bad = rows[BAD]["issues"]
    assert {"mapped_not_in_ipam", "unused_object", "invalid_name"} <= set(bad)

    r = client.get("/nat", params={"tenant_id": tid, "issues_only": "true"})
    names = [n["name"] for n in r.json()]
    assert BAD in names and VIP not in names
    assert r.headers["X-Total-Count"] == str(len(names))

    detail = client.get(f"/nat/{rows[BAD]['id']}").json()
    assert detail["issues"] == bad and detail["object_id"] is not None
    obj = client.get(f"/objects/{detail['object_id']}").json()
    assert obj["name"] == BAD and obj["category"] == "vip"
    assert detail["vms"] == []  # kein vCenter
