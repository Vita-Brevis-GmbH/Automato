"""Grosse Netze in Regeln zuordnen (0.111.0, ADR 0040).

Anwender 2026-10-29: „Haben wir ein Objekt mit 10.0.0.0/8 in der Regel, lernen die Subnetze aber
einzeln, wird das Objekt der naechst besseren Route zugewiesen (0.0.0.0/0) … wir muessen diese
markieren und zuordnen lassen, wo sie hin gehoeren, oder sie anhand von Angaben in Anbindungen und
Zonen ausschliessen oder zuweisen“ - gewaehlt: kombiniert.

Ein Netz ist **groesser als die Routen**, wenn das Geraet es nicht als Ganzes routet (die Route
ist kuerzer als das Netz) und entweder genauere Routen darin anders laufen oder nur die
Default-Route bleibt (bei privaten Netzen). Wohin es gehoert, in dieser Reihenfolge:

1. **von Hand** (`net_assignment`, Operator, Audit): zur Zone, ueber die Anbindung, der Weg nach
   dem Routing stimmt (`route`) bzw. nur die genauer gerouteten Teile zaehlen (`ignore`);
2. **Zone** (Netzwerkdesign): eine Zone enthaelt das ganze Netz (Netze und Ausschluesse);
3. **Anbindung**: ihre Netze dahinter enthalten das ganze Netz;
4. **Teile nach dem Routing**: die genauer gerouteten Teile als eigene Verbindungen; was nur ueber
   die kuerzere Route ginge, bleibt **unklar** - Befund `net_wider_than_routes` (Pruefung
   `app/checks/wege/netz_groesser.py`).

Nur lesend gegenueber den Quellsystemen; die Zuordnung von Hand ist Datenhaltung der Plattform.
"""

import ipaddress
from dataclasses import dataclass
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import NetAssignment, TopoExit

Net = ipaddress.IPv4Network | ipaddress.IPv6Network
MANUAL, ZONE, EXIT, ROUTING, UNCLEAR = "manual", "zone", "exit", "routing", "unclear"


@dataclass
class Assignment:
    prefix: str
    state: str  # zone | exit | route | ignore (von Hand bzw. abgeleitet) | split | unclear
    how: str  # manual | zone | exit | routing
    text: str
    zone_id: int | None = None
    zone: str | None = None
    exit_id: int | None = None
    exit: str | None = None
    id: int | None = None  # Zuordnung von Hand

    @property
    def open(self) -> bool:
        """Noch zuzuordnen (Befund): nach dem Routing aufgeteilt bzw. ganz unklar."""
        return self.state in ("split", UNCLEAR)

    def out(self) -> dict:
        return {k: v for k, v in {
            "prefix": self.prefix, "state": self.state, "how": self.how, "text": self.text,
            "zone_id": self.zone_id, "zone": self.zone, "exit_id": self.exit_id,
            "exit": self.exit, "id": self.id, "open": self.open,
        }.items() if v is not None}  # fmt: skip


def as_net(value: str | None) -> Net | None:
    """Netz (kein Host, nicht any) aus einem Wert der Regel."""
    try:
        net = ipaddress.ip_network((value or "").strip(), strict=False)
    except ValueError:
        return None
    full = 32 if net.version == 4 else 128
    return net if 0 < net.prefixlen < full else None


def wider(net: Net, route: dict | None) -> bool:
    """Routet das Geraet das Netz nicht als Ganzes - genauere Routen darin bzw. nur die
    Default-Route (bei privaten Netzen; oeffentliche ueber die Default-Route sind normal)?"""
    if route is None or route.get("prefix_len") is None or route["prefix_len"] >= net.prefixlen:
        return False
    if route.get("inner"):
        return True
    return route["prefix_len"] == 0 and not net.is_global


class Assigner:
    """Zuordnungen eines Mandanten (von Hand, Zonen, Anbindungen) - einmal je Durchgang."""

    def __init__(self, session: Session, tenant_id: int):
        self.session, self.tenant_id = session, tenant_id

    @cached_property
    def manual(self) -> dict[str, NetAssignment]:
        rows = self.session.scalars(select(NetAssignment)
                                    .where(NetAssignment.tenant_id == self.tenant_id))  # fmt: skip
        return {r.prefix: r for r in rows}

    @cached_property
    def zones(self):
        from app.store.appdoc_zones import ZoneMap  # spaet: Zonen nutzen Kunden und Doku

        return ZoneMap(self.session, self.tenant_id)

    @cached_property
    def exits(self) -> list[tuple[TopoExit, list[Net]]]:
        out = []
        for e in self.session.scalars(select(TopoExit).where(TopoExit.tenant_id == self.tenant_id)):
            nets = [n for n in (_net(p) for p in e.prefixes or []) if n is not None]
            if nets:
                out.append((e, nets))
        return out

    def _zone_name(self, zone_id: int | None) -> str | None:
        z = self.zones.items.get(zone_id) if zone_id is not None else None
        return z.name if z is not None else None

    def _exit_name(self, exit_id: int | None) -> str | None:
        if exit_id is None:
            return None
        e = self.session.get(TopoExit, exit_id)
        return e.name if e is not None and e.tenant_id == self.tenant_id else None

    def decide(self, net: Net, route: dict | None = None) -> Assignment:
        """Wohin gehoert das Netz (siehe Modul)? `route`: die Route des Geraets fuer das Netz
        (`paths.Graph.route`) - fuer Teile und Text."""
        prefix = str(net)
        m = self.manual.get(prefix)
        if m is not None:
            hit = self._manual(m, prefix)
            if hit is not None:
                return hit
        ids, whole = self.zones.match(prefix) if self.zones.active else ([], False)
        if whole and len(ids) == 1:
            name = self._zone_name(ids[0])
            return Assignment(prefix, ZONE, ZONE, L(f"gehört zur Zone {name} (Netzwerkdesign)",
                                                    f"belongs to zone {name} (network design)"),
                              zone_id=ids[0], zone=name)  # fmt: skip
        for e, nets in self.exits:
            if any(net.version == n.version and net.subnet_of(n) for n in nets):
                return Assignment(prefix, EXIT, EXIT,
                                  L(f"über Anbindung {e.name} (Netze dahinter)",
                                    f"via exit {e.name} (networks behind)"),
                                  exit_id=e.id, exit=e.name)  # fmt: skip
        via = (route or {}).get("prefix") or "?"
        inner = (route or {}).get("inner_total") or len((route or {}).get("inner") or [])
        if inner:
            return Assignment(
                prefix,
                "split",
                ROUTING,
                L(
                    f"{inner} Teile genauer geroutet, der Rest nur über {via} – zuordnen",
                    f"{inner} parts routed more specifically, the rest only via {via} – assign",
                ),
            )
        return Assignment(
            prefix,
            UNCLEAR,
            ROUTING,
            L(
                f"nur über {via} geroutet – unklar, wohin {prefix} gehört; zuordnen",
                f"only routed via {via} – unclear where {prefix} belongs; assign",
            ),
        )

    def _manual(self, m: NetAssignment, prefix: str) -> Assignment | None:
        if m.kind == "zone":
            name = self._zone_name(m.zone_id)
            if name is None:
                return None  # Zone geloescht: wie ohne Zuordnung
            return Assignment(prefix, ZONE, MANUAL, L(f"gehört zur Zone {name} (von Hand)",
                                                      f"belongs to zone {name} (manual)"),
                              zone_id=m.zone_id, zone=name, id=m.id)  # fmt: skip
        if m.kind == "exit":
            name = self._exit_name(m.exit_id)
            if name is None:
                return None
            return Assignment(prefix, EXIT, MANUAL, L(f"über Anbindung {name} (von Hand)",
                                                      f"via exit {name} (manual)"),
                              exit_id=m.exit_id, exit=name, id=m.id)  # fmt: skip
        if m.kind == "ignore":
            return Assignment(
                prefix,
                "ignore",
                MANUAL,
                L(
                    "nur die genauer gerouteten Teile zählen (von Hand)",
                    "only the parts routed more specifically count (manual)",
                ),
                id=m.id,
            )
        return Assignment(prefix, "route", MANUAL, L("Weg nach dem Routing stimmt (von Hand)",
                                                     "path by the routing is right (manual)"),
                          id=m.id)  # fmt: skip


def _net(value: str | None) -> Net | None:
    try:
        return ipaddress.ip_network((value or "").strip(), strict=False)
    except ValueError:
        return None
