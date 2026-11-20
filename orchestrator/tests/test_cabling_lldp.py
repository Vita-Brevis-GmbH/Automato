"""Firewalls in der Verkabelung und VLAN getrennt nur je L2-Domaene (1.23.0).

Anwender 2026-11-20 (Test von 1.22.0): „die Firewalls werden nicht in der Verkabelung erkannt,
LLDP ist aktiv“ - ein Catalyst 1300 meldet die FortiGate per LLDP mit Systemname `fw-a1`,
Chassis-ID (eine MAC fuer alle Ports) und Port-ID = Interface-Name (`Management`, `internal4`,
`internal1`); in Automato heisst die FortiGate anders (`fw-a1r`). Zugeordnet wird jetzt auch ueber
die Chassis-ID (MAC eines gelesenen Interfaces), HA-Mitglieder (Hostname, Seriennummer), die
Beschreibung des Ports und die LLDP-Nachbarn der FortiGate selbst. Nicht Zuordenbares bleibt
sichtbar.

Und: „VLAN getrennt, obwohl sie an verschiedenen Standorten ist … auf zwei getrennte Systeme ist
das gleiche VLAN zu brauchen normal“ - der Befund `vlan_split` gilt nur innerhalb einer
L2-Domaene (gleicher Standort, Switches ueber Kabel verbunden, nicht ueber das ACI-Fabric).
Namen, MACs und Adressen anonymisiert.
"""

from app.store import cabling, l2_topo

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}
CHASSIS = "02:00:00:00:20:53"  # FortiGate: eine MAC fuer alle Ports


def _port(device, name, **kw):
    base = {"device": device, "name": name, "kind": "physical", "status": "connected",
            "enabled": True, "mode": "access", "untagged_vlan": 10}  # fmt: skip
    return {**base, **kw}


def _nb(port, name, remote_port=None, chassis=None, **kw):
    return {"device": "sw-a1", "port": port, "protocol": "lldp", "remote_name": name,
            "remote_port": remote_port, "remote_chassis": chassis, **kw}  # fmt: skip


SWITCH = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "sw-a1", "role": "switch", "model": "C1300-24P"}],
        "interfaces": [_port("sw-a1", p) for p in ("gi1/0/1", "gi1/0/2", "gi1/0/9", "gi1/0/20",
                                                    "gi1/0/21", "gi1/0/22", "gi1/0/23",
                                                    "gi1/0/24")],
        "neighbors": [
            # wie `show lldp neighbors` des Catalyst 1300
            _nb("gi1/0/22", "fw-a1", "Management", CHASSIS, remote_caps=["R"]),
            _nb("gi1/0/23", "fw-a1", "internal4", CHASSIS, remote_caps=["R"]),
            _nb("gi1/0/24", "fw-a1", "internal1", CHASSIS, remote_caps=["R"]),
            # Gegenstellen nur mit Chassis-ID: zwei MACs sind zwei Geraete (keine Domaene)
            _nb("gi1/0/1", "0200.0000.6050", "0200.0000.6050", "0200.0000.6050"),
            _nb("gi1/0/2", "0200.0000.6051", "0200.0000.6051", "0200.0000.6051"),
            # FortiGate, die Automato nicht liest: bleibt sichtbar, als Firewall
            _nb("gi1/0/9", "fw-x9", "port1", "02:00:00:00:50:f0",
                remote_description="FortiGate-60F v7.2.8,build1639,240313 (GA.M)"),
            # zweites HA-Mitglied: eigener Hostname bzw. nur die Seriennummer
            _nb("gi1/0/20", "fw-a2", "internal1", "02:00:00:00:21:53"),
            _nb("gi1/0/21", "FGT60F0000000002", "internal4", None),
        ],
    },
}  # fmt: skip
FORTIGATE = {
    "status": "completed",
    "inventory": {
        # Hostname nicht geliefert: das Geraet heisst wie das Quellsystem
        "devices": [{"name": "fw-a1r", "role": "firewall", "serial": "FGT60F0000000001"}],
        "interfaces": [
            {"device": "fw-a1r", "name": "mgmt", "kind": "physical", "description": "Management",
             "attrs": {"mac": CHASSIS}, "ips": ["192.0.2.10/24"]},
            {"device": "fw-a1r", "name": "internal1", "kind": "physical",
             "attrs": {"mac": "02:00:00:00:20:54"}, "ips": ["10.10.0.1/24"]},
            {"device": "fw-a1r", "name": "internal4", "kind": "physical",
             "attrs": {"mac": "02:00:00:00:20:57"}},
        ],
        # LLDP der FortiGate selbst (Monitor-API): hoert den Switch an internal1
        "neighbors": [{"device": "fw-a1r", "port": "internal1", "protocol": "lldp",
                       "remote_name": "sw-a1", "remote_port": "gi1/0/24",
                       "remote_chassis": "02:00:00:00:02:51"}],
    },
    "routing": {"ha": [{"node": "fw-a1r", "mode": "a-p", "members": [
        {"hostname": "fw-a1r", "serial": "FGT60F0000000001", "role": "primary"},
        {"hostname": "fw-a2", "serial": "FGT60F0000000002", "role": "secondary"}]}]},
}  # fmt: skip


def _system(client, tid, **body):
    r = client.post("/systems", json={"tenant_id": tid, **body}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _deliver(client, tid, sid, platform, result):
    req = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _setup(client):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    sw = _system(client, tid, name="Switches A", platform="cisco_switch", read_scope="10.0.0.11",
                 username="ro", password="x")  # fmt: skip
    fw = _system(client, tid, name="fw-a1r", platform="fortigate",
                 base_url="https://fw-a1r.example.ch")  # fmt: skip
    _deliver(client, tid, fw, "fortigate", FORTIGATE)
    _deliver(client, tid, sw, "cisco_switch", SWITCH)
    return tid


def _at(cab, port):
    """Gegenstelle am Port von sw-a1."""
    for lk in cab.links.values():
        for mine, other in ((lk.a, lk.b), (lk.b, lk.a)):
            if mine.device.name == "sw-a1" and mine.port == port:
                return lk, other
    raise AssertionError(port)


def test_fortigate_ueber_chassis_id_port_und_eigene_nachbarn(client, session_factory):
    tid = _setup(client)
    with session_factory() as s:
        cab = cabling.build(s, tid)
        assert "fw-a1" not in cab.devices  # kein unbekanntes Geraet neben der FortiGate
        ends = {p: _at(cab, p)[1] for p in ("gi1/0/22", "gi1/0/23", "gi1/0/24")}
        assert {p: (e.device.name, e.device.kind, e.device.read, e.port)
                for p, e in ends.items()} == {
            "gi1/0/22": ("fw-a1r", "firewall", True, "mgmt"),  # Port-ID = Beschreibung
            "gi1/0/23": ("fw-a1r", "firewall", True, "internal4"),
            "gi1/0/24": ("fw-a1r", "firewall", True, "internal1"),
        }  # fmt: skip
        assert all(e.iface is not None for e in ends.values())
        # internal1 melden beide Seiten; mgmt hoert die FortiGate nicht (LLDP-Empfang aus)
        assert _at(cab, "gi1/0/24")[0].state == "both"
        assert _at(cab, "gi1/0/22")[0].state == "one_sided"
        # nur mit Chassis-ID: zwei verschiedene Geraete, MAC als Schluessel
        one, two = _at(cab, "gi1/0/1")[1], _at(cab, "gi1/0/2")[1]
        assert (one.device.key, two.device.key) == ("02:00:00:00:60:50", "02:00:00:00:60:51")
        # nicht gelesene FortiGate: sichtbar, als Firewall (leitet nicht weiter)
        x9 = _at(cab, "gi1/0/9")[1].device
        assert (x9.name, x9.kind, x9.read, x9.switching) == ("fw-x9", "firewall", False, False)
        # HA-Mitglied: eigener Hostname bzw. Seriennummer; Port ohne Konfiguration (die gehoert
        # dem gelesenen Geraet - auch fuer Kabel nach NetBox)
        for port, name in (("gi1/0/20", "internal1"), ("gi1/0/21", "internal4")):
            lk, end = _at(cab, port)
            assert (end.device.name, end.device.ha_of, end.port, end.iface) == (
                "fw-a2",
                "fw-a1r",
                name,
                None,
            )
            assert end.device.kind == "firewall" and lk.state == "unread"
    links = client.get("/cabling/links", params={"tenant_id": tid, "device": "fw-a1r"}).json()
    assert len(links["items"]) == 3
    devices = client.get("/cabling/devices", params={"tenant_id": tid}).json()["items"]
    names = {d["name"]: (d["kind"], d["read"]) for d in devices}
    assert {d["name"]: d["ha_of"] for d in devices}["fw-a2"] == "fw-a1r"
    assert names["fw-a1r"] == ("firewall", True) and names["fw-a2"] == ("firewall", True)
    assert names["fw-x9"] == ("firewall", False) and "fw-a1" not in names


def test_gegenstelle_ueber_adresse_und_name_vor_mac():
    """Einheiten: MAC in jeder Schreibweise; der Name geht vor der Chassis-ID."""
    assert cabling.mac_key("0200.0000.2053") == cabling.mac_key("02-00-00-00-20-53") == CHASSIS
    assert cabling.mac_key("internal1") is None and cabling.mac_key("gi1/0/1") is None
    assert cabling.device_key("sw-a1.example.ch") == "sw-a1"
    assert cabling.device_key("0200.0000.2053") == CHASSIS


# -- VLAN getrennt nur je L2-Domaene --------------------------------------------------------


def _sw(device, *ports, neighbors=()):
    return {
        "status": "completed",
        "inventory": {
            "devices": [{"name": device, "role": "switch"}],
            "interfaces": list(ports),
            "neighbors": list(neighbors),
        },
    }


def _trunk(device, name, vlans):
    return _port(device, name, mode="tagged", untagged_vlan=1, native_vlan=1, trunk_vlans=vlans)


def test_vlan_getrennt_nur_innerhalb_einer_l2_domaene(client, session_factory):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    sites = {}
    for code, name in (("site-a", "Standort A"), ("site-b", "Standort B")):
        r = client.post(f"/tenants/{tid}/sites", json={"code": code, "name": name}, headers=ADMIN)
        sites[code] = r.json()["id"]
    # Standort A: sw-a1 und sw-a2 ueber einen Trunk ohne VLAN 50 -> getrennt (Befund)
    a = _system(client, tid, name="Switches A", platform="cisco_switch", read_scope="10.0.0.11",
                username="ro", password="x", site_id=sites["site-a"])  # fmt: skip
    _deliver(client, tid, a, "cisco_switch", {"status": "completed", "inventory": {
        "devices": [{"name": "sw-a1", "role": "switch"}, {"name": "sw-a2", "role": "switch"}],
        "interfaces": [_port("sw-a1", "gi1/0/1", untagged_vlan=50),
                       _port("sw-a1", "gi1/0/10", untagged_vlan=10),
                       _trunk("sw-a1", "gi1/0/48", "10,20"),
                       _port("sw-a2", "gi1/0/1", untagged_vlan=50),
                       _trunk("sw-a2", "gi1/0/48", "10,20")],
        "neighbors": [
            {"device": "sw-a1", "port": "gi1/0/48", "remote_name": "sw-a2",
             "remote_port": "gi1/0/48"},
            {"device": "sw-a2", "port": "gi1/0/48", "remote_name": "sw-a1",
             "remote_port": "gi1/0/48"}]}})  # fmt: skip
    # Standort B: dieselben VLAN-Nummern (10 Mgmt, 50 Clients) - kein Kabel nach A
    b = _system(client, tid, name="Switches B", platform="cisco_switch", read_scope="10.0.1.11",
                username="ro", password="x", site_id=sites["site-b"])  # fmt: skip
    _deliver(client, tid, b, "cisco_switch",
             _sw("sw-b1", _port("sw-b1", "gi1/0/1", untagged_vlan=50),
                 _port("sw-b1", "gi1/0/10", untagged_vlan=10)))  # fmt: skip
    # drittes System am selben Standort A, ohne Kabel zu sw-a1/sw-a2: eigene L2-Domaene
    c = _system(client, tid, name="Switches C", platform="cisco_switch", read_scope="10.0.2.11",
                username="ro", password="x", site_id=sites["site-a"])  # fmt: skip
    _deliver(
        client, tid, c, "cisco_switch", _sw("sw-c1", _port("sw-c1", "gi1/0/10", untagged_vlan=10))
    )
    with session_factory() as s:
        topo = l2_topo.L2(s, tid)
        doms = topo.domains()
        assert doms["sw-a1"] == doms["sw-a2"] and doms["sw-a1"] != doms["sw-b1"]
        assert doms["sw-c1"] != doms["sw-a1"]
        assert len(topo.vlan_islands(10)) == 3 and topo.vlan_split(10) == []  # normal
        assert topo.vlan_split(50) == [[{"sw-a1"}, {"sw-a2"}]]
        checks = [c for c in topo.checks() if c["type"] == "vlan_split"]
    assert [(c["subject"], c["text"]) for c in checks] == [
        ("vlan|50", "VLAN 50 ist getrennt: sw-a1 | sw-a2")
    ]
    found = client.get("/findings", params={"tenant_id": tid, "type": "vlan_split"}).json()
    items = found["items"] if isinstance(found, dict) else found
    assert [f["message"] for f in items if f["type"] == "vlan_split"] == [
        "VLAN 50 ist getrennt: sw-a1 | sw-a2."
    ]
    plan = client.get("/cabling/plan", params={"tenant_id": tid, "vlan": 10}).json()["vlan"]
    assert len(plan["islands"]) == 3 and plan["split"] == []
    plan = client.get("/cabling/plan", params={"tenant_id": tid, "vlan": 50}).json()["vlan"]
    assert plan["split"] == [[["sw-a1"], ["sw-a2"]]]


def test_lldp_ohne_leserecht_ist_ein_hinweis():
    """Fehlt der FortiGate das Leserecht auf LLDP, steht ein Hinweis im Lauf (Information), kein
    Teilfehler - Regeln, Routing und Inventar sind vollstaendig."""
    from app.store import data_quality

    msg = ("VDOMs: root, kunde-a · Hinweise: LLDP der FortiGate nicht abgefragt (LLDP-Nachbarn "
           "(HTTP 403)) - Leserecht Netzwerk (netgrp) im Profil ergaenzen")  # fmt: skip
    assert [p["kind"] for p in data_quality.partial_parts(msg)] == ["notes"]
