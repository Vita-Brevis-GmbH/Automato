"""DNS-Server (z. B. AD-DNS) per Zonentransfer lesen - Referenzquelle nur fuer DNS-Namen (ADR 0045).

Liest die angegebenen Zonen komplett per AXFR (TCP 53) und liefert je Adresse einen
`ReferenceRecord`: A/AAAA als `a_record` (auch Aliase per CNAME auf einen Host der gelesenen
Zonen), PTR aus den Reverse-Zonen als `ptr_record` - IP und DNS-Name. Netze, Ranges oder
VLANs kennt ein DNS-Server nicht: er ist nur Anreicherung, nie fuehrendes IPAM.

Weggelassen werden Eintraege, die keine Hosts sind: der Zonenkopf (im AD die Adressen aller
DCs), Dienst- und Standort-Eintraege des AD (`_msdcs`, `_sites`, `_tcp`, `_ldap` …), die
Anwendungspartitionen `DomainDnsZones`/`ForestDnsZones`, Platzhalter (`*`) und alles unterhalb
einer Delegation (gehoert zur Unterzone).

Nur lesend schon vom Protokoll her: ein Zonentransfer ist eine Abfrage, dynamische Updates
(`dns.update`) werden nie benutzt (Test `test_dns_zone_adapter.py`). Ohne Zugangsdaten: der
DNS-Server erlaubt den Transfer nur der Adresse von Automato bzw. des Agenten (AD: Zone →
Eigenschaften → Zonenuebertragungen → „Nur an folgende Server“).
"""

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable, Iterator
from urllib.parse import urlsplit

import dns.exception
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdatatype
import dns.reversename
import dns.xfr

from sot_gateway.models import ReferenceKind, ReferenceRecord

DEFAULT_PORT = 53
TIMEOUT = 15.0  # Sekunden je Antwort-Nachricht
LIFETIME = 600.0  # Sekunden je Zone (grosse Zonen: einige 10 000 Eintraege)
MAX_ZONES = 200
_CNAME_DEPTH = 8

_LABEL = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")
# Anwendungspartitionen des AD: zeigen auf alle DCs, keine Hosts
_SKIP_LABELS = frozenset({b"domaindnszones", b"forestdnszones"})
_REVERSE = (".in-addr.arpa", ".ip6.arpa")

Transfer = Callable[[str, str, int, float, float], Iterable[dns.message.Message]]


class ZoneTransferError(Exception):
    """Zonentransfer einer Zone gescheitert. `reason`: refused (DNS-Antwort mit Fehlercode,
    `rcode`), timeout, connect (TCP abgelehnt), closed (Verbindung ohne Antwort beendet),
    invalid (Antwort kein Zonentransfer), network (sonstiger Netzwerkfehler)."""

    def __init__(self, zone: str, server: str, reason: str, detail: str = "") -> None:
        self.zone, self.server, self.reason, self.detail = zone, server, reason, detail
        super().__init__(f"zone transfer {zone} from {server}: {reason} {detail}".strip())


def normalize_zones(text: str | Iterable[str]) -> list[str]:
    """Zonennamen aus Text (je Zeile, Komma oder Leerzeichen getrennt) bzw. einer Liste:
    klein, ohne Punkt am Ende, ohne Doppelte. ValueError bei einem ungueltigen Namen."""
    parts = re.split(r"[\s,;]+", text) if isinstance(text, str) else list(text)
    zones = list(dict.fromkeys(p.strip().lower().rstrip(".") for p in parts if p.strip()))
    if len(zones) > MAX_ZONES:
        raise ValueError(f"at most {MAX_ZONES} zones")
    for zone in zones:
        labels = zone.split(".")
        if len(zone) > 253 or not all(_LABEL.match(label) for label in labels):
            raise ValueError(f"invalid zone name: {zone}")
    return zones


def parse_server(base_url: str) -> tuple[str, int]:
    """(Server, Port) aus `dns://server[:port]`, `server`, `server:port` bzw. `[v6]:port`."""
    value = (base_url or "").strip()
    if not value:
        raise ValueError("no DNS server")
    try:
        ipaddress.ip_address(value)
        return value, DEFAULT_PORT  # IPv6 ohne Klammern
    except ValueError:
        pass
    parts = urlsplit(value if "://" in value else f"dns://{value}")
    if parts.scheme != "dns" or not parts.hostname or (parts.path not in ("", "/")):
        raise ValueError(f"invalid DNS server: {value}")
    try:
        port = parts.port or DEFAULT_PORT
    except ValueError as exc:
        raise ValueError(f"invalid DNS server: {value}") from exc
    return parts.hostname, port


def server_url(base_url: str) -> str:
    """Einheitliche Adresse `dns://server[:port]` (Port nur, wenn nicht 53)."""
    host, port = parse_server(base_url)
    shown = f"[{host}]" if ":" in host else host
    return f"dns://{shown}" + (f":{port}" if port != DEFAULT_PORT else "")


def _axfr(address: str, zone: str, port: int, timeout: float, lifetime: float):
    return dns.query.xfr(
        address, zone, port=port, timeout=timeout, lifetime=lifetime, relativize=False
    )


def _resolve(host: str, port: int) -> str:
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return str(infos[0][4][0])


def _text(name: dns.name.Name) -> str:
    return name.to_text(omit_final_dot=True).lower()


class DnsZoneAdapter:
    """Lesender Zugriff auf einen DNS-Server ueber Zonentransfers (AXFR)."""

    name = "ad_dns"

    def __init__(
        self,
        server: str,
        zones: Iterable[str],
        *,
        port: int = DEFAULT_PORT,
        timeout: float = TIMEOUT,
        lifetime: float = LIFETIME,
        transfer: Transfer | None = None,
        resolve: Callable[[str, int], str] | None = None,
    ) -> None:
        self.server, self.port = server, port
        self.zones = normalize_zones(zones)
        if not self.zones:
            raise ValueError("no zones")
        self._timeout, self._lifetime = timeout, lifetime
        self._transfer = transfer or _axfr
        self._resolve = resolve or _resolve
        self._address: str | None = None

    @classmethod
    def from_url(cls, base_url: str, zones: str | Iterable[str], **kw) -> "DnsZoneAdapter":
        server, port = parse_server(base_url)
        return cls(server, normalize_zones(zones), port=port, **kw)

    # -- Referenzquelle --------------------------------------------------------

    def ping(self) -> dict[str, int]:
        """Je Zone nur den Anfang des Transfers (Erlaubnis + SOA-Seriennummer)."""
        serials: dict[str, int] = {}
        for zone in self.zones:
            messages = self._messages(zone)
            try:
                first = next(iter(messages), None)
            finally:
                close = getattr(messages, "close", None)
                if close is not None:
                    close()
            answer = first.answer if first is not None else []
            soa = next((r for r in answer if r.rdtype == dns.rdatatype.SOA), None)
            if soa is None:
                raise ZoneTransferError(zone, self.server, "invalid", "no SOA")
            serials[zone] = int(soa[0].serial)
        return serials

    def export(self) -> Iterator[ReferenceRecord]:
        """Alle Zonen lesen, dann liefern: CNAMEs zeigen oft in eine andere Zone, und eine
        gescheiterte Zone bricht den Lauf ab (kein halber Stand)."""
        hosts: dict[str, list[str]] = {}  # Name -> IPs (A/AAAA aller Zonen)
        aliases: list[tuple[str, str, str]] = []  # (Zone, Alias, Ziel)
        out: list[ReferenceRecord] = []
        for zone in self.zones:
            for kind, rdtype, owner, data in self._zone_records(zone):
                if kind == "cname":
                    aliases.append((zone, owner, data))
                    continue
                if kind == ReferenceKind.A_RECORD:
                    hosts.setdefault(owner, []).append(data)
                    out.append(self._record(kind, zone, data, owner, f"{owner}/{rdtype}/{data}"))
                else:  # PTR: Name = Ziel des Eintrags, Wert = IP aus dem Namen
                    out.append(self._record(kind, zone, data, owner, f"PTR/{data}/{owner}"))
        for zone, alias, target in aliases:
            seen = 0
            while target not in hosts and seen < _CNAME_DEPTH:
                nxt = next((t for z, a, t in aliases if a == target), None)
                if nxt is None:
                    break
                target, seen = nxt, seen + 1
            for ip in hosts.get(target, []):
                rec = self._record(ReferenceKind.A_RECORD, zone, ip, alias, f"{alias}/CNAME/{ip}")
                rec.attrs["cname"] = target
                out.append(rec)
        yield from out

    # -- intern ---------------------------------------------------------------

    def _record(self, kind, zone: str, ip: str, name: str, ref_id: str) -> ReferenceRecord:
        return ReferenceRecord(
            kind=kind,
            value=ip,
            dns_name=name,
            tags=[f"zone={zone}"],
            attrs={"zone": zone},
            ref_id=ref_id,
        )

    def _messages(self, zone: str) -> Iterator[dns.message.Message]:
        """Nachrichten des Transfers; Fehler als ZoneTransferError mit Zone und Ursache."""
        try:
            if self._address is None:
                self._address = self._resolve(self.server, self.port)
            yield from self._transfer(self._address, zone, self.port, self._timeout, self._lifetime)
        except dns.xfr.TransferError as exc:
            raise ZoneTransferError(zone, self.server, "refused",
                                    dns.rcode.to_text(exc.rcode)) from exc  # fmt: skip
        except dns.exception.Timeout as exc:
            raise ZoneTransferError(zone, self.server, "timeout") from exc
        except ConnectionRefusedError as exc:
            raise ZoneTransferError(zone, self.server, "connect", str(exc)) from exc
        except (EOFError, ConnectionResetError, BrokenPipeError) as exc:
            raise ZoneTransferError(zone, self.server, "closed") from exc
        except dns.exception.DNSException as exc:  # FormError u. a.: keine gueltige Antwort
            raise ZoneTransferError(zone, self.server, "invalid", str(exc)) from exc
        except socket.gaierror as exc:
            raise ZoneTransferError(zone, self.server, "dns", exc.strerror or str(exc)) from exc
        except OSError as exc:
            raise ZoneTransferError(zone, self.server, "network",
                                    exc.strerror or str(exc)) from exc  # fmt: skip

    def _zone_records(self, zone: str) -> list[tuple]:
        """(Art, Typ, Name, Wert) je Host-Eintrag der Zone - Art a_record (Name, IP), ptr_record
        (Ziel des Eintrags, IP) bzw. cname (Alias, Ziel)."""
        origin = dns.name.from_text(zone)
        reverse = f".{zone}".endswith(_REVERSE) or zone in ("in-addr.arpa", "ip6.arpa")
        rrsets = [rr for msg in self._messages(zone) for rr in msg.answer]
        cuts = {
            rr.name for rr in rrsets if rr.rdtype == dns.rdatatype.NS and rr.name != origin
        }  # Delegationen: darunter gehoert alles zur Unterzone
        out: list[tuple] = []
        for rr in rrsets:
            name = rr.name
            if name == origin or not name.is_subdomain(origin):
                continue
            if self._below_cut(name, origin, cuts):
                continue
            labels = name.relativize(origin).labels
            if any(lab.startswith(b"_") or lab == b"*" or lab.lower() in _SKIP_LABELS
                   for lab in labels):  # fmt: skip
                continue
            owner = _text(name)
            if reverse:
                if rr.rdtype != dns.rdatatype.PTR:
                    continue
                try:
                    ip = ipaddress.ip_address(dns.reversename.to_address(name)).compressed
                except (dns.exception.SyntaxError, ValueError):
                    continue  # unvollstaendige Adresse (z. B. klassenlose Delegation)
                out += [(ReferenceKind.PTR_RECORD, "PTR", _text(r.target), ip) for r in rr]
            elif rr.rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
                rdtype = dns.rdatatype.to_text(rr.rdtype)
                out += [(ReferenceKind.A_RECORD, rdtype, owner,
                         ipaddress.ip_address(r.address).compressed) for r in rr]  # fmt: skip
            elif rr.rdtype == dns.rdatatype.CNAME:
                out += [("cname", "CNAME", owner, _text(r.target)) for r in rr]
        return out

    @staticmethod
    def _below_cut(name: dns.name.Name, origin: dns.name.Name, cuts: set) -> bool:
        if not cuts:
            return False
        node = name
        while node != origin and len(node) > len(origin):
            if node in cuts:
                return True
            node = node.parent()
        return False
