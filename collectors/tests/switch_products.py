# ruff: noqa: E501 - Ausgaben der Geraete in Originalbreite
"""Beispielausgaben je Produkt (0.68.0): Catalyst 1300 (Business-CLI), Juniper MX/EX (Junos,
JSON), Catalyst 9200L/3650/4500 (IOS/IOS-XE). Namen und Adressen erfunden (example.ch)."""

import json


def _d(value):
    return [{"data": value}]


def _json(obj) -> str:
    return json.dumps(obj, indent=4) + "\n"


# -- Catalyst 1300 (Business-CLI) -----------------------------------------------------------

C1300 = {
    "show version": """Active-image: flash://system/images/image_tesla_hybrid_4.1.3.36_release_cisco_signed.bin
  Version: 4.1.3.36
  MD5 Digest: 3f2c1b0e8c0a6f7d9e2b4a1c5d6e7f80
  Date: 20-Dec-2023
  Time: 20:15:42
Inactive-image: flash://system/images/image_tesla_hybrid_4.1.2.10_release_cisco_signed.bin
  Version: 4.1.2.10
  MD5 Digest: 9a8b7c6d5e4f30211a2b3c4d5e6f7081
  Date: 02-Aug-2023
  Time: 11:02:17
""",
    "show system": """System Description:                       Catalyst 1300-24T-4X 24-Port Gigabit Ethernet Managed Switch
System Up Time (days,hour:min:sec):       41,03:22:41
System Contact:
System Name:                              acc-1300-01
System Location:                          Lager EG
System MAC Address:                       e0:5f:b9:00:00:01
System Object ID:                         1.3.6.1.4.1.9.6.1.1003.24.1
""",
    "show inventory": """NAME: "1"   DESCR: "Catalyst 1300-24T-4X 24-Port Gigabit Ethernet Managed Switch"
PID: C1300-24T-4X      VID: V01  SN: PSZ27161ABC
""",
    "show interfaces status": """                                             Flow Link          Back   Mdix
Port     Type         Duplex  Speed Neg      ctrl State       Pressure Mode
-------- ------------ ------  ----- -------- ---- ----------- -------- -------
gi1/0/1  1G-Copper    Full    1000  Enabled  Off  Up          Disabled On
gi1/0/2  1G-Copper      --      --     --     --  Down           --     --
gi1/0/3  1G-Copper    Full    100   Enabled  Off  Up          Disabled Off
gi1/0/5  1G-Copper    Full    1000  Enabled  Off  Up          Disabled Off
gi1/0/6  1G-Copper    Full    1000  Enabled  Off  Up          Disabled Off
te1/0/1  10G-Fiber    Full    10000 Enabled  Off  Up          Disabled Off
te1/0/2  10G-Fiber    Full    10000 Enabled  Off  Up          Disabled Off

                                          Flow    Link
Ch       Type    Duplex  Speed  Neg      control  State
-------- ------- ------  -----  -------- -------  -----------
Po1      1G      Full    2000   Enabled  Off      Up
""",
    "show interfaces description": """Port      Description
-------   -----------
gi1/0/1   srv-esx01 vmnic0
gi1/0/2
gi1/0/3   Drucker 2.OG
te1/0/1   Uplink nx-core-01 Eth1/49
te1/0/2   Uplink nx-core-02 Eth1/49

Ch        Description
-------   -----------
Po1       Server-Bond
""",
    "show vlan": """Created by: D-Default, S-Static, G-GVRP, R-Radius Assigned VLAN, V-Voice VLAN

Vlan       Name         Tagged Ports       UnTagged Ports    Created by
---- ----------------- ------------------ ------------------ ----------
 1           1                            gi1/0/2,gi1/0/3,       DV
                                          te1/0/1-2,Po1
 10        Server      te1/0/1-2,Po1      gi1/0/1                S
 20        Clients     te1/0/1-2,Po1                             S
""",
    "show interfaces port-channel": """Load balancing: src-dst-mac-ip.

Gathering information...
Channel  Ports
-------  -----
Po1      Active: gi1/0/5-6
""",
    "show lldp neighbors": """System capability supported: Bridge, Router
Port        Device ID          Port ID          System Name    Capabilities  TTL
--------- ----------------- ----------------- ----------------- ------------ -----
te1/0/1   00:3a:9c:00:00:01  Ethernet1/49      nx-core-01          B, R        91
te1/0/2   00:3a:9c:00:00:02  Ethernet1/49      nx-core-02          B, R        91
gi1/0/1   00:50:56:aa:00:01  vmnic0            srv-esx01.example.  B           119
                                               ch
""",
    "show mac address-table": """Flags: I - Internal usage VLAN
Aging time is 300 sec

    Vlan          Mac Address         Port       Type
------------ --------------------- ---------- ----------
     1         e0:5f:b9:00:00:01         0         self
     10        00:50:56:01:00:01     gi1/0/1    dynamic
     1         00:1b:a9:00:00:02     gi1/0/3    dynamic
     10        00:3a:9c:00:00:01     te1/0/1    dynamic
     10        01:00:5e:00:00:fb     te1/0/1    dynamic
""",
    "show arp": """  VLAN    Interface     IP address        HW address          status
--------------------- --------------- ------------------- ---------------
vlan 1                 10.0.1.1          00:3a:9c:00:00:01   dynamic
vlan 10                10.10.0.21        00:50:56:01:00:01   dynamic
""",
}


# -- Juniper MX (Router) ----------------------------------------------------------------------


def _phy(name, admin="up", oper="up", speed="10Gbps", mode="Full-duplex", desc=None, units=()):
    out = {"name": _d(name), "admin-status": [{"data": admin,
                                               "attributes": {"junos:format": "Enabled"}}],
           "oper-status": _d(oper), "speed": _d(speed), "link-mode": _d(mode),
           "link-level-type": _d("Ethernet"), "logical-interface": list(units)}  # fmt: skip
    if desc:
        out["description"] = _d(desc)
    return out


def _unit(name, tag=None, inet=(), inet6=(), bundle=None, desc=None):
    fams = []
    if inet:
        fams.append(
            {
                "address-family-name": _d("inet"),
                "interface-address": [
                    {"ifa-local": _d(local), "ifa-destination": _d(dest)} for local, dest in inet
                ],
            }
        )
    if inet6:
        fams.append(
            {
                "address-family-name": _d("inet6"),
                "interface-address": [
                    {"ifa-local": _d(local), "ifa-destination": _d(dest)} for local, dest in inet6
                ],
            }
        )
    if bundle:
        fams.append({"address-family-name": _d("aenet"), "ae-bundle-name": _d(bundle)})
    out = {"name": _d(name), "address-family": fams}
    if tag is not None:
        out["link-address"] = _d(f"VLAN-Tag [ 0x8100.{tag} ] ")
    if desc:
        out["description"] = _d(desc)
    return out


MX = {
    "show version": """Hostname: mx-edge-01
Model: mx240
Junos: 21.4R3-S5.4
JUNOS OS Kernel 64-bit  [20230321.14a7d65_builder_stable_12_214]
JUNOS OS libs [20230321.14a7d65_builder_stable_12_214]
JUNOS OS runtime [20230321.14a7d65_builder_stable_12_214]
JUNOS Packet Forwarding Engine Support (MX Common) [21.4R3-S5.4]
""",
    "show chassis hardware | display json | no-more": _json({"chassis-inventory": [{"chassis": [{
        "name": _d("Chassis"), "serial-number": _d("JN12A3B4CAFC"), "description": _d("MX240"),
        "chassis-module": [{"name": _d("Midplane"), "serial-number": _d("ACRB1234")}]}]}]}),
    "show interfaces | display json | no-more": _json({"interface-information": [{
        "physical-interface": [
            _phy("xe-0/0/0", desc="nx-core-01 Eth1/1", units=[_unit("xe-0/0/0.0", bundle="ae0.0")]),
            _phy("xe-0/0/1", desc="nx-core-02 Eth1/1", units=[_unit("xe-0/0/1.0", bundle="ae0.0")]),
            _phy("ae0", desc="Core-Bundle", speed="20Gbps", units=[
                _unit("ae0.100", tag=100, inet=[("10.100.0.1", "10.100.0.0/24")],
                      inet6=[("2001:db8:100::1", "2001:db8:100::/64"),
                             ("fe80::1", "fe80::/64")], desc="Server"),
                _unit("ae0.200", tag=200, inet=[("10.200.0.1", "10.200.0.0/24")]),
            ]),
            _phy("ge-0/0/9", speed="1000mbps", desc="ISP A",
                 units=[_unit("ge-0/0/9.0", inet=[("198.51.100.2", "198.51.100.0/30")])]),
            _phy("ge-0/0/8", admin="down", oper="down", speed="1000mbps"),
            _phy("lo0", speed="Unspecified", mode="", units=[
                _unit("lo0.0", inet=[("10.255.0.1", "10.255.0.1")]),
                _unit("lo0.16384", inet=[("127.0.0.1", "127.0.0.1")]),
            ]),
            _phy("fxp0", speed="1000mbps",
                 units=[_unit("fxp0.0", inet=[("10.0.0.40", "10.0.0.0/24")])]),
            _phy("pfe-0/0/0", speed="800mbps"),
            _phy("cbp0", speed="Unspecified"),
        ]}]}),
    "show lldp neighbors | display json | no-more": _json({"lldp-neighbors-information": [{
        "lldp-neighbor-information": [
            {"lldp-local-port-id": _d("xe-0/0/0"), "lldp-local-parent-interface-name": _d("ae0"),
             "lldp-remote-chassis-id-subtype": _d("Mac address"),
             "lldp-remote-chassis-id": _d("00:3a:9c:00:00:01"),
             "lldp-remote-port-id-subtype": _d("Interface name"),
             "lldp-remote-port-id": _d("Ethernet1/1"), "lldp-remote-system-name": _d("nx-core-01")},
            {"lldp-local-port-id": _d("xe-0/0/1"), "lldp-local-parent-interface-name": _d("ae0"),
             "lldp-remote-chassis-id": _d("00:3a:9c:00:00:02"),
             "lldp-remote-port-id": _d("00:3a:9c:00:01:01"),
             "lldp-remote-port-description": _d("Ethernet1/1"),
             "lldp-remote-system-name": _d("nx-core-02")},
        ]}]}),
    "show vlans | display json | no-more": "error: syntax error, expecting <command>: vlans\n",
    "show arp no-resolve | display json | no-more": _json({"arp-table-information": [{
        "arp-table-entry": [
            {"mac-address": _d("00:50:56:01:00:01"), "ip-address": _d("10.100.0.21"),
             "interface-name": _d("ae0.100"), "arp-table-entry-flags": [{"none": [None]}]},
            {"mac-address": _d("00:00:5e:00:01:01"), "ip-address": _d("198.51.100.1"),
             "interface-name": _d("ge-0/0/9.0")},
        ]}]}),
    "show ethernet-switching table | display json | no-more": (
        "error: syntax error, expecting <command>: ethernet-switching\n"),
    "show bridge mac-table | display json | no-more": _json({"l2ald-rtb-macdb": [{
        "l2ald-mac-entry": [{"l2-mac-routing-instance": _d("default-switch"),
                             "l2-bridging-domain": _d("bd100"), "l2-bridge-vlan": _d("100"),
                             "l2-mac-entry": [
            {"l2-mac-address": _d("00:50:56:01:00:01"), "l2-mac-flags": _d("D"),
             "l2-mac-logical-interface": _d("ae0.100")},
            {"l2-mac-address": _d("00:50:56:01:00:02"), "l2-mac-flags": _d("S"),
             "l2-mac-logical-interface": _d("ae0.100")},
        ]}]}]}),
}  # fmt: skip


# -- Juniper EX (ELS): VLANs und MAC-Tabelle ----------------------------------------------------

EX = {
    "show version": "Hostname: ex-acc-01\nModel: ex4300-48t\nJunos: 21.4R3.15\n",
    "show interfaces | display json | no-more": _json({"interface-information": [{
        "physical-interface": [
            _phy("ge-0/0/1", speed="1000mbps", units=[_unit("ge-0/0/1.0")]),
            _phy("xe-0/2/0", units=[_unit("xe-0/2/0.0")]),
            _phy("irb", speed="Unspecified", mode="", units=[
                _unit("irb.10", inet=[("10.10.0.2", "10.10.0.0/24")])]),
        ]}]}),
    "show vlans | display json | no-more": _json({"l2ng-l2ald-vlan-instance-information": [{
        "l2ng-l2ald-vlan-instance-group": [
            {"l2ng-l2rtb-vlan-name": _d("Server"), "l2ng-l2rtb-vlan-tag": _d("10"),
             "l2ng-l2rtb-vlan-member": [
                 {"l2ng-l2rtb-vlan-member-interface": _d("ge-0/0/1.0*"),
                  "l2ng-l2rtb-vlan-member-tagness": _d("untagged")},
                 {"l2ng-l2rtb-vlan-member-interface": _d("xe-0/2/0.0*"),
                  "l2ng-l2rtb-vlan-member-tagness": _d("tagged")}]},
            {"l2ng-l2rtb-vlan-name": _d("Clients"), "l2ng-l2rtb-vlan-tag": _d("20"),
             "l2ng-l2rtb-vlan-member": [
                 {"l2ng-l2rtb-vlan-member-interface": _d("xe-0/2/0.0*"),
                  "l2ng-l2rtb-vlan-member-tagness": _d("tagged")}]},
        ]}]}),
    "show ethernet-switching table | display json | no-more": _json({"l2ng-l2ald-rtb-macdb": [{
        "l2ng-l2ald-mac-entry-vlan": [{"l2ng-mac-entry": [
            {"l2ng-l2-mac-vlan-name": _d("Server"), "l2ng-l2-vlan-id": _d("10"),
             "l2ng-l2-mac-address": _d("00:50:56:01:00:31"), "l2ng-l2-mac-flags": _d("D"),
             "l2ng-l2-mac-logical-interface": _d("ge-0/0/1.0")},
        ]}]}]}),
    "show bridge mac-table | display json | no-more": (
        "error: syntax error, expecting <command>: bridge\n"),
}  # fmt: skip


# -- Catalyst 9200L, 3650, 4500 (IOS/IOS-XE): Erkennung und Modell ------------------------------

C9200L = """Cisco IOS XE Software, Version 17.09.04a
Cisco IOS Software [Cupertino], Catalyst L3 Switch Software (CAT9K_LITE_IOSXE), Version 17.9.4a, RELEASE SOFTWARE (fc3)
Technical Support: http://www.cisco.com/techsupport
Copyright (c) 1986-2023 by Cisco Systems, Inc.
Compiled Fri 20-Oct-23 10:44 by mcpre

ROM: IOS-XE ROMMON
BOOTLDR: System Bootstrap, Version 17.9.1r, RELEASE SOFTWARE (P)

acc-9200l-01 uptime is 12 weeks, 3 days, 4 hours, 21 minutes
Uptime for this control processor is 12 weeks, 3 days, 4 hours, 23 minutes
System returned to ROM by Reload Command
System image file is "flash:packages.conf"
Last reload reason: Reload Command

cisco C9200L-48P-4X (ARM64) processor with 600040K/3071K bytes of memory.
Processor board ID JAE25110ABC
1 Virtual Ethernet interface
52 Gigabit Ethernet interfaces
4 Ten Gigabit Ethernet interfaces
2048K bytes of non-volatile configuration memory.

Base Ethernet MAC Address          : 70:18:a7:00:00:01
Motherboard Assembly Number        : 73-18998-06
Model Revision Number              : C0
Model Number                       : C9200L-48P-4X
System Serial Number               : JAE25110ABC

Configuration register is 0x102
"""

C3650 = """Cisco IOS XE Software, Version 16.12.10a
Cisco IOS Software [Gibraltar], Catalyst L3 Switch Software (CAT3K_CAA-UNIVERSALK9-M), Version 16.12.10a, RELEASE SOFTWARE (fc2)
Technical Support: http://www.cisco.com/techsupport
Copyright (c) 1986-2023 by Cisco Systems, Inc.
Compiled Mon 06-Feb-23 18:21 by mcpre

ROM: IOS-XE ROMMON
BOOTLDR: CAT3K_CAA Boot Loader (CAT3K_CAA-HBOOT-M) Version 4.78, RELEASE SOFTWARE (P)

dist-3650-01 uptime is 2 years, 1 week, 3 days, 2 hours, 5 minutes
Uptime for this control processor is 2 years, 1 week, 3 days, 2 hours, 7 minutes
System returned to ROM by Power Failure
System image file is "flash:packages.conf"
Last reload reason: Power Failure

cisco WS-C3650-48PS (MIPS) processor (revision L0) with 873759K/6147K bytes of memory.
Processor board ID FDO2124ABCD
4 Virtual Ethernet interfaces
52 Gigabit Ethernet interfaces
2048K bytes of non-volatile configuration memory.

Base Ethernet MAC Address          : 00:a5:bf:00:00:01
Model Revision Number              : L0
Model Number                       : WS-C3650-48PS
System Serial Number               : FDO2124ABCD

Configuration register is 0x102
"""

C4500 = """Cisco IOS Software, Catalyst 4500 L3 Switch  Software (cat4500e-ENTSERVICESK9-M), Version 15.2(2)E9, RELEASE SOFTWARE (fc4)
Technical Support: http://www.cisco.com/techsupport
Copyright (c) 1986-2018 by Cisco Systems, Inc.
Compiled Sat 08-Sep-18 17:33 by prod_rel_team
Image text-base: 0x10000000, data-base: 0x12A8E8D8

ROM: 15.0(1r)SG10
core-4506-01 uptime is 3 years, 12 weeks, 2 days, 7 hours, 9 minutes
Uptime for this control processor is 3 years, 12 weeks, 2 days, 7 hours, 11 minutes
System returned to ROM by power-on
System image file is "bootflash:cat4500e-entservicesk9-mz.152-2.E9.bin"
Hobgoblin revision 2, Fortooine revision 1.40.0x1c
Last reload reason: power-on

cisco WS-C4506-E (MPC8572) processor (revision 10) with 2097152K/20480K bytes of memory.
Processor board ID FOX1234ABCD
MPC8572 CPU at 1.5GHz, Supervisor 6L-E
Last reset from PowerUp
2 Virtual Ethernet interfaces
96 Gigabit Ethernet interfaces
4 Ten Gigabit Ethernet interfaces
511K bytes of non-volatile configuration memory.

Configuration register is 0x2102
"""

C4500_MAC = """Unicast Entries
 vlan     mac address     type        protocols               port
---------+---------------+--------+---------------------+-------------------------
  10      0050.5601.0011   dynamic ip,ipx,assigned,other GigabitEthernet3/1
  20      0050.5601.0012   dynamic ip,ipx,assigned,other GigabitEthernet3/2
   1      001b.2a00.0001    static ip,ipx,assigned,other Switch

Multicast Entries
 vlan    mac address     type    ports
---------+---------------+-------+--------------------------------------------
   1      ffff.ffff.ffff   system Switch,Gi3/1,Gi3/2
"""
