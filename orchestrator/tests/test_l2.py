"""Endpunkte der Switches (0.63.0, ADR 0025 Nr. 8): MAC-Tabelle und ARP gesammelt, Anschluss
ohne Uplinks, „Wo haengt …“ nach MAC, IP und VM-Name, VLAN der VMs am Uplink des ESXi-Hosts,
Aufbewahrung und Loeschen.

Aufbau wie `test_cabling`: sw-acc-01 und sw-core-01 ueber Po1/Po10 verbunden, sw-core-01 Eth1/10
zum ACI-Leaf. Dazu esx01 an sw-core-01 Eth1/20 (Trunk nur VLAN 20) und esx02 an sw-acc-01
Gi1/0/6 (Trunk 20,30); web01 und app01 laufen auf esx01, db01 auf esx02 - app01 braucht VLAN 30.
"""

import copy
from datetime import UTC, datetime, timedelta

import test_cabling as tc
from app.models import ArpEntry, MacEntry
from app.store import l2
from sqlalchemy import select, update

ADMIN, OP, TOKEN = tc.ADMIN, tc.OP, tc.TOKEN


def _nic(mac, vlan, ips, pg):
    return {"name": "Network adapter 1", "mac": mac, "portgroup": pg, "switch": "DVS-A",
            "vlan": vlan, "connected": True, "ips": ips}  # fmt: skip


def _vm(name, host, nic):
    return {"name": name, "power_state": "poweredOn", "datacenter": "DC-A", "cluster": "c1",
            "host": host, "nics": [nic]}  # fmt: skip


VCENTER = {
    "status": "completed",
    "virtual": {
        "vms": [
            _vm("web01", "esx01.example.ch",
                _nic("00:50:56:01:00:01", 20, ["10.20.0.5"], "PG_web")),
            _vm("app01", "esx01.example.ch",
                _nic("00:50:56:01:00:02", 30, ["10.30.0.7"], "PG_app")),
            _vm("db01", "esx02.example.ch", _nic("00:50:56:01:00:03", 20, [], "PG_web")),
        ],
        "portgroups": [{"name": "PG_web", "vlan": 20}, {"name": "PG_app", "vlan": 30}],
    },
}  # fmt: skip
SRV = "a0:b1:c2:d3:e4:f5"  # physischer Server an sw-acc-01


def _switches(macs, arps, extra_ports=True):
    result = copy.deepcopy(tc.SWITCHES)
    inv = result["inventory"]
    if extra_ports:
        inv["interfaces"] += [
            tc._port("sw-core-01", "Eth1/20", mode="tagged", untagged_vlan=1, native_vlan=1,
                     trunk_vlans="20", tagged_vlans=[20], description="esx01 vmnic2"),
            tc._port("sw-acc-01", "Gi1/0/6", mode="tagged", untagged_vlan=1, native_vlan=1,
                     trunk_vlans="20,30", tagged_vlans=[20, 30]),
            tc._port("sw-acc-01", "Gi1/0/7", mode="access", untagged_vlan=20),
            tc._port("sw-acc-01", "Gi1/0/8", mode="access", untagged_vlan=20),
        ]  # fmt: skip
        inv["neighbors"].append({"device": "sw-core-01", "port": "Eth1/20", "protocol": "cdp",
                                 "remote_name": "esx01.example.ch", "remote_port": "vmnic2",
                                 "remote_platform": "VMware ESX",
                                 "remote_caps": ["Switch"]})  # fmt: skip
    inv["macs"] = macs
    inv["arps"] = arps
    return result


def _mac(device, port, mac, vlan):
    return {"device": device, "port": port, "mac": mac, "vlan": vlan, "kind": "dynamic"}


FIRST = [
    _mac("sw-core-01", "Eth1/20", "0050.5601.0001", 20),  # web01 an esx01
    _mac("sw-acc-01", "Po1", "00:50:56:01:00:01", 20),  # derselbe auf dem Uplink
    _mac("sw-acc-01", "Gi1/0/6", "00-50-56-01-00-03", 20),  # db01 an esx02
    _mac("sw-acc-01", "Gi1/0/7", SRV, 20),  # physischer Server
    _mac("sw-core-01", "Po10", SRV, 20),  # ... ueber den Uplink
    _mac("sw-core-01", "Eth1/10", "00:00:0c:9f:f0:0a", 10),  # nur hinter dem ACI-Leaf
]
ARPS = [{"device": "sw-core-01", "ip": "10.20.0.15", "mac": SRV, "interface": "Vl20"}]


def _deliver(client, tid, sid, platform, result):
    req = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _setup(client):
    tid, sw = tc._setup(client)
    vc = client.post("/systems", json={"tenant_id": tid, "name": "vc", "platform": "vmware",
                                       "base_url": "https://vc"}, headers=ADMIN).json()  # fmt: skip
    _deliver(client, tid, vc["id"], "vmware", VCENTER)
    _deliver(client, tid, sw["id"], "cisco_switch", _switches(FIRST, ARPS))
    return tid, sw


def test_endpunkte_anschluss_und_suche(client, session_factory):
    tid, sw = _setup(client)
    s = session_factory()
    assert l2.mac_key("A0B1.C2D3.E4F5") == SRV and l2.mac_key("zz") is None
    loc = l2.Locator(s, tid)
    rows = {(e.device, e.port): e for e in loc.current()}
    assert loc.is_uplink(rows[("sw-acc-01", "Po1")])  # Port-Channel mit Mitglied zum Switch
    assert loc.is_uplink(rows[("sw-core-01", "Eth1/10")])  # ACI-Leaf
    assert not loc.is_uplink(rows[("sw-core-01", "Eth1/20")])  # ESXi-Host (CDP „Switch“)
    s.close()

    # Endpunkte: ohne Uplinks, mit VM bzw. IP aus ARP
    ep = client.get("/cabling/endpoints", params={"tenant_id": tid}).json()
    assert ep["total"] == 3 and ep["summary"] == {"macs": 3, "ports": 3, "vms": 2, "with_ip": 2}
    by_mac = {i["mac"]: i for i in ep["items"]}
    web = by_mac["00:50:56:01:00:01"]
    assert (web["device"], web["port"], web["vlan"]) == ("sw-core-01", "Eth1/20", 20)
    assert web["vm"]["name"] == "web01" and web["ips"] == ["10.20.0.5"]
    assert web["via"] == {"device": "esx01.example.ch", "port": "vmnic2", "kind": "esxi",
                          "read": False}  # fmt: skip
    assert by_mac[SRV]["ips"] == ["10.20.0.15"] and by_mac[SRV]["port_mode"] == "access"
    every = client.get("/cabling/endpoints", params={"tenant_id": tid, "uplinks": "true"}).json()
    assert every["total"] == 6
    one = client.get("/cabling/endpoints", params={"tenant_id": tid, "q": "web01"}).json()
    assert [i["port"] for i in one["items"]] == ["Eth1/20"]
    at_port = {"tenant_id": tid, "device": "sw-acc-01", "port": "GigabitEthernet1/0/7"}
    port = client.get("/cabling/endpoints", params=at_port).json()
    assert [i["mac"] for i in port["items"]] == [SRV]
    ports = client.get("/cabling/ports", params={"tenant_id": tid, "q": "Eth1/20"}).json()
    assert ports["items"][0]["endpoints"] == 1
    dev = client.get("/cabling/devices", params={"tenant_id": tid}).json()
    assert dev["summary"]["endpoints"] == 3

    # Wo haengt …: VM-Name, IP (ARP), MAC; nur hinter dem Leaf
    hit = client.get("/cabling/locate", params={"tenant_id": tid, "q": "web01"}).json()
    assert hit["kind"] == "name"
    (item,) = (i for i in hit["items"] if i["mac"] == "00:50:56:01:00:01")
    assert item["reason"] == "VM web01" and item["seen_on"] == 2
    assert [(x["device"], x["port"]) for x in item["locations"]] == [("sw-core-01", "Eth1/20")]
    ip = client.get("/cabling/locate", params={"tenant_id": tid, "q": "10.20.0.15"}).json()
    assert ip["kind"] == "ip" and ip["items"][0]["mac"] == SRV
    assert ip["items"][0]["locations"][0]["port"] == "Gi1/0/7"
    vm_ip = client.get("/cabling/locate", params={"tenant_id": tid, "q": "10.20.0.5"}).json()
    assert vm_ip["items"][0]["reason"].startswith("VM web01")
    mac = client.get("/cabling/locate", params={"tenant_id": tid, "q": "0000.0c9f.f00a"}).json()
    assert mac["kind"] == "mac" and mac["items"][0]["locations"] == []
    assert mac["items"][0]["behind"][0]["via"]["device"] == "leaf-101"
    none = client.get("/cabling/locate", params={"tenant_id": tid, "q": "gibt-es-nicht"}).json()
    assert none["items"] == []

    # zweiter Lauf: Server an einem anderen Port; ARP nachgefuehrt, nichts ersetzt
    later = [m for m in FIRST if m["mac"] != SRV] + [_mac("sw-acc-01", "Gi1/0/8", SRV, 20)]
    _deliver(client, tid, sw["id"], "cisco_switch", _switches(later, ARPS))
    hit = client.get("/cabling/locate", params={"tenant_id": tid, "q": SRV}).json()["items"][0]
    assert [x["port"] for x in hit["locations"]] == ["Gi1/0/8"]
    assert [x["port"] for x in hit["earlier"]] == ["Gi1/0/7"]
    with session_factory() as s:
        assert s.scalar(select(ArpEntry.first_seen)) < s.scalar(select(ArpEntry.last_seen))
    # dritter Lauf nur mit sw-acc-01: sw-core-01 bleibt mit seinem letzten Stand aktuell
    _deliver(client, tid, sw["id"], "cisco_switch",
             _switches([m for m in later if m["device"] == "sw-acc-01"], []))  # fmt: skip
    hit = client.get("/cabling/locate", params={"tenant_id": tid, "q": "web01"}).json()
    web = next(i for i in hit["items"] if i["mac"] == "00:50:56:01:00:01")
    assert web["locations"][0]["port"] == "Eth1/20"

    # nicht fuer Kunden-Benutzer
    cust = client.post("/customers", json={"tenant_id": tid, "name": "K", "selectors": []},
                       headers=OP).json()  # fmt: skip
    who = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(cust["id"])}
    for path in ("/cabling/endpoints", "/cabling/locate?q=web01"):
        assert client.get(path, params={"tenant_id": tid}, headers=who).status_code == 403


def test_vlan_am_uplink_aufbewahrung_und_loeschen(client, session_factory):
    tid, sw = _setup(client)
    params = {"tenant_id": tid, "type": "uplink_vlan_missing"}
    found = client.get("/findings", params=params).json()
    items = found["items"] if isinstance(found, dict) else found
    (f,) = items  # nur esx01 an Eth1/20: VLAN 30 (app01) fehlt; Gi1/0/6 erlaubt 20 und 30
    assert f["subject_id"] == "sw-core-01|eth1/20" and f["severity"] == "warning"
    assert f["detail"]["vlans"] == [30] and f["detail"]["portgroups"] == {"30": ["PG_app"]}
    assert f["detail"]["hosts"] == ["esx01.example.ch"]
    assert "VLAN 30 fehlt an sw-core-01 Eth1/20" in f["message"]
    assert "PG_app" in f["message"] and "vMotion" in f["hint"]

    # Aufbewahrung: Eintraege, die seit der Frist niemand gesehen hat
    old = datetime.now(UTC) - timedelta(days=40)
    with session_factory() as s:
        s.execute(update(MacEntry).where(MacEntry.port == "Gi1/0/7").values(last_seen=old))
        s.commit()
    st = client.get("/system/retention", headers=ADMIN).json()
    assert st["policy"]["l2_days"] == 30 and st["due"]["l2"] == 1
    body = client.post("/system/retention/purge", headers=ADMIN).json()
    assert body["deleted"]["l2"] == 1
    with session_factory() as s:
        assert not s.scalars(select(MacEntry).where(MacEntry.port == "Gi1/0/7")).all()

    # Quellsystem loeschen: Eintraege weg
    r = client.delete(f"/systems/{sw['id']}?confirm=Switches A", headers=ADMIN)
    assert r.status_code in (200, 204)
    with session_factory() as s:
        assert s.scalars(select(MacEntry)).all() == [] and s.scalars(select(ArpEntry)).all() == []
