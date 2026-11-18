# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Prüfung A „Wege“ (Fable, Phase 1): Abweichungen in der Normalisierung der Collectors.

Wie `orchestrator/tests/test_fable_wege.py`: jeder Test verlangt das Verhalten des echten Geräts
und schlägt heute fehl (`xfail(strict=True)`); Nummern wie im Bericht.
"""

import pytest

from collectors.cisco_switch import routing as sw
from collectors.fortigate import routing as fgt


@pytest.mark.xfail(
    strict=True,
    reason="Fable A-11: FortiGate-Routen gleicher Distanz mit verschiedener Priorität werden "
    "zu ECMP zusammengefasst",
)
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


@pytest.mark.xfail(
    strict=True,
    reason="Fable A-12: IOS/IOS-XE - HSRP-/VRRP-Adressen werden nicht gelesen, ein Next-Hop "
    "auf die virtuelle Adresse endet am „unbekannten“ Gerät",
)
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


@pytest.mark.xfail(
    strict=True,
    reason="Fable A-18 (Stichprobe Fall 3): Umleitung HTTP -> HTTPS des Load Balancers wird "
    "nicht gelesen - Port 80 gilt als Weiterleitung zum Pool",
)
def test_a18_umleitung_http_https_ist_keine_weiterleitung_zum_pool():
    """Der Virtual Service beantwortet Port 80 selbst mit einer Umleitung (HTTP 30x); der Fluss
    auf Port 80 endet am Load Balancer, der Client kommt auf 443 neu. Nur 443 geht zum Pool."""
    from collectors.avi.normalize import normalize

    by_port = {n.ext_port: n for n in normalize(AVI)}
    assert by_port["443"].mapped_ip == "10.10.0.20"  # heute richtig
    http = by_port["80"]
    assert http.attrs.get("redirect") and not http.targets
