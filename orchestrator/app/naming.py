"""Namenskonvention-Parser (ADR 0004).

Model 1 nutzt strikte Praefixe plus FQDN im Objektnamen:
  H_server.domain.info   -> Host,        fqdn=server.domain.info, domain=domain.info
  N_...                   -> Netzwerk
  VIP_...                 -> Virtual IP
  R_...                   -> Range
  H-EXT_...               -> externe IP / DNS-Name (nicht im IPAM erwartet)

Der Parser ist die Default-Konvention; pro Mandant kann sie ueber
Tenant.naming_convention ersetzt werden (spaeter). Reine Funktion, ohne DB/IO.
"""

from dataclasses import dataclass

from app.models.network_object import ObjectClass

# Reihenfolge: spezifischste Praefixe zuerst (H-EXT_ vor H_)
_PREFIXES: list[tuple[str, ObjectClass]] = [
    ("H-EXT_", ObjectClass.EXTERNAL),
    ("VIP_", ObjectClass.VIP),
    ("H_", ObjectClass.HOST),
    ("N_", ObjectClass.NETWORK),
    ("R_", ObjectClass.RANGE),
]


@dataclass(frozen=True)
class ParsedName:
    object_class: ObjectClass
    remainder: str  # Teil nach dem Praefix (i. d. R. der FQDN)
    fqdn: str | None
    domain: str | None  # alles nach dem ersten Punkt des FQDN


def parse_object_name(name: str) -> ParsedName:
    """Zerlegt einen Objektnamen in Klasse (aus Praefix), FQDN und Domain."""
    for prefix, obj_class in _PREFIXES:
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
    """H-EXT_ ist bewusst extern -> kein IPAM-Match erwartet (kein Fehler)."""
    return object_class not in (ObjectClass.EXTERNAL, ObjectClass.UNKNOWN)
