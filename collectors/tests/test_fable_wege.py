# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Prüfung A „Wege“ (Fable): die Normalisierung der Collectors nach echtem Geräteverhalten.

Wie `orchestrator/tests/test_fable_wege.py`: jeder Test verlangt das Verhalten des echten Geräts
(Phase 1: als xfail, seit ADR 0070 behoben); Nummern wie im Bericht der Prüfung.
"""

from collectors.cisco_switch import routing as sw
from collectors.fortigate import routing as fgt


def test_a11_gleiche_distanz_andere_prioritaet_ist_kein_ecmp():
    """FortiOS hält beide Routen in der Routing-Tabelle, leitet aber nur über die mit der
    kleineren Priorität weiter; ECMP verlangt gleiche Distanz, Metrik und Priorität."""
    rows = [
        {"ip_mask": "0.0.0.0/0", "type": "static", "distance": 10, "metric": 0, "priority": 1,
         "gateway": "198.51.100.1", "interface": "wan1"},
        {"ip_mask": "0.0.0.0/0", "type": "static", "distance": 10, "metric": 0, "priority": 10,
         "gateway": "198.51.100.5", "interface": "wan2"},
    ]  # fmt: skip
    best = [r for r in fgt.rib(rows, "fw-01", "root") if r.best]
    used = {h.interface for r in best for h in r.next_hops}
    assert used == {"wan1"}


def test_gleiche_distanz_gleiche_prioritaet_bleibt_ecmp():
    """Gegenprobe (heute richtig): gleiche Distanz und Priorität = ECMP über beide."""
    rows = [
        {"ip_mask": "0.0.0.0/0", "type": "static", "distance": 10, "metric": 0, "priority": 1,
         "gateway": "198.51.100.1", "interface": "wan1"},
        {"ip_mask": "0.0.0.0/0", "type": "static", "distance": 10, "metric": 0, "priority": 1,
         "gateway": "198.51.100.5", "interface": "wan2"},
    ]  # fmt: skip
    (route,) = fgt.rib(rows, "fw-01", "root")
    assert {h.interface for h in route.next_hops} == {"wan1", "wan2"}


IOS_RIB = """\
Gateway of last resort is not set

      10.0.0.0/8 is variably subnetted, 2 subnets, 2 masks
C        10.0.0.0/24 is directly connected, Vlan10
L        10.0.0.2/32 is directly connected, Vlan10
"""
IOS_STANDBY = """\
                     P indicates configured to preempt.
                     |
Interface   Grp  Pri P State   Active          Standby         Virtual IP
Vl10        10   110 P Active  local           10.0.0.3        10.0.0.1
"""


def test_a12_ios_hsrp_adresse_ist_eigene_virtuelle_adresse():
    """Der übliche Next-Hop einer Firewall ist die HSRP-Adresse des Core-Paars. NX-OS zeigt sie
    in der Routing-Tabelle (`hsrp`, heute gelesen), IOS/IOS-XE nur in `show standby`."""
    command = "show standby brief"
    assert command in sw.COMMANDS["ios"]
    inv = sw.device("sw-core-01", "ios", {sw.IOS_RIB: IOS_RIB, command: IOS_STANDBY}, None)
    virtual = [r for r in inv.routes if r.prefix == "10.0.0.1/32" and r.protocol == "local"]
    assert virtual and virtual[0].attrs.get("virtual") is True


AVI = {
    "tenants": {
        "admin": {
            "virtualservice": [
                {
                    "uuid": "vs-1",
                    "name": "vs-shop",
                    "vsvip_ref": "https://alb.example.ch/api/vsvip/vip-1#vip-1",
                    "pool_ref": "https://alb.example.ch/api/pool/pool-1#pool-1",
                    "application_profile_ref": "https://alb.example.ch/api/applicationprofile/ap-1#ap",
                    "services": [{"port": 80}, {"port": 443, "enable_ssl": True}],
                }
            ],
            "vsvip": [{"uuid": "vip-1", "name": "vip-1",
                       "vip": [{"ip_address": {"addr": "10.30.0.50", "type": "V4"}}]}],
            "pool": [{"uuid": "pool-1", "name": "pool-1", "default_server_port": 8080,
                      "servers": [{"ip": {"addr": "10.10.0.20", "type": "V4"}}]}],
            # Anwendungsprofil mit Umleitung HTTP -> HTTPS (`http_to_https`)
            "applicationprofile": [{"uuid": "ap-1", "name": "ap",
                                    "type": "APPLICATION_PROFILE_TYPE_HTTP",
                                    "http_profile": {"http_to_https": True}}],
        }
    }
}  # fmt: skip


def test_a18_umleitung_http_https_ist_keine_weiterleitung_zum_pool():
    """Der Virtual Service beantwortet Port 80 selbst mit einer Umleitung (HTTP 30x); der Fluss
    auf Port 80 endet am Load Balancer, der Client kommt auf 443 neu. Nur 443 geht zum Pool."""
    from collectors.avi.normalize import normalize

    by_port = {n.ext_port: n for n in normalize(AVI)}
    assert by_port["443"].mapped_ip == "10.10.0.20"  # heute richtig
    http = by_port["80"]
    assert http.attrs.get("redirect") and not http.targets


# -- Phase 2 (ADR 0070): was der Collector fuer die Weg-Suche zusaetzlich liest ---------------


def test_hsrp_standby_und_vrrp_mit_zustand():
    """Der Zustand je Gruppe kommt mit (`state`): den Verkehr nimmt nur der aktive Router an;
    eine Gruppe im Zustand Init (Interface down) hat keine Adresse."""
    standby = IOS_STANDBY.replace(
        "Active  local           10.0.0.3", "Standby 10.0.0.3        local   "
    )
    inv = sw.device("sw-core-02", "ios", {sw.IOS_RIB: IOS_RIB, sw.IOS_STANDBY: standby}, None)
    (virtual,) = (r for r in inv.routes if r.prefix == "10.0.0.1/32")
    assert virtual.attrs == {"virtual": True, "state": "standby"}
    assert [h.interface for h in virtual.next_hops] == ["Vl10"]
    vrrp = """\
Interface          Grp Pri Time  Own Pre State   Master addr     Group addr
Vl10               1   100 3609       Y  Master  10.0.0.2        10.0.0.1
Vl20               2   100 3609       Y  Init    0.0.0.0         10.0.9.1
"""
    inv = sw.device("sw-core-01", "ios", {sw.IOS_RIB: IOS_RIB, sw.IOS_VRRP: vrrp}, None)
    found = {r.prefix: r.attrs for r in inv.routes if r.attrs.get("virtual")}
    assert found == {"10.0.0.1/32": {"virtual": True, "state": "active"}}


def test_policy_routen_tragen_ihre_position_in_der_liste():
    """A-5: die FortiGate prueft die Liste von oben nach unten - nach `move` weicht sie von
    `seq-num` ab; der Collector haelt die Position fest."""
    rows = [
        {"seq-num": 5, "gateway": "198.51.100.5", "output-device": "wan2"},
        {"seq-num": 2, "gateway": "198.51.100.1", "output-device": "wan1", "action": "deny"},
    ]
    found = fgt.policy_routes(rows, "fw-01", "root")
    assert [(p.seq, p.attrs["position"], p.action) for p in found] == [
        (5, 0, "permit"), (2, 1, "deny")]  # fmt: skip


def test_einstellungen_des_vdoms_und_central_snat():
    """Reverse Path und Central NAT haengen an `system settings`, `src-check` je Interface und
    `firewall central-snat-map`: gelesen je VDOM, nur ueber die Positivliste."""
    raw = {
        "missing": [],
        "settings": {"central-nat": "enable", "asymroute": "disable",
                     "strict-src-check": "enable", "vdom": "root"},
        "central_snat": [
            {"policyid": 3, "status": "enable", "srcintf": [{"name": "lan"}],
             "dstintf": [{"name": "wan1"}], "orig-addr": [{"name": "N_lan"}],
             "dst-addr": [{"name": "all"}], "nat": "enable",
             "nat-ippool": [{"name": "IPP_out"}], "protocol": 0, "uuid": "x"},
        ],
    }  # fmt: skip
    ifaces = [{"name": "port9", "vdom": "root", "src-check": "disable"},
              {"name": "lan", "vdom": "root", "src-check": "enable"},
              {"name": "other", "vdom": "kunde", "src-check": "disable"}]  # fmt: skip
    found = fgt.settings(raw, "root", ifaces)
    assert found == {
        "central_nat": True, "asymroute": False, "asymroute6": False, "strict_src_check": True,
        "src_check_off": ["port9"],
        "central_snat": [{"id": 3, "status": "enable", "srcintf": ["lan"], "dstintf": ["wan1"],
                          "orig_addr": ["N_lan"], "dst_addr": ["all"], "nat": "enable",
                          "nat_ippool": ["IPP_out"]}],
    }  # fmt: skip
    # nicht gelesen (kein Recht, aeltere Version): leer - die Weg-Suche sagt „nicht geprueft“
    assert fgt.settings({"missing": ["VDOM-Einstellungen (HTTP 403)"], "settings": []}, "root",
                        ifaces) == {}  # fmt: skip
    # Central NAT an, die Tabelle aber nicht lesbar: kein `central_snat` (= nicht geprueft)
    unread = {"missing": ["Central SNAT (HTTP 403)"], "settings": raw["settings"],
              "central_snat": []}  # fmt: skip
    assert "central_snat" not in fgt.settings(unread, "root", ifaces)
    inv = fgt.normalize([{"vdom": "root", "routing": raw}], None, "fw-01", ifaces)
    assert inv.contexts[0].settings["central_nat"] is True


def test_umleitung_nur_mit_ssl_port_und_profil():
    """Ohne Umleitung im Profil bzw. ohne SSL-Port bleibt Port 80 eine Weiterleitung zum Pool."""
    import copy

    from collectors.avi.normalize import normalize

    plain = copy.deepcopy(AVI)
    plain["tenants"]["admin"]["applicationprofile"][0]["http_profile"] = {}
    assert all(n.targets and not n.attrs.get("redirect") for n in normalize(plain))
    only80 = copy.deepcopy(AVI)
    only80["tenants"]["admin"]["virtualservice"][0]["services"] = [{"port": 80}]
    (n,) = normalize(only80)
    assert n.targets and not n.attrs.get("redirect")
