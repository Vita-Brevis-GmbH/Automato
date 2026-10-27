# ruff: noqa: E501 - Ausgaben der Geraete in Originalbreite
"""Beispielausgaben weiterer Hersteller und der IPv6-Nachbarn (0.88.x): Arista EOS (JSON wie
eAPI), HPE Aruba CX (AOS-CX, Form der ntc-Vorlagen `aruba_aoscx`), `show ipv6 neighbors` je
Plattform. Namen und Adressen erfunden (example.ch, 2001:db8::/32)."""

import json


def _json(obj) -> str:
    return json.dumps(obj, indent=2) + "\n"


# -- IPv6-Nachbarn (NDP) je Plattform ---------------------------------------------------------

IOS_ND = """IPv6 Address                              Age Link-layer Addr State Interface
2001:DB8:10::21                             0 0050.5601.0021  REACH Vl10
FE80::250:56FF:FE01:21                      0 0050.5601.0021  REACH Vl10
2001:DB8:20::15                             3 a0b1.c2d3.e4f5  STALE Vl20
2001:DB8:20::99                             0 -               INCMP Vl20
"""

NXOS_ND = """
Flags: # - Adjacencies Throttled for Glean
       G - Adjacencies of vPC peer with G/W bit
       R - Adjacencies learnt remotely
       CP - Added via L2RIB, Control plane Adjacencies
       PS - Added via L2RIB, Peer Sync
       RO - Re-Originated Peer Sync Entry

IPv6 Adjacency Table for VRF default
Total number of entries: 3
Address         Age       MAC Address     Pref Source     Interface
2001:db8:10::33
                00:01:12  0050.5602.0003  50   icmpv6     Vlan10
fe80::250:56ff:fe02:3
                00:01:12  0050.5602.0003  50   icmpv6     Vlan10
2001:db8:5::7   00:00:40  0050.5602.0007  50   icmpv6     Vlan5

IPv6 Adjacency Table for VRF TENANT-A
Total number of entries: 1
Address         Age       MAC Address     Pref Source     Interface
2001:db8:a:10::9
                00:03:05  0050.5602.0009  50   icmpv6     Vlan110
"""

NXOS_ND_DETAIL = """IPv6 Adjacency Table for VRF default
Total number of entries: 1
Address :            2001:db8:10::33
    Age :            00:01:12
    MacAddr :        0050.5602.0003
    Preference :     50
    Source :         icmpv6
    Interface :      Vlan10
    Physical Interface :      Ethernet1/3
    Packet Count :   0
    Byte Count :     0
    Best :           Yes
    Throttled :      No
"""

CBS_ND = """Interface  IPv6 address                     HW address          Type      State    Router
---------- -------------------------------- ------------------- --------- -------- ------
vlan 10    2001:db8:10::41                  00:50:56:01:00:41   Dynamic   REACH    No
vlan 10    fe80::250:56ff:fe01:41           00:50:56:01:00:41   Dynamic   STALE    No
vlan 1     2001:db8:1::1                    e0:5f:b9:00:00:fe   Dynamic   REACH    Yes
"""


def _nd_entry(addr, mac, state, iface):
    return {"ipv6-nd-neighbor-address": [{"data": addr}],
            "ipv6-nd-neighbor-l2-address": [{"data": mac}],
            "ipv6-nd-state": [{"data": state}], "ipv6-nd-expire": [{"data": "27"}],
            "ipv6-nd-isrouter": [{"data": "no"}], "ipv6-nd-issecure": [{"data": "no"}],
            "ipv6-nd-interface-name": [{"data": iface}]}  # fmt: skip


JUNOS_ND = _json({"ipv6-nd-information": [{
    "ipv6-nd-entry": [
        _nd_entry("2001:db8:100::21", "00:50:56:01:01:21", "reachable", "ae0.100"),
        _nd_entry("fe80::250:56ff:fe01:121", "00:50:56:01:01:21", "stale", "ae0.100"),
        _nd_entry("2001:db8:100::99", "", "incomplete", "ae0.100"),
    ],
    "ipv6-nd-total": [{"data": "3"}],
}]})  # fmt: skip

# -- Arista EOS (`| json`) ----------------------------------------------------------------------

EOS_VERSION_TEXT = """Arista DCS-7050SX3-48YC8
Hardware version: 11.01
Serial number: JPE20201234
Hardware MAC address: 001c.7300.0001
System MAC address: 001c.7300.0001

Software image version: 4.28.3M
Architecture: x86_64
Internal build version: 4.28.3M-28837868.4283M
Internal build ID: 3f5c1a2b-0d4e-4f6a-9b8c-7d6e5f4a3b2c
Image format version: 1.0
Image optimization: Strata-4GB

Uptime: 12 weeks, 3 days, 2 hours and 1 minute
Total memory: 8099732 kB
Free memory: 6243124 kB
"""


def _status(desc, link, mode, bandwidth, itype, duplex="duplexFull", vlan=None, explanation=None):
    info = {"interfaceForwardingModel": "bridged"}
    if mode:
        info["interfaceMode"] = mode
    if vlan is not None:
        info["vlanId"] = vlan
    if explanation:
        info = {"interfaceForwardingModel": "dataLink", "vlanExplanation": explanation}
    return {"description": desc, "linkStatus": link, "lineProtocolStatus": "up" if link == "connected" else "down",
            "vlanInformation": info, "bandwidth": bandwidth, "interfaceType": itype, "duplex": duplex,
            "autoNegotiateActive": True}  # fmt: skip


def _switchport(mode, access=1, native=1, allowed="ALL"):
    return {"enabled": True, "switchportInfo": {
        "mode": mode, "accessVlanId": access, "accessVlanName": "default",
        "trunkingNativeVlanId": native, "trunkingNativeVlanName": "default",
        "trunkAllowedVlans": allowed, "macLearning": True, "tpid": "0x8100", "tpidStatus": True,
        "staticTrunkGroups": [], "dynamicTrunkGroups": [], "vlanForwardingMode": "allowedVlansOnly"}}  # fmt: skip


def _lldp(name, port_id, desc, caps, mgmt, id_type="interfaceName", port_desc=""):
    return {"lldpNeighborInfo": [{
        "chassisIdType": "macAddress", "chassisId": "001c.7300.0002", "systemName": name,
        "systemDescription": desc, "systemCapabilities": caps,
        "managementAddresses": [{"addressType": "ipv4", "address": mgmt, "interfaceNumType": "ifIndex",
                                 "interfaceNum": 999001, "oidString": ""}] if mgmt else [],
        "neighborDiscoveryVersion": 1, "ttl": 120,
        "neighborInterfaceInfo": {"interfaceIdType": id_type, "interfaceId": f'"{port_id}"',
                                  "interfaceId_v2": port_id, "interfaceDescription": port_desc,
                                  "maxFrameSize": 9236},
    }]}  # fmt: skip


def _mac(mac, vlan, iface, kind="dynamic"):
    return {"macAddress": mac, "lastMove": 1761500000.0, "interface": iface, "moves": 1,
            "entryType": kind, "vlanId": vlan}  # fmt: skip


EOS = {
    "show version": EOS_VERSION_TEXT,
    "show version | json": _json({
        "mfgName": "Arista", "modelName": "DCS-7050SX3-48YC8", "hardwareRevision": "11.01",
        "serialNumber": "JPE20201234", "systemMacAddress": "00:1c:73:00:00:01",
        "hwMacAddress": "00:1c:73:00:00:01", "configMacAddress": "00:00:00:00:00:00",
        "version": "4.28.3M", "architecture": "x86_64", "internalVersion": "4.28.3M-28837868.4283M",
        "internalBuildId": "3f5c1a2b-0d4e-4f6a-9b8c-7d6e5f4a3b2c", "imageFormatVersion": "1.0",
        "imageOptimization": "Strata-4GB", "bootupTimestamp": 1754000000.0, "uptime": 7500000.0,
        "memTotal": 8099732, "memFree": 6243124, "isIntlVersion": False}),
    "show hostname | json": _json({"hostname": "leaf-ar-01", "fqdn": "leaf-ar-01.example.ch"}),
    "show interfaces status | json": _json({"interfaceStatuses": {
        "Ethernet1": _status("srv-esx01 vmnic0", "connected", "trunk", 10_000_000_000, "10GBASE-T"),
        "Ethernet2": _status("Drucker 3.OG", "notconnect", "bridged", 1_000_000_000, "1000BASE-T",
                             duplex="duplexUnknown", vlan=20),
        "Ethernet3": _status("", "disabled", "bridged", 1_000_000_000, "1000BASE-T", vlan=1),
        "Ethernet49/1": _status("Uplink sw-core-01 Eth1/49", "connected", None, 25_000_000_000,
                                "25GBASE-SR", explanation="in Po100"),
        "Ethernet50/1": _status("Uplink sw-core-01 Eth1/50", "connected", None, 25_000_000_000,
                                "25GBASE-SR", explanation="in Po100"),
        "Port-Channel100": _status("Uplink sw-core-01", "connected", "trunk", 50_000_000_000, "N/A"),
        "Management1": _status("", "connected", None, 1_000_000_000, "10/100/1000"),
    }}),
    "show interfaces description | json": _json({"interfaceDescriptions": {
        "Ethernet1": {"interfaceStatus": "up", "description": "srv-esx01 vmnic0", "lineProtocolStatus": "up"},
        "Ethernet2": {"interfaceStatus": "down", "description": "Drucker 3.OG", "lineProtocolStatus": "down"},
        "Ethernet3": {"interfaceStatus": "adminDown", "description": "", "lineProtocolStatus": "down"},
        "Ethernet49/1": {"interfaceStatus": "up", "description": "Uplink sw-core-01 Eth1/49", "lineProtocolStatus": "up"},
        "Ethernet50/1": {"interfaceStatus": "up", "description": "Uplink sw-core-01 Eth1/50", "lineProtocolStatus": "up"},
        "Port-Channel100": {"interfaceStatus": "up", "description": "Uplink sw-core-01", "lineProtocolStatus": "up"},
        "Vlan10": {"interfaceStatus": "up", "description": "Server", "lineProtocolStatus": "up"},
        "Management1": {"interfaceStatus": "up", "description": "", "lineProtocolStatus": "up"},
    }}),
    "show interfaces switchport | json": _json({"switchports": {
        "Ethernet1": _switchport("trunk", native=1, allowed="10,20"),
        "Ethernet2": _switchport("access", access=20),
        "Ethernet3": _switchport("access", access=1),
        "Port-Channel100": _switchport("trunk", native=1, allowed="ALL"),
    }}),
    "show vlan | json": _json({"vlans": {
        "1": {"status": "active", "name": "default", "interfaces": {"Ethernet3": {"privatePromoted": False}}, "dynamic": False},
        "10": {"status": "active", "name": "Server", "interfaces": {"Ethernet1": {"privatePromoted": False},
                                                                   "Port-Channel100": {"privatePromoted": False}}, "dynamic": False},
        "20": {"status": "active", "name": "Clients", "interfaces": {"Ethernet2": {"privatePromoted": False}}, "dynamic": False},
    }, "sourceDetail": ""}),
    "show port-channel | json": _json({"portChannels": {"Port-Channel100": {
        "recircFeature": [], "maxWeight": 16, "minSpeed": "0 gbps", "rxPorts": {}, "currWeight": 0,
        "minLinks": 0, "inactiveLag": False,
        "activePorts": {"Ethernet49/1": {"protocol": "lacp", "lacpMode": "active", "weight": 1},
                        "Ethernet50/1": {"protocol": "lacp", "lacpMode": "active", "weight": 1}},
        "inactivePorts": {}}}}),
    "show lldp neighbors detail | json": _json({"lldpNeighbors": {
        "Ethernet1": _lldp("srv-esx01.example.ch", "00:50:56:aa:00:01",
                           "VMware ESX Releasebuild-21495797", {"bridge": True}, None,
                           id_type="macAddress", port_desc="vmnic0"),
        "Ethernet49/1": _lldp("sw-core-01", "Ethernet1/49",
                              "Cisco Nexus Operating System (NX-OS) Software 10.3(4a)",
                              {"bridge": True, "router": True}, "10.0.0.2"),
        "Ethernet50/1": _lldp("sw-core-01", "Ethernet1/50",
                              "Cisco Nexus Operating System (NX-OS) Software 10.3(4a)",
                              {"bridge": True, "router": True}, "10.0.0.2"),
        "Management1": {"lldpNeighborInfo": []},
    }}),
    "show mac address-table | json": _json({
        "multicastTable": {"tableEntries": [{"macAddress": "01:00:5e:00:00:fb", "vlanId": 10,
                                             "interfaces": ["Ethernet1"], "entryType": "igmp"}]},
        "unicastTable": {"tableEntries": [
            _mac("00:50:56:01:00:21", 10, "Ethernet1"),
            _mac("00:50:56:01:00:22", 20, "Ethernet1"),
            _mac("a0:b1:c2:d3:e4:f5", 20, "Port-Channel100"),
            _mac("00:1c:73:00:00:01", 10, "Router", "static"),
            _mac("00:1c:73:00:00:99", 10, "Vxlan1", "learnedRemote"),
            _mac("00:1c:73:00:00:02", 10, "Cpu", "static"),
        ]},
        "disabledMacLearningVlans": []}),
    "show ip arp vrf all | json": _json({"vrfs": {
        "default": {"ipV4Neighbors": [
            {"hwAddress": "0050.5601.0021", "address": "10.10.0.21", "interface": "Vlan10, Ethernet1", "age": 0},
            {"hwAddress": "a0b1.c2d3.e4f5", "address": "10.20.0.15", "interface": "Vlan20, Port-Channel100", "age": 12},
        ], "totalEntries": 2, "dynamicEntries": 2, "staticEntries": 0, "notLearnedEntries": 0},
        "MGMT": {"ipV4Neighbors": [
            {"hwAddress": "5254.00aa.0099", "address": "10.255.0.1", "interface": "Management1", "age": 3},
        ], "totalEntries": 1},
    }}),
    "show ipv6 neighbors vrf all | json": _json({"vrfs": {"default": {"ipV6Neighbors": [
        {"hwAddress": "0050.5601.0021", "address": "2001:db8:10::21", "interface": "Vlan10, Ethernet1", "age": 3, "state": "REACHABLE"},
        {"hwAddress": "0050.5601.0021", "address": "fe80::250:56ff:fe01:21", "interface": "Vlan10, Ethernet1", "age": 3, "state": "STALE"},
    ]}}}),
}  # fmt: skip

# -- HPE Aruba CX (AOS-CX) ----------------------------------------------------------------------

AOSCX_VERSION = """-----------------------------------------------------------------------------
ArubaOS-CX
(c) Copyright 2017-2023 Hewlett Packard Enterprise Development LP
-----------------------------------------------------------------------------
Version      : FL.10.10.1060
Build Date   : 2023-04-26 21:16:41 UTC
Build ID     : ArubaOS-CX:FL.10.10.1060:6f3a1c2b0d4e:202304262046
Build SHA    : 6f3a1c2b0d4e5f6a7b8c9d0e1f2a3b4c5d6e7f80
Active Image : primary

Service OS Version : FL.01.11.0002
BIOS Version       : FL-01-0002
"""

_RATE = """ Rate collection interval: 300 seconds

 Rate                               RX                   TX        Total (RX+TX)
 ---------------- -------------------- -------------------- --------------------
 Mbits / sec                      0.01                 0.02                 0.03
 KPkts / sec                      0.00                 0.00                 0.00
   Unicast                        0.00                 0.00                 0.00
   Multicast                      0.00                 0.00                 0.00
   Broadcast                      0.00                 0.00                 0.00
 Utilization                      0.00                 0.00                 0.00

 Statistic                          RX                   TX                Total
 ---------------- -------------------- -------------------- --------------------
 Packets                         12345                23456                35801
   Unicast                       12000                23000                35000
   Multicast                       300                  400                  700
   Broadcast                        45                   56                  101
 Bytes                         1234567              2345678              3580245
 Jumbos                              0                    0                    0
 Pause Frames                        0                    0                    0
 Errors                              0                    0                    0
   CRC/FCS                           0                  n/a                    0
   Collision                       n/a                    0                    0
   Runts                             0                  n/a                    0
   Giants                            0                  n/a                    0
 Dropped                             0                    0                    0
 Filtered                            0                    0                    0
"""


def _cx_port(name, state, desc, speed, vlan_lines, admin="up", itype="1GbT"):
    return f"""Interface {name} is {state}
 Admin state is {admin}
 Link state: {state} for 3 days (since Fri Oct 24 08:12:41 UTC 2026)
 Link transitions: 1
 Description: {desc}
 Hardware: Ethernet, MAC Address: 38:21:c7:5d:7a:0{name[-1]}
 MTU 1500
 Type {itype}
 Full-duplex
 qos trust none
 Speed {speed}
 Auto-negotiation is on
 Flow-control: off
 Error-control: off
 MDI mode: MDIX
{vlan_lines}{_RATE}
"""


AOSCX = {
    "show version": AOSCX_VERSION,
    "show system": """Hostname                        : acc-cx-01
System Description              : FL.10.10.1060
System Contact                  :
System Location                 : Lager OG

Vendor                          : Aruba
Product Name                    : JL658A 6300M 24SFP+ 4SFP56 Swch
Chassis Serial Nbr              : SG30ABC123
Base MAC Address                : 38:21:c7:5d:7a:00
ArubaOS-CX Version              : FL.10.10.1060

Time Zone                       : UTC

Up Time                         : 3 weeks, 2 days, 4 hours, 12 minutes
CPU Util (%)                    : 5
Memory Usage (%)                : 21
""",
    "show interface": (
        _cx_port("1/1/1", "up", "srv-esx02 vmnic0", "1000 Mb/s",
                 " VLAN Mode: native-untagged\n Native VLAN: 1\n Allowed VLAN List: 1,10,20,30,40,50,60,70\n")
        + _cx_port("1/1/2", "down", "Drucker 1.OG", "1000 Mb/s",
                   " VLAN Mode: access\n Access VLAN: 20\n", admin="down")
        + _cx_port("1/1/49", "up", "Uplink sw-core-01 Eth1/51", "10 Gb/s", "", itype="SFP+SR")
        + _cx_port("1/1/50", "up", "Uplink sw-core-01 Eth1/52", "10 Gb/s", "", itype="SFP+SR")
        + """Aggregate lag1 is up
 Admin state is up
 Link state: up for 3 days (since Fri Oct 24 08:12:41 UTC 2026)
 Link transitions: 1
 Description: Uplink sw-core-01
 MAC Address: 38:21:c7:5d:7a:00
 Aggregated-interfaces : 1/1/49 1/1/50
 Aggregation-key : 1
 Aggregate mode : active
 Speed 20000 Mb/s
 qos trust none
 VLAN Mode: native-untagged
 Native VLAN: 1
 Allowed VLAN List: all
"""
        + _RATE + "\n"
    ),
    "show vlan": """
--------------------------------------------------------------------------------------
VLAN  Name                              Status  Reason          Type      Interfaces
--------------------------------------------------------------------------------------
1     DEFAULT_VLAN_1                    up      ok              default   1/1/1,lag1
10    Server                            up      ok              static    1/1/1,lag1
20    Clients                           up      ok              static    1/1/1-1/1/2,lag1
""",
    "show lldp neighbor-info detail": """
LLDP Neighbor Information
=========================

Total Neighbor Entries          : 2
Total Neighbor Entries Deleted  : 0
Total Neighbor Entries Dropped  : 0
Total Neighbor Entries Aged-Out : 0

--------------------------------------------------------------------------------
Port                           : 1/1/1
Neighbor Entries               : 1
Neighbor Entries Deleted       : 0
Neighbor Entries Dropped       : 0
Neighbor Entries Aged-Out      : 0
Neighbor Chassis-Name          : srv-esx02.example.ch
Neighbor Chassis-Description   : VMware ESX Releasebuild-21495797
Neighbor Chassis-ID            : 00:50:56:aa:00:02
Neighbor Management-Address    : 10.0.0.102
Chassis Capabilities Available : Bridge
Chassis Capabilities Enabled   : Bridge
Neighbor Port-ID               : 00:50:56:aa:00:02
Neighbor Port-Desc             : vmnic0
TTL                            : 180

--------------------------------------------------------------------------------
Port                           : 1/1/49
Neighbor Entries               : 1
Neighbor Entries Deleted       : 0
Neighbor Entries Dropped       : 0
Neighbor Entries Aged-Out      : 0
Neighbor Chassis-Name          : sw-core-01
Neighbor Chassis-Description   : Cisco Nexus Operating System (NX-OS) Software 10.3(4a)
Neighbor Chassis-ID            : 5254.00aa.0002
Neighbor Management-Address    : 10.0.0.2
Chassis Capabilities Available : Bridge, Router
Chassis Capabilities Enabled   : Bridge, Router
Neighbor Port-ID               : Ethernet1/51
Neighbor Port-Desc             : Downlink
TTL                            : 120

--------------------------------------------------------------------------------
""",
    "show mac-address-table": """MAC age-time            : 300 seconds
Number of MAC addresses : 4

MAC Address          VLAN     Type                      Port
--------------------------------------------------------------
00:50:56:01:00:31    10       dynamic                   1/1/1
00:50:56:01:00:32    20       dynamic                   1/1/1
a0:b1:c2:d3:e4:f5    20       dynamic                   lag1
38:21:c7:5d:7a:00    1        static                    vlan1
""",
    "show arp all-vrfs": """IPv4 Address     MAC                Port         Physical Port                      State     VRF
--------------------------------------------------------------------------------------------------------
10.10.0.31       00:50:56:01:00:31  vlan10       1/1/1                              reachable default
10.20.0.15       a0:b1:c2:d3:e4:f5  vlan20       lag1                               reachable default
10.99.0.9        00:50:56:01:00:99  vlan99       1/1/1                              reachable TENANT-B

Total Number Of ARP Entries Listed: 3.
-------------------------------------------------------------------------------------------------------
""",
    "show ipv6 neighbors all-vrfs": """IPv6 Address                              MAC                Port         Physical Port      State      VRF
--------------------------------------------------------------------------------------------------------------
2001:db8:10::31                           00:50:56:01:00:31  vlan10       1/1/1              reachable  default
fe80::250:56ff:fe01:31                    00:50:56:01:00:31  vlan10       1/1/1              stale      default
2001:db8:99::9                            00:50:56:01:00:99  vlan99       1/1/1              reachable  TENANT-B

Total Number Of IPv6 Neighbors Entries Listed: 3.
--------------------------------------------------------------------------------------------------------------
""",
}  # fmt: skip
