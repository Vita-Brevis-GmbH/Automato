"""VIP-Gruppen und IPv6-IP-Pools der FortiGate (0.95.0, ADR 0027 Ergänzung).

Rückmeldung des Anwenders: in NAT / VIPs fehlte die Verknüpfung der VIPs der Internet-Firewall
mit dem Load Balancer. Eine Ursache: Policies auf eine **VIP-Gruppe** (`firewall/vipgrp`)
wurden nicht aufgelöst - die Regel kannte die VIPs der Gruppe nicht. Jetzt liest der
Collector die Gruppen (optional, nur lesend) und löst sie wie Adressgruppen auf; IPv6-Pools
werden Objekte (Namenskonvention).
"""

import httpx

from collectors.fortigate import normalize as norm
from collectors.fortigate.client import FortiGateClient

VIPS = [
    {"name": "V-EXT_shop-443", "type": "static-nat", "extip": "203.0.113.10",
     "mappedip": [{"range": "10.30.0.50"}], "portforward": "enable", "extport": "443",
     "mappedport": "443", "protocol": "tcp"},
    {"name": "V-EXT_shop-80", "type": "static-nat", "extip": "203.0.113.10",
     "mappedip": [{"range": "10.30.0.50"}], "portforward": "enable", "extport": "80",
     "mappedport": "80", "protocol": "tcp"},
]  # fmt: skip
GROUPS = [
    {"name": "VIPGRP_shop", "interface": "wan1", "color": 3, "comments": "Shop",
     "member": [{"name": "V-EXT_shop-443"}, {"name": "V-EXT_shop-80"}]},
]  # fmt: skip
POLICY = {
    "policyid": 30,
    "name": "inet-to-shop",
    "action": "accept",
    "srcintf": [{"name": "wan1"}],
    "dstintf": [{"name": "dmz"}],
    "srcaddr": [{"name": "all"}],
    "dstaddr": [{"name": "VIPGRP_shop"}],
    "service": [{"name": "HTTPS"}],
}


def test_policy_auf_vip_gruppe_loest_in_die_vips_auf():
    (rule,) = norm.normalize([POLICY], [], [], [], VIPS, vipgrps=GROUPS)
    assert rule.destination_refs == ["VIPGRP_shop"]
    assert rule.destinations == [
        "vip:V-EXT_shop-443=203.0.113.10",
        "vip:V-EXT_shop-80=203.0.113.10",
    ]
    # ohne Gruppen-Tabelle (ältere Agenten, kein Recht) bleibt der Name wie bisher
    (old,) = norm.normalize([POLICY], [], [], [], VIPS)
    assert old.destinations == ["VIPGRP_shop"]


def test_vip_gruppe_und_ipv6_pool_als_objekte():
    pools6 = [{"name": "IPP_v6-inet", "type": "overload", "startip": "2001:db8::10",
               "endip": "2001:db8::1f"}]  # fmt: skip
    objs = norm.build_device_objects([], vips=VIPS, vipgrps=GROUPS, ippools6=pools6)
    by = {(o.category, o.name): o for o in objs}
    grp = by[("address_group", "VIPGRP_shop")]
    assert grp.obj_type == "vipgrp" and grp.family == "v4" and grp.color == 3
    assert grp.members == ["V-EXT_shop-443", "V-EXT_shop-80"]
    assert grp.values == ["203.0.113.10"] and grp.interface == "wan1" and grp.comment == "Shop"
    pool = by[("ippool", "IPP_v6-inet")]
    assert pool.family == "v6" and pool.values == ["2001:db8::10-2001:db8::1f"]


def test_client_liest_vip_gruppen_nur_lesend_und_optional():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # nur lesend
        path = request.url.path.removeprefix("/api/v2/cmdb/")
        seen.append(path)
        if path == "firewall/vipgrp":
            return httpx.Response(200, json={"results": GROUPS})
        if path == "firewall/vipgrp6":
            return httpx.Response(403, json={})  # ohne Recht: leer, kein Abbruch
        if path == "system/global":
            return httpx.Response(200, json={"results": {"hostname": "fw-01"}})
        return httpx.Response(200, json={"results": []})

    client = FortiGateClient(
        "https://fw-01.lab", "t", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    tables = client.vdom_tables(None)
    assert tables["vipgrps"] == GROUPS and tables["vipgrps6"] == []
    assert {"firewall/vipgrp", "firewall/vipgrp6"} <= set(seen)
