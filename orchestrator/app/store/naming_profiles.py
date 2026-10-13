"""Namensprofil-Store: Wörterbücher pro Kunde (ADR 0004-Ergänzung).

Lesen/Schreiben der anwendungsverwalteten, erweiterbaren Wörterbücher. `kind` ist ein
offener String; `upsert_entry` aktualisiert einen bestehenden (profile, kind, key)
statt zu duplizieren. `seed_sop_profile` legt das SOP-Profil + bekannte Werte idempotent
an (menschenlesbare Referenz: `docs/naming-conventions.md`).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DictionaryEntry, NamingProfile

# Bekannte SOP-Werte als Seed (erweiterbar in der UI). Keine Vollständigkeit -
# Kap. 3.1/3.2 (GG-/Projektlisten) werden per CSV nachgeseedet.
_SOP_SEED: dict[str, list[tuple[str, str]]] = {
    "object_prefix": [
        ("H_", "host"),
        ("H-EXT_", "host_external"),
        ("N_", "network"),
        ("N-EXT_", "network_external"),
        ("T_", "transfer"),
        ("V-EXT_", "vip_dnat"),
        ("IPP_", "snat_pool"),
    ],
    "utm_prefix": [
        ("AV_", "antivirus"),
        ("WF_", "webfilter"),
        ("DF_", "dnsfilter"),
        ("AC_", "application_control"),
        ("IPS_", "ips"),
        ("WAF_", "waf"),
        ("SSL_", "ssl_inspection"),
    ],
    "color_code": [
        ("13", "sop_host"),
        ("16", "sop_network"),
        ("18", "swisscom_host"),
        ("2", "swisscom_network"),
        ("6", "mpls_host"),
        ("4", "mpls_network"),
        ("23", "vpn_host"),
        ("21", "vpn_network"),
        ("9", "extern_host"),
        ("8", "extern_network"),
    ],
    "l3out": [
        ("L3O_inet", "internet"),
        ("L3O_res", "ressourcen"),
        ("L3O_vpn", "vpn"),
        ("L3O_bit", "unternehmensnetz"),
        ("L3O_sra", "remote_access"),
    ],
    "gg_code": [
        ("res01", "0"),
        ("res02", "1"),
        ("ssgch", "2"),
        ("casch", "3"),
        ("test1", "4"),
        ("test2", "5"),
        ("test3", "6"),
    ],
    "function_code": [
        ("app", "Applikationsserver"),
        ("web", "Webserver"),
        ("dbs", "Datenbankserver"),
        ("fwl", "Firewall"),
        ("fwc", "Firewall-Cluster"),
        ("lba", "Loadbalancer"),
        ("orc", "Orchestrator"),
        ("ddi", "IPAM/DNS/DHCP"),
        ("vho", "VMware-Host"),
    ],
    "site_code": [
        ("000", "RZ01 / Hauptstandort"),
    ],
    "service": [
        ("FT_https", "tcp/443"),
        ("FT_http", "tcp/80"),
        ("FT_ssh", "tcp/22"),
        ("FT_dns", "tcp+udp/53"),
        ("FT_rdp", "tcp/3389"),
        ("FT_mssql", "tcp+udp/1433:1434"),
        ("FT_ldap", "tcp+udp/389"),
        ("FT_ldapssl", "tcp/636"),
        ("FT_smtp", "tcp/25"),
        ("FT_ntp", "tcp+udp/123"),
        ("FT_snmp", "udp/161"),
        ("FT_syslog", "udp/514"),
    ],
}


def list_profiles(session: Session) -> list[NamingProfile]:
    return list(session.scalars(select(NamingProfile).order_by(NamingProfile.id)))


def get_profile(session: Session, profile_id: int) -> NamingProfile | None:
    return session.get(NamingProfile, profile_id)


def get_or_create_profile(session: Session, customer: str, name: str) -> NamingProfile:
    stmt = select(NamingProfile).where(
        NamingProfile.customer == customer, NamingProfile.name == name
    )
    profile = session.scalars(stmt).first()
    if profile is None:
        profile = NamingProfile(customer=customer, name=name)
        session.add(profile)
        session.flush()
    return profile


def list_entries(
    session: Session, profile_id: int, kind: str | None = None
) -> list[DictionaryEntry]:
    stmt = select(DictionaryEntry).where(DictionaryEntry.profile_id == profile_id)
    if kind is not None:
        stmt = stmt.where(DictionaryEntry.kind == kind)
    return list(session.scalars(stmt.order_by(DictionaryEntry.kind, DictionaryEntry.key)))


def upsert_entry(
    session: Session,
    profile_id: int,
    kind: str,
    key: str,
    value: str | None = None,
    extra: dict | None = None,
) -> DictionaryEntry:
    stmt = select(DictionaryEntry).where(
        DictionaryEntry.profile_id == profile_id,
        DictionaryEntry.kind == kind,
        DictionaryEntry.key == key,
    )
    entry = session.scalars(stmt).first()
    if entry is None:
        entry = DictionaryEntry(profile_id=profile_id, kind=kind, key=key, value=value, extra=extra)
        session.add(entry)
    else:
        entry.value = value
        entry.extra = extra
    session.flush()
    return entry


def delete_entry(session: Session, entry_id: int) -> bool:
    entry = session.get(DictionaryEntry, entry_id)
    if entry is None:
        return False
    session.delete(entry)
    return True


def seed_sop_profile(session: Session) -> NamingProfile:
    """Legt das SOP-Profil + bekannte Wörterbuchwerte idempotent an."""
    profile = get_or_create_profile(session, customer="SOP", name="Standard")
    for kind, pairs in _SOP_SEED.items():
        for key, value in pairs:
            upsert_entry(session, profile.id, kind, key, value)
    return profile
