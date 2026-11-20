"""Spanning Tree der Switches (1.19.0, Anwender 2026-11-17 Prioritaet 5): Auswertung je Plattform
(IOS/IOS-XE PVST+ und MST, NX-OS Rapid-PVST und MST) mit Textbeispielen, Topologieaenderungen,
err-disabled durch BPDU-Guard, nicht unterstuetzte Plattformen und Befehle nur lesend."""

import pytest
import switch_products as sp
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_switch import client as sc
from collectors.cisco_switch import stp, tasks
from collectors.cisco_switch.normalize import normalize

IOS_PVST = """
VLAN0001
  Spanning tree enabled protocol ieee
  Root ID    Priority    32769
             Address     0019.e86a.6f80
             Cost        19
             Port        1 (FastEthernet0/1)
             Hello Time   2 sec  Max Age 20 sec  Forward Delay 15 sec

  Bridge ID  Priority    32769  (priority 32768 sys-id-ext 1)
             Address     0019.e86a.7100
             Hello Time   2 sec  Max Age 20 sec  Forward Delay 15 sec
             Aging Time  300 sec

Interface           Role Sts Cost      Prio.Nbr Type
------------------- ---- --- --------- -------- --------------------------------
Fa0/1               Root FWD 19        128.1    P2p
Fa0/2               Altn BLK 19        128.2    P2p
Fa0/3               Desg LIS 19        128.3    Shr
Gi0/1               Desg BKN*4         128.25   P2p *ROOT_Inc
Gi0/2               Back BLK 4         128.26   Shr


VLAN0020
  Spanning tree enabled protocol ieee
  Root ID    Priority    24596
             Address     0019.e86a.7100
             This bridge is the root
             Hello Time   2 sec  Max Age 20 sec  Forward Delay 15 sec

  Bridge ID  Priority    24596  (priority 24576 sys-id-ext 20)
             Address     0019.e86a.7100
             Hello Time   2 sec  Max Age 20 sec  Forward Delay 15 sec
             Aging Time  300 sec

Interface           Role Sts Cost      Prio.Nbr Type
------------------- ---- --- --------- -------- --------------------------------
Fa0/1               Desg FWD 19        128.1    P2p Peer(STP)
"""

IOS_DETAIL = """
 VLAN0001 is executing the ieee compatible Spanning Tree protocol
  Bridge Identifier has priority 32768, sysid 1, address 0019.e86a.7100
  Configured hello time 2, max age 20, forward delay 15
  Current root has priority 32769, address 0019.e86a.6f80
  Root port is 1 (FastEthernet0/1), cost of root path is 19
  Topology change flag not set, detected flag not set
  Number of topology changes 1234 last change occurred 00:01:52 ago
          from FastEthernet0/3
  Times:  hold 1, topology change 35, notification 2

 Port 1 (FastEthernet0/1) of VLAN0001 is root forwarding
   Port path cost 19, Port priority 128, Port Identifier 128.1.
   Designated root has priority 32769, address 0019.e86a.6f80
   BPDU: sent 1, received 4521

 VLAN0020 is executing the ieee compatible Spanning Tree protocol
  Bridge Identifier has priority 24576, sysid 20, address 0019.e86a.7100
  We are the root of the spanning tree
  Topology change flag not set, detected flag not set
  Number of topology changes 2 last change occurred 1w2d ago
          from FastEthernet0/1
"""

IOS_MST = """
MST0
  Spanning tree enabled protocol mstp
  Root ID    Priority    4096
             Address     00aa.0000.0001
             Cost        0
             Port        49 (GigabitEthernet1/1/1)
             Hello Time   2 sec  Max Age 20 sec  Forward Delay 15 sec

  Bridge ID  Priority    32768  (priority 32768 sys-id-ext 0)
             Address     00aa.0000.0003
             Hello Time   2 sec  Max Age 20 sec  Forward Delay 15 sec

Interface           Role Sts Cost      Prio.Nbr Type
------------------- ---- --- --------- -------- --------------------------------
Gi1/1/1             Root FWD 20000     128.49   P2p
Gi1/1/2             Altn BLK 20000     128.50   P2p Bound(PVST)

MST1
  Spanning tree enabled protocol mstp
  Root ID    Priority    8193
             Address     00aa.0000.0002
             Cost        20000
             Port        50 (GigabitEthernet1/1/2)
             Hello Time   2 sec  Max Age 20 sec  Forward Delay 15 sec

  Bridge ID  Priority    32769  (priority 32768 sys-id-ext 1)
             Address     00aa.0000.0003

Interface           Role Sts Cost      Prio.Nbr Type
------------------- ---- --- --------- -------- --------------------------------
Gi1/1/1             Altn BLK 20000     128.49   P2p
Gi1/1/2             Root FWD 20000     128.50   P2p
"""

MST_CONFIG = """Name      [region-a]
Revision  3     Instances configured 2

Instance  Vlans mapped
--------  ---------------------------------------------------------------------
0         1-9,21-99,101-199,201-299,301-399,401-499,501-599,601-699,701-799,801-899
          ,901-4094
1         10-20,100,200,300,400,500,600,700,800,900
-------------------------------------------------------------------------------
"""

NXOS_TREE = """
MST0000
  Spanning tree enabled protocol mstp
  Root ID    Priority    4096
             Address     00aa.0000.0001
             This bridge is the root
             Hello Time  2  sec  Max Age 20 sec  Forward Delay 15 sec

  Bridge ID  Priority    4096   (priority 4096 sys-id-ext 0)
             Address     00aa.0000.0001
             Hello Time  2  sec  Max Age 20 sec  Forward Delay 15 sec

Interface        Role Sts Cost      Prio.Nbr Type
---------------- ---- --- --------- -------- --------------------------------
Po1              Desg FWD 1000      128.4096 (vPC peer-link) Network P2p
Po10             Desg FWD 1000      128.4105 (vPC) P2p
Eth1/5           Desg BKN*2000      128.133  Edge P2p *BA_Inc
"""

NXOS_DETAIL = """
 MST0000 is executing the mstp compatible Spanning Tree protocol
  Bridge Identifier has priority 4096, sysid 0, address 00aa.0000.0001
  We are the root of the spanning tree
  Topology change flag not set, detected flag not set
  Number of topology changes 17 last change occurred 142:31:17 ago
          from port-channel10
"""

NXOS_ERR = """
--------------------------------------------------------------------------------
Port          Name               Status   Reason
--------------------------------------------------------------------------------
Eth1/7        Drucker Empfang    down     BPDUGuard errDisable
Eth1/8        --                 down     Link Flapping errDisable
"""

IOS_ERR = """
Port      Name               Status       Reason               Err-disabled Vlans
Gi1/0/5   Besprechung 2.OG   err-disabled bpduguard
Gi1/0/9                      err-disabled psecure-violation    20
"""


def test_ios_pvst_root_bridge_und_ports():
    trees = {t["number"]: t for t in stp.parse_tree(IOS_PVST)}
    one, twenty = trees[1], trees[20]
    assert (one["kind"], one["protocol"], one["is_root"]) == ("vlan", "ieee", False)
    assert (one["root_priority"], one["root_mac"], one["root_cost"], one["root_port"]) == (
        32769, "00:19:e8:6a:6f:80", 19, "Fa0/1")  # fmt: skip
    assert (one["bridge_priority"], one["bridge_mac"]) == (32769, "00:19:e8:6a:71:00")
    ports = {p.port: p for p in one["ports"]}
    assert (ports["Fa0/2"].role, ports["Fa0/2"].state, ports["Fa0/2"].cost) == (
        "alternate", "blocking", 19)  # fmt: skip
    assert (ports["Fa0/3"].state, ports["Fa0/3"].type) == ("listening", "Shr")
    # gebrochen durch Root Guard: Kosten direkt hinter „BKN*“, Inkonsistenz getrennt
    assert (ports["Gi0/1"].state, ports["Gi0/1"].cost, ports["Gi0/1"].inconsistent) == (
        "broken", 4, "ROOT_Inc")  # fmt: skip
    assert ports["Gi0/1"].type == "P2p" and ports["Gi0/2"].role == "backup"
    # Root dieser Bridge: Kosten 0, kein Root-Port
    assert twenty["is_root"] and (twenty["root_cost"], twenty["root_port"]) == (0, None)
    assert twenty["ports"][0].type == "P2p Peer(STP)"


def test_detail_topologieaenderungen_und_zeiten():
    d = stp.parse_detail(IOS_DETAIL)
    assert d[("vlan", 1)]["tc_count"] == 1234 and d[("vlan", 1)]["tc_ago"] == 112
    assert d[("vlan", 1)]["tc_from"] == "Fa0/3"  # nicht der Port aus den Port-Bloecken
    assert (d[("vlan", 1)]["root_port"], d[("vlan", 1)]["root_cost"]) == ("Fa0/1", 19)
    assert d[("vlan", 20)]["is_root"] and d[("vlan", 20)]["tc_ago"] == 9 * 86400
    assert d[("vlan", 20)]["bridge_priority"] == 24596
    assert stp.seconds("2d03h") == 2 * 86400 + 3 * 3600
    assert stp.seconds("142:31:17") == 142 * 3600 + 31 * 60 + 17
    assert stp.seconds("1y2w") == (365 + 14) * 86400
    assert stp.seconds("never") is None and stp.seconds("") is None


def test_ios_mst_mit_region_und_grenze():
    cfg = stp.parse_mst(MST_CONFIG)
    assert (cfg["region"], cfg["revision"]) == ("region-a", 3)
    assert cfg["instances"][0].endswith("801-899,901-4094")  # Folgezeile angehaengt
    assert cfg["instances"][1] == "10-20,100,200,300,400,500,600,700,800,900"
    outputs = {stp.TREE: IOS_MST, stp.MST_CONFIG: MST_CONFIG, stp.DETAIL: "",
               stp.ERRDISABLED["ios"]: IOS_ERR}  # fmt: skip
    dev, inst = stp.inventory("sw-dist-01", "ios", outputs)
    assert (dev.status, dev.mode, dev.region, dev.revision) == ("read", "mst", "region-a", 3)
    by = {i.instance: i for i in inst}
    assert set(by) == {"MST0", "MST1"}
    mst1 = by["MST1"]
    assert (mst1.mst, mst1.vlan, mst1.vlans) == (1, None, cfg["instances"][1])
    assert by["MST0"].ports[1].type == "P2p Bound(PVST)"
    errs = {e["port"]: e for e in dev.errdisabled}
    assert errs["Gi1/0/5"]["reason"] == "bpduguard" and stp.bpdu_guard(errs["Gi1/0/5"]["reason"])
    assert errs["Gi1/0/9"] == {"port": "Gi1/0/9", "reason": "psecure-violation", "vlans": "20"}
    assert not stp.bpdu_guard(errs["Gi1/0/9"]["reason"])


def test_nxos_mst_root_vpc_und_bridge_assurance():
    outputs = {stp.TREE: NXOS_TREE, stp.DETAIL: NXOS_DETAIL, stp.MST_CONFIG: MST_CONFIG,
               stp.ERRDISABLED["nxos"]: NXOS_ERR}  # fmt: skip
    dev, inst = stp.inventory("sw-core-01", "nxos", outputs)
    assert dev.mode == "mst" and len(inst) == 1
    i = inst[0]
    assert (i.instance, i.mst, i.is_root, i.root_cost, i.root_port) == ("MST0", 0, True, 0, None)
    assert (i.bridge_mac, i.bridge_priority) == ("00:aa:00:00:00:01", 4096)
    assert (i.tc_count, i.tc_ago, i.tc_from) == (17, 513077, "Po10")
    ports = {p.port: p for p in i.ports}
    assert ports["Po1"].type == "(vPC peer-link) Network P2p"
    assert (ports["Eth1/5"].state, ports["Eth1/5"].inconsistent, ports["Eth1/5"].type) == (
        "broken", "BA_Inc", "Edge P2p")  # fmt: skip
    assert [(e["port"], stp.bpdu_guard(e["reason"])) for e in dev.errdisabled] == [
        ("Eth1/7", True), ("Eth1/8", False)]  # fmt: skip


def test_ohne_err_disabled_befehl_status_der_ports():
    outputs = {stp.TREE: IOS_PVST, stp.ERRDISABLED["ios"]: "% Invalid input detected at '^'"}
    dev, _ = stp.inventory("sw-acc-02", "ios", outputs, {"Gi0/5": "err-disabled",
                                                         "Gi0/6": "connected"})  # fmt: skip
    assert dev.errdisabled == [{"port": "Gi0/5", "reason": None, "vlans": None}]
    assert dev.mode == "pvst"


def test_aus_nicht_lesbar_und_nicht_unterstuetzt():
    off, inst = stp.inventory("sw-a", "ios", {stp.TREE: "No spanning tree instance exists.\n"})
    assert (off.status, inst) == ("off", [])
    bad, _ = stp.inventory("sw-b", "nxos", {stp.TREE: "% Invalid command at '^' marker.\n"})
    assert bad.status == "unreadable" and "Invalid command" in bad.note
    missing, _ = stp.inventory("sw-c", "ios", {})
    assert missing.status == "unreadable"
    for platform in ("junos", "eos", "aoscx", "snmp"):  # seit 1.23.0 ohne Catalyst 1200/1300
        dev, inst = stp.inventory("sw-x", platform, {stp.TREE: IOS_PVST})
        assert (dev.status, inst) == ("unsupported", [])


def test_beispieldaten_und_normalisierung():
    inv = normalize(tasks._sample())
    devs = {d.device: d for d in inv.stp_devices}
    assert {d: v.status for d, v in devs.items()} == {"sw-acc-01": "read", "sw-core-01": "read"}
    assert devs["sw-acc-01"].errdisabled == [{"port": "Gi1/0/12", "reason": "bpduguard",
                                              "vlans": None}]  # fmt: skip
    by = {(i.device, i.instance): i for i in inv.stp}
    dmz = by[("sw-acc-01", "VLAN0030")]
    assert (dmz.tc_count, dmz.tc_ago, dmz.tc_from, dmz.bridge_priority) == (57, 252, "Te1/1/1",
                                                                            32798)  # fmt: skip
    assert [(p.port, p.role, p.state) for p in dmz.ports if p.state == "blocking"] == [
        ("Te1/1/1", "alternate", "blocking")]  # fmt: skip
    core = by[("sw-core-01", "VLAN0010")]
    assert core.is_root and core.root_mac == by[("sw-acc-01", "VLAN0010")].root_mac
    # die alten Felder am Port bleiben (0.65.0)
    ports = {(i.device, i.name): i for i in inv.interfaces}
    assert ports[("sw-acc-01", "Te1/1/1")].attrs["stp_blocked"] == "30"
    # Business-CLI ohne Spanning Tree: gezaehlt, nicht still weggelassen
    raw = {"switches": [{"host": "10.0.0.50", "platform": "snmp", "outputs": {}, "notes": [],
                         "name": "sw-snmp"}]}  # fmt: skip
    from collectors.cisco_switch import normalize as sn

    def fake(host, platform, outputs, notes=None):
        from collectors.common.dto import NetworkInventory, NormalizedDevice

        return NetworkInventory(devices=[NormalizedDevice(name="sw-snmp", role="switch")])

    original = sn.switch_inventory
    sn.switch_inventory = fake
    try:
        inv = sn.normalize(raw)
    finally:
        sn.switch_inventory = original
    assert [(d.device, d.status) for d in inv.stp_devices] == [("sw-snmp", "unsupported")]


def test_befehle_nur_lesend():
    for platform, cmds in stp.COMMANDS.items():
        assert set(cmds) <= set(sc.COMMANDS[platform]), platform
        for cmd in cmds:
            assert cmd.startswith("show ") and cmd in sc.ALLOWED
    with pytest.raises(ReadOnlyViolation):
        sc.SwitchClient("10.0.0.1", "ro", "pw")._run("spanning-tree mode mst", 1.0)


# -- Catalyst 1200/1300 (1.23.0, Anwender 2026-11-20: „Rapid per vlan spanningtree“) ---------------

CBS_RSTP = """
Spanning tree enabled mode: RSTP
Default port cost method:   long
Loopback guard:             Disabled

Root ID        Priority:   32768
               Address:    02:00:00:00:02:51
               This switch is the root
               Hello Time: 2 sec Max Age: 20 sec Forward Delay: 15 sec

Name      State     Prio.Nbr  Cost      Sts    Role      PortFast Type
--------- --------- --------- --------- ------ --------- -------- --------------
 gi1/0/1   enabled    128.1     20000    Frw     Desg      No     P2P (RSTP)
 gi1/0/2   enabled    128.2     20000    Dsbl    Dsbl      No          -
"""


def test_catalyst_1300_rapid_pvst():
    dev, inst = stp.inventory("acc-1300-01", "cbs", sp.C1300, {"Gi1/0/1": "up"})
    assert (dev.status, dev.mode, dev.note) == ("read", "rapid-pvst", None)
    by = {i.instance: i for i in inst}
    v1, v10 = by["VLAN0001"], by["VLAN0010"]
    assert (v1.vlan, v1.protocol, v1.is_root, v1.root_mac, v1.root_cost, v1.root_port) == (
        1, "rstp", False, "00:3a:9c:00:00:01", 2000, "Te1/0/1")  # fmt: skip
    assert (v1.bridge_priority, v1.bridge_mac, v1.tc_count, v1.tc_ago) == (
        32768, "e0:5f:b9:00:00:01", 7, (2 * 24 + 18) * 3600)  # fmt: skip
    ports = {p.port: p for p in v1.ports}
    assert set(ports) == {"Gi1/0/1", "Te1/0/1", "Te1/0/2", "Po1"}  # Port-Channel als Port
    assert (ports["Te1/0/2"].role, ports["Te1/0/2"].state) == ("alternate", "blocking")
    assert (ports["Te1/0/1"].role, ports["Gi1/0/1"].type) == ("root", "P2P Edge (rpvst)")
    assert ports["Po1"].priority == "128.1000"
    assert (v10.is_root, v10.root_cost, v10.root_port, v10.tc_count, v10.tc_ago) == (
        True, 0, None, 55, None)  # fmt: skip
    assert {p.port: p.inconsistent for p in v10.ports}["Te1/0/2"] == "TYPE/PVID_Inc"
    # blockierte VLANs bzw. Root-Port je Port wie bei IOS (attrs)
    sw = {"host": "10.0.1.30", "platform": "cbs", "outputs": sp.C1300, "notes": []}
    inv = normalize({"switches": [sw]})
    ifs = {i.name: i.attrs or {} for i in inv.interfaces}
    assert ifs["Te1/0/2"].get("stp_blocked") == "1" and ifs["Te1/0/1"].get("stp_root") == "1"
    assert [d.status for d in inv.stp_devices] == ["read"] and len(inv.stp) == 2


def test_catalyst_1300_rstp_aus_und_stufe_1():
    dev, inst = stp.inventory("sw-a", "cbs", {stp.TREE: CBS_RSTP})
    assert (dev.status, dev.mode) == ("read", "rstp")
    (one,) = inst
    assert (one.instance, one.mst, one.vlans, one.is_root, one.root_cost) == (
        "MST0", 0, "1-4094", True, 0)  # fmt: skip
    assert one.bridge_mac == "02:00:00:00:02:51"
    assert [(p.port, p.state, p.type) for p in one.ports] == [
        ("Gi1/0/1", "forwarding", "P2P (RSTP)"), ("Gi1/0/2", "disabled", None)]  # fmt: skip
    off, inst = stp.inventory("sw-b", "cbs", {stp.TREE: "\nSpanning tree disabled\n"})
    assert (off.status, inst) == ("off", [])
    bad, _ = stp.inventory("sw-c", "cbs", {stp.TREE: "% Unrecognized command\n"}, privilege="user")
    assert bad.status == "unreadable" and "Privileged EXEC" in bad.note
    bad, _ = stp.inventory("sw-d", "cbs", {stp.TREE: "% Unrecognized command\n"})
    assert bad.note == "show spanning-tree: Unrecognized command"  # Prompt `#`: kein Rechte-Text
