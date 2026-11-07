"""Anbindung als Quelle bzw. Ziel der Weg-Suche (ADR 0019 Ergaenzung, Test des Anwenders
2026-10-27: „das Tool weiss ja, welche Netzwerke man von dort lernt“).

Eine Anbindung - von Hand erfasst (`topo_exit`) bzw. erkannt (`store/exits.py`) - geht als Quelle
oder Ziel in die Weg-Suche:

- **Firewalls der Anbindung:** je gelesenem Knoten (Firewall bzw. VDOM) das Interface und die
  Zone, ueber die er die Router der Anbindung erreicht - aus den erkannten Routern (zugeordnet
  wie im Weg: Next-Hop, sonst Interface bzw. Zone), sonst aus den Interfaces bzw. Zonen der
  Anbindung im Inventar. Mehrere Firewalls (`source_system_ids`, Verbund) = je ein Start bzw.
  eine Variante; `@<Quellsystem>` an der Referenz waehlt eine davon.
- **Netze der Anbindung:** von Hand eingetragen, sonst die Praefixe, die die Firewalls ueber ihre
  Router lernen (aktive Routen, sonst Backup). Als Adresse gilt das erste Netz als Ganzes
  (`topology.endpoint`, 0.39.0); bei Default-Route bzw. Internet eine neutrale oeffentliche
  Test-Adresse aus dem Dokumentationsbereich (RFC 5737 bzw. RFC 3849) - „beliebige Adresse im
  Internet“ -, die an keinem gelesenen Geraet angeschlossen ist.
- **Als Quelle:** der Weg beginnt am Knoten mit dem Eingang der Anbindung (Interface, Zone) - so
  pruefen die Policies den Eingang (z. B. `wan1`), eine VIP ihr `extintf`.
- **Als Ziel:** der Weg folgt dem Routing; je Weg steht, ob er ueber die Anbindung hinausgeht
  (`via_exit`) - sonst ein Hinweis.

Referenz: `exit:<id>` (von Hand) bzw. `router:<IP>` (erkannt: ein Router des Vorschlags), optional
mit `@<Quellsystem>`. Nur lesend, alles aus den gelesenen Daten.
"""

import ipaddress
import re
from dataclasses import dataclass, field, replace

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import InvInterface, SourceSystem, TopoExit
from app.store import exits as exit_store
from app.store.paths import _EXIT_LABELS_EN, EXIT_LABELS, Graph, Key, exit_kind
from app.store.topology import Table

# Dokumentationsbereiche (RFC 5737, RFC 3849): oeffentlich und nie wirklich vergeben
INTERNET_V4 = ("198.51.100.10", "192.0.2.10", "203.0.113.10")
INTERNET_V6 = ("2001:db8:ffff::10", "2001:db8:fffe::10")
NETS_SHOWN = 20  # Netze je Anbindung in der Antwort (Auswahl in der Oberflaeche)
FIREWALL_KINDS = ("firewall", "vdom")
_REF = re.compile(r"^(exit|router):([^@\s]+)(?:@(\d+))?$")
# Woerter, bei denen die Namenssuche auch die erkannten Anbindungen sucht (sonst nur die von Hand
# erfassten): Art, Interface/Zone bzw. Firewall - erkennen liest das ganze Routing
_WORDS = ("anbindung", "exit", "internet", "inet", "isp", "provider", "mpls", "wan", "partner",
          "extern", "router", "uplink", "transit")  # fmt: skip


@dataclass
class Side:
    """Ein Knoten der Anbindung: ueber welches Interface bzw. welche Zone, mit welchen Routern."""

    key: Key
    interface: str | None = None
    zone: str | None = None
    table: Table = 0
    routers: list[str] = field(default_factory=list)

    @property
    def port(self) -> str | None:
        return self.interface or self.zone


@dataclass
class End:
    """Anbindung als Ende eines Wegs: Knoten mit Eingang/Ausgang, Router und Netze."""

    ref: str
    kind: str
    name: str
    manual: bool
    exit_id: int | None = None
    sides: list[Side] = field(default_factory=list)
    routers: list[str] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)  # ohne Default-Route, groesste zuerst
    default: bool = False  # Default-Route ueber die Anbindung
    given: bool = False  # Netze von Hand eingetragen
    ports: set[str] = field(default_factory=set)  # Interfaces bzw. Zonen (von Hand)
    system: int | None = None  # gewaehlte Firewall (`@<Quellsystem>`)
    all_sides: list[Side] | None = None  # vor der Wahl einer Firewall (Auswahl in der Anzeige)

    @property
    def label(self) -> str:
        return label(self.kind, self.name)

    @property
    def family(self) -> int:
        for text in [*self.routers, *self.nets]:
            try:
                return ipaddress.ip_network(text, strict=False).version
            except ValueError:
                continue
        return 4

    def only(self, sid: int | None) -> "End":
        """Nur die Knoten dieser Firewall (Auswahl bei mehreren Firewalls)."""
        if sid is None:
            return self
        sides = [s for s in self.sides if s.key[0] == sid]
        routers = sorted({ip for s in sides for ip in s.routers}) or self.routers
        return replace(self, sides=sides, routers=routers, system=sid,
                       ref=f"{self.ref.split('@', 1)[0]}@{sid}",
                       all_sides=self.all_sides or self.sides)  # fmt: skip

    def address(self, g: Graph, family: int | None = None) -> tuple[str, str]:
        """Adresse fuer die Weg-Suche: (IP bzw. Netz, Art) - `net` = erstes Netz der Anbindung
        (als Ganzes), `internet` = beliebige Adresse im Internet (Default-Route, Internet),
        `unknown` = keine Netze bekannt (ebenfalls die Test-Adresse)."""
        nets = [n for n in self.nets if family in (None, _version(n))]
        if nets and (self.given or self.kind != "internet"):
            return nets[0], "net"
        how = "internet" if self.default or self.kind == "internet" else "unknown"
        return internet_address(g, family or self.family), how

    def entries(self, g: Graph) -> list[tuple[Key, str | None, str | None, str]]:
        """Starts der Weg-Suche (`paths.trace(entries=)`): je Knoten Eingang und Zone."""
        out = []
        for s in self.sides:
            if s.key not in g.nodes:
                continue
            port = f" ({s.port})" if s.port else ""
            via = L(f"über {self.label}{port}", f"via {self.label}{port}")
            out.append((s.key, s.interface, s.zone, via))
        return out

    def reaches(self, path: dict) -> bool:
        """Geht der Weg ueber die Anbindung hinaus? Anbindung am Ausgang, ein Router der
        Anbindung als Next-Hop bzw. am letzten Hop ihr Interface (ihre Zone) nach aussen."""
        keys = {s.key for s in self.sides}
        ports = {p for s in self.sides for p in (s.interface, s.zone) if p} | self.ports
        hops = path.get("hops") or []
        for i, hop in enumerate(hops):
            for h in hop.get("parts") or [hop]:
                eg = h.get("egress") or {}
                if self.system is not None and h.get("source_system_id") != self.system:
                    continue
                if self.exit_id is not None and (eg.get("exit") or {}).get("id") == self.exit_id:
                    return True
                if eg.get("next_hop") and eg["next_hop"] in self.routers:
                    return True
                last = i == len(hops) - 1 and path["end"]["kind"] in ("exit", "external", "unknown")
                key = (h.get("source_system_id"), h.get("context"))
                if last and key in keys and ports & {eg.get("interface"), eg.get("zone")}:
                    return True
        return False

    def out(self, g: Graph, address: str | None = None, how: str | None = None) -> dict:
        """Fuer die Antwort der Weg-Suche: Firewalls (`choices` = alle vor der Wahl), Netze und
        die gepruefte Adresse; `internet` = beliebige Adresse im Internet zur Auswahl."""
        firewalls = []
        for s in self.sides:
            node = g.nodes.get(s.key)
            if node is None:
                continue
            firewalls.append({"key": node.key, "source_system_id": s.key[0], "source": node.source,
                              "label": node.label, "interface": s.interface, "zone": s.zone,
                              "routers": s.routers})  # fmt: skip
        choices: dict[int, str] = {}
        for s in self.all_sides or self.sides:
            if s.key in g.nodes:
                choices.setdefault(s.key[0], g.nodes[s.key].source)
        family = _version(address or "") or self.family
        internet = self.default or self.kind == "internet" or how in ("internet", "unknown")
        return {
            "ref": self.ref,
            "exit_id": self.exit_id,
            "kind": self.kind,
            "name": self.name,
            "label": self.label,
            "manual": self.manual,
            "address": address,
            "address_kind": how,
            "nets": self.nets[:NETS_SHOWN],
            "nets_total": len(self.nets),
            "default": self.default,
            "given": self.given,
            "routers": self.routers,
            "firewalls": firewalls,
            "systems": sorted({f["source_system_id"] for f in firewalls}),
            "system": self.system,
            "choices": [{"source_system_id": k, "source": v} for k, v in choices.items()],
            "internet": internet_address(g, family) if internet else None,
        }


def label(kind: str | None, name: str) -> str:
    """„Internet ISP-A“ - die Art nur, wenn der Name sie nicht schon nennt (wie im Weg)."""
    word = exit_kind(kind, L("Anbindung", "Exit"))
    return name if name.lower().startswith(word.lower()) else f"{word} {name}"


def _version(text: str) -> int | None:
    try:
        return ipaddress.ip_network(text, strict=False).version
    except ValueError:
        return None


def _sorted_nets(prefixes) -> tuple[list[str], bool]:
    """Netze ohne Default-Route (groesste zuerst) und ob eine Default-Route dabei ist."""
    nets, default = [], False
    for p in prefixes or []:
        try:
            net = ipaddress.ip_network(str(p), strict=False)
        except ValueError:
            continue
        if net.prefixlen == 0:
            default = True
        elif str(net) not in nets:
            nets.append(str(net))
    nets.sort(key=exit_store._net_sort)
    return nets, default


def internet_address(g: Graph, family: int = 4) -> str:
    """Beliebige Adresse im Internet: die erste Test-Adresse (Dokumentationsbereich), die an
    keinem gelesenen Geraet angeschlossen ist und in keinem EPG-/BD-Subnetz liegt."""
    known: dict[int, str] = g.__dict__.setdefault("_internet_address", {})  # je Graph einmal
    if family not in known:
        candidates = INTERNET_V6 if family == 6 else INTERNET_V4
        free = (t for t in candidates if _unused(g, ipaddress.ip_address(t)))
        known[family] = next(free, candidates[0])
    return known[family]


def _unused(g: Graph, ip) -> bool:
    return not g.connected_nodes(ip) and not g.fabric(ip)


# -- Anbindungen ermitteln ---------------------------------------------------------------


def _inventory_ports(g: Graph, e: TopoExit) -> list[tuple[Key, str | None, str | None]]:
    """Interfaces bzw. Zonen der Anbindung im Inventar ihrer Firewalls (ohne erkannte Router):
    Interface -> (Knoten, Interface, Zone), Zone -> (Knoten, -, Zone)."""
    ports = set(e.interfaces or [])
    if not ports:
        return []
    sids = set(e.source_system_ids or []) or {
        k[0] for k, n in g.nodes.items() if n.kind in FIREWALL_KINDS
    }
    if not sids:
        return []
    contexts: dict[int, list[str]] = {}
    for sid, ctx in g.nodes:
        contexts.setdefault(sid, []).append(ctx)
    out: list[tuple[Key, str | None, str | None]] = []
    for sid, name, zone, vdom in g.session.execute(
        select(InvInterface.source_system_id, InvInterface.name, InvInterface.zone,
               InvInterface.vdom)
        .where(InvInterface.source_system_id.in_(sorted(sids)))
        .order_by(InvInterface.source_system_id, InvInterface.name)
    ):  # fmt: skip
        ctxs = contexts.get(sid, [])
        key = (sid, vdom or (ctxs[0] if len(ctxs) == 1 else ""))
        if key not in g.nodes:
            continue
        found = (key, name, zone) if name in ports else (key, None, zone) if zone in ports else None
        if found is not None and found not in out:
            out.append(found)
    return out


def _manual(g: Graph, e: TopoExit, found) -> End:
    sides: dict[tuple, Side] = {}
    active: set[str] = set()
    backup: set[str] = set()
    for ip, uses in found.items():
        for key, u in uses.items():
            if u.exit is None or u.exit.id != e.id:
                continue
            side = sides.setdefault((key, u.interface, u.zone),
                                    Side(key, u.interface, u.zone, u.table))  # fmt: skip
            side.routers.append(ip)
            active |= u.active
            backup |= u.backup
    seen = {k[0][0] for k in sides}  # Firewalls mit erkannten Routern
    for key, iface, zone in _inventory_ports(g, e):
        if key[0] not in seen:
            sides.setdefault((key, iface, zone), Side(key, iface, zone, g.table(key, iface)))
    nets, default = _sorted_nets(e.prefixes or active or backup)
    routers = list(dict.fromkeys([*(e.next_hops or []), *(ip for s in sides.values()
                                                          for ip in s.routers)]))  # fmt: skip
    return End(
        ref=f"exit:{e.id}",
        kind=e.kind,
        name=e.name,
        manual=True,
        exit_id=e.id,
        sides=_ordered(g, sides.values()),
        routers=routers,
        nets=nets,
        default=default,
        given=bool(e.prefixes),
        ports=set(e.interfaces or []),
    )


def _detected(g: Graph, ips: list[str], found) -> End:
    sides: dict[tuple, Side] = {}
    active: set[str] = set()
    backup: set[str] = set()
    for ip in ips:
        for key, u in found[ip].items():
            side = sides.setdefault((key, u.interface, u.zone),
                                    Side(key, u.interface, u.zone, u.table))  # fmt: skip
            side.routers.append(ip)
            active |= u.active
            backup |= u.backup
    routers = [exit_store._router(g, ip, found[ip]) for ip in ips]
    ports = sorted({s.port for s in sides.values() if s.port})
    nets, default = _sorted_nets(active or backup)
    return End(
        ref=f"router:{ips[0]}",
        kind=exit_store._kind(routers, ports),
        name=exit_store._name(routers, ports),
        manual=False,
        sides=_ordered(g, sides.values()),
        routers=list(ips),
        nets=nets,
        default=default,
    )


def _ordered(g: Graph, sides) -> list[Side]:
    return sorted(sides, key=lambda s: (g.nodes[s.key].label if s.key in g.nodes else "",
                                        s.interface or "", s.zone or ""))  # fmt: skip


def build(g: Graph, include_covered: bool = False) -> list[End]:
    """Alle Anbindungen: von Hand erfasste und erkannte (ohne ganz abgedeckte bzw. ausgeblendete,
    wie die Vorschlaege; `include_covered` = alle erkannten)."""
    found = exit_store.detect(g)
    out = [_manual(g, e, found) for e in g.exits]
    hidden = exit_store.dismissed(g.session, g.tenant_id)
    for ips in exit_store._groups(g, found):
        uses = [u for ip in ips for u in found[ip].values()]
        if not include_covered:
            if all(u.exit is not None for u in uses):
                continue  # ganz abgedeckt: die Anbindung von Hand steht schon da
            open_ = [ip for ip in ips if any(u.exit is None for u in found[ip].values())]
            if open_ and all(ip in hidden for ip in open_):
                continue
        out.append(_detected(g, ips, found))
    return out


def find(g: Graph, ref: str) -> End | None:
    """Anbindung zur Referenz `exit:<id>` bzw. `router:<IP>` (ein Router des Vorschlags), mit
    `@<Quellsystem>` nur an dieser Firewall; unbekannt -> None."""
    m = _REF.match((ref or "").strip())
    if m is None:
        return None
    what, value, sid = m.group(1), m.group(2), m.group(3)
    found = exit_store.detect(g)
    end = None
    if what == "exit":
        e = next((x for x in g.exits if str(x.id) == value), None)
        end = _manual(g, e, found) if e is not None else None
    else:
        group = next((ips for ips in exit_store._groups(g, found) if value in ips), None)
        end = _detected(g, group, found) if group else None
    if end is None:
        return None
    return end.only(int(sid)) if sid else end


# -- Namenssuche (store/resolve.py) ------------------------------------------------------


def wants_detected(session: Session, tenant_id: int, q: str) -> bool:
    """Sucht die Eingabe auch nach erkannten Anbindungen? Bei Woertern der Art (Internet, MPLS,
    WAN, Anbindung …) bzw. dem Namen einer Firewall, eines Interfaces oder einer Zone - das
    Erkennen liest das ganze Routing, nicht bei jedem Namen."""
    if len(q) < 3:
        return False
    if any(w in q or q in w for w in _WORDS):
        return True
    like = f"%{q}%"
    if session.scalar(
        select(func.count(SourceSystem.id)).where(
            SourceSystem.tenant_id == tenant_id, func.lower(SourceSystem.name).like(like)
        )
    ):
        return True
    return bool(
        session.scalar(
            select(InvInterface.id)
            .where(
                InvInterface.tenant_id == tenant_id,
                or_(func.lower(InvInterface.name).like(like),
                    func.lower(InvInterface.zone).like(like)),
            )
            .limit(1)
        )
    )  # fmt: skip


def from_row(e: TopoExit) -> End:
    """Anbindung von Hand ohne Erkennen (Namenssuche): Netze, Router und Interfaces wie
    eingetragen, die Knoten erst in der Weg-Suche."""
    nets, default = _sorted_nets(e.prefixes)
    return End(ref=f"exit:{e.id}", kind=e.kind, name=e.name, manual=True, exit_id=e.id,
               routers=list(e.next_hops or []), nets=nets, default=default,
               given=bool(e.prefixes), ports=set(e.interfaces or []))  # fmt: skip


def text_of(end: End, systems: list[str]) -> str:
    """Suchtext einer Anbindung: Name, Art (beide Sprachen), Interfaces/Zonen und Firewalls."""
    kind = end.kind or "other"
    ports = [p for s in end.sides for p in (s.interface, s.zone) if p]
    words = [end.name, end.label, kind, EXIT_LABELS.get(kind), _EXIT_LABELS_EN.get(kind),
             "anbindung", "exit", *sorted(end.ports), *ports, *systems]  # fmt: skip
    return " ".join(w for w in words if w).lower()
