"""Namenskonvention-Parser (ADR 0004 + Ergaenzung): Default + profil-gesteuert."""

from app.models import ObjectClass
from app.naming import expects_ipam_match, parse_object_name, prefixes_from_entries


def test_host_mit_fqdn():
    p = parse_object_name("H_server.domain.info")
    assert p.object_class is ObjectClass.HOST
    assert p.fqdn == "server.domain.info"
    assert p.domain == "domain.info"
    assert expects_ipam_match(p.object_class)


def test_praefixe_werden_erkannt():
    assert parse_object_name("N_dmz01").object_class is ObjectClass.NETWORK
    assert parse_object_name("VIP_webshop.kunde.ch").object_class is ObjectClass.VIP
    assert parse_object_name("R_dhcp-pool").object_class is ObjectClass.RANGE


def test_h_ext_ist_extern_und_erwartet_kein_ipam_match():
    p = parse_object_name("H-EXT_api.partner.com")
    assert p.object_class is ObjectClass.EXTERNAL
    assert p.domain == "partner.com"
    assert expects_ipam_match(p.object_class) is False


def test_h_ext_wird_nicht_als_host_fehlinterpretiert():
    # H-EXT_ darf nicht am H_-Praefix haengenbleiben
    assert parse_object_name("H-EXT_x.y.z").object_class is ObjectClass.EXTERNAL


def test_unbekanntes_praefix_ist_unknown():
    p = parse_object_name("irgendwas_ohne_praefix")
    assert p.object_class is ObjectClass.UNKNOWN
    assert expects_ipam_match(p.object_class) is False


def test_multi_domain_disambiguierung():
    # Mehrere AD-Domaenen pro Mandant: die Domain steckt im FQDN
    assert parse_object_name("H_dc01.ad1.local").domain == "ad1.local"
    assert parse_object_name("H_dc01.ad2.local").domain == "ad2.local"


def test_erweiterte_sop_praefixe():
    # Volle SOP-Konvention wird erkannt (frueher UNKNOWN):
    assert parse_object_name("N-EXT_partnernetz").object_class is ObjectClass.EXTERNAL
    assert parse_object_name("T_transfer01").object_class is ObjectClass.NETWORK
    assert parse_object_name("V-EXT_my.testserver.de").object_class is ObjectClass.VIP
    assert parse_object_name("IPP_share-inet01").object_class is ObjectClass.NETWORK
    # N-EXT_ ist extern -> kein IPAM-Match erwartet
    assert expects_ipam_match(parse_object_name("N-EXT_x").object_class) is False


def test_prefixes_from_entries_steuert_parser():
    # Kundenspezifisches Woerterbuch: eigenes Praefix -> Klasse
    prefixes = prefixes_from_entries([("SRV_", "host"), ("NET_", "network")])
    assert parse_object_name("SRV_app01.kunde.ch", prefixes).object_class is ObjectClass.HOST
    assert parse_object_name("NET_dmz", prefixes).object_class is ObjectClass.NETWORK
    # SOP-Praefix H_ ist in diesem Profil NICHT definiert -> unknown
    assert parse_object_name("H_server", prefixes).object_class is ObjectClass.UNKNOWN


def test_leeres_profil_faellt_auf_default_zurueck():
    # Leeres/unbekanntes Woerterbuch -> SOP-Default (keine Funktionsabhaengigkeit)
    prefixes = prefixes_from_entries([("XY_", "voellig_unbekannt")])
    assert parse_object_name("H_server.kunde.ch", prefixes).object_class is ObjectClass.HOST
