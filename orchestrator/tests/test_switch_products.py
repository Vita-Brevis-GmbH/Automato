"""Weitere Switch- und Router-Produkte in der Verkabelung (0.68.0, ADR 0025 Nr. 11): ein
Quellsystem mit Nexus 9000 (NX-OS), Catalyst 1300 (Business-CLI) und Juniper MX (Junos). Der MX
ist Router (leitet in der L2-Sicht nicht weiter), seine Interface-Netze kommen ins Inventar; Speed
und Duplex in der Schreibweise des Herstellers werden gleich verglichen."""

from app.models import InvSubnet
from app.store import cabling

ADMIN = {"X-Dev-Roles": "admin"}
TOKEN = {"X-Internal-Token": "dev-only-token"}


def _port(device, name, **kw):
    return {"device": device, "name": name, "kind": "physical", "enabled": True, **kw}


RESULT = {
    "status": "completed",
    "message": "Switches: 3 gelesen (Catalyst 1200/1300 (Business-CLI), Junos, NX-OS)",
    "inventory": {
        "devices": [
            {"name": "nx-core-01", "role": "switch", "model": "N9K-C93180YC-FX",
             "version": "10.3(4a)", "mgmt_ip": "10.0.0.2"},
            {"name": "acc-1300-01", "role": "switch", "model": "C1300-24T-4X",
             "version": "4.1.3.36", "mgmt_ip": "10.0.1.30"},
            {"name": "mx-edge-01", "role": "router", "model": "mx240", "version": "21.4R3-S5.4",
             "mgmt_ip": "10.0.0.40"},
        ],
        "interfaces": [
            _port("nx-core-01", "Eth1/1", status="connected", speed="10G", duplex="full",
                  lag="Po1"),
            _port("nx-core-01", "Po1", kind="lag", status="connected"),
            _port("nx-core-01", "Eth1/49", status="connected", speed="10G", duplex="full",
                  mode="tagged", native_vlan=1, trunk_vlans="10,20", tagged_vlans=[10, 20]),
            _port("acc-1300-01", "Te1/0/1", status="up", speed="10000", duplex="Full",
                  mode="tagged", native_vlan=1, trunk_vlans="10,20", tagged_vlans=[10, 20]),
            _port("acc-1300-01", "Gi1/0/1", status="up", speed="1000", duplex="Full",
                  mode="access", untagged_vlan=10),
            _port("mx-edge-01", "xe-0/0/0", status="up", speed="10gbps", duplex="full",
                  lag="ae0"),
            _port("mx-edge-01", "ae0", kind="lag", status="up", mode="tagged",
                  tagged_vlans=[100], trunk_vlans="100"),
            _port("mx-edge-01", "ae0.100", kind="vlan", parent="ae0", mode="tagged",
                  tagged_vlans=[100], ips=["10.100.0.1/24"]),
        ],
        "subnets": [{"prefix": "10.100.0.0/24", "gateway": "10.100.0.1/24", "vlan": 100,
                     "context": "interface", "context_name": "ae0.100"}],
        "neighbors": [
            {"device": "nx-core-01", "port": "Eth1/1", "protocol": "lldp",
             "remote_name": "mx-edge-01", "remote_port": "xe-0/0/0"},
            {"device": "mx-edge-01", "port": "xe-0/0/0", "protocol": "lldp",
             "remote_name": "nx-core-01", "remote_port": "Ethernet1/1"},
            {"device": "nx-core-01", "port": "Eth1/49", "protocol": "lldp",
             "remote_name": "acc-1300-01", "remote_port": "te1/0/1"},
            {"device": "acc-1300-01", "port": "Te1/0/1", "protocol": "lldp",
             "remote_name": "nx-core-01", "remote_port": "Ethernet1/49",
             "remote_caps": ["B", "R"]},
        ],
        "macs": [{"device": "acc-1300-01", "mac": "00:50:56:01:00:01", "port": "Gi1/0/1",
                  "vlan": 10, "kind": "dynamic"}],
        "arps": [{"device": "mx-edge-01", "ip": "10.100.0.21", "mac": "00:50:56:01:00:01",
                  "interface": "ae0.100"}],
    },
}  # fmt: skip


def test_nexus_catalyst_1300_und_juniper_mx(client, session_factory):
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "Netz", "platform": "cisco_switch",
            "read_scope": "10.0.0.2\n10.0.1.30\n10.0.0.40", "username": "ro",
            "password": "x"}  # fmt: skip
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    req = {"platform": "cisco_switch", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=req).json()["run_id"]
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=RESULT)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text

    dev = client.get("/cabling/devices", params={"tenant_id": tid}).json()
    assert [(d["name"], d["kind"]) for d in dev["items"]] == [
        ("acc-1300-01", "switch"),
        ("nx-core-01", "switch"),
        ("mx-edge-01", "router"),
    ]
    assert (dev["summary"]["switches"], dev["summary"]["routers"]) == (2, 1)
    mx = next(d for d in dev["items"] if d["name"] == "mx-edge-01")
    assert (mx["model"], mx["version"], mx["links"]) == ("mx240", "21.4R3-S5.4", 1)

    s = session_factory()
    cab = cabling.build(s, tid)
    router = cab.devices[cabling.device_key("mx-edge-01")]
    assert router.kind == "router" and not router.switching  # Router leitet L2 nicht weiter
    assert cab.devices[cabling.device_key("acc-1300-01")].switching
    # beide Verbindungen beidseitig; 10gbps/10G/10000 und Full/full gelten als gleich
    links = {frozenset((link.a.device.name, link.b.device.name)): link
             for link in cab.links.values()}  # fmt: skip
    core_mx = links[frozenset(("nx-core-01", "mx-edge-01"))]
    core_acc = links[frozenset(("nx-core-01", "acc-1300-01"))]
    assert core_mx.state == core_acc.state == "both"
    assert core_mx.issues == [] and core_acc.issues == []
    # Netz des MX im Inventar (Interface-Netz)
    net = s.query(InvSubnet).filter_by(source_system_id=sid).one()
    assert (net.prefix, net.context_name, net.vlan) == ("10.100.0.0/24", "ae0.100", 100)
    s.close()
    plan = client.get("/cabling/plan", params={"tenant_id": tid}).json()
    nodes = {n["name"]: n for n in plan["nodes"]}
    assert nodes["mx-edge-01"]["switching"] is False and nodes["nx-core-01"]["switching"]
