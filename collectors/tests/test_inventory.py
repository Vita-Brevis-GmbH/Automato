"""Netzinventar: FortiGate-Interfaces/VLANs/Subnetze und APIC-Fabric (ADR 0010)."""

import json
from importlib import resources

from collectors.cisco_aci.inventory import parse_inventory
from collectors.cisco_aci.tasks import fetch_fabric_sample, fetch_mo_export
from collectors.fortigate import normalize as norm


def _fgt(name: str):
    return json.loads(resources.files("collectors.fortigate").joinpath(name).read_text())


def test_fortigate_interfaces_vlans_subnetze():
    inv = norm.normalize_inventory(
        _fgt("sample_interfaces.json"), _fgt("sample_zones.json"), _fgt("sample_system.json"), "fw"
    )
    (dev,) = inv.devices
    assert (dev.name, dev.role, dev.model, dev.serial) == (
        "fw-lab-01",
        "firewall",
        "FGVM64",
        "FGVMLAB0001",
    )
    ifaces = {i.name: i for i in inv.interfaces}
    assert ifaces["port3"].mode == "tagged" and ifaces["port3"].tagged_vlans == [10, 99]
    vl = ifaces["srv-vl10"]
    # VLAN-Subinterface: getaggt ueber den Parent, nicht untagged (Dev-Befund 0.11.4)
    assert (vl.kind, vl.mode, vl.untagged_vlan, vl.tagged_vlans, vl.parent, vl.zone) == (
        "vlan",
        "tagged",
        None,
        [10],
        "port3",
        "Z_server",
    )
    assert vl.ips == ["10.10.10.1/24", "2001:db8:10::1/64"]
    assert ifaces["mgmt-vl99"].enabled is False
    assert ifaces["port4"].lag == "agg1" and ifaces["agg1"].kind == "lag"
    assert {(v.vid, v.name) for v in inv.vlans} == {(10, "Server"), (99, "Management")}
    subnets = {s.prefix: s for s in inv.subnets}
    assert (
        subnets["10.10.10.0/24"].gateway == "10.10.10.1/24" and subnets["10.10.10.0/24"].vlan == 10
    )
    assert subnets["2001:db8:10::/64"].context_name == "srv-vl10"
    assert (
        "10.10.20.0/24" in subnets
        and len([s for s in inv.subnets if s.prefix.startswith("0.")]) == 0
    )


def test_aci_inventar_aus_tenant_und_fabric():
    inv = parse_inventory(fetch_mo_export(), fetch_fabric_sample())
    assert inv.vrfs == ["TE_share:VRF_share"]
    devices = {d.name: d for d in inv.devices}
    assert devices["leaf-101"].role == "leaf" and devices["spine-201"].role == "spine"
    assert devices["leaf-101"].node_id == "101"

    ifaces = {(i.device, i.name): i for i in inv.interfaces}
    # vPC: VLAN 110 (EG_web) auf dem Bundle beider Leafs, Port eth1/10 als Mitglied
    vpc = ifaces[("leaf-101", "VPC_srv01")]
    assert (vpc.kind, vpc.mode, vpc.tagged_vlans) == ("lag", "tagged", [110])
    assert vpc.epgs == ["TE_share/AP_001/EG_web"] and ("leaf-102", "VPC_srv01") in ifaces
    member = ifaces[("leaf-101", "eth1/10")]
    assert (
        member.lag == "VPC_srv01"
        and member.neighbor == "srv-web01"
        and member.neighbor_port == "ens192"
    )
    assert member.description == "srv-web01 ens192" and member.enabled is True
    # Access-Port: VLAN 120 untagged (EG_db), Policy Group, CDP-Nachbar
    access = ifaces[("leaf-101", "eth1/20")]
    assert (access.mode, access.untagged_vlan, access.policy_group) == (
        "access",
        120,
        "APG_srv-access",
    )
    assert access.neighbor == "srv-db01"
    assert ("leaf-101", "eth1/48") not in ifaces  # Port ohne jede Information

    vlans = {v.vid: v for v in inv.vlans}
    assert vlans[110].name == "EG_web" and vlans[110].epgs == ["TE_share/AP_001/EG_web"]
    assert vlans[120].name == "EG_db"

    subnets = {(s.context, s.prefix): s for s in inv.subnets}
    bd = subnets[("bd", "10.20.0.0/24")]
    assert (bd.gateway, bd.vrf, bd.bd, bd.epg, bd.ap, bd.vlan) == (
        "10.20.0.1/24",
        "TE_share:VRF_share",
        "BD_app",
        "EG_web",
        "AP_001",
        110,
    )
    epg = subnets[("epg", "10.20.1.0/24")]
    assert epg.epg == "EG_web" and epg.aci_tenant == "TE_share"


def test_aci_ohne_fabric_daten():
    inv = parse_inventory(fetch_mo_export(), None)
    assert inv.devices == [] and {i.name for i in inv.interfaces} == {"VPC_srv01", "eth1/20"}
    assert {i.device for i in inv.interfaces} == {"node-101", "node-102"}


def test_fortigate_vdom_link_als_eigene_art():
    """0.27.0: Inter-VDOM-Links (`type: vdom-link`) behalten ihre Art - der Weg verbindet die
    Enden `<name>0`/`<name>1` in den VDOMs (ADR 0019)."""
    raw = [
        {"name": "vl_root_dmz0", "type": "vdom-link", "vdom": "root", "ip": "0.0.0.0 0.0.0.0"},
        {"name": "vl_root_dmz1", "type": "vdom-link", "vdom": "dmz", "ip": "0.0.0.0 0.0.0.0"},
    ]
    inv = norm.normalize_inventory(raw, [], {}, "fw")
    assert [(i.name, i.kind, i.vdom, i.ips) for i in inv.interfaces] == [
        ("vl_root_dmz0", "vdom-link", "root", []),
        ("vl_root_dmz1", "vdom-link", "dmz", []),
    ]


def test_fortigate_mgmt_ip_aus_interface_mit_https():
    """Dev-Befund 0.33.0: Management-Adresse = IPv4 des Interfaces mit HTTPS-Zugriff,
    Management-Interfaces zuerst; ohne HTTPS bzw. ohne Adresse keine."""
    raw = [
        {"name": "port1", "type": "physical", "ip": "10.1.1.1 255.255.255.0",
         "allowaccess": "ping https ssh"},
        {"name": "mgmt", "type": "physical", "ip": "192.0.2.10 255.255.255.0",
         "allowaccess": "https ssh", "dedicated-to": "management"},
        {"name": "port2", "type": "physical", "ip": "10.2.2.1 255.255.255.0",
         "allowaccess": "ping"},
    ]  # fmt: skip
    assert norm.normalize_inventory(raw, [], {}, "fw").devices[0].mgmt_ip == "192.0.2.10"
    assert norm.normalize_inventory(raw[:1], [], {}, "fw").devices[0].mgmt_ip == "10.1.1.1"
    off = [{**raw[0], "status": "down"}, raw[2]]
    assert norm.normalize_inventory(off, [], {}, "fw").devices[0].mgmt_ip is None
