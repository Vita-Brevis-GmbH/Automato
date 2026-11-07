# ruff: noqa: E501, S104 - Ausgaben der Geraete in Originalbreite; 0.0.0.0 ist eine Adresse, kein Bind
"""Beispielausgaben der Routing-Befehle (ADR 0032): IOS-XE-Aussenrouter mit eBGP zu zwei
Providern, NX-OS-Core mit VRFs und HSRP, Juniper MX mit Routing-Instanz, Arista mit eBGP.
Namen und Adressen erfunden (Dokumentationsnetze, example.ch)."""

import json

from switch_products import C4500


def _d(value):
    return [{"data": value}]


def _json(obj) -> str:
    return json.dumps(obj, indent=4) + "\n"


# -- IOS-XE: rt-edge-01, eBGP zu ISP A (64500) und ISP B (64511, nur Backup), iBGP zum Partner --

IOS_VERSION = C4500.replace("core-4506-01", "rt-edge-01")

IOS_RIB = """Codes: L - local, C - connected, S - static, R - RIP, M - mobile, B - BGP
       D - EIGRP, EX - EIGRP external, O - OSPF, IA - OSPF inter area
       N1 - OSPF NSSA external type 1, N2 - OSPF NSSA external type 2
       E1 - OSPF external type 1, E2 - OSPF external type 2
       i - IS-IS, su - IS-IS summary, L1 - IS-IS level-1, L2 - IS-IS level-2
       ia - IS-IS inter area, * - candidate default, U - per-user static route
       o - ODR, P - periodic downloaded static route, H - NHRP, l - LISP
       a - application route
       + - replicated route, % - next hop override, p - overrides from PfR

Gateway of last resort is 198.51.100.1 to network 0.0.0.0

B*    0.0.0.0/0 [20/0] via 198.51.100.1, 2w0d
      10.0.0.0/8 is variably subnetted, 7 subnets, 3 masks
C        10.0.0.0/30 is directly connected, GigabitEthernet0/1
L        10.0.0.1/32 is directly connected, GigabitEthernet0/1
C        10.0.0.4/30 is directly connected, GigabitEthernet0/2
L        10.0.0.5/32 is directly connected, GigabitEthernet0/2
O        10.10.0.0/16 [110/2] via 10.0.0.2, 1d02h, GigabitEthernet0/1
                      [110/2] via 10.0.0.6, 1d02h, GigabitEthernet0/2
S        10.20.0.0/16 [1/0] via 10.0.0.2
O E2     10.30.0.0/16 [110/20] via 10.0.0.6, 00:12:01, GigabitEthernet0/2
      172.16.0.0/16 is subnetted, 1 subnets
B        172.16.0.0 [200/0] via 10.255.0.2, 1d02h
      198.51.100.0/24 is variably subnetted, 4 subnets, 2 masks
C        198.51.100.0/30 is directly connected, GigabitEthernet0/0
L        198.51.100.2/32 is directly connected, GigabitEthernet0/0
C        198.51.100.8/30 is directly connected, GigabitEthernet0/3
L        198.51.100.10/32 is directly connected, GigabitEthernet0/3
B        203.0.113.128/25
           [20/0] via 198.51.100.1, 1w0d
"""

IOS_RIB_VRF = """
Routing Table: CUST-A
Codes: L - local, C - connected, S - static, R - RIP, M - mobile, B - BGP
       + - replicated route, % - next hop override, p - overrides from PfR

Gateway of last resort is 10.50.0.254 to network 0.0.0.0

S*    0.0.0.0/0 [1/0] via 10.50.0.254
      10.0.0.0/8 is variably subnetted, 2 subnets, 2 masks
C        10.50.0.0/24 is directly connected, GigabitEthernet0/4
L        10.50.0.1/32 is directly connected, GigabitEthernet0/4

Routing Table: Mgmt-vrf
Codes: L - local, C - connected, S - static, R - RIP, M - mobile, B - BGP

Gateway of last resort is 192.168.99.1 to network 0.0.0.0

S*    0.0.0.0/0 [1/0] via 192.168.99.1
      192.168.99.0/24 is variably subnetted, 2 subnets, 2 masks
C        192.168.99.0/24 is directly connected, GigabitEthernet0
L        192.168.99.10/32 is directly connected, GigabitEthernet0
"""

IOS_RIB6 = """IPv6 Routing Table - default - 7 entries
Codes: C - Connected, L - Local, S - Static, U - Per-user Static route
       B - BGP, R - RIP, H - NHRP, I1 - ISIS L1
       I2 - ISIS L2, IA - ISIS interarea, IS - ISIS summary, D - EIGRP
       EX - EIGRP external, ND - ND Default, NDp - ND Prefix, DCE - Destination
       NDr - Redirect, O - OSPF Intra, OI - OSPF Inter, OE1 - OSPF ext 1
       OE2 - OSPF ext 2, ON1 - OSPF NSSA ext 1, ON2 - OSPF NSSA ext 2
       la - LISP alt, lr - LISP site-registrations, ld - LISP dyn-eid
       a - Application
B   ::/0 [20/0]
     via FE80::1, GigabitEthernet0/0
C   2001:DB8:1::/64 [0/0]
     via GigabitEthernet0/1, directly connected
L   2001:DB8:1::1/128 [0/0]
     via GigabitEthernet0/1, receive
O   2001:DB8:2::/64 [110/2]
     via FE80::2, GigabitEthernet0/1
     via FE80::6, GigabitEthernet0/2
L   FF00::/8 [0/0]
     via Null0, receive
"""

IOS_BGP_SUMMARY = """BGP router identifier 10.255.0.1, local AS number 65001
BGP table version is 12, main routing table version 12
5 network entries using 1240 bytes of memory
7 path entries using 952 bytes of memory

Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
10.255.0.2      4        65001     500     510       12    0    0 1d02h           2
198.51.100.1    4        64500    1234    1200       12    0    0 1w0d            3
198.51.100.9    4        64511     800     790       12    0    0 1w0d            2
198.51.100.13   4        64512       0       0        1    0    0 never    Idle (Admin)
"""

IOS_BGP = """BGP table version is 12, local router ID is 10.255.0.1
Status codes: s suppressed, d damped, h history, * valid, > best, i - internal,
              r RIB-failure, S Stale, m multipath, b backup-path, f RT-Filter,
              x best-external, a additional-path, c RIB-compressed,
              t secondary path, L long-lived-stale,
Origin codes: i - IGP, e - EGP, ? - incomplete
RPKI validation codes: V valid, I invalid, N Not found

     Network          Next Hop            Metric LocPrf Weight Path
 *>  0.0.0.0          198.51.100.1             0             0 64500 i
 *                    198.51.100.9             0             0 64511 64500 i
 *>  10.20.0.0/16     0.0.0.0                  0         32768 i
 *>i 172.16.0.0       10.255.0.2               0    100      0 64600 i
 * i                  10.255.0.3               0    100      0 64600 i
 *>  203.0.113.128/25 198.51.100.1             0             0 64500 64496 i
"""

IOS = {
    "show version": IOS_VERSION,
    "show ip route": IOS_RIB,
    "show ip route vrf *": IOS_RIB_VRF,
    "show ipv6 route": IOS_RIB6,
    "show ip bgp summary": IOS_BGP_SUMMARY,
    "show bgp ipv4 unicast": IOS_BGP,
}

# IOS-Switch ohne `ip routing`: nur das Default-Gateway
IOS_L2_RIB = """Default gateway is 10.0.0.1

Host               Gateway           Last Use    Total Uses  Interface
ICMP redirect cache is empty
"""

# -- NX-OS: sw-core-01 mit VRF CUST-A, HSRP und eBGP zur Firewall ---------------------------

NXOS_RIB = """IP Route Table for VRF "default"
'*' denotes best ucast next-hop
'**' denotes best mcast next-hop
'[x/y]' denotes [preference/metric]
'%<string>' in via output denotes VRF <string>

0.0.0.0/0, ubest/mbest: 1/0
    *via 10.0.0.1, Eth1/49, [20/0], 3w2d, bgp-65010, external, tag 65001
10.0.0.0/30, ubest/mbest: 1/0, attached
    *via 10.0.0.2, Eth1/49, [0/0], 3w2d, direct
10.0.0.2/32, ubest/mbest: 1/0, attached
    *via 10.0.0.2, Eth1/49, [0/0], 3w2d, local
10.10.0.0/24, ubest/mbest: 1/0, attached
    *via 10.10.0.2, Vlan10, [0/0], 3w2d, direct
10.10.0.1/32, ubest/mbest: 1/0, attached
    *via 10.10.0.1, Vlan10, [0/0], 3w2d, hsrp
10.10.0.2/32, ubest/mbest: 1/0, attached
    *via 10.10.0.2, Vlan10, [0/0], 3w2d, local
10.40.0.0/16, ubest/mbest: 2/0
    *via 10.10.0.5, Vlan10, [110/41], 1d02h, ospf-1, intra
    *via 10.10.0.6, Vlan10, [110/41], 1d02h, ospf-1, intra
     via 10.10.0.7, Vlan10, [110/50], 1d02h, ospf-1, intra

IP Route Table for VRF "CUST-A"
'*' denotes best ucast next-hop
'**' denotes best mcast next-hop
'[x/y]' denotes [preference/metric]
'%<string>' in via output denotes VRF <string>

0.0.0.0/0, ubest/mbest: 1/0
    *via 10.0.0.1%default, [20/0], 1w0d, bgp-65010, external, tag 65001
10.60.0.0/24, ubest/mbest: 1/0, attached
    *via 10.60.0.1, Vlan60, [0/0], 3w2d, direct
10.60.0.1/32, ubest/mbest: 1/0, attached
    *via 10.60.0.1, Vlan60, [0/0], 3w2d, local

IP Route Table for VRF "management"
'*' denotes best ucast next-hop

0.0.0.0/0, ubest/mbest: 1/0
    *via 192.168.99.1, [1/0], 3w2d, static
"""

NXOS_RIB6 = """IPv6 Routing Table for VRF "default"
'*' denotes best ucast next-hop
'**' denotes best mcast next-hop
'[x/y]' denotes [preference/metric]

::/0, ubest/mbest: 1/0
    *via 2001:db8:ff::1, Eth1/49, [1/0], 3w2d, static
2001:db8:10::/64, ubest/mbest: 1/0, attached
    *via 2001:db8:10::2, Vlan10, [0/0], 3w2d, direct,
2001:db8:10::2/128, ubest/mbest: 1/0, attached
    *via 2001:db8:10::2, Vlan10, [0/0], 3w2d, local
fe80::/10, ubest/mbest: 1/0, attached
    *via fe80::1, Vlan10, [0/0], 3w2d, local
"""

NXOS_BGP_SUMMARY = """BGP summary information for VRF default, address family IPv4 Unicast
BGP router identifier 10.255.0.11, local AS number 65010
BGP table version is 25, IPv4 Unicast config peers 2, capable peers 1
8 network entries and 10 paths using 1800 bytes of memory

Neighbor        V    AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
10.0.0.1        4 65001    1500    1490       25    0    0     3w2d 5
10.0.0.9        4 65010       0       0        0    0    0    never Idle

BGP summary information for VRF CUST-A, address family IPv4 Unicast
BGP router identifier 10.60.0.1, local AS number 65010
BGP table version is 5, IPv4 Unicast config peers 1, capable peers 1

Neighbor        V    AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
10.60.0.254     4 65060     100     100        5    0    0     1w0d 2
"""

NXOS = {
    "show ip route vrf all": NXOS_RIB,
    "show ipv6 route vrf all": NXOS_RIB6,
    "show ip bgp summary vrf all": NXOS_BGP_SUMMARY,
}

# -- Junos: mx-edge-01 mit Routing-Instanz CUST-A ---------------------------------------------


def _rt(dest, *entries):
    return {"rt-destination": _d(dest), "rt-entry": list(entries)}


def _entry(proto, pref, hops, active=True, **extra):
    out = {"protocol-name": _d(proto), "preference": _d(str(pref)), "nh": hops}
    if active:
        out["active-tag"] = _d("*")
    for key, value in extra.items():
        out[key.replace("_", "-")] = _d(value)
    return out


def _nh(to=None, via=None, local=None):
    out = {}
    if to:
        out["to"] = _d(to)
    if via:
        out["via"] = _d(via)
    if local:
        out["nh-local-interface"] = _d(local)
    return out


JUNOS_ROUTE = _json({"route-information": [{"route-table": [
    {"table-name": _d("inet.0"), "rt": [
        _rt("0.0.0.0/0",
            _entry("BGP", 170, [_nh("203.0.113.1", "xe-0/0/0.0")], local_preference="100",
                   learned_from="203.0.113.1", as_path="64511 I"),
            _entry("BGP", 170, [_nh("203.0.113.5", "xe-0/0/1.0")], active=False,
                   local_preference="90", learned_from="203.0.113.5", as_path="64512 64511 I"),
            _entry("Static", 200, [_nh("203.0.113.5", "xe-0/0/1.0")], active=False)),
        _rt("10.100.0.0/24", _entry("Direct", 0, [_nh(via="ge-0/0/2.100")])),
        _rt("10.100.0.1/32", _entry("Local", 0, [_nh(local="ge-0/0/2.100")])),
        _rt("10.200.0.0/16", _entry("OSPF", 10, [_nh("10.100.0.2", "ge-0/0/2.100"),
                                                  _nh("10.100.0.3", "ge-0/0/2.100")], metric="20")),
        _rt("203.0.113.0/30", _entry("Direct", 0, [_nh(via="xe-0/0/0.0")])),
        _rt("203.0.113.2/32", _entry("Local", 0, [_nh(local="xe-0/0/0.0")])),
    ]},
    {"table-name": _d("inet.3"), "rt": [_rt("10.255.0.9/32", _entry("LDP", 9, [_nh("10.100.0.2")]))]},
    {"table-name": _d("CUST-A.inet.0"), "rt": [
        _rt("0.0.0.0/0", _entry("Static", 5, [_nh("10.150.0.254", "ge-0/0/3.0")])),
        _rt("10.150.0.0/24", _entry("Direct", 0, [_nh(via="ge-0/0/3.0")])),
        _rt("10.150.0.1/32", _entry("Local", 0, [_nh(local="ge-0/0/3.0")])),
    ]},
    {"table-name": _d("mgmt_junos.inet.0"), "rt": [
        _rt("0.0.0.0/0", _entry("Static", 5, [_nh("192.168.99.1", "fxp0.0")])),
    ]},
    {"table-name": _d("__juniper_private1__.inet.0"), "rt": [
        _rt("10.0.0.1/32", _entry("Direct", 0, [_nh(via="lo0.16385")])),
    ]},
    {"table-name": _d("inet6.0"), "rt": [
        _rt("2001:db8:100::/64", _entry("Direct", 0, [_nh(via="ge-0/0/2.100")])),
        _rt("fe80::/64", _entry("Direct", 0, [_nh(via="ge-0/0/2.100")])),
    ]},
]}]})  # fmt: skip


def _peer(address, peer_as, local_as, kind, state, rti, local=None):
    out = {"peer-address": _d(address), "peer-as": _d(peer_as), "local-as": _d(local_as),
           "peer-type": _d(kind), "peer-state": _d(state), "peer-cfg-rti": _d(rti)}  # fmt: skip
    if local:
        out["local-address"] = _d(local)
    return out


JUNOS_BGP = _json({"bgp-information": [{"bgp-peer": [
    _peer("203.0.113.1+179", "64511", "65100", "External", "Established", "master",
          "203.0.113.2+60123"),
    _peer("203.0.113.5+179", "64512", "65100", "External", "Active", "master"),
    _peer("10.255.0.1", "65100", "65100", "Internal", "Established", "master"),
    _peer("10.150.0.254", "65150", "65100", "External", "Established", "CUST-A"),
]}]})  # fmt: skip

JUNOS = {"show route | display json | no-more": JUNOS_ROUTE,
         "show bgp neighbor | display json | no-more": JUNOS_BGP}  # fmt: skip

# -- Arista EOS: leaf-ar-01 mit eBGP zum Core und VARP-Gateway --------------------------------

EOS_RIB = _json({"vrfs": {
    "default": {"routingDisabled": False, "routes": {
        "0.0.0.0/0": {"routeType": "eBGP", "preference": 200, "metric": 0,
                      "vias": [{"nexthopAddr": "10.0.0.1", "interface": "Ethernet49/1"}]},
        "10.0.0.0/31": {"routeType": "connected", "directlyConnected": True,
                        "vias": [{"interface": "Ethernet49/1"}]},
        "10.10.0.0/24": {"routeType": "connected", "vias": [{"interface": "Vlan10"}]},
        "10.40.0.0/16": {"routeType": "iBGP", "preference": 200, "metric": 0,
                         "vias": [{"nexthopAddr": "10.255.0.31", "interface": "Ethernet50/1"},
                                  {"nexthopAddr": "10.255.0.32", "interface": "Ethernet51/1"}]},
    }},
    "MGMT": {"routes": {"0.0.0.0/0": {"routeType": "static", "preference": 1,
                                      "vias": [{"nexthopAddr": "10.255.0.254",
                                                "interface": "Management1"}]}}},
}})  # fmt: skip

EOS_RIB6 = _json({"vrfs": {"default": {"routes": {
    "2001:db8:10::/64": {"routeType": "connected", "vias": [{"interface": "Vlan10"}]},
}}}})  # fmt: skip

EOS_BGP_SUMMARY = _json({"vrfs": {"default": {"routerId": "10.255.0.21", "asn": "65021",
    "peers": {"10.0.0.1": {"asn": "65001", "peerState": "Established"},
              "10.255.0.31": {"asn": "65021", "peerState": "Established"},
              "10.255.0.40": {"asn": "1.10", "peerState": "Connect"}}}}})  # fmt: skip

EOS_ADDRESSES = _json({"interfaces": {
    "Ethernet49/1": {"name": "Ethernet49/1", "vrf": "default", "interfaceAddress": {
        "primaryIp": {"address": "10.0.0.0", "maskLen": 31}, "secondaryIpsOrderedList": [],
        "virtualIp": {"address": "0.0.0.0", "maskLen": 0}}},
    "Vlan10": {"name": "Vlan10", "vrf": "default", "interfaceAddress": {
        "primaryIp": {"address": "10.10.0.2", "maskLen": 24},
        "secondaryIpsOrderedList": [{"address": "10.11.0.2", "maskLen": 24}],
        "virtualIp": {"address": "10.10.0.1", "maskLen": 24}}},
    "Management1": {"name": "Management1", "vrf": "MGMT", "interfaceAddress": {
        "primaryIp": {"address": "10.255.0.21", "maskLen": 24}}},
}})  # fmt: skip

EOS = {
    "show ip route vrf all | json": EOS_RIB,
    "show ipv6 route vrf all | json": EOS_RIB6,
    "show ip bgp summary vrf all | json": EOS_BGP_SUMMARY,
    "show ip interface | json": EOS_ADDRESSES,
}
