"""Korrektur nach der Gegenprüfung (ADR 0071 Nachtrag): was die Collectors zusätzlich liefern.

Nur lesend, ohne neues Leserecht - alles aus Tabellen, die der Collector schon liest.
"""

from collectors.cisco_aci import mo
from collectors.fortigate import routing as fgt

SETTINGS = {"central-nat": "disable", "asymroute": "disable", "asymroute6": "enable",
            "strict-src-check": "disable"}  # fmt: skip


def test_verkehr_ohne_policy_intrazone_und_traffic_redirect():
    """P-6/P-7: `intrazone` der Zonen des VDOMs und `allow-traffic-redirect` (system global,
    gilt für alle VDOMs) stehen in den Einstellungen des VDOMs; dazu `asymroute6`."""
    zones = [{"name": "dmz", "intrazone": "allow"}, {"name": "lan", "intrazone": "deny"}]
    system = {"hostname": "fw-01", "allow-traffic-redirect": "enable",
              "ipv6-allow-traffic-redirect": "disable"}  # fmt: skip
    found = fgt.settings({"missing": [], "settings": SETTINGS}, "root", [], zones, system)
    assert found["intrazone_allow"] == ["dmz"]
    assert found["allow_traffic_redirect"] is True
    assert found["allow_traffic_redirect6"] is False
    assert found["asymroute6"] is True and found["asymroute"] is False


def test_verkehr_ohne_policy_nicht_gelesen_fehlt_der_schluessel():
    """Nicht gelesen ist nicht „aus“: ohne Zonen bzw. ohne das Feld fehlt der Schlüssel - der
    Abgleich weist die Stelle als „nicht geprüft“ aus. Keine Zonen = gelesen, keine mit allow."""
    raw = {"missing": [], "settings": SETTINGS}
    found = fgt.settings(raw, "root", [])
    assert not {"intrazone_allow", "allow_traffic_redirect", "allow_traffic_redirect6"} & set(found)
    # eine Zone ohne das Feld (andere Version): nicht gelesen
    assert "intrazone_allow" not in fgt.settings(raw, "root", [], [{"name": "dmz"}], {})
    assert fgt.settings(raw, "root", [], [], {})["intrazone_allow"] == []
    # auch ohne lesbare `system settings` (die Zonen kommen mit den Regeln)
    only = fgt.settings({"missing": ["VDOM-Einstellungen (HTTP 403)"], "settings": []}, "root",
                        [], [{"name": "dmz", "intrazone": "allow"}],
                        {"allow-traffic-redirect": "disable"})  # fmt: skip
    assert only == {"intrazone_allow": ["dmz"], "allow_traffic_redirect": False}


def test_normalize_reicht_zonen_und_system_durch():
    vdoms = [{"vdom": "root", "zones": [{"name": "dmz", "intrazone": "allow"}],
              "routing": {"missing": [], "settings": SETTINGS}}]  # fmt: skip
    inv = fgt.normalize(vdoms, None, "fw-01", [], {"allow-traffic-redirect": "enable"})
    (ctx,) = inv.contexts
    assert ctx.settings["intrazone_allow"] == ["dmz"]
    assert ctx.settings["allow_traffic_redirect"] is True


def test_p11_dienst_je_ethertype():
    """Filter ohne EtherType = `unspecified/any`, EtherType `ip` = `ip/any`; mit Protokoll wie
    bisher. Die Contract-Dicts tragen das Kennzeichen `ether_types` (Vorrang im Abgleich)."""
    assert mo._entry_service({}) == "unspecified/any"
    assert mo._entry_service({"etherT": "unspecified", "prot": "unspecified"}) == "unspecified/any"
    assert mo._entry_service({"etherT": "ip"}) == "ip/any"
    assert mo._entry_service({"etherT": "arp"}) == "arp/any"
    assert mo._entry_service({"etherT": "ip", "prot": "tcp", "dFromPort": "https",
                              "dToPort": "https"}) == "tcp/443"  # fmt: skip
