"""Netzlisten fuer Anbindungen (0.111.0, Anwender: „grosse Listen von Subnetzen angeben“).

- **Einfuegen** (`parse`): eine Liste aus Konfiguration, Tabelle oder Mail - Netze getrennt durch
  Zeilen, Kommas, Strichpunkte bzw. Leerzeichen; erkannt werden CIDR (`10.1.0.0/16`), einzelne
  Adressen (`/32` bzw. `/128`) und Adresse mit Maske (`10.1.0.0 255.255.0.0`). Text daneben
  (Namen, Kommentare) wird ignoriert, was wie eine Adresse aussieht und keine ist, gemeldet.
  Zusammenfassen (`collapse`): enthaltene und doppelte Netze fallen weg, benachbarte werden zu
  einem (256 × /24 → /16).
- **Aus dem Routing** (`learned`): die Netze, die die gelesenen Geraete ueber die Next-Hops bzw.
  Interfaces der Anbindung erhalten (beste Routen, ohne Default-Route) - als Vorschlag; die
  grossen Netze dahinter (z. B. aus dem BGP-Filter) traegt man weiter selbst ein.

Nur lesend gegenueber den Quellsystemen; gespeichert wird erst mit der Anbindung.
"""

import ipaddress
import re
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TopoRoute

Net = ipaddress.IPv4Network | ipaddress.IPv6Network
MAX_PREFIXES = 5000  # Netze je Anbindung
LEARNED = 2000  # Vorschlaege aus dem Routing

_V4 = r"\d{1,3}(?:\.\d{1,3}){3}"
_TOKEN = re.compile(
    rf"(?P<v4>{_V4})(?:/(?P<len4>\d{{1,3}})|\s+(?P<mask>{_V4}))?"
    r"|(?P<v6>[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})(?:/(?P<len6>\d{1,3}))?"
)


def _mask_len(mask: str) -> int | None:
    """Praefixlaenge einer Netzmaske bzw. Wildcard-Maske (255.255.0.0 bzw. 0.0.255.255 -> 16);
    None, wenn es keine ist."""
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except ValueError:
        return None


def parse(text: str) -> dict:
    """Netze aus einem Text: gueltige (in der Reihenfolge, ohne Doppelte), ungueltige
    Eintraege, Anzahl Doppelte und zusammengefasst."""
    valid: list[str] = []
    invalid: list[str] = []
    dupes = 0
    for m in _TOKEN.finditer(text or ""):
        raw = m.group(0)
        if m.group("v4"):
            addr, length = m.group("v4"), m.group("len4")
            if m.group("mask"):
                mlen = _mask_len(m.group("mask"))
                if mlen is None:  # zweite Adresse, keine Maske: zwei Eintraege
                    for single in (addr, m.group("mask")):
                        dupes += _add(valid, invalid, single, single)
                    continue
                length = str(mlen)
        else:
            addr, length = m.group("v6"), m.group("len6")
            if addr.count(":") < 2 or not any(c.isdigit() or c.isalpha() for c in addr):
                continue  # z. B. Uhrzeit „10:30“ bzw. „::“-Reste im Text
            if not length:
                try:
                    ipaddress.ip_address(addr)
                except ValueError:
                    continue  # kein Netz gemeint (z. B. MAC-Adresse, Uhrzeit)
        value = f"{addr}/{length}" if length else addr
        dupes += _add(valid, invalid, value, raw)
    return {"valid": valid, "invalid": invalid, "duplicates": dupes,
            "collapsed": collapse(valid)}  # fmt: skip


def _add(valid: list[str], invalid: list[str], value: str, raw: str) -> int:
    try:
        net = str(ipaddress.ip_network(value, strict=False))
    except ValueError:
        if raw not in invalid:
            invalid.append(raw.strip())
        return 0
    if net in valid:
        return 1
    valid.append(net)
    return 0


def collapse(values: Iterable[str]) -> list[str]:
    """Zusammengefasst je Familie (enthaltene und benachbarte Netze), IPv4 vor IPv6."""
    nets: dict[int, list[Net]] = {4: [], 6: []}
    for v in values:
        try:
            n = ipaddress.ip_network(v, strict=False)
        except ValueError:
            continue
        nets[n.version].append(n)
    return [str(n) for fam in (4, 6) for n in ipaddress.collapse_addresses(nets[fam])]


def learned(session: Session, tenant_id: int, source_ids: list[int], next_hops: list[str],
            interfaces: list[str], limit: int = LEARNED) -> dict:  # fmt: skip
    """Netze, die die Geraete ueber die Next-Hops bzw. Interfaces der Anbindung erhalten:
    beste Routen der Routing-Tabelle, ohne Default-Route und ohne angeschlossene Netze."""
    hops = {h.strip() for h in next_hops if h.strip()}
    ports = {i.strip() for i in interfaces if i.strip()}
    if not hops and not ports:
        return {"prefixes": [], "collapsed": [], "total": 0, "cut": False}
    stmt = select(TopoRoute.prefix, TopoRoute.prefix_len, TopoRoute.protocol,
                  TopoRoute.next_hops).where(
        TopoRoute.tenant_id == tenant_id, TopoRoute.table == "rib",
        TopoRoute.best.is_(True), TopoRoute.prefix_len > 0)  # fmt: skip
    if source_ids:
        stmt = stmt.where(TopoRoute.source_system_id.in_(source_ids))
    found: dict[str, str | None] = {}
    for prefix, _plen, protocol, nhs in session.execute(stmt.order_by(TopoRoute.lo)):
        if protocol in ("connected", "local"):
            continue
        for h in nhs or []:
            ip = str(h.get("ip") or "").split("%")[0]
            if (ip and ip in hops) or (h.get("interface") in ports):
                found.setdefault(prefix, protocol)
                break
    prefixes = list(found)
    return {"prefixes": prefixes[:limit], "collapsed": collapse(prefixes)[:limit],
            "total": len(prefixes), "cut": len(prefixes) > limit}  # fmt: skip
