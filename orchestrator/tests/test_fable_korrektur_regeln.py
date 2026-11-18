"""Korrektur nach der Gegenprüfung (ADR 0071 Nachtrag, P-3, P-4, P-6, P-7, P-11).

Die Tests der Gegenprüfung selbst stehen in `test_fable_pruefung.py` (ohne `xfail`); hier die
Gegenproben der Korrektur: was nicht gelesen ist, bleibt ausgewiesen und gezählt - nie still.

Beispiele anonymisiert (RFC 1918, „Kunde A“).
"""

import copy

import test_aci_check as ac
import test_aci_relevance as ar
import test_fable_pruefung as fp
import test_fable_regeln as fr
import test_fable_wege as fw
import test_paths as tp
import test_policy_check as pc
from test_fable_wege import _active, _ctx, _firewall, _if, _r, _tenant, _trace

READ = fp.READ


# -- P-6: intrazone -----------------------------------------------------------------------------


def _zone_fw(client, rules, **settings) -> int:
    tid = _tenant(client)
    extra = {"contexts": _ctx("fw-01", **settings)} if settings else {}
    _firewall(client, tid, "fw-01", [
        _if("fw-01", "dmz1", "10.1.1.1/24", "dmz"),
        _if("fw-01", "dmz2", "10.1.2.1/24", "dmz"),
        _if("fw-01", "lan", "10.20.0.1/24", "lan"),
    ], [
        _r("fw-01", "root", "10.1.1.0/24", [{"interface": "dmz1"}], "connected"),
        _r("fw-01", "root", "10.1.2.0/24", [{"interface": "dmz2"}], "connected"),
        _r("fw-01", "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
    ], rules=rules, **extra)  # fmt: skip
    return tid


def _hop(client, tid, **params):
    out = _trace(client, tid, **params)
    (path,) = _active(out)
    return path["hops"][0]["rules"], out


LAN_DMZ = fw._policy(1, "lan", "dmz", "all", "all")


def test_p6_intrazone_deny_bleibt_implizit_gesperrt(client):
    """Gelesen und nicht `allow` (Vorgabe `deny`): ohne Policy gesperrt, nichts unsicher."""
    tid = _zone_fw(client, [LAN_DMZ], **READ, intrazone_allow=[])
    rules, out = _hop(client, tid, src="10.1.1.5", dst="10.1.2.5", service="tcp/443")
    assert rules["verdict"] == "implicit_deny" and "no_policy" not in rules
    assert "no_policy" not in (out.get("uncertain") or {})


def test_p6_intrazone_nicht_gelesen_ist_ausgewiesen_und_gezaehlt(client):
    """Lauf eines älteren Collectors: das Urteil bleibt (gesperrt), die Stelle ist „nicht
    geprüft“ - am Hop, als Hinweis am Weg und gezählt."""
    tid = _zone_fw(client, [LAN_DMZ], **READ)
    rules, out = _hop(client, tid, src="10.1.1.5", dst="10.1.2.5", service="tcp/443")
    assert rules["verdict"] == "implicit_deny"
    assert rules["no_policy"] == {"kind": "intrazone", "state": "unchecked", "name": "dmz"}
    assert "intrazone" in rules["text"]
    assert out["uncertain"]["no_policy"] == 1
    assert any("intrazone" in n for n in out["notes"])


def test_p6_policy_entscheidet_vor_intrazone_allow(client):
    """FortiOS: erst die Policies, dann `intrazone`, dann die implizite Sperre - eine sperrende
    Policy Zone → Zone gilt auch mit `intrazone allow`; ein anderer Dienst geht ohne Policy."""
    deny = fw._policy(2, "dmz", "dmz", "all", "all", "tcp/22") | {"action": "deny"}
    tid = _zone_fw(client, [deny, LAN_DMZ], **READ, intrazone_allow=["dmz"])
    rules, _out = _hop(client, tid, src="10.1.1.5", dst="10.1.2.5", service="tcp/22")
    assert rules["verdict"] == "deny" and "no_policy" not in rules
    rules, _out = _hop(client, tid, src="10.1.1.5", dst="10.1.2.5", service="tcp/443")
    assert rules["verdict"] == "allow" and rules["no_policy"]["state"] == "allowed"
    # ohne Dienst: die Sperre ist nur möglich - abhängig, sonst erlaubt ohne Policy
    rules, _out = _hop(client, tid, src="10.1.1.5", dst="10.1.2.5")
    assert rules["verdict"] == "depends" and "intrazone allow" in rules["text"]


def test_p6_andere_zone_braucht_weiter_eine_policy(client):
    tid = _zone_fw(client, [LAN_DMZ], **READ, intrazone_allow=["dmz"])
    rules, _out = _hop(client, tid, src="10.1.1.5", dst="10.20.0.5", service="tcp/443")
    assert rules["verdict"] == "implicit_deny" and "no_policy" not in rules


# -- P-7: allow-traffic-redirect ----------------------------------------------------------------


def _one_arm(client, **settings) -> int:
    tid = _tenant(client)
    extra = {"contexts": _ctx("fw-01", **settings)} if settings else {}
    _firewall(client, tid, "fw-01", [
        _if("fw-01", "lan", "10.20.0.1/24", "lan"),
        _if("fw-01", "dmz1", "10.1.1.1/24", "dmz"),
    ], [
        _r("fw-01", "root", "10.20.0.0/24", [{"interface": "lan"}], "connected"),
        _r("fw-01", "root", "10.1.1.0/24", [{"interface": "dmz1"}], "connected"),
        _r("fw-01", "root", "10.99.0.0/24", [{"ip": "10.20.0.254", "interface": "lan"}]),
    ], rules=[LAN_DMZ], **extra)  # fmt: skip
    return tid


def test_p7_traffic_redirect_aus_braucht_eine_policy(client):
    tid = _one_arm(client, **READ, allow_traffic_redirect=False)
    rules, out = _hop(client, tid, src="10.20.0.5", dst="10.99.0.5", service="tcp/443")
    assert rules["verdict"] == "implicit_deny" and "no_policy" not in rules
    assert "no_policy" not in (out.get("uncertain") or {})


def test_p7_traffic_redirect_nicht_gelesen_ist_abhaengig_und_gezaehlt(client):
    """Die Vorgabe hängt an der FortiOS-Version (ein bis 7.4.9/7.6.4, danach aus): ohne die
    gelesene Einstellung weder „gesperrt“ noch „erlaubt“ - abhängig, ausgewiesen, gezählt."""
    tid = _one_arm(client, **READ)
    rules, out = _hop(client, tid, src="10.20.0.5", dst="10.99.0.5", service="tcp/443")
    assert rules["verdict"] == "depends"
    assert rules["no_policy"] == {"kind": "redirect", "state": "unchecked", "name": "lan"}
    assert "allow-traffic-redirect" in rules["text"]
    assert out["uncertain"]["no_policy"] == 1


def test_p7_erlaubt_ohne_policy_ist_im_ergebnis_ausgewiesen(client):
    tid = _one_arm(client, **READ, allow_traffic_redirect=True)
    rules, out = _hop(client, tid, src="10.20.0.5", dst="10.99.0.5", service="tcp/443")
    assert rules["verdict"] == "allow" and rules["rules"] == []
    assert rules["no_policy"] == {"kind": "redirect", "state": "allowed", "name": "lan"}
    assert "no_policy" not in (out.get("uncertain") or {})


# -- P-3 / P-11: ACI-Vorrang --------------------------------------------------------------------


def _pair(client, rules, service="tcp/8080", name="Kunde A"):
    apic = copy.deepcopy(ac.APIC)
    apic["rules"] += rules
    tid = client.post("/tenants", json={"name": name}, headers=tp.ADMIN).json()["id"]
    for system, platform, result in (("fw-01", "fortigate", ac.FW1),
                                     ("apic-01", "cisco_aci", apic)):  # fmt: skip
        tp._deliver(client, tid, tp._system(client, tid, system, platform), platform, result)
    found, _policy, _out = ac._aci(client, tid, src="10.50.1.10", dst="10.50.2.20",
                                   service=service)  # fmt: skip
    return found


def _any_any(services, **raw):
    rule = ac._rule("CT_all", [f"epg:{ac.ANY}"], [f"epg:{ac.ANY}"], services)
    rule["raw"] |= raw
    return rule


def test_p3_mitglieder_der_preferred_group_bleiben_erlaubt(client):
    found = _pair(client, [fp._pref_group([ar.WEB, ac.DB])])
    assert found["verdict"] == "allow", found


def test_p3_sperre_nennt_die_gruppe_und_den_verdraengten_contract(client):
    found = _pair(client, [fp._pref_group([ar.WEB]), _any_any(["ip/any"])])
    assert found["verdict"] == "deny"
    assert "Preferred Group VRF1" in found["text"] and "CT_all" in found["text"]
    decisive = next(r for r in found["rules"] if r["decides"])
    assert decisive["kind"] == "preferred_group" and decisive["action"] == "deny"


def test_p3_ohne_offenen_contract_bleibt_der_text_kein_contract(client):
    """Nichtmitglied ohne Contract, der sonst erlaubt hätte: wie bisher „kein Contract“ bzw.
    „öffnet den Dienst nicht“ (implizit gesperrt) - die Sperre der Gruppe ändert nichts."""
    found = _pair(client, [fp._pref_group([ar.WEB])], service="tcp/8080")
    assert found["verdict"] == "implicit_deny", found
    assert "Preferred Group" not in found["text"]


def test_p3_contract_zwischen_den_epgs_gewinnt(client):
    """EPG ↔ EPG (Priorität 7) vor der Sperre für das Nichtmitglied (18/19)."""
    found = _pair(client, [fp._pref_group([ar.WEB])], service="tcp/1433")
    assert found["verdict"] == "allow", found  # CT_db: EPG_web -> EPG_db tcp/1433


def test_p11_filter_mit_ethertype_ip_hat_die_hoehere_stufe(client):
    """vzAny ↔ vzAny: mit EtherType `ip` Priorität 17 - vor der Sperre der Preferred Group
    (18/19); ohne EtherType (`unspecified/any`) 21 - dahinter."""
    group = fp._pref_group([ar.WEB])
    found = _pair(client, [group, _any_any(["ip/any"], ether_types=True)])
    assert found["verdict"] == "allow", found
    found = _pair(client, [group, _any_any(["unspecified/any"], ether_types=True)], name="Kunde B")
    assert found["verdict"] == "deny", found


def test_p11_stufe_des_passenden_filters(client):
    """Contract mit bestimmtem und offenem Filter: für tcp/8080 gilt die Stufe des bestimmten
    Filters (17, erlaubt vor der Sperre), für tcp/23 nur der offene (21, gesperrt)."""
    rules = [fp._pref_group([ar.WEB]),
             _any_any(["tcp/8080", "unspecified/any"], ether_types=True)]  # fmt: skip
    assert _pair(client, rules, service="tcp/8080")["verdict"] == "allow"
    assert _pair(client, rules, service="tcp/23", name="Kunde B")["verdict"] == "deny"


# -- P-4: belegter Teil des Wegs ----------------------------------------------------------------


def _s2_with(client, inet_routes):
    """S2, die Internet-Firewall mit zusätzlichen Routen."""
    fwa = fp._fw_a(
        extra_ifs=[{"name": "mpls", "ips": ["10.250.5.1/29"], "zone": "mpls", "vdom": "root"}],
        extra_routes=[
            {"prefix": "10.250.5.0/29", "protocol": "connected",
             "next_hops": [{"interface": "mpls"}]},
            {"prefix": "10.90.0.0/16", "protocol": "bgp", "distance": 20,
             "next_hops": fp.S2_MPLS},
        ], rules=(fp.S1_RULE, fp.S2_RULE))  # fmt: skip
    inet = pc._fw("fw-inet", [copy.deepcopy(fp.S2_RULE)], [
        {"name": "to-fw-a", "ips": ["10.250.9.2/30"], "zone": "transit", "vdom": "root"},
        {"name": "wan1", "ips": ["198.51.100.2/30"], "zone": "internet", "vdom": "root"},
    ], [
        {"context": "root", "prefix": "10.250.9.0/30", "protocol": "connected",
         "next_hops": [{"interface": "to-fw-a"}]},
        {"context": "root", "prefix": "198.51.100.0/30", "protocol": "connected",
         "next_hops": [{"interface": "wan1"}]},
        {"context": "root", "prefix": "0.0.0.0/0", "protocol": "static",
         "next_hops": [{"ip": "198.51.100.1", "interface": "wan1"}]},
        *inet_routes,
    ])  # fmt: skip
    return fp._seed_all(client, [("apic-01", "cisco_aci", fp.S1_APIC),
                                 ("fw-a", "fortigate", fwa),
                                 ("fw-inet", "fortigate", inet)])  # fmt: skip


def _doc_systems(client, tid):
    doc = fr._doc(client, tid, "10.41.6.30")
    by_id = {r["id"]: r for r in doc["rules"]}
    return doc, {by_id[h["rule_id"]]["source_system_id"]: h for h in doc["hits"]}


def test_p4_firewall_auf_dem_belegten_teil_ohne_kennzeichen_abseits_gezaehlt(client):
    tid, ids = _s2_with(client, [])
    doc, hits = _doc_systems(client, tid)
    assert list(hits) == [ids["fw-a"]]
    assert not hits[ids["fw-a"]].get("_path_unchecked")
    assert doc["summary"]["path_unchecked"] == 0
    counted = {x["key"]: x["count"] for x in doc["summary"]["filtered"]}
    assert counted.get("off_path_specific") == 1  # fw-inet: gezählt, nie still


def test_p4_firewall_mit_eigener_route_zum_ziel_bleibt_markiert(client):
    """Kennt die Firewall abseits das Ziel über eine eigene Route (nicht die Default-Route),
    kann sie hinter dem nicht gelesenen Übergang liegen: die Regel bleibt, „Weg nicht geprüft“."""
    tid, ids = _s2_with(client, [
        {"context": "root", "prefix": "10.90.0.0/16", "protocol": "static",
         "next_hops": [{"ip": "198.51.100.1", "interface": "wan1"}]},
    ])  # fmt: skip
    doc, hits = _doc_systems(client, tid)
    assert sorted(hits) == sorted([ids["fw-a"], ids["fw-inet"]])
    assert not hits[ids["fw-a"]].get("_path_unchecked")
    assert hits[ids["fw-inet"]].get("_path_unchecked")
    assert doc["summary"]["path_unchecked"] == 1
