"""VLANs je Standort (1.9.0).

Anwender 2026-11-11: „In der VLAN Database … haben wir die VLAN ID mit Geräten an beiden
Standorten, eigentlich sind die VLANs entweder Physischer Standort spezifisch oder für ein
Virtueller Standort spezifisch … pro Standort aufklappen“ und „VLAN 50 in NetBox nicht
eindeutig“. Ein VLAN gehoert zum Standort des Quellsystems, das es liest: VLAN 50 in Zürich
und VLAN 50 in Bern sind zwei VLANs - in der Ansicht und in NetBox."""

from test_netbox_sync import _apply
from test_netbox_sync_cabling_apps import (
    ADMIN,
    _deliver,
    _items,
    _plan,
    _port,
    _system,
    netbox,  # noqa: F401  (Fixture: NetBox mit Kabeln)
)


def _switches(device: str, name: str) -> dict:
    return {
        "status": "completed",
        "inventory": {
            "devices": [{"name": device, "role": "switch", "model": "C9300-48P"}],
            "interfaces": [_port(device, "Gi1/0/1", mode="access", untagged_vlan=50),
                           _port(device, "Gi1/0/2", mode="tagged", native_vlan=1,
                                 trunk_vlans="1,50")],
            "vlans": [{"vid": 1, "name": "default"}, {"vid": 50, "name": name}],
        },
    }  # fmt: skip


def _setup(client) -> dict:
    tid = client.post("/tenants", json={"name": "Kunde A", "code": "KA"}, headers=ADMIN).json()
    tid = tid["id"]
    sites = {}
    for code, name in (("chzrh1", "Zürich"), ("chbrn1", "Bern")):
        r = client.post(f"/tenants/{tid}/sites", json={"code": code, "name": name}, headers=ADMIN)
        sites[code] = r.json()["id"]
    ids = {"tid": tid}
    switches = (("chzrh1", "sw-zrh-01", "Server-ZRH"), ("chbrn1", "sw-brn-01", "Server-BRN"))
    for code, device, vname in switches:
        sid = _system(client, tid, name=f"Switches {code}", platform="cisco_switch",
                      read_scope="10.0.0.11", username="ro", password="x",
                      site_id=sites[code])  # fmt: skip
        _deliver(client, tid, sid, "cisco_switch", _switches(device, vname))
    ids["nb"] = _system(client, tid, name="netbox", platform="netbox",
                        base_url="https://nb.example.ch", token="nb-read",
                        write_token="nb-write")  # fmt: skip
    return ids


def test_vlans_je_standort_in_der_ansicht(client):
    ids = _setup(client)
    rows = client.get("/cabling/vlans", params={"tenant_id": ids["tid"]}, headers=ADMIN).json()
    fifty = [(r["site"]["name"], r["names"], [d["device"] for d in r["devices"]])
             for r in rows if r["vid"] == 50]  # fmt: skip
    assert fifty == [("Bern", ["Server-BRN"], ["sw-brn-01"]),
                     ("Zürich", ["Server-ZRH"], ["sw-zrh-01"])]  # fmt: skip
    assert {r["site"]["kind"] for r in rows} == {"physical"}


def test_netbox_vlan_50_je_standort_eindeutig(client, netbox):  # noqa: F811
    ids = _setup(client)
    netbox.add("dcim/sites", {"name": "Zürich", "slug": "chzrh1", "status": "active"})
    netbox.add("dcim/sites", {"name": "Bern", "slug": "chbrn1", "status": "active"})
    # eine global geltende Gruppe mit VLAN 50 (wie die Firewalls bis 1.8.x) macht nichts mehr
    # mehrdeutig: die eigene Gruppe des Quellsystems geht vor
    for slug in ("fw-alt-a", "fw-alt-b"):
        group = netbox.add("ipam/vlan-groups", {"name": slug, "slug": slug})
        netbox.add("ipam/vlans", {"vid": 50, "name": "Alt", "group": group["id"]})
    plan = _plan(client, ids, sections=["cabling"])
    assert not any("eindeutig" in w for w in plan["warnings"]), plan["warnings"]
    groups = {i["key"]: i for i in _items(client, plan["id"], kind="vlan_group", action="create")}
    assert set(groups) == {"kunde-a-switches-chzrh1", "kunde-a-switches-chbrn1"}, set(groups)
    assert _apply(client, plan["id"])["status"] == "applied"
    for site, device, vname in (("chzrh1", "sw-zrh-01", "Server-ZRH"),
                                ("chbrn1", "sw-brn-01", "Server-BRN")):  # fmt: skip
        port = next(i for i in netbox.objects("dcim/interfaces")
                    if i["device"]["name"] == device and i["name"] == "Gi1/0/1")  # fmt: skip
        vlan = netbox.db["ipam/vlans"][port["untagged_vlan"]["id"]]
        group = netbox.db["ipam/vlan-groups"][vlan["group"]["id"]]
        assert vlan["name"] == vname and group["scope_type"] == "dcim.site"
        assert netbox.db["dcim/sites"][group["scope_id"]]["slug"] == site
    again = _plan(client, ids, sections=["cabling"])
    assert again["totals"]["create"] == again["totals"]["update"] == 0, again["totals"]


# -- Native VLAN (1.9.0) --------------------------------------------------------------------
# Anwender: „Native VLAN von dem Interface IP wird nicht übernommen, obwohl dies auf dem Switch
# auslesbar ist. Nur das VLAN 1 ist absolut default und dieses darf nicht verwendet werden. Im
# Netbox sind die VLAN somit nicht zu den Subnetzen verknüpft.“


def _fw_port(name: str, ip: str) -> dict:
    return {"device": "fw-08", "name": name, "kind": "physical", "enabled": True, "ips": [ip]}


FW = {
    "status": "completed",
    "rules": [],
    "inventory": {
        "devices": [{"name": "fw-08", "role": "firewall", "model": "FG-100F"}],
        "interfaces": [_fw_port("port5", "10.0.10.1/24"), _fw_port("port6", "10.0.66.1/24"),
                       _fw_port("port7", "10.0.20.1/24")],
        "subnets": [{"prefix": "10.0.10.0/24", "gateway": "10.0.10.1/24", "context": "interface",
                     "context_name": "port5"},
                    {"prefix": "10.0.66.0/24", "gateway": "10.0.66.1/24", "context": "interface",
                     "context_name": "port6"},
                    {"prefix": "10.0.20.0/24", "gateway": "10.0.20.1/24", "context": "interface",
                     "context_name": "port7"}],
    },
}  # fmt: skip


def _native_setup(client, netbox) -> dict:  # noqa: F811
    from test_netbox_sync_cabling_apps import SWITCHES, _setup_cabling

    ids = _setup_cabling(client, netbox)
    sw = {**SWITCHES, "inventory": {**SWITCHES["inventory"]}}
    inv = sw["inventory"]
    inv["interfaces"] = inv["interfaces"] + [
        _port("sw-acc-01", "Gi1/0/8", mode="tagged", native_vlan=1, trunk_vlans="1,10"),
        _port("sw-acc-01", "Gi1/0/9", mode="tagged", native_vlan=20, trunk_vlans="10,20"),
    ]
    inv["neighbors"] = inv["neighbors"] + [
        {"device": "sw-acc-01", "port": "Gi1/0/8", "protocol": "lldp", "remote_name": "fw-08",
         "remote_port": "port6"},
        {"device": "sw-acc-01", "port": "Gi1/0/9", "protocol": "lldp", "remote_name": "fw-08",
         "remote_port": "port7"},
    ]  # fmt: skip
    _deliver(client, ids["tid"], ids["sw"], "cisco_switch", sw)
    fw = _system(client, ids["tid"], name="fw-08", platform="fortigate", base_url="https://fw")
    _deliver(client, ids["tid"], fw, "fortigate", FW)
    return ids


def test_native_vlan_der_netze_einer_firewall(client, netbox):  # noqa: F811
    ids = _native_setup(client, netbox)
    rows = client.get("/inventory/subnets", params={"tenant_id": ids["tid"]}, headers=ADMIN)
    by_prefix = {r["prefix"]: r for r in rows.json()}
    # port5 am Access-Port VLAN 10, port7 am Trunk mit Native VLAN 20; port6: Native VLAN 1 - nie
    assert by_prefix["10.0.10.0/24"]["vlan_switch"] == {
        "vid": 10, "device": "sw-acc-01", "port": "Te1/1/1", "mode": "access"}  # fmt: skip
    assert by_prefix["10.0.20.0/24"]["vlan_switch"]["vid"] == 20
    assert by_prefix["10.0.20.0/24"]["vlan_switch"]["mode"] == "native"
    assert "vlan_switch" not in by_prefix["10.0.66.0/24"]

    plan = _plan(client, ids, sections=["inventory", "cabling"])
    assert _apply(client, plan["id"])["status"] == "applied"
    p10 = netbox.find("ipam/prefixes", prefix="10.0.10.0/24")
    assert netbox.db["ipam/vlans"][p10["vlan"]["id"]]["vid"] == 10  # das globale VLAN 10
    p20 = netbox.find("ipam/prefixes", prefix="10.0.20.0/24")
    assert netbox.db["ipam/vlans"][p20["vlan"]["id"]]["vid"] == 20  # am Standort ZRH
    assert not netbox.find("ipam/prefixes", prefix="10.0.66.0/24").get("vlan")
