"""Namenskonvention-Parser (ADR 0004 + Ergaenzung).

Objektnamen tragen ein Praefix, das die Objektklasse signalisiert:
  H_server.domain.info   -> Host,        fqdn=server.domain.info, domain=domain.info
  N_...                   -> Netzwerk
  V-EXT_... / VIP_...     -> VIP (DNAT)
  H-EXT_ / N-EXT_...      -> extern (nicht im IPAM erwartet)
  T_ / IPP_ / R_...       -> Transfernetz / SNAT-Pool / Range

Die Praefix-Liste ist **profil-gesteuert**: der Parser nimmt eine Liste
`(prefix, ObjectClass)` entgegen, die aus dem in-App pflegbaren Woerterbuch
`object_prefix` eines Namensprofils gebaut wird (`prefixes_from_entries`). Ohne Profil
gilt die SOP-Default-Konvention (`_DEFAULT_PREFIXES`) - die Plattform funktioniert also
auch ohne befuellte Woerterbuecher (keine Funktionsabhaengigkeit). Reine Funktion, ohne DB/IO.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from app.models.network_object import ObjectClass

# Woerterbuch-Wert (kind="object_prefix") -> Objektklasse. Offen erweiterbar; unbekannte
# Werte werden beim Bauen der Praefix-Liste ignoriert (kein Absturz).
_VALUE_TO_CLASS: dict[str, ObjectClass] = {
    "host": ObjectClass.HOST,
    "host_external": ObjectClass.EXTERNAL,
    "network": ObjectClass.NETWORK,
    "network_external": ObjectClass.EXTERNAL,  # N-EXT_: extern, kein IPAM-Match erwartet
    "transfer": ObjectClass.NETWORK,  # T_: Transfernetz
    "vip": ObjectClass.VIP,
    "vip_dnat": ObjectClass.VIP,  # V-EXT_: DNAT-VIP
    "snat_pool": ObjectClass.NETWORK,  # IPP_: Adressbereich
    "range": ObjectClass.RANGE,
}

# SOP-Default-Konvention als (prefix, value), falls kein Profil vorliegt. Werte wie im
# Woerterbuch; die Reihenfolge ist egal (longest-first wird beim Bauen erzwungen).
_DEFAULT_ENTRIES: list[tuple[str, str]] = [
    ("H_", "host"),
    ("H-EXT_", "host_external"),
    ("N_", "network"),
    ("N-EXT_", "network_external"),
    ("T_", "transfer"),
    ("V-EXT_", "vip_dnat"),
    ("VIP_", "vip"),
    ("IPP_", "snat_pool"),
    ("R_", "range"),
]

PrefixList = list[tuple[str, ObjectClass]]


def _to_prefix_list(entries: Iterable[tuple[str, str]]) -> PrefixList:
    """(prefix, value) -> (prefix, ObjectClass), laengste Praefixe zuerst."""
    pairs = [(k, _VALUE_TO_CLASS[v]) for k, v in entries if k and v in _VALUE_TO_CLASS]
    return sorted(pairs, key=lambda kv: len(kv[0]), reverse=True)


_DEFAULT_PREFIXES: PrefixList = _to_prefix_list(_DEFAULT_ENTRIES)


def prefixes_from_entries(entries: Iterable[tuple[str, str]]) -> PrefixList:
    """Baut die Praefix-Liste aus `object_prefix`-Woerterbucheintraegen (key, value).

    Leeres/unbekanntes Profil -> SOP-Default (keine Funktionsabhaengigkeit von Daten).
    """
    built = _to_prefix_list(entries)
    return built or _DEFAULT_PREFIXES


@dataclass(frozen=True)
class ParsedName:
    object_class: ObjectClass
    remainder: str  # Teil nach dem Praefix (i. d. R. der FQDN)
    fqdn: str | None
    domain: str | None  # alles nach dem ersten Punkt des FQDN


def parse_object_name(name: str, prefixes: PrefixList | None = None) -> ParsedName:
    """Zerlegt einen Objektnamen in Klasse (aus Praefix), FQDN und Domain.

    `prefixes` stammt aus dem Namensprofil (`prefixes_from_entries`); None = SOP-Default.
    """
    for prefix, obj_class in prefixes or _DEFAULT_PREFIXES:
        if name.startswith(prefix):
            remainder = name[len(prefix) :]
            fqdn = remainder or None
            domain = None
            if fqdn and "." in fqdn:
                domain = fqdn.split(".", 1)[1]
            return ParsedName(object_class=obj_class, remainder=remainder, fqdn=fqdn, domain=domain)

    # Kein bekanntes Praefix -> unknown (Signal fuer ein invalid_name-Finding)
    return ParsedName(object_class=ObjectClass.UNKNOWN, remainder=name, fqdn=None, domain=None)


def expects_ipam_match(object_class: ObjectClass) -> bool:
    """H-EXT_/N-EXT_ sind bewusst extern -> kein IPAM-Match erwartet (kein Fehler)."""
    return object_class not in (ObjectClass.EXTERNAL, ObjectClass.UNKNOWN)
