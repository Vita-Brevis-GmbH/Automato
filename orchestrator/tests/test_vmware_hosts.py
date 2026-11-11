"""ESXi-Hosts aus dem vCenter im Netzinventar und in der Verkabelung (1.8.0).

Anwender: „Wird in VMware die Ports und Nachbarschaft nicht ausgelesen von den Hosts?“ - „Nur
wegen den Verbindungen“. Der vCenter-Lauf bringt je Host Hardware, vmnics und den Nachbarn, den
der Host per CDP/LLDP hoert; sie landen als Geraet (Rolle `esxi`), Ports und Nachbarn im Inventar
des vCenters.

sw-acc-01 (gelesen) und die Hosts:
- esx01 vmnic0 hoert sw-acc-01 Gi1/0/10, der Switch sieht den Host nicht (ESXi „Zuhören“),
- esx01 vmnic1 an Gi1/0/11: nur der Switch hoert den Host (CDP vom Host),
- esx01 vmnic2 (10G) hoert sw-acc-01 Gi1/0/13 (1000) - Speed weicht ab,
- esx02 vmnic0 an Gi1/0/12: beide melden sich,
- esx09 kennt Automato nur als Host einer VM (nicht gelesen) - wie bisher ohne CDP/LLDP.
"""

import copy

from app.models import DataChange, InvDevice, InvInterface, InvNeighbor, Vm
from app.store import cabling, data_state
from sqlalchemy import select

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _port(name, **kw):
    base = {"device": "sw-acc-01", "name": name, "kind": "physical", "status": "connected",
            "enabled": True, "speed": "a-1000", "duplex": "a-full", "mode": "tagged",
            "native_vlan": 1, "trunk_vlans": "10,20", "tagged_vlans": [10, 20]}  # fmt: skip
    return {**base, **kw}


def _cdp(port, remote, remote_port, ip=None):
    return {
        "device": "sw-acc-01",
        "port": port,
        "protocol": "cdp",
        "remote_name": remote,
        "remote_port": remote_port,
        "remote_ip": ip,
        "remote_platform": "VMware ESX",
    }


SWITCH = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "sw-acc-01", "role": "switch", "model": "C9300-48P",
                     "mgmt_ip": "10.0.0.11", "version": "17.9.4a"}],
        "interfaces": [_port("Gi1/0/10"), _port("Gi1/0/11"), _port("Gi1/0/12"),
                       _port("Gi1/0/13"), _port("Gi1/0/20")],
        "neighbors": [
            # der Switch sieht esx01 an vmnic1 (Host sendet hier CDP) - Name aus CDP ohne Domaene
            _cdp("Gi1/0/11", "esx01", "vmnic1"),
            _cdp("Gi1/0/12", "esx02.example.ch", "vmnic0", "10.0.10.12"),
            _cdp("Gi1/0/20", "esx09.example.ch", "vmnic0"),
        ],
    },
}  # fmt: skip


def _nic(name, speed="1000", neighbor=None, mac=None):
    return {"name": name, "mac": mac, "speed": speed, "duplex": "full" if speed else None,
            "status": "up" if speed else "down", "neighbor": neighbor}  # fmt: skip


def _heard(port, protocol="cdp"):
    return {"protocol": protocol, "remote_name": "sw-acc-01.example.ch", "remote_port": port,
            "remote_ip": "10.0.0.11", "remote_platform": "cisco C9300-48P",
            "remote_caps": ["switch"]}  # fmt: skip


HOSTS = [
    {"name": "esx01.example.ch", "vendor": "Dell Inc.", "model": "PowerEdge R650",
     "serial": "SN-ESX01", "version": "8.0.2", "mgmt_ip": "10.0.10.11",
     "pnics": [_nic("vmnic0", neighbor=_heard("GigabitEthernet1/0/10"),
                    mac="b4:96:91:00:11:00"),
               _nic("vmnic1"),
               _nic("vmnic2", "10G", _heard("GigabitEthernet1/0/13", "lldp")),
               _nic("vmnic3", None)]},
    {"name": "esx02.example.ch", "vendor": "HPE", "model": "ProLiant DL380 Gen10",
     "version": "7.0.3", "mgmt_ip": "10.0.10.12",
     "pnics": [_nic("vmnic0", neighbor=_heard("GigabitEthernet1/0/12"))]},
]  # fmt: skip
VCENTER = {
    "status": "completed",
    "virtual": {
        "vms": [{"name": "web01", "uuid": "u-1", "host": "esx01.example.ch", "nics": []},
                {"name": "old01", "uuid": "u-9", "host": "esx09.example.ch", "nics": []}],
        "portgroups": [],
        "hosts": HOSTS,
    },
}  # fmt: skip


def _deliver(client, tid, sid, platform, result) -> None:
    body = {"platform": platform, "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=body).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=result)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text


def _system(client, **body) -> int:
    r = client.post("/systems", headers=ADMIN, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _setup(client) -> tuple[int, int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    sw = _system(client, tenant_id=tid, name="Switches A", platform="cisco_switch",
                 read_scope="10.0.0.11", username="ro", password="x")  # fmt: skip
    vc = _system(client, tenant_id=tid, name="vc-01", platform="vmware",
                 base_url="https://vc.example.ch", username="ro", password="x")  # fmt: skip
    _deliver(client, tid, sw, "cisco_switch", SWITCH)
    _deliver(client, tid, vc, "vmware", VCENTER)
    return tid, sw, vc


def test_hosts_landen_im_netzinventar_des_vcenters(client, session_factory):
    tid, _sw, vc = _setup(client)
    s = session_factory()
    try:
        devs = {d.name: d for d in s.scalars(select(InvDevice).where(
            InvDevice.source_system_id == vc))}  # fmt: skip
        assert set(devs) == {"esx01.example.ch", "esx02.example.ch"}
        esx01 = devs["esx01.example.ch"]
        assert (esx01.role, esx01.vendor, esx01.model, esx01.serial, esx01.version,
                esx01.mgmt_ip) == ("esxi", "Dell Inc.", "PowerEdge R650", "SN-ESX01", "8.0.2",
                                   "10.0.10.11")  # fmt: skip
        ports = {(i.device, i.name): i for i in s.scalars(select(InvInterface).where(
            InvInterface.source_system_id == vc))}  # fmt: skip
        assert len(ports) == 5
        vmnic0 = ports[("esx01.example.ch", "vmnic0")]
        assert (vmnic0.kind, vmnic0.speed, vmnic0.duplex, vmnic0.status, vmnic0.attrs) == (
            "physical", "1000", "full", "up", {"mac": "b4:96:91:00:11:00"})  # fmt: skip
        assert ports[("esx01.example.ch", "vmnic3")].status == "down"
        nbs = {(n.device, n.port): n for n in s.scalars(select(InvNeighbor).where(
            InvNeighbor.source_system_id == vc))}  # fmt: skip
        assert set(nbs) == {("esx01.example.ch", "vmnic0"), ("esx01.example.ch", "vmnic2"),
                            ("esx02.example.ch", "vmnic0")}  # fmt: skip
        nb = nbs[("esx01.example.ch", "vmnic0")]
        assert (nb.protocol, nb.remote_name, nb.remote_port) == (
            "cdp", "sw-acc-01.example.ch", "GigabitEthernet1/0/10")  # fmt: skip
        assert (nb.remote_ip, nb.remote_caps) == ("10.0.0.11", ["switch"])
        assert nbs[("esx01.example.ch", "vmnic2")].protocol == "lldp"
    finally:
        s.close()
    # Netzinventar-Ansicht: der Hersteller steht beim Geraet
    rows = client.get("/inventory/devices", params={"tenant_id": tid,
                                                    "source_system_id": vc}).json()  # fmt: skip
    assert {r["name"]: r["vendor"] for r in rows} == {"esx01.example.ch": "Dell Inc.",
                                                      "esx02.example.ch": "HPE"}  # fmt: skip


def test_verkabelung_switch_port_und_vmnic_bestaetigt(client, session_factory):
    tid, _sw, _vc = _setup(client)
    s = session_factory()
    try:
        cab = cabling.build(s, tid)
    finally:
        s.close()
    by_port = {}
    for link in cab.links.values():
        for end, other in ((link.a, link.b), (link.b, link.a)):
            if end.device.name == "sw-acc-01":
                by_port[end.port] = (link, other)
    # Host hoert den Switch, der Switch sieht den Host nicht (ESXi „Zuhören“): bestaetigt
    heard, host = by_port["Gi1/0/10"]
    assert (host.device.name, host.port, host.device.kind, host.device.read) == (
        "esx01.example.ch", "vmnic0", "esxi", True)  # fmt: skip
    assert (heard.state, heard.confirmed, heard.issues) == ("both", "esxi", [])
    assert heard.reported_by == {"esx01"}  # nur der Host meldet sie
    # nur der Switch hoert den Host - an einer vmnic, die der gelesene Host hat: bestaetigt
    sent, host = by_port["Gi1/0/11"]
    assert (host.port, sent.state, sent.confirmed) == ("vmnic1", "both", "esxi")
    # beide melden sich (gleiche Verbindung, Name per Domaene bzw. Management-IP)
    both, host = by_port["Gi1/0/12"]
    assert (host.device.name, both.state, both.confirmed) == ("esx02.example.ch", "both", "both")
    # vmnic 10G an einem 1G-Port: Speed weicht ab (nur Speed - keine VLAN-/Duplex-Pruefung)
    fast, _host = by_port["Gi1/0/13"]
    assert [i["type"] for i in fast.issues] == ["link_speed_mismatch"]
    assert fast.issues[0]["text"] == "Speed 10G ↔ a-1000"  # Ende a = esx01 (Schluessel)
    # Host nur aus den VMs (nicht gelesen): wie bisher Gegenstelle ohne CDP/LLDP
    old, host = by_port["Gi1/0/20"]
    assert (host.device.read, old.state, old.confirmed) == (False, "unread", None)
    assert cab.devices["esx01"].reports and not cab.devices["esx09"].reports

    # Befunde: keine einseitige Verbindung zu einem gelesenen Host, Speed als Warnung
    rows = client.get("/findings", params={"tenant_id": tid, "limit": 200}).json()
    items = rows["items"] if isinstance(rows, dict) else rows
    links = [f for f in items if f["type"].startswith("link_")]
    assert [f["type"] for f in links] == ["link_speed_mismatch"]

    # API der Ansicht
    dev = client.get("/cabling/devices", params={"tenant_id": tid}).json()
    assert dev["summary"]["states"] == {"both": 4, "one_sided": 0, "unread": 1}
    esx = next(d for d in dev["items"] if d["name"] == "esx01.example.ch")
    assert (esx["kind"], esx["read"], esx["source"], esx["model"], esx["version"]) == (
        "esxi", True, "vc-01", "PowerEdge R650", "8.0.2")  # fmt: skip
    assert (esx["ports"], esx["connected"], esx["links"], esx["one_sided"]) == (4, 3, 3, 0)
    out = client.get("/cabling/links", params={"tenant_id": tid, "device": "esx01"}).json()
    vmnic0 = next(i for i in out["items"] if i["a"]["port"] == "vmnic0")
    assert (vmnic0["state"], vmnic0["confirmed"], vmnic0["b"]["port"]) == (
        "both", "esxi", "Gi1/0/10")  # fmt: skip
    assert vmnic0["a"]["speed"] == "1000" and vmnic0["b"]["vlan"] is None
    patches = client.get("/cabling/patches", params={"tenant_id": tid,
                                                     "device": "sw-acc-01"}).json()  # fmt: skip
    assert {r["a"]["port"]: r["confirmed"] for r in patches["items"]} == {
        "Gi1/0/10": "esxi", "Gi1/0/11": "esxi", "Gi1/0/12": "both", "Gi1/0/13": "esxi",
        "Gi1/0/20": None}  # fmt: skip
    # Reiter Ports: nur die Ports der Switches (keine vmnics), Zustand wie in den Verbindungen
    ports = client.get("/cabling/ports", params={"tenant_id": tid}).json()["items"]
    seen = {p["name"]: (p["link_state"], p["link_confirmed"], p["peer"]["device"]) for p in ports}
    assert seen == {
        "Gi1/0/10": ("both", "esxi", "esx01.example.ch"),
        "Gi1/0/11": ("both", "esxi", "esx01.example.ch"),
        "Gi1/0/12": ("both", "both", "esx02.example.ch"),
        "Gi1/0/13": ("both", "esxi", "esx01.example.ch"),
        "Gi1/0/20": ("unread", None, "esx09.example.ch"),
    }


def test_ohne_hosts_bleibt_das_inventar_stehen(client, session_factory):
    """Aelterer Agent (ohne `hosts`): Hosts im Inventar bleiben; `hosts: []` = keine Hosts mehr
    - die VMs und Portgruppen des vCenters bleiben dabei unangetastet."""
    tid, sw, vc = _setup(client)

    def counts():
        s = session_factory()
        try:
            return tuple(len(list(s.scalars(select(m).where(m.source_system_id == vc))))
                         for m in (InvDevice, InvInterface, InvNeighbor, Vm))  # fmt: skip
        finally:
            s.close()

    assert counts() == (2, 5, 3, 2)
    older = copy.deepcopy(VCENTER)
    del older["virtual"]["hosts"]
    _deliver(client, tid, vc, "vmware", older)
    assert counts() == (2, 5, 3, 2)
    s = session_factory()
    try:
        last = s.scalars(select(DataChange).order_by(DataChange.seq.desc())).first()
        before = last.seq if last else 0
    finally:
        s.close()
    # nur die Hosts geaendert: neuer Datenstand ohne Wirkung auf Doku und Wege
    fewer = copy.deepcopy(VCENTER)
    fewer["virtual"]["hosts"] = fewer["virtual"]["hosts"][:1]
    _deliver(client, tid, vc, "vmware", fewer)
    assert counts() == (1, 4, 2, 2)
    s = session_factory()
    try:
        change = s.scalars(select(DataChange).order_by(DataChange.seq.desc())).first()
        assert change.seq == before + 1 and change.parts == ["virtual.hosts"]
        assert not change.all and not change.ips and not change.devices
        assert data_state.scope("virtual.hosts") == data_state.NONE
    finally:
        s.close()
    gone = copy.deepcopy(VCENTER)
    gone["virtual"]["hosts"] = []
    _deliver(client, tid, vc, "vmware", gone)
    assert counts() == (0, 0, 0, 2)
    # die Switches behalten ihr Inventar (Ersetzen nur je Quellsystem)
    s = session_factory()
    try:
        assert s.scalars(select(InvDevice.name).where(InvDevice.source_system_id == sw)).all() \
            == ["sw-acc-01"]  # fmt: skip
    finally:
        s.close()
