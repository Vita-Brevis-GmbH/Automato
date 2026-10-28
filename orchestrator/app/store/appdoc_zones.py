"""Zonen in den Kategorien der Applikations-Doku (0.101.0, ADR 0012 Ergaenzung 0.101.0).

Anwender 2026-10-28: Firewall-Regeln, die grosse Bereiche zulassen, aber direkt auf das System
zeigen, standen unter „Unspezifische Zugriffe“ - sie gehoeren in die Kategorie, aus der der
Zugriff kommt. Dafuer haengen die logischen Zonen (ADR 0024) an den Kategorien: eine Zone kann
einem **Bereich** zugeordnet sein (`logical_zone.appdoc_area`) - Internet (Kategorien 1/2),
Unternehmensnetzwerk (3/4), VPN (5), Remote Access (6), andere Security Tenants (9). Die Netze
der Zone (ihre Selektoren, z. B. die Quell-Netze als Praefix) bestimmen, welche Gegenstellen
dazugehoeren.

Zone einer Gegenstelle (Quelle eingehend, Ziel ausgehend) je Wert der Regel:
- die Zone des laengsten Zonen-Netzes, das den Wert ganz enthaelt (wie in der Zonenmatrix);
  ein Zonen-Netz 0.0.0.0/0 bzw. ::/0 (Internet) nur fuer oeffentliche Adressen;
- sonst - ein grosser Bereich wie 10.0.0.0/8 - die Zonen der Netze darin;
- any: die Zonen der Anbindung bzw. der Netze hinter dem Interface der Regel.
Die Kategorie folgt dem Bereich, wenn die Zonen der Gegenstelle genau einen Bereich nennen;
nennen sie mehrere, bleibt es bei den bisherigen Merkmalen (Zone der Firewall, NAT, extern).
Die Spalte „Zone“ nennt die Zonen der Gegenstelle, der Kopf jeder Kategorie ihre Zonen mit
den Netzen.

Rein lesend; nichts davon wird gespeichert. Kunden-Benutzer sehen weder die Spalte noch die
Zonen im Kopf (Netze der Infrastruktur, `strip`) - die Kategorie gilt fuer sie gleich.
"""

from __future__ import annotations

import bisect
import ipaddress
from collections import defaultdict

from sqlalchemy.orm import Session

from app.i18n import L
from app.models import LogicalZone

Net = ipaddress.IPv4Network | ipaddress.IPv6Network
BITS = {4: 32, 6: 128}
# Kategorien je Bereich (eingehend bzw. ausgehend waehlt `appdoc.categorize`)
AREA_TABLES: dict[str, tuple[int, ...]] = {
    "inet": (1, 2),
    "bit": (3, 4),
    "vpn": (5,),
    "sra": (6,),
    "res": (9,),
}
SHOWN_NETS = 6  # Netze je Zone im Kopf einer Kategorie
SHOWN_ZONES = 4  # Zonen je Zelle


def _nets(value: str | None) -> list[Net]:
    """Netze eines Werts der Regel: CIDR bzw. Host, Bereich „a-b“; sonst keine."""
    text = (value or "").strip().split(" ")[0]
    if not text or text.lower() == "any":
        return []
    if "-" in text:
        lo, _, hi = text.partition("-")
        try:
            first, last = ipaddress.ip_address(lo.strip()), ipaddress.ip_address(hi.strip())
            return list(ipaddress.summarize_address_range(first, last))
        except (ValueError, TypeError):
            return []
    try:
        return [ipaddress.ip_network(text, strict=False)]
    except ValueError:
        return []


class ZoneMap:
    """Logische Zonen eines Mandanten mit ihren Netzen - einmal je Doku geladen."""

    def __init__(self, session: Session, tenant_id: int):
        from app.store import zones as zone_store  # spaet: zones -> customers -> ...

        self.items: dict[int, LogicalZone] = {z.id: z for z in zone_store.load(session, tenant_id)}
        derived = (
            zone_store.derive(session, tenant_id, list(self.items.values())) if self.items else []
        )
        self.nets: dict[int, list[Net]] = {
            d.id: sorted(d.nets, key=lambda n: (n.version, n.prefixlen, int(n.network_address)))
            for d in derived
        }
        # laengstes Praefix: je Familie und Laenge die Zonen je Netz-Anfang
        self._by_len: dict[int, dict[int, dict[int, list[int]]]] = {4: {}, 6: {}}
        spans: dict[int, list[tuple[int, int, int]]] = {4: [], 6: []}
        for zid, nets in self.nets.items():
            for n in nets:
                fam, plen = n.version, n.prefixlen
                key = int(n.network_address) >> (BITS[fam] - plen)
                owners = self._by_len[fam].setdefault(plen, {}).setdefault(key, [])
                if zid not in owners:
                    owners.append(zid)
                spans[fam].append((int(n.network_address), int(n.broadcast_address), zid))
        self._lengths = {f: sorted(self._by_len[f], reverse=True) for f in (4, 6)}
        self._spans = {f: sorted(spans[f]) for f in (4, 6)}
        self._starts = {f: [s[0] for s in self._spans[f]] for f in (4, 6)}
        self._exits: dict[str, list[int]] = defaultdict(list)
        for z in self.items.values():
            for s in z.selectors:
                if s.kind == "exit" and not s.exclude:
                    self._exits[s.value.strip().lower()].append(z.id)
        self._cache: dict[str, tuple[list[int], bool]] = {}

    @property
    def active(self) -> bool:
        """Gibt es Zonen? Ohne Zonen bleibt die Doku wie bisher."""
        return bool(self.items)

    @property
    def has_areas(self) -> bool:
        return any(z.appdoc_area for z in self.items.values())

    def _covering(self, net: Net) -> list[int]:
        fam, bits = net.version, BITS[net.version]
        lo, hi = int(net.network_address), int(net.broadcast_address)
        for plen in self._lengths[fam]:
            if plen > net.prefixlen:
                continue
            if plen == 0 and not net.is_global:  # Internet (0/0) nur fuer oeffentliche Adressen
                continue
            shift = bits - plen
            if lo >> shift != hi >> shift:
                continue
            ids = self._by_len[fam][plen].get(lo >> shift)
            if ids:
                return list(ids)
        return []

    def _inner(self, net: Net) -> list[int]:
        fam = net.version
        lo, hi = int(net.network_address), int(net.broadcast_address)
        spans, starts = self._spans[fam], self._starts[fam]
        out: list[int] = []
        i = bisect.bisect_left(starts, lo)
        while i < len(spans) and spans[i][0] <= hi:
            s_lo, s_hi, zid = spans[i]
            if s_hi <= hi and zid not in out:
                out.append(zid)
            i += 1
        return out

    def match(self, value: str | None) -> tuple[list[int], bool]:
        """Zonen eines Werts und ob eine Zone ihn ganz enthaelt (sonst: Zonen darin)."""
        key = value or ""
        if key not in self._cache:
            ids: list[int] = []
            whole = True
            for net in _nets(value):
                cover = self._covering(net)
                if not cover:
                    whole = False
                    cover = self._inner(net)
                ids += [z for z in cover if z not in ids]
            self._cache[key] = (ids, whole and bool(ids))
        return self._cache[key]

    def of_exit(self, name: str | None) -> list[int]:
        """Zonen mit den Netzen hinter einer Anbindung (Selektor `exit`)."""
        return list(self._exits.get((name or "").strip().lower(), []))

    def zones_of(self, values: list[str], exits: list[str] | None = None) -> list[int]:
        out: list[int] = []
        for v in values:
            out += [z for z in self.match(v)[0] if z not in out]
        for name in exits or []:
            out += [z for z in self.of_exit(name) if z not in out]
        return out

    def area(self, zone_ids: list[int]) -> str | None:
        """Bereich der Zonen: genau einer, sonst keiner (mehrere bzw. ohne Bereich)."""
        areas = {self.items[z].appdoc_area for z in zone_ids if self.items[z].appdoc_area}
        return areas.pop() if len(areas) == 1 else None

    def text(self, zone_ids: list[int]) -> str:
        """Zonen fuer die Spalte „Zone“, je eine Zeile (die ersten, dann „+n“)."""
        names = [self.items[z].name for z in zone_ids if z in self.items]
        more = len(names) - SHOWN_ZONES
        shown = names[:SHOWN_ZONES] + ([L(f"+{more} weitere", f"+{more} more")] if more > 0 else [])
        return "\n".join(shown)

    def name_of(self, value: str | None) -> str | None:
        """Zone, die einen Wert ganz enthaelt (genau eine) - sonst keine."""
        ids, whole = self.match(value)
        return self.items[ids[0]].name if whole and len(ids) == 1 else None

    def table_zones(self, index: int) -> list[dict]:
        """Zonen einer Kategorie mit ihren Netzen (Kopf der Kategorie)."""
        out = []
        for z in self.items.values():
            if index not in AREA_TABLES.get(z.appdoc_area or "", ()):
                continue
            nets = [str(n) for n in self.nets.get(z.id, [])]
            out.append({"id": z.id, "name": z.name, "area": z.appdoc_area,
                        "nets": nets[:SHOWN_NETS], "nets_total": len(nets)})  # fmt: skip
        return out


def strip(tables: list[dict], column: str) -> None:
    """Kunden-Benutzer: ohne Spalte „Zone“ und ohne Zonen im Kopf (Netze der Infrastruktur)."""
    for t in tables:
        t["zones"] = []
        t["columns"] = [c for c in t.get("columns") or [] if c != column]
        if t.get("labels") is not None and len(t["labels"]) != len(t["columns"]):
            from app.store import appdoc  # spaet: appdoc -> appdoc_zones

            t["labels"] = appdoc.column_labels(t["columns"])
        for row in [*(t.get("rows") or []), *(t.get("hidden") or [])]:
            row.pop(column, None)
