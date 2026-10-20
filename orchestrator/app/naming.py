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
gilt die eingebaute Standard-Konvention (`_DEFAULT_PREFIXES`, = Profil „default") - die
Plattform funktioniert also auch ohne befuellte Woerterbuecher (keine
Funktionsabhaengigkeit). Reine Funktion, ohne DB/IO.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.models.network_object import ObjectClass
from app.naming_defaults import DEFAULT_DICTIONARY

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

# Eingebaute Standard-Konvention als (prefix, value), falls kein Profil vorliegt - dieselben
# Werte wie das Standardprofil „default" (app.naming_defaults). Reihenfolge egal
# (longest-first wird beim Bauen erzwungen).
_DEFAULT_ENTRIES: list[tuple[str, str]] = DEFAULT_DICTIONARY["object_prefix"]

PrefixList = list[tuple[str, ObjectClass]]


def _to_prefix_list(entries: Iterable[tuple[str, str]]) -> PrefixList:
    """(prefix, value) -> (prefix, ObjectClass), laengste Praefixe zuerst."""
    pairs = [(k, _VALUE_TO_CLASS[v]) for k, v in entries if k and v in _VALUE_TO_CLASS]
    return sorted(pairs, key=lambda kv: len(kv[0]), reverse=True)


_DEFAULT_PREFIXES: PrefixList = _to_prefix_list(_DEFAULT_ENTRIES)


def prefixes_from_entries(entries: Iterable[tuple[str, str]]) -> PrefixList:
    """Baut die Praefix-Liste aus `object_prefix`-Woerterbucheintraegen (key, value).

    Leeres/unbekanntes Profil -> Standard-Konvention (keine Funktionsabhaengigkeit von Daten).
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

    `prefixes` stammt aus dem Namensprofil (`prefixes_from_entries`); None = Standard.
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


def server_name(name: str, prefixes: PrefixList | None = None) -> str:
    """Name ohne Konventions-Praefix - so steht der Server im IPAM (DDI/NetBox), waehrend
    Firewall, ACI und Doku ihn mit Praefix fuehren: H_web01.example.ch -> web01.example.ch.
    Ohne bekanntes Praefix bleibt der Name, wie er ist."""
    return parse_object_name(name, prefixes).remainder or name


def short_name(name: str | None) -> str:
    """Erstes Label eines (DNS-)Namens, klein und ohne Schlusspunkt: web01.example.ch -> web01."""
    return (name or "").strip().rstrip(".").lower().split(".", 1)[0]


def expects_ipam_match(object_class: ObjectClass) -> bool:
    """H-EXT_/N-EXT_ sind bewusst extern -> kein IPAM-Match erwartet (kein Fehler)."""
    return object_class not in (ObjectClass.EXTERNAL, ObjectClass.UNKNOWN)


# -- Services (FortiGate-Services, ACI-Filter): Namenskonvention FT_ ----------------------

# Ohne Woerterbuch `service_prefix`: eingebautes Praefix der Konvention (FT_ = Filter/Service)
SERVICE_PREFIXES: tuple[str, ...] = ("FT_",)

# FortiOS-Standardobjekte (vordefiniert, nicht umbenennbar) - keine Konventions-/Nutzungspruefung
FORTIOS_PREDEFINED_SERVICES = frozenset(
    s.lower()
    for s in (
        "ALL ALL_TCP ALL_UDP ALL_ICMP ALL_ICMP6 GRE AH ESP AOL BGP DHCP DHCP6 DNS FINGER FTP "
        "FTP_GET FTP_PUT GOPHER H323 HTTP HTTPS IKE IMAP IMAPS Internet-Locator-Service IRC "
        "L2TP LDAP LDAP_UDP NetMeeting NFS NNTP NTP OSPF PC-Anywhere PING PING6 TIMESTAMP "
        "INFO_REQUEST INFO_ADDRESS ONC-RPC DCE-RPC POP3 POP3S PPTP QUAKE RAUDIO REXEC RIP "
        "RLOGIN RSH SCCP SIP SIP-MSNmessenger SAMBA SMTP SMTPS SNMP SSH SYSLOG TALK TELNET TFTP "
        "MGCP UUCP VDOLIVE WAIS WINFRAME X-WINDOWS MS-SQL MYSQL RDP VNC SQUID SOCKS WINS "
        "RADIUS RADIUS-OLD CVSPSERVER AFS3 TRACEROUTE RTSP MMS KERBEROS SMB NONE webproxy"
    ).split()
) | frozenset({"email access", "web access", "windows ad", "exchange server"})

_SERVICE_PATTERN = re.compile(r"^FT_(tcpudp|tcp|udp|sctp)(\d+)(?:-(\d+))?$", re.IGNORECASE)


def service_tokens(spec: str) -> list[str]:
    """Woerterbuch-Wert eines Services -> Tokens: 'tcp+udp/1433:1434' -> tcp/1433-1434, udp/..."""
    spec = (spec or "").strip().lower().replace(" ", "")
    if "/" not in spec:
        return [spec] if spec else []
    protos, _, ports = spec.partition("/")
    ports = ports.replace(":", "-")
    return [f"{p}/{ports}" for p in protos.split("+") if p]


def expected_service(name: str) -> list[str] | None:
    """Protokoll/Port, den der Name nach Schema verspricht: FT_tcp8443 -> [tcp/8443]."""
    hit = _SERVICE_PATTERN.match(name or "")
    if hit is None:
        return None
    proto, lo, hi = hit[1].lower(), hit[2], hit[3]
    ports = f"{lo}-{hi}" if hi and hi != lo else lo
    protos = ("tcp", "udp") if proto == "tcpudp" else (proto,)
    return [f"{p}/{ports}" for p in protos]


def normalize_tokens(tokens: list[str]) -> set[str]:
    return {t.strip().lower().replace(":", "-") for t in tokens or [] if t}
