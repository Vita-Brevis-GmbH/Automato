"""Normalisierung beider Plattformen gegen die Beispieldaten."""

import json
from importlib import resources

from collectors.cisco_aci.normalize import normalize_contract
from collectors.cisco_aci.tasks import fetch_contracts
from collectors.fortigate import normalize as norm


def _sample(name: str):
    return json.loads(resources.files("collectors.fortigate").joinpath(name).read_text())


def _fortigate_rules():
    return norm.normalize(
        _sample("sample_policies.json"),
        _sample("sample_addresses.json"),
        _sample("sample_addrgrps.json"),
        _sample("sample_services.json"),
    )


def test_fortigate_adressen_werden_zu_werten_aufgeloest():
    rules = {r.external_id: r for r in _fortigate_rules()}
    assert len(rules) == 4

    r1 = rules["1"]
    assert r1.action == "allow"
    assert r1.src_zone == "port2"
    assert r1.sources == ["10.10.20.0/24"]  # N_dmz-net -> CIDR
    assert r1.destinations == ["10.10.10.5"]  # H_web01 /32 -> Host-IP
    assert r1.services == ["tcp/443"]  # Built-in HTTPS aufgeloest

    r2 = rules["2"]
    assert r2.action == "deny"
    assert r2.destinations == ["all"]
    assert r2.services == ["all"]


def test_fortigate_fqdn_wird_typisiert_und_custom_service():
    r3 = {r.external_id: r for r in _fortigate_rules()}["3"]
    # FQDN-Objekt -> typisierter Wert (Orchestrator: expected_external, kein Fehler)
    assert r3.destinations == ["fqdn:api.partner.example"]
    # Custom-Service aufgeloest
    assert r3.services == ["tcp/8443"]


def test_fortigate_resolve_address_typen():
    amap = {
        "IFSUB": {
            "name": "IFSUB",
            "type": "interface-subnet",
            "subnet": "172.25.15.0 255.255.255.0",
        },
        "RNG": {
            "name": "RNG",
            "type": "iprange",
            "start-ip": "172.26.0.100",
            "end-ip": "172.26.0.150",
        },
        "FQ": {"name": "FQ", "type": "fqdn", "fqdn": "ntp.example"},
        "GEO": {"name": "GEO", "type": "geography", "country": "CH"},
        "MC": {"name": "MC", "type": "mac"},
        "DYN": {"name": "DYN", "type": "dynamic"},
    }
    assert norm.resolve_address("IFSUB", amap) == ["172.25.15.0/24"]  # echtes Subnetz
    assert norm.resolve_address("RNG", amap) == ["range:172.26.0.100-172.26.0.150"]
    assert norm.resolve_address("FQ", amap) == ["fqdn:ntp.example"]
    assert norm.resolve_address("GEO", amap) == ["geo:GEO"]
    assert norm.resolve_address("MC", amap) == ["mac:MC"]
    assert norm.resolve_address("DYN", amap) == ["dynamic:DYN"]
    # VIP (nicht in der Adress-Tabelle) wird ueber die VIP-Map zu externer IP + Name
    assert norm.resolve_address("VIP_web", {}, {"VIP_web": "203.0.113.10"}) == [
        "vip:VIP_web=203.0.113.10"
    ]


def test_fortigate_normalize_nat():
    vips = [
        {
            "name": "VIP_web01",
            "extip": "203.0.113.10",
            "mappedip": [{"range": "10.10.10.5"}],
            "extport": "443",
            "mappedport": "8443",
            "protocol": "tcp",
            "portforward": "enable",
        }
    ]
    nat = norm.normalize_nat(vips)
    assert len(nat) == 1
    n = nat[0]
    assert n.name == "VIP_web01"
    assert n.ext_ip == "203.0.113.10"
    assert n.mapped_ip == "10.10.10.5"
    assert n.ext_port == "443"
    assert n.mapped_port == "8443"
    assert n.protocol == "tcp"
    assert n.portforward is True


def test_aci_contract_wird_auf_regeln_abgebildet():
    contracts = fetch_contracts()
    rules = [r for c in contracts for r in normalize_contract(c)]
    assert len(rules) == 1

    rule = rules[0]
    assert rule.source_platform == "cisco_aci"
    assert rule.sources == ["epg:prod/web"]  # EPGs typisiert (epg:)
    assert rule.destinations == ["epg:prod/db"]
    assert rule.services == ["tcp/3306"]
    assert rule.external_id == "uni/tn-prod/brc-web-to-db/mysql"


def test_fortigate_ipv6_adresse_wird_aufgeloest():
    amap = {"H6": {"name": "H6", "type": "ipmask", "ip6": "2a07:c180:100:b032::1:26/128"}}
    assert norm.resolve_address("H6", amap) == ["2a07:c180:100:b032::1:26"]
    amap2 = {"N6": {"name": "N6", "type": "ipprefix", "ip6": "2a07:c180:100:a000::/56"}}
    assert norm.resolve_address("N6", amap2) == ["2a07:c180:100:a000::/56"]


def test_fortigate_dual_stack_policy_und_protections():
    policies = [
        {
            "policyid": 9,
            "name": "v6-web",
            "action": "accept",
            "srcintf": [{"name": "wan"}],
            "dstintf": [{"name": "dmz"}],
            "srcaddr6": [{"name": "N6"}],
            "dstaddr6": [{"name": "H6"}],
            "service": [{"name": "HTTPS"}],
            "utm-status": "enable",
            "ips-sensor": "IPS_Media",
            "ssl-ssh-profile": "SSL_Media",
        }
    ]
    addr6 = [
        {"name": "N6", "type": "ipprefix", "ip6": "2a07:c180:100:a000::/56"},
        {"name": "H6", "type": "ipmask", "ip6": "2a07:c180:100:b032::1:26/128"},
    ]
    r = norm.normalize(policies, [], [], [], None, addr6, [])[0]
    assert r.sources == ["2a07:c180:100:a000::/56"]
    assert r.destinations == ["2a07:c180:100:b032::1:26"]
    assert r.protections == ["IPS_Media", "SSL_Media"]


def test_fortigate_normalize_snat():
    nat = norm.normalize_snat([{"name": "IPP_x", "startip": "1.1.100.168", "endip": "1.1.100.168"}])
    assert len(nat) == 1
    assert nat[0].nat_type == "snat_pool"
    assert nat[0].ext_ip == "1.1.100.168"
    assert nat[0].mapped_ip is None
    assert (nat[0].family, nat[0].attrs) == ("v4", {"type": "overload"})


def test_fortigate_snat_pool_typen_und_ipv6():
    """0.47.0: Typ des Pools, interner Bereich bei fixed-port-range, IPv6-Pools."""
    pools = [{"name": "IPP_fpr", "type": "fixed-port-range", "startip": "198.51.100.10",
              "endip": "198.51.100.11", "source-startip": "10.10.0.0",
              "source-endip": "10.10.0.255"}]  # fmt: skip
    pools6 = [{"name": "IPP6_x", "startip": "2001:db8::100", "endip": "2001:db8::1ff"}]
    fpr, v6 = norm.normalize_snat(pools, pools6)
    assert (fpr.ext_ip, fpr.family) == ("198.51.100.10-198.51.100.11", "v4")
    assert fpr.attrs == {"type": "fixed-port-range", "source": "10.10.0.0-10.10.0.255"}
    assert (v6.name, v6.family, v6.ext_ip) == ("IPP6_x", "v6", "2001:db8::100-2001:db8::1ff")


def test_fortigate_object_catalog_farbe_und_wert():
    cat = {
        o.name: o
        for o in norm.build_object_catalog(
            [
                {
                    "name": "H_a",
                    "type": "ipmask",
                    "subnet": "10.0.0.5 255.255.255.255",
                    "color": 13,
                },
                {"name": "H-EXT_x", "type": "fqdn", "fqdn": "x.example", "color": 9},
            ],
            [{"name": "H6", "type": "ipmask", "ip6": "2a07::1/128"}],
        )
    }
    assert cat["H_a"].color == 13
    assert cat["H_a"].value == "10.0.0.5"
    assert cat["H-EXT_x"].color == 9
    assert cat["H-EXT_x"].value is None  # fqdn -> kein IP-Wert im Katalog
    assert cat["H6"].value == "2a07::1"


def test_aci_mo_export_wird_geparst_und_normalisiert():
    from collectors.cisco_aci.mo import parse_mo_export
    from collectors.cisco_aci.tasks import fetch_mo_export

    contracts = parse_mo_export(fetch_mo_export())
    assert len(contracts) == 1
    c = contracts[0]
    assert c["name"] == "CT_web"
    assert c["consumer_epgs"] == ["EG_web"]
    assert c["provider_epgs"] == ["EG_db"]
    # EPG-zu-EPG: keine L3Out-Zone, auch wenn BD_app an L3O_inet gebunden ist (Dev-Befund)
    assert c["src_zone"] is None and c["dst_zone"] is None

    rules = [r for c in contracts for r in normalize_contract(c)]
    assert len(rules) == 1
    rule = rules[0]
    assert rule.sources == ["epg:uni/tn-TE_share/ap-AP_001/epg-EG_web"]  # DN = eindeutig
    assert rule.destinations == ["epg:uni/tn-TE_share/ap-AP_001/epg-EG_db"]
    assert c["consumers"] == [
        {
            "kind": "epg",
            "tenant": "TE_share",
            "name": "EG_web",
            "dn": "uni/tn-TE_share/ap-AP_001/epg-EG_web",
            "ap": "AP_001",
            "bd": "BD_app",
        }
    ]
    assert rule.services == ["tcp/3306"]
    assert rule.src_zone is None and rule.dst_zone is None
    assert rule.external_id == "uni/tn-TE_share/brc-CT_web/SJ_mysql"


def test_aci_parse_epgs_und_scope():
    from collectors.cisco_aci.mo import parse_epgs, parse_mo_export
    from collectors.cisco_aci.tasks import fetch_mo_export

    export = fetch_mo_export()
    epgs = {e["name"]: e for e in parse_epgs(export)}
    assert set(epgs) == {"EG_web", "EG_db", "EN_inet"}
    assert epgs["EG_web"]["ap"] == "AP_001" and epgs["EG_web"]["tenant"] == "TE_share"
    assert epgs["EG_web"]["bd"] == "BD_app" and epgs["EG_web"]["vrf"] == "VRF_share"
    assert epgs["EG_web"]["subnets"] == ["10.20.1.1/24"]
    assert epgs["EG_web"]["bd_subnets"] == ["10.20.0.1/24"]  # Gateway der Bridge Domain
    ext = epgs["EN_inet"]
    assert ext["kind"] == "external" and ext["l3out"] == "L3O_inet"
    assert ext["subnets"] == ["0.0.0.0/0"]

    # Contract-Scope landet an der normalisierten Regel
    from collectors.cisco_aci.normalize import normalize_contract

    rules = [r for c in parse_mo_export(export) for r in normalize_contract(c)]
    assert rules[0].aci_scope == "context"


def test_aci_parse_epgs_gelernte_endpunkte():
    from collectors.cisco_aci.mo import parse_epgs
    from collectors.cisco_aci.tasks import fetch_mo_export

    epgs = {e["name"]: e for e in parse_epgs(fetch_mo_export())}
    eps = epgs["EG_web"]["endpoints"]
    assert eps == [{"ip": "10.20.1.50", "mac": "00:50:56:AA:BB:CC", "origin": "learned"}]
    assert epgs["EG_db"]["endpoints"] == []


def test_fortigate_regeln_tragen_objektnamen_und_gruppen():
    rules = {r.external_id: r for r in _fortigate_rules_mit_gruppen()}
    r4 = rules["4"]
    assert r4.source_refs == ["N_dmz-net"] and r4.destination_refs == ["G_web-servers"]
    assert r4.service_refs == ["SG_web"]
    assert r4.destinations == ["10.10.10.5", "10.10.10.0/24"]  # Gruppe aufgeloest
    assert r4.services == ["tcp/443", "tcp/8443"]


def _fortigate_rules_mit_gruppen():
    return norm.normalize(
        _sample("sample_policies.json"),
        _sample("sample_addresses.json"),
        _sample("sample_addrgrps.json"),
        _sample("sample_services.json"),
        service_groups=_sample("sample_service_groups.json"),
    )


def test_device_objects_wie_auf_der_firewall():
    objs = {
        (o.category, o.name): o
        for o in norm.build_device_objects(
            _sample("sample_addresses.json"),
            _sample("sample_addrgrps.json"),
            _sample("sample_addresses6.json"),
            [],
            _sample("sample_vips.json"),
            _sample("sample_ippools.json"),
            _sample("sample_services.json"),
            _sample("sample_service_groups.json"),
        )
    }
    web = objs[("address", "H_web01")]
    assert web.value == "10.10.10.5" and web.values == ["10.10.10.5"] and web.family == "v4"
    grp = objs[("address_group", "G_web-servers")]
    assert grp.members == ["H_web01", "N_server-net"] and grp.comment == "Webserver DMZ"
    assert grp.values == ["10.10.10.5", "10.10.10.0/24"]
    assert objs[("address", "H-EXT_partner-api")].values == ["fqdn:api.partner.example"]
    assert objs[("vip", "VIP_web01")].values == ["203.0.113.10", "10.10.10.5"]
    assert objs[("ippool", "IPP_share-000-inet01")].values == ["1.1.100.168"]
    assert objs[("service_group", "SG_web")].values == ["tcp/443", "tcp/8443"]
    assert objs[("address", "N_share-000-v6")].family == "v6"


def test_dynamische_adresse_mit_sdn_connector():
    """0.36.0: dynamische Adressen (z. B. ACI über FortiManager) tragen den SDN-Connector und
    den Filter als Attribute (Positivliste) - für den Typ-Filter in der Objekt-Liste."""
    dyn = {"name": "TE_web_epg", "type": "dynamic", "sub-type": "sdn", "sdn": "aci-fmg",
           "filter": "EPG=TE_web", "comment": "", "uuid": "x", "list": []}  # fmt: skip
    (obj,) = norm.build_device_objects([dyn], [], [], [], [], [], [], [])
    assert (obj.obj_type, obj.values) == ("dynamic", ["dynamic:TE_web_epg"])
    assert obj.attrs == {"sub-type": "sdn", "sdn": "aci-fmg", "filter": "EPG=TE_web"}
    (plain,) = norm.build_device_objects(
        [{"name": "H_a", "type": "ipmask", "subnet": "10.0.0.1 255.255.255.255"}],
        [], [], [], [], [], [], [],
    )  # fmt: skip
    assert plain.attrs == {}


def test_service_mit_mehreren_portbereichen():
    smap = norm.build_service_map(
        [{"name": "MULTI", "tcp-portrange": "443 8443-8445:1024-65535", "udp-portrange": "53"}]
    )
    assert smap["MULTI"] == ["tcp/443", "tcp/8443-8445", "udp/53"]


def _mo(cls: str, name: str = "", children=(), **attrs) -> dict:
    a = {"name": name, **attrs} if name else dict(attrs)
    return {cls: {"attributes": a, "children": list(children)}}


def _epg(name, bd=None, cons=(), prov=(), cons_if=(), endpoints=()):
    kids = [_mo("fvRsBd", tnFvBDName=bd)] if bd else []
    kids += [_mo("fvRsCons", tnVzBrCPName=c) for c in cons]
    kids += [_mo("fvRsProv", tnVzBrCPName=c) for c in prov]
    kids += [_mo("fvRsConsIf", tnVzCPIfName=c) for c in cons_if]
    kids += [_mo("fvCEp", mac=m, ip=ip) for ip, m in endpoints]
    return _mo("fvAEPg", name, kids)


def _contract(name, flt, scope="context"):
    subj = _mo("vzSubj", "SJ_1", [_mo("vzRsSubjFiltAtt", tnVzFilterName=flt)])
    return _mo("vzBrCP", name, [subj], scope=scope)


def _filter(name, port):
    return _mo("vzFilter", name, [_mo("vzEntry", "e", prot="tcp", dFromPort=port, dToPort=port)])


MULTI_TENANT = [
    _mo(
        "fvTenant",
        "common",
        [
            _contract("CT_dns", "FT_dns"),
            _filter("FT_dns", "53"),
            _mo("fvAp", "AP_infra", [_epg("EG_dns", prov=["CT_dns"])]),
        ],
    ),
    _mo(
        "fvTenant",
        "TE_a",
        [
            _mo("fvCtx", "VRF_a"),
            _mo(
                "fvBD",
                "BD_web",
                [_mo("fvRsCtx", tnFvCtxName="VRF_a"), _mo("fvSubnet", ip="10.1.0.1/24")],
            ),
            _contract("CT_web", "FT_web"),
            _filter("FT_web", "443"),
            _mo("vzCPIf", "IF_glob", [_mo("vzRsIf", tDn="uni/tn-TE_b/brc-CT_glob")]),
            _mo(
                "fvAp",
                "AP_100",
                [
                    _epg("EG_app", "BD_web", cons=["CT_dns"], cons_if=["IF_glob"]),
                    _epg("EG_web", "BD_web", prov=["CT_web"], endpoints=[("10.1.0.10", "AA")]),
                ],
            ),
            _mo(
                "l3extOut",
                "L3O_inet",
                [
                    _mo("l3extRsEctx", tnFvCtxName="VRF_a"),
                    _mo(
                        "l3extInstP",
                        "EN_inet",
                        [
                            _mo("fvRsCons", tnVzBrCPName="CT_web"),
                            _mo("l3extSubnet", ip="0.0.0.0/0"),
                        ],
                    ),
                ],
            ),
        ],
    ),
    _mo(
        "fvTenant",
        "TE_b",
        [
            _contract("CT_web", "FT_web"),  # gleicher Name wie in TE_a, anderer Contract
            _filter("FT_web", "8443"),
            _contract("CT_glob", "FT_web", scope="global"),
            _mo(
                "fvCtx",
                "VRF_b",
                [_mo("vzAny", children=[_mo("vzRsAnyToCons", tnVzBrCPName="CT_web")])],
            ),
            _mo(
                "fvAp",
                "AP_200",
                [_epg("EG_app", prov=["CT_web", "CT_glob"])],  # gleicher EPG-Name wie in TE_a
            ),
        ],
    ),
]


def test_aci_mehrere_tenants_gleiche_namen_common_extern_vzany_interface():
    """Dev-Befund (25 ACI-Tenants): Identitaet per DN, Aufloesung eigener Tenant -> common."""
    from collectors.cisco_aci.mo import parse_epgs, parse_mo_export

    by_dn = {c["dn"]: c for c in parse_mo_export(MULTI_TENANT)}
    dns = lambda side: [e["dn"] for e in side]  # noqa: E731

    web_a = by_dn["uni/tn-TE_a/brc-CT_web"]
    assert dns(web_a["providers"]) == ["uni/tn-TE_a/ap-AP_100/epg-EG_web"]
    assert dns(web_a["consumers"]) == ["uni/tn-TE_a/out-L3O_inet/instP-EN_inet"]  # extern
    assert web_a["consumers"][0]["kind"] == "external" and web_a["src_zone"] == "L3O_inet"
    assert web_a["subjects"][0]["services"] == ["tcp/443"]  # Filter aus TE_a, nicht TE_b

    web_b = by_dn["uni/tn-TE_b/brc-CT_web"]
    assert dns(web_b["providers"]) == ["uni/tn-TE_b/ap-AP_200/epg-EG_app"]
    assert dns(web_b["consumers"]) == ["uni/tn-TE_b/ctx-VRF_b/any"]  # vzAny der VRF
    assert web_b["subjects"][0]["services"] == ["tcp/8443"]

    dns_common = by_dn["uni/tn-common/brc-CT_dns"]  # TE_a verweist per Name auf common
    assert dns(dns_common["consumers"]) == ["uni/tn-TE_a/ap-AP_100/epg-EG_app"]
    assert dns(dns_common["providers"]) == ["uni/tn-common/ap-AP_infra/epg-EG_dns"]
    assert dns_common["subjects"][0]["services"] == ["tcp/53"]
    # EPG zu EPG: keine Zone (nur externe EPGs liegen hinter einem L3Out)
    assert dns_common["src_zone"] is None and dns_common["dst_zone"] is None

    glob = by_dn["uni/tn-TE_b/brc-CT_glob"]  # ueber das Contract-Interface aus TE_a
    assert dns(glob["consumers"]) == ["uni/tn-TE_a/ap-AP_100/epg-EG_app"]

    rules = {r.external_id: r for c in by_dn.values() for r in normalize_contract(c)}
    assert rules["uni/tn-TE_a/brc-CT_web/SJ_1"].destinations == [
        "epg:uni/tn-TE_a/ap-AP_100/epg-EG_web"
    ]

    eps = {e["dn"]: e for e in parse_epgs(MULTI_TENANT)}
    assert len([e for e in eps.values() if e["name"] == "EG_app"]) == 2  # nicht zusammengelegt
    web = eps["uni/tn-TE_a/ap-AP_100/epg-EG_web"]
    assert web["vrf"] == "VRF_a" and web["bd_subnets"] == ["10.1.0.1/24"]
    assert web["endpoints"] == [{"ip": "10.1.0.10", "mac": "AA", "origin": "learned"}]
    vzany = eps["uni/tn-TE_b/ctx-VRF_b/any"]
    assert vzany["kind"] == "vzany" and vzany["vrf"] == "VRF_b" and vzany["tenant"] == "TE_b"


def test_aci_shared_services_vrf_aufgeloest_und_route_leaking():
    """0.44.0: Scope der BD-/EPG-Subnetze (`shared` = zwischen VRFs geteilt), der Tenant der
    VRF aus dem aufgeloesten Ziel (`tDn`; BD in TE_c mit der VRF aus common) und das
    Route-Leaking einer VRF (`leakRoutes`)."""
    from collectors.cisco_aci.mo import parse_epgs, parse_leaks

    leak = _mo("leakRoutes", children=[
        _mo("leakInternalSubnet", ip="10.3.0.0/24", scope="public",
            children=[_mo("leakTo", tenantName="TE_d", ctxName="VRF_d")]),
        _mo("leakExternalPrefix", ip="0.0.0.0/0",
            children=[_mo("leakTo", ctxName="VRF_c2")]),
    ])  # fmt: skip
    export = [
        _mo("fvTenant", "common", [_mo("fvCtx", "VRF_shared")]),
        _mo("fvTenant", "TE_c", [
            _mo("fvCtx", "VRF_c", [leak]),
            _mo("fvBD", "BD_c", [
                _mo("fvRsCtx", tnFvCtxName="VRF_shared", tDn="uni/tn-common/ctx-VRF_shared"),
                _mo("fvSubnet", ip="10.3.0.1/24", scope="public,shared"),
            ]),
            _mo("fvAp", "AP_1", [_mo("fvAEPg", "EG_c", [
                _mo("fvRsBd", tnFvBDName="BD_c"),
                _mo("fvSubnet", ip="10.3.0.10/32", scope="private,shared"),
            ])]),
        ]),
    ]  # fmt: skip
    (epg,) = (e for e in parse_epgs(export) if e["name"] == "EG_c")
    assert (epg["vrf"], epg["vrf_tenant"]) == ("VRF_shared", "common")
    assert epg["subnet_scopes"] == {"10.3.0.10/32": "private,shared"}
    assert epg["bd_subnet_scopes"] == {"10.3.0.1/24": "public,shared"}
    assert parse_leaks(export) == [
        {"vrf": "TE_c:VRF_c", "prefix": "10.3.0.0/24", "to": ["TE_d:VRF_d"], "external": False},
        {"vrf": "TE_c:VRF_c", "prefix": "0.0.0.0/0", "to": ["TE_c:VRF_c2"], "external": True},
    ]
    # ohne tDn wie der APIC: eigener Tenant, sonst common
    eps = {e["dn"]: e for e in parse_epgs(MULTI_TENANT)}
    assert eps["uni/tn-TE_a/ap-AP_100/epg-EG_web"]["vrf_tenant"] == "TE_a"
    assert eps["uni/tn-TE_b/ctx-VRF_b/any"]["vrf_tenant"] == "TE_b"


def test_aci_zone_nur_wenn_die_ganze_seite_extern_ist():
    """Dev-Befund: eine VPN-EPG konsumiert viele Contracts mit - L3O_vpn nur, wenn alle
    Consumer dahinter liegen."""
    from collectors.cisco_aci.mo import _zone

    vpn = {"kind": "external", "l3out": "L3O_vpn"}
    inet = {"kind": "external", "l3out": "L3O_inet"}
    epg = {"kind": "epg"}
    assert _zone([vpn, epg]) is None and _zone([epg, vpn]) is None  # gemischt
    assert _zone([vpn, dict(vpn)]) == "L3O_vpn"  # nur externe EPGs desselben L3Out
    assert _zone([vpn, inet]) is None and _zone([]) is None and _zone([epg]) is None

    export = [
        _mo(
            "fvTenant",
            "TE_a",
            [
                _contract("CT_web", "FT_web"),
                _filter("FT_web", "443"),
                _mo(
                    "fvAp",
                    "AP_1",
                    [_epg("EG_app", cons=["CT_web"]), _epg("EG_web", prov=["CT_web"])],
                ),
                _mo(
                    "l3extOut",
                    "L3O_vpn",
                    [_mo("l3extInstP", "EN_vpn", [_mo("fvRsCons", tnVzBrCPName="CT_web")])],
                ),
            ],
        )
    ]
    from collectors.cisco_aci.mo import parse_mo_export

    (c,) = parse_mo_export(export)
    assert {e["name"] for e in c["consumers"]} == {"EG_app", "EN_vpn"}
    assert c["src_zone"] is None and c["dst_zone"] is None


ESG_TABOO_PREF = [
    _mo(
        "fvTenant",
        "TE_a",
        [
            _mo("fvCtx", "VRF_a", [_mo("vzAny", prefGrMemb="enabled")]),
            _mo("fvBD", "BD_app", [_mo("fvRsCtx", tnFvCtxName="VRF_a")]),
            _mo(
                "vzTaboo",
                "TB_noadmin",
                [_mo("vzTSubj", "TS_rdp", [_mo("vzRsDenyRule", tnVzFilterName="FT_rdp")])],
            ),
            _filter("FT_rdp", "3389"),
            _mo(
                "fvAp",
                "AP_1",
                [
                    _mo(
                        "fvAEPg",
                        "EG_web",
                        [
                            _mo("fvRsBd", tnFvBDName="BD_app"),
                            _mo("fvRsProtBy", tnVzTabooName="TB_noadmin"),
                        ],
                        prefGrMemb="include",
                    ),
                    _mo(
                        "fvAEPg",
                        "EG_db",
                        [_mo("fvRsBd", tnFvBDName="BD_app")],
                        prefGrMemb="exclude",
                    ),
                    _mo(
                        "fvESg",
                        "ES_pci",
                        [
                            _mo("fvRsScope", tnFvCtxName="VRF_a"),
                            _mo("fvTagSelector", matchKey="zone", matchValue="pci"),
                            _mo("fvEPSelector", matchExpression="ip=='10.9.0.0/24'"),
                            _mo("fvEPgSelector", matchEpgDn="uni/tn-TE_a/ap-AP_1/epg-EG_db"),
                        ],
                        prefGrMemb="include",
                    ),
                ],
            ),
        ],
    )
]


def test_aci_esg_taboo_und_preferred_group():
    from collectors.cisco_aci.mo import parse_epgs, parse_mo_export

    by_kind = {}
    for c in parse_mo_export(ESG_TABOO_PREF):
        by_kind.setdefault(c["kind"], []).append(c)

    (taboo,) = by_kind["taboo"]
    assert taboo["dn"] == "uni/tn-TE_a/taboo-TB_noadmin"
    assert taboo["consumers"] == [] and taboo["provider_epgs"] == ["EG_web"]
    assert taboo["subjects"] == [
        {"name": "TS_rdp", "services": ["tcp/3389"], "filter_refs": ["TE_a/FT_rdp"]}
    ]
    (deny,) = normalize_contract(taboo)
    assert deny.action == "deny" and deny.sources == ["any"]
    assert deny.destinations == ["epg:uni/tn-TE_a/ap-AP_1/epg-EG_web"]

    (pref,) = by_kind["preferred_group"]
    assert pref["name"] == "Preferred Group VRF_a" and pref["dn"] == "uni/tn-TE_a/ctx-VRF_a/any"
    assert sorted(pref["consumer_epgs"]) == ["EG_web", "ES_pci"]  # EG_db: exclude
    (rule,) = normalize_contract(pref)
    assert rule.name == "Preferred Group VRF_a" and rule.services == ["ip/any"]
    assert sorted(rule.sources) == sorted(rule.destinations)

    epgs = {e["name"]: e for e in parse_epgs(ESG_TABOO_PREF)}
    esg = epgs["ES_pci"]
    assert (esg["kind"], esg["ap"], esg["vrf"]) == ("esg", "AP_1", "VRF_a")
    assert esg["selectors"] == [
        "Tag zone=pci",
        "IP ip=='10.9.0.0/24'",
        "EPG uni/tn-TE_a/ap-AP_1/epg-EG_db",
    ]
    assert esg["subnets"] == ["10.9.0.0/24"] and esg["pref_group"]
    assert epgs["EG_web"]["pref_group"] and not epgs["EG_db"]["pref_group"]
    assert not epgs["vzAny"]["pref_group"]  # Schalter der VRF, kein Mitglied


def test_aci_preferred_group_aus_ohne_regel():
    from collectors.cisco_aci.mo import parse_mo_export

    export = [
        _mo(
            "fvTenant",
            "TE_a",
            [
                _mo("fvCtx", "VRF_a", [_mo("vzAny", prefGrMemb="disabled")]),
                _mo("fvAp", "AP_1", [_mo("fvAEPg", "EG_web", prefGrMemb="include")]),
            ],
        )
    ]
    assert parse_mo_export(export) == []


def test_aci_contract_master_und_intra_epg():
    """Dev-Befund (Regeln ohne Quelle/Ziel): geerbte Contracts (Contract Master) und
    Intra-EPG-Contracts fuellen Consumer/Provider."""
    from collectors.cisco_aci.mo import parse_mo_export

    master_dn = "uni/tn-TE/ap-AP_1/epg-EG_master"
    export = [
        _mo(
            "fvTenant",
            "TE",
            [
                _contract("CT_app", "FT_web"),
                _contract("CT_intra", "FT_web"),
                _contract("CT_db", "FT_web"),
                _filter("FT_web", "443"),
                _mo(
                    "fvAp",
                    "AP_1",
                    [
                        _epg("EG_master", cons=["CT_app"]),
                        # erbt CT_app vom Master, sonst kein Contract
                        _mo("fvAEPg", "EG_child", [_mo("fvRsSecInherited", tDn=master_dn)]),
                        _epg("EG_web", prov=["CT_app"]),
                        _mo("fvAEPg", "EG_cluster", [_mo("fvRsIntraEpg", tnVzBrCPName="CT_intra")]),
                        _epg("EG_db", prov=["CT_db"]),  # niemand konsumiert CT_db
                    ],
                ),
            ],
        )
    ]
    by_name = {c["name"]: c for c in parse_mo_export(export)}
    names = lambda side: sorted(e["name"] for e in side)  # noqa: E731
    assert names(by_name["CT_app"]["consumers"]) == ["EG_child", "EG_master"]
    assert names(by_name["CT_intra"]["consumers"]) == ["EG_cluster"]
    assert names(by_name["CT_intra"]["providers"]) == ["EG_cluster"]
    assert by_name["CT_db"]["consumers"] == []  # bleibt leer -> im Orchestrator "nicht wirksam"


def test_fortigate_internet_service_identitaet_und_ztna():
    """Dev-Befund: Policies mit Internet Service (ISDB) bzw. Benutzergruppen hatten keine
    Quelle/kein Ziel."""
    from collectors.fortigate.normalize import normalize

    policies = [
        {
            "policyid": 7,
            "name": "o365",
            "action": "accept",
            "srcaddr": [{"name": "N_clients"}],
            "dstaddr": [],
            "service": [],
            "internet-service": "enable",
            "internet-service-name": [{"name": "Microsoft-Office365"}],
            "groups": [{"name": "VPN_Users"}],
            "ztna-ems-tag": [{"name": "EMS_compliant"}],
        },
        {
            "policyid": 8,
            "name": "from-isdb",
            "action": "accept",
            "srcaddr": [],
            "internet-service-src": "enable",
            "internet-service-src-name": [{"name": "Google-DNS"}],
            "dstaddr": [{"name": "all"}],
            "service": [{"name": "DNS"}],
        },
    ]
    addresses = [{"name": "N_clients", "type": "ipmask", "subnet": "10.5.0.0 255.255.0.0"}]
    o365, from_isdb = normalize(policies, addresses, [], [])
    assert o365.destination_refs == ["Microsoft-Office365"]
    assert o365.destinations == ["isdb:Microsoft-Office365"]
    assert o365.services == ["internet-service"]
    assert o365.source_refs == ["N_clients", "VPN_Users", "EMS_compliant"]
    assert o365.sources == ["10.5.0.0/16", "usergroup:VPN_Users", "dynamic:EMS_compliant"]
    assert from_isdb.sources == ["isdb:Google-DNS"] and from_isdb.services == ["udp/53", "tcp/53"]


# IPv4- und IPv6-Objekte gleichen Namens (Dev-Befund): je Familie eigenes Objekt
ADDR4 = [{"name": "H_web01", "type": "ipmask", "subnet": "10.10.10.5 255.255.255.255"}]
ADDR6 = [{"name": "H_web01", "type": "ipmask", "ip6": "2001:db8:10::5/128"}]
GRP4 = [{"name": "G_web", "member": [{"name": "H_web01"}]}]
GRP6 = [{"name": "G_web", "member": [{"name": "H_web01"}]}]


def test_fortigate_gleichnamige_ipv4_ipv6_objekte_je_familie():
    from collectors.fortigate.normalize import build_device_objects, normalize

    policies = [
        {"policyid": 1, "name": "web-in", "action": "accept", "srcaddr": [{"name": "all"}],
         "dstaddr": [{"name": "G_web"}], "service": [{"name": "HTTPS"}]},
        {"policyid": 2, "name": "web-in-v6", "action": "accept", "srcaddr6": [{"name": "all"}],
         "dstaddr6": [{"name": "G_web"}], "service": [{"name": "HTTPS"}]},
        {"policyid": 3, "name": "web-dual", "action": "accept", "srcaddr": [{"name": "all"}],
         "dstaddr": [{"name": "H_web01"}], "srcaddr6": [{"name": "all"}],
         "dstaddr6": [{"name": "H_web01"}], "service": [{"name": "HTTPS"}]},
    ]  # fmt: skip
    v4, v6, dual = normalize(policies, ADDR4, GRP4, [], None, ADDR6, GRP6)
    assert (v4.family, v4.destinations, v4.destination_refs6) == ("v4", ["10.10.10.5"], [])
    assert v6.family == "v6" and v6.destinations == ["2001:db8:10::5"]
    assert v6.destination_refs == [] and v6.destination_refs6 == ["G_web"]
    assert dual.family == "dual"
    assert dual.destinations == ["10.10.10.5", "2001:db8:10::5"]
    assert dual.destination_refs == ["H_web01"] and dual.destination_refs6 == ["H_web01"]

    objs = build_device_objects(ADDR4, GRP4, ADDR6, GRP6)
    by_key = {(o.category, o.family, o.name): o for o in objs}
    assert by_key[("address", "v4", "H_web01")].values == ["10.10.10.5"]
    assert by_key[("address", "v6", "H_web01")].values == ["2001:db8:10::5"]
    assert by_key[("address_group", "v6", "G_web")].values == ["2001:db8:10::5"]
    assert by_key[("address_group", "v4", "G_web")].values == ["10.10.10.5"]


def test_fortigate_policy6_tabelle():
    """FortiOS 6.x: IPv6-Policies in firewall/policy6 - eigene IDs, alles IPv6."""
    from collectors.fortigate.normalize import normalize

    p6 = [{"policyid": 1, "name": "web-in", "action": "accept", "srcaddr": [{"name": "all"}],
           "dstaddr": [{"name": "H_web01"}], "service": [{"name": "HTTPS"}]}]  # fmt: skip
    (rule,) = normalize([], ADDR4, [], [], None, ADDR6, [], None, p6)
    assert rule.external_id == "v6-1" and rule.family == "v6"
    assert rule.destinations == ["2001:db8:10::5"] and rule.destination_refs6 == ["H_web01"]


SLB_VIP = {
    "name": "V-EXT_shop.example.ch",
    "type": "server-load-balance",
    "extip": "203.0.113.20",
    "extintf": "wan1",
    "extport": "443",
    "server-type": "https",
    "ldb-method": "round-robin",
    "persistence": "http-cookie",
    "monitor": [{"name": "HC_https"}],
    "ssl-certificate": [{"name": "CERT_shop"}],
    "realservers": [
        {"id": 1, "ip": "10.10.30.11", "port": 8443, "status": "active", "weight": 1,
         "healthcheck": "vip", "max-connections": 0},
        {"id": 2, "ip": "10.10.30.12", "port": 8443, "status": "standby", "weight": 2,
         "monitor": [{"name": "HC_tcp"}]},
        {"id": 3, "type": "address", "address": "H-EXT_backup", "ip": "0.0.0.0", "port": 8443},  # noqa: S104 - FortiOS-Platzhalter
    ],
}  # fmt: skip


def test_fortigate_virtual_server_mit_real_servern_und_health_checks():
    from collectors.fortigate.normalize import build_device_objects, normalize, normalize_nat

    monitors = [{"name": "HC_https", "type": "https", "port": 8443, "interval": 10,
                 "http-get": "/health", "q_origin_key": "x"}]  # fmt: skip
    vip6 = [
        {"name": "VIP6_web", "extip": "2001:db8:ff::10", "mappedip": [{"range": "2001:db8:10::5"}]}
    ]
    slb, static6 = normalize_nat([SLB_VIP], vip6, monitors)
    assert (slb.vip_type, slb.family, slb.ext_ip, slb.ext_port) == (
        "server-load-balance",
        "v4",
        "203.0.113.20",
        "443",
    )
    assert (slb.mapped_ip, slb.mapped_port, slb.protocol) == ("10.10.30.11", "8443", "https")
    assert [t.get("ip") or t.get("address") for t in slb.targets] == [
        "10.10.30.11",
        "10.10.30.12",
        "H-EXT_backup",
    ]
    assert slb.targets[1]["status"] == "standby" and slb.targets[1]["monitor"] == ["HC_tcp"]
    assert "max_connections" not in slb.targets[0]  # 0 = unbegrenzt, kein Attribut
    assert slb.attrs["ldb-method"] == "round-robin" and slb.attrs["persistence"] == "http-cookie"
    assert slb.attrs["ssl-certificate"] == ["CERT_shop"]
    assert slb.attrs["monitor"] == [
        {"name": "HC_https", "type": "https", "port": 8443, "interval": 10, "http-get": "/health"}
    ]
    assert (static6.family, static6.mapped_ip, static6.vip_type) == (
        "v6",
        "2001:db8:10::5",
        "static-nat",
    )

    objs = {o.name: o for o in build_device_objects([], vips=[SLB_VIP], vips6=vip6)}
    assert objs["V-EXT_shop.example.ch"].values == ["203.0.113.20", "10.10.30.11", "10.10.30.12"]
    assert objs["VIP6_web"].family == "v6" and objs["VIP6_web"].values[0] == "2001:db8:ff::10"

    policy = {"policyid": 1, "name": "shop6", "action": "accept", "srcaddr6": [{"name": "all"}],
              "dstaddr6": [{"name": "VIP6_web"}], "service": [{"name": "HTTPS"}]}  # fmt: skip
    (rule,) = normalize([policy], [], [], [], [SLB_VIP], [], [], None, None, vip6)
    assert rule.destinations == ["vip:VIP6_web=2001:db8:ff::10"]


def test_aci_scope_der_subnetze_externer_epgs():
    """0.28.0: `l3extSubnet.scope` - nur import-security ordnet Verkehr der externen EPG zu;
    ohne Attribut gilt der APIC-Standard (kein Eintrag)."""
    from collectors.cisco_aci.mo import parse_epgs

    export = [
        _mo("fvTenant", "TE_a", [
            _mo("l3extOut", "L3O_fw", [
                _mo("l3extRsEctx", tnFvCtxName="VRF_a"),
                _mo("l3extInstP", "EN_srv", [
                    _mo("l3extSubnet", ip="10.20.0.0/24", scope="import-security,shared-rtctrl"),
                    _mo("l3extSubnet", ip="10.20.0.0/16", scope="export-rtctrl"),
                    _mo("l3extSubnet", ip="10.30.0.0/16"),
                ]),
            ]),
        ]),
    ]  # fmt: skip
    (ext,) = parse_epgs(export)
    assert ext["subnets"] == ["10.20.0.0/24", "10.20.0.0/16", "10.30.0.0/16"]
    assert ext["subnet_scopes"] == {
        "10.20.0.0/24": "import-security,shared-rtctrl",
        "10.20.0.0/16": "export-rtctrl",
    }


def test_aci_statische_und_vmm_endpunkte():
    """0.46.0 (Konfiguration vor Gelerntem): statischer Endpunkt (fvStCEp/fvStIp) und
    Lebenszyklus des gelernten (`lcC`: static, vmm, learned) - je IP die staerkste Herkunft."""
    from collectors.cisco_aci.mo import parse_epgs

    epg = _mo("fvAEPg", "EG_s", [
        _mo("fvStCEp", mac="AA", ip="10.9.0.7", children=[_mo("fvStIp", addr="10.9.0.8")]),
        _mo("fvCEp", mac="AA", ip="10.9.0.7", lcC="learned"),  # dieselbe IP: statisch bleibt
        _mo("fvCEp", mac="BB", ip="10.9.0.5", lcC="learned,vmm"),
        _mo("fvCEp", mac="CC", ip="10.9.0.6", lcC="learned"),
    ])  # fmt: skip
    export = [_mo("fvTenant", "TE", [_mo("fvAp", "AP", [epg])])]
    (e,) = parse_epgs(export)
    assert {x["ip"]: x["origin"] for x in e["endpoints"]} == {
        "10.9.0.7": "static", "10.9.0.8": "static", "10.9.0.5": "vmm", "10.9.0.6": "learned",
    }  # fmt: skip


def test_aci_zustandslos_dienste_je_richtung():
    """0.69.1 (Anwender): ACI ist zustandslos. Filter ohne Ports oeffnen auch den Weg vom
    Provider zum Consumer; mit Zielport (tcp/443) zurueck nur Antworten (Quellport 443)."""
    from collectors.cisco_aci.mo import parse_mo_export

    def flt(name, **entry):
        return _mo("vzFilter", name, [_mo("vzEntry", "e", **entry)])

    def subj(name, kids, **attrs):
        return _mo("vzSubj", name, kids, **attrs)

    both = lambda *fs: [_mo("vzRsSubjFiltAtt", tnVzFilterName=f) for f in fs]  # noqa: E731
    term = lambda cls, *fs: _mo(cls, "", [_mo("vzRsFiltAtt", tnVzFilterName=f) for f in fs])  # noqa: E731
    export = [_mo("fvTenant", "TE", [
        flt("FT_any", etherT="unspecified"),
        flt("FT_https", prot="tcp", dFromPort="https", dToPort="https"),
        flt("FT_ret", prot="tcp", sFromPort="443", sToPort="443"),
        flt("FT_ssh", prot="tcp", dFromPort="22", dToPort="22"),
        flt("FT_tcp_st", prot="tcp", stateful="yes"),
        flt("FT_icmp", prot="icmp"),
        _mo("vzBrCP", "CT_x", [
            subj("SJ_both", both("FT_https", "FT_any")),
            subj("SJ_norev", both("FT_https"), revFltPorts="no"),
            subj("SJ_terms", [term("vzInTerm", "FT_https"), term("vzOutTerm", "FT_ret", "FT_ssh")]),
            subj("SJ_state", both("FT_tcp_st", "FT_icmp")),
        ]),
        _mo("fvAp", "AP", [_epg("EG_c", cons=["CT_x"]), _epg("EG_p", prov=["CT_x"])]),
    ])]  # fmt: skip
    (c,) = parse_mo_export(export)
    subjects = {s["name"]: s for s in c["subjects"]}
    assert subjects["SJ_both"]["services"] == ["tcp/443", "ip/any"]
    assert subjects["SJ_both"]["reverse"] == ["ip/any"]  # tcp/443 zurueck: nur Antworten
    assert subjects["SJ_norev"]["reverse"] == ["tcp/443"]  # ohne Vertauschen: auch zurueck
    # je Richtung: Out-Term nur Provider -> Consumer (Antwort mit Quellport 443, SSH)
    assert subjects["SJ_terms"]["services"] == ["tcp/443"]
    assert subjects["SJ_terms"]["reverse"] == ["tcp/22"]
    # stateful: TCP zurueck nur mit ACK - ICMP bleibt offen
    assert subjects["SJ_state"]["services"] == ["tcp/any", "icmp/any"]
    assert subjects["SJ_state"]["reverse"] == ["icmp/any"]
    rules = {r.external_id.rsplit("/", 1)[-1]: r for r in normalize_contract(c)}
    assert rules["SJ_both"].raw["reverse"] == ["ip/any"]
    assert rules["SJ_both"].raw["subject"] == "SJ_both"
    assert rules["SJ_terms"].services == ["tcp/443"]
