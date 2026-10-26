"""Switches und Verkabelung (0.62.0, ADR 0025): Quellsystem als Gruppe von Switches,
Host-Schluessel festhalten bzw. vergessen, Verbindungen aus CDP/LLDP aller Quellen (auch
ACI-Leafs), Abweichungen zwischen zwei gelesenen Switches als Befund, API der Ansicht.

sw-acc-01 und sw-core-01 sind ueber zwei Kabel verbunden: Gi1/0/47 <-> Eth1/1 sauber,
Gi1/0/48 <-> Eth1/2 mit Speed, Duplex, Native VLAN, erlaubten VLANs und Port-Channel
abweichend. Dazu fw-08 (nicht gelesen), ein Port, den nur sw-core-01 meldet, und leaf-101 aus
dem ACI.
"""

import copy

from app.models import AuditLog, SourceSystem
from app.store import agents as agent_store
from app.store import cabling

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
TOKEN = {"X-Internal-Token": "dev-only-token"}
KEY_A = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
KEY_B = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA4Vh9a1Hkqq1v6GSpdx9Wq7LNRF1WxSvlIqJ7mcK0Z8"


def _port(device, name, **kw):
    base = {"device": device, "name": name, "kind": "physical", "status": "connected",
            "enabled": True, "speed": "a-1000", "duplex": "a-full"}  # fmt: skip
    return {**base, **kw}


TRUNK = {"mode": "tagged", "untagged_vlan": 1, "native_vlan": 1, "trunk_vlans": "10,20,30",
         "tagged_vlans": [10, 20, 30]}  # fmt: skip
SWITCHES = {
    "status": "completed",
    "message": "Switches: 2 gelesen",
    "host_keys": {"10.0.0.11": KEY_A},
    "inventory": {
        "devices": [
            {"name": "sw-acc-01", "role": "switch", "model": "C9300-48P", "mgmt_ip": "10.0.0.11",
             "version": "17.9.4a"},
            {"name": "sw-core-01", "role": "switch", "model": "C93180YC-FX",
             "mgmt_ip": "10.0.0.2", "version": "10.3(4a)"},
        ],
        "interfaces": [
            _port("sw-acc-01", "Gi1/0/47", lag="Po1", **TRUNK),
            _port("sw-acc-01", "Gi1/0/48", lag="Po1", **TRUNK),
            _port("sw-acc-01", "Po1", kind="lag", **TRUNK),
            _port("sw-acc-01", "Gi1/0/5", mode="access", untagged_vlan=20),
            _port("sw-acc-01", "Te1/1/1", mode="access", untagged_vlan=30, speed="10G",
                  duplex="full", description="fw-08 port5"),
            _port("sw-acc-01", "Gi1/0/2", mode="access", untagged_vlan=20, status="notconnect"),
            _port("sw-core-01", "Eth1/1", lag="Po10", speed="1000", duplex="full", **TRUNK),
            _port("sw-core-01", "Eth1/2", speed="100", duplex="half", mode="tagged",
                  untagged_vlan=99, native_vlan=99, trunk_vlans="10,20", tagged_vlans=[10, 20]),
            _port("sw-core-01", "Eth1/3", mode="access", untagged_vlan=20),
            _port("sw-core-01", "Eth1/10", **TRUNK),
            _port("sw-core-01", "Po10", kind="lag", attrs={"vpc": "10"}, **TRUNK),
        ],
        "vlans": [{"vid": 10, "name": "Server"}, {"vid": 20, "name": "Clients"},
                  {"vid": 30, "name": "DMZ"}],
        "neighbors": [
            {"device": "sw-acc-01", "port": "Gi1/0/47", "protocol": "cdp",
             "remote_name": "sw-core-01", "remote_port": "Ethernet1/1", "remote_ip": "10.0.0.2"},
            {"device": "sw-acc-01", "port": "Gi1/0/48", "protocol": "cdp",
             "remote_name": "sw-core-01", "remote_port": "Ethernet1/2"},
            {"device": "sw-acc-01", "port": "Te1/1/1", "protocol": "lldp", "remote_name": "fw-08",
             "remote_port": "port5", "remote_ip": "10.0.30.1"},
            {"device": "sw-core-01", "port": "Eth1/1", "protocol": "cdp",
             "remote_name": "sw-acc-01.example.ch", "remote_port": "GigabitEthernet1/0/47"},
            {"device": "sw-core-01", "port": "Eth1/2", "protocol": "cdp",
             "remote_name": "sw-acc-01", "remote_port": "GigabitEthernet1/0/48"},
            {"device": "sw-core-01", "port": "Eth1/3", "protocol": "lldp",
             "remote_name": "sw-acc-01", "remote_port": "Gi1/0/5"},  # meldet nur sw-core-01
            {"device": "sw-core-01", "port": "Eth1/10", "protocol": "cdp",
             "remote_name": "leaf-101", "remote_port": "Ethernet1/10"},
        ],
    },
}  # fmt: skip
ACI = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "leaf-101", "role": "leaf", "node_id": "101"}],
        "interfaces": [{"device": "leaf-101", "name": "eth1/10", "kind": "physical",
                        "neighbor": "sw-core-01", "neighbor_port": "Eth1/10"}],
    },
}  # fmt: skip


def _setup(client):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "Switches A", "platform": "cisco_switch",
            "read_scope": "10.0.0.11, 10.0.0.2\n10.0.0.11",
            "username": "ro", "password": "x"}  # fmt: skip
    r = client.post("/systems", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    sw = r.json()
    apic = {"tenant_id": tid, "name": "apic", "platform": "cisco_aci", "base_url": "https://apic"}
    aci = client.post("/systems", json=apic, headers=ADMIN).json()
    for sid, platform, result in ((sw["id"], "cisco_switch", SWITCHES),
                                  (aci["id"], "cisco_aci", ACI)):  # fmt: skip
        req = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
        run_id = client.post("/jobs/collect", json=req).json()["run_id"]
        r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
        assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    return tid, sw


def test_quellsystem_switches_und_host_schluessel(client, session_factory):
    tid, sw = _setup(client)
    assert sw["read_scope"] == "10.0.0.11\n10.0.0.2" and sw["base_url"] == "ssh://10.0.0.11"
    bad = {"tenant_id": tid, "name": "x", "platform": "cisco_switch", "read_scope": "a b;c$"}
    assert client.post("/systems", json=bad, headers=ADMIN).status_code == 422
    empty = {"tenant_id": tid, "name": "y", "platform": "cisco_switch", "read_scope": ""}
    assert client.post("/systems", json=empty, headers=ADMIN).status_code == 422
    # Host-Schluessel beim ersten Lauf festgehalten (nur Fingerabdruck in der Ausgabe)
    listed = client.get("/systems", params={"tenant_id": tid}).json()
    got = next(x for x in listed if x["id"] == sw["id"])
    (entry,) = got["host_keys"]
    assert entry["host"] == "10.0.0.11" and entry["fingerprint"].startswith("ssh-ed25519 SHA256:")
    assert "AAAA" not in str(got)
    s = session_factory()
    src = s.get(SourceSystem, sw["id"])
    target = agent_store.target_for(src, src.agent_id)
    assert target["known_hosts"] == {"10.0.0.11": KEY_A}
    assert target["read_scope"].split() == ["10.0.0.11", "10.0.0.2"]
    # ein anderer Schluessel fuer dieselbe Adresse ersetzt den festgehaltenen nicht
    later = copy.deepcopy(SWITCHES)
    later["host_keys"] = {"10.0.0.11": KEY_B, "10.0.0.2": KEY_B}
    req = {"platform": "cisco_switch", "tenant_id": tid, "source_system_id": sw["id"]}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=later)
    s.expire_all()
    assert s.get(SourceSystem, sw["id"]).ssh_host_keys == {"10.0.0.11": KEY_A, "10.0.0.2": KEY_B}
    # vergessen (Admin) - beim naechsten Lauf neu lernen; Adresse entfernt = Schluessel weg
    r = client.patch(f"/systems/{sw['id']}", json={"forget_host_keys": ["10.0.0.11"]},
                     headers=ADMIN)  # fmt: skip
    assert [h["host"] for h in r.json()["host_keys"]] == ["10.0.0.2"]
    r = client.patch(f"/systems/{sw['id']}", json={"read_scope": "10.0.0.11"}, headers=ADMIN)
    assert r.json()["host_keys"] == [] and r.json()["base_url"] == "ssh://10.0.0.11"
    actions = {a.action for a in s.query(AuditLog)}
    assert "source.ssh_host_keys_learned" in actions
    s.close()


def test_verkabelung_verbindungen_und_befunde(client, session_factory):
    tid, sw = _setup(client)
    s = session_factory()
    cab = cabling.build(s, tid)
    links = {lid: link for lid, link in cab.links.items()}
    by_ports = {(link.a.device.name, link.a.port, link.b.device.name, link.b.port): link
                for link in links.values()}  # fmt: skip
    ok = next(v for k, v in by_ports.items() if "Gi1/0/47" in k)
    assert ok.state == "both" and ok.issues == [] and ok.protocols == {"cdp"}
    assert {ok.a.port, ok.b.port} == {"Gi1/0/47", "Eth1/1"}  # Langform des Nachbarn erkannt
    bad = next(v for k, v in by_ports.items() if "Gi1/0/48" in k)
    kinds = {i["type"]: i["text"] for i in bad.issues}
    assert set(kinds) == {"link_speed_mismatch", "link_duplex_mismatch", "link_vlan_mismatch",
                          "link_lag_mismatch"}  # fmt: skip
    assert "Native VLAN 1 ↔ 99" in kinds["link_vlan_mismatch"]
    assert "nur auf sw-acc-01: 30" in kinds["link_vlan_mismatch"]
    assert kinds["link_lag_mismatch"] == "Port-Channel nur auf sw-acc-01 (Po1)"
    fw = next(v for k, v in by_ports.items() if "port5" in k)
    assert fw.state == "unread" and not fw.issues
    one = next(v for k, v in by_ports.items() if "Eth1/3" in k)
    assert one.state == "one_sided"
    leaf = next(v for k, v in by_ports.items() if "leaf-101" in k)
    assert leaf.state == "both"  # ACI-Leaf meldet am Port, Switch per CDP
    assert cabling.speed_mbps("a-1000") == 1000 and cabling.speed_mbps("10G") == 10000
    assert cabling.speed_mbps("auto") is None
    assert cabling.port_key("GigabitEthernet1/0/48") == cabling.port_key("Gi1/0/48")
    assert cabling.device_key("sw-core-01(FDO123)") == cabling.device_key("SW-CORE-01.example.ch")
    s.close()

    # Befunde
    rows = client.get("/findings", params={"tenant_id": tid, "limit": 200}).json()
    items = rows["items"] if isinstance(rows, dict) else rows
    types = sorted(f["type"] for f in items if f["type"].startswith("link_"))
    assert types == ["link_duplex_mismatch", "link_lag_mismatch", "link_one_sided",
                     "link_speed_mismatch", "link_vlan_mismatch"]  # fmt: skip
    dup = next(f for f in items if f["type"] == "link_duplex_mismatch")
    assert dup["severity"] == "error" and dup["subject_kind"] == "link"

    # API der Ansicht
    dev = client.get("/cabling/devices", params={"tenant_id": tid}).json()
    assert dev["summary"]["switches"] == 2 and dev["summary"]["links"] == 5
    assert dev["summary"]["states"] == {"both": 3, "one_sided": 1, "unread": 1}
    names = [d["name"] for d in dev["items"]]
    assert names[:2] == ["sw-acc-01", "sw-core-01"] and "fw-08" in names
    acc = next(d for d in dev["items"] if d["name"] == "sw-acc-01")
    assert (acc["ports"], acc["connected"], acc["links"], acc["issues"]) == (5, 4, 4, 4)
    links = client.get("/cabling/links", params={"tenant_id": tid, "device": "sw-acc-01"}).json()
    assert links["total"] == 4 and all(i["a"]["device"] == "sw-acc-01" for i in links["items"])
    assert links["items"][0]["issues"]  # Abweichungen zuerst
    only = client.get("/cabling/links", params={"tenant_id": tid, "issues": "true"}).json()
    assert only["total"] == 1
    state = client.get("/cabling/links", params={"tenant_id": tid, "state": "one_sided"}).json()
    assert state["items"][0]["b"]["port"] in ("Gi1/0/5", "Eth1/3")
    ports = client.get("/cabling/ports", params={"tenant_id": tid, "device": "sw-core-01",
                                                 "vlan": 30}).json()  # fmt: skip
    assert [p["name"] for p in ports["items"]] == ["Eth1/1", "Eth1/10", "Po10"]
    eth2 = client.get("/cabling/ports", params={"tenant_id": tid, "q": "Eth1/2"}).json()["items"]
    assert eth2[0]["peer"]["device"] == "sw-acc-01" and len(eth2[0]["issues"]) == 4
    down = client.get("/cabling/ports", params={"tenant_id": tid, "status": "down"}).json()
    assert [p["name"] for p in down["items"]] == ["Gi1/0/2"]
    vlans = {v["vid"]: v for v in client.get("/cabling/vlans", params={"tenant_id": tid}).json()}
    assert vlans[30]["names"] == ["DMZ"]
    assert {d["device"]: (d["access"], d["trunks"]) for d in vlans[30]["devices"]} == {
        "sw-acc-01": (1, 2), "sw-core-01": (0, 2)}  # fmt: skip
    assert client.get("/cabling/sources", params={"tenant_id": tid}).json() == [
        {"id": sw["id"], "name": "Switches A", "switches": 2}]  # fmt: skip
    # nicht fuer Kunden-Benutzer
    cust = client.post("/customers", json={"tenant_id": tid, "name": "K", "selectors": []},
                       headers=OP).json()  # fmt: skip
    who = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(cust["id"])}
    assert client.get("/cabling/devices", params={"tenant_id": tid}, headers=who).status_code == 403
    # Quellsystem loeschen: Nachbarn weg
    assert client.delete(f"/systems/{sw['id']}?confirm=Switches A", headers=ADMIN).status_code \
        in (200, 204)  # fmt: skip
    dev = client.get("/cabling/devices", params={"tenant_id": tid}).json()
    assert dev["summary"]["switches"] == 0


def test_vlans_auch_aus_dem_aci(client):
    """0.69.1 (Anwender: „Die VLANs werden auf dem ACI nicht ausgelesen, die Liste ist leer“):
    der Reiter VLANs zeigt auch die VLANs des APIC mit ihren EPGs, je Leaf Ports und vPCs."""
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    apic = {"tenant_id": tid, "name": "apic", "platform": "cisco_aci", "base_url": "https://apic"}
    aci = client.post("/systems", json=apic, headers=ADMIN).json()
    result = {"status": "completed", "inventory": {
        "devices": [{"name": "leaf-101", "role": "leaf", "node_id": "101"}],
        "interfaces": [
            {"device": "leaf-101", "name": "eth1/5", "kind": "physical", "mode": "tagged",
             "tagged_vlans": [300], "epgs": ["TE/AP1/EPG_vm"]},
            {"device": "leaf-101", "name": "PG_esx", "kind": "lag", "mode": "tagged",
             "tagged_vlans": [300, 310]},
            {"device": "leaf-101", "name": "eth1/20", "kind": "physical", "mode": "access",
             "untagged_vlan": 310},
        ],
        "vlans": [{"vid": 300, "name": "EPG_vm", "epgs": ["TE/AP1/EPG_vm"]},
                  {"vid": 310, "name": "EPG_db", "epgs": ["TE/AP1/EPG_db"]}],
    }}  # fmt: skip
    req = {"platform": "cisco_aci", "tenant_id": tid, "source_system_id": aci["id"]}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200, r.text
    vlans = {v["vid"]: v for v in client.get("/cabling/vlans", params={"tenant_id": tid}).json()}
    assert set(vlans) == {300, 310}
    vm = vlans[300]
    assert (vm["names"], vm["epgs"], vm["sources"]) == (["EPG_vm"], ["TE/AP1/EPG_vm"], ["aci"])
    assert vm["devices"] == [{"device": "leaf-101", "access": 0, "trunks": 2}]  # Port + vPC
    assert vlans[310]["devices"] == [{"device": "leaf-101", "access": 1, "trunks": 1}]


def test_patchungen_eines_geraets(client):
    """0.70.0 (Anwender: „im Plan unten gleich die Patchungen der Kabel mit Details“)."""
    tid, _sw = _setup(client)
    r = client.get("/cabling/patches", params={"tenant_id": tid, "device": "sw-core-01"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["device"]["model"] == "C93180YC-FX" and out["device"]["mgmt_ip"] == "10.0.0.2"
    assert out["total"] == 4 and out["issues"] >= 1
    rows = {x["a"]["port"]: x for x in out["items"]}
    assert set(rows) == {"Eth1/1", "Eth1/2", "Eth1/3", "Eth1/10"}
    # eigenes Ende links, Port-Channel zuerst (gruppiert), vPC am Channel gelesen
    assert out["items"][0]["a"]["port"] == "Eth1/1"
    eth1 = rows["Eth1/1"]
    assert (eth1["a"]["lag"], eth1["a"]["vpc"], eth1["a"]["trunk_vlans"]) == (
        "Po10",
        "10",
        "10,20,30",
    )
    assert (eth1["b"]["device"], eth1["b"]["port"], eth1["b"]["model"]) == (
        "sw-acc-01",
        "Gi1/0/47",
        "C9300-48P",
    )
    assert eth1["state"] == "both" and eth1["protocols"] == ["cdp"]
    assert rows["Eth1/2"]["issues"]  # Speed/Duplex/VLAN weichen ab
    assert rows["Eth1/10"]["b"]["device"] == "leaf-101"
    # nur die Kabel zu einer Gegenstelle
    only = client.get("/cabling/patches", params={"tenant_id": tid, "device": "sw-core-01",
                                                  "peer": "leaf-101"}).json()  # fmt: skip
    assert only["peer"] == "leaf-101" and [x["a"]["port"] for x in only["items"]] == ["Eth1/10"]
    # ACI-Leaf: VLANs aus der Liste, Gegenrichtung
    leaf = client.get("/cabling/patches", params={"tenant_id": tid, "device": "leaf-101"}).json()
    assert [x["b"]["device"] for x in leaf["items"]] == ["sw-core-01"]
    assert (
        client.get(
            "/cabling/patches", params={"tenant_id": tid, "device": "gibtsnicht"}
        ).status_code
        == 404
    )
