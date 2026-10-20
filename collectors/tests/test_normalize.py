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
    assert eps == [{"ip": "10.20.1.50", "mac": "00:50:56:AA:BB:CC"}]
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
    assert web["endpoints"] == [{"ip": "10.1.0.10", "mac": "AA"}]
    vzany = eps["uni/tn-TE_b/ctx-VRF_b/any"]
    assert vzany["kind"] == "vzany" and vzany["vrf"] == "VRF_b" and vzany["tenant"] == "TE_b"


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
    assert taboo["subjects"] == [{"name": "TS_rdp", "services": ["tcp/3389"]}]
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
