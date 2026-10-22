"""Weg einer Verbindung durchs Netz (ADR 0019 Stufe 3, erste Fassung 0.17.0).

Knoten sind Routing-Instanzen: ein FortiGate-VDOM (bzw. die Firewall, wenn sie nur `root`
hat) oder der Rand einer ACI-VRF - alle Border-Leafs der VRF zusammen, vier Anbindungen sind
ein Knoten und nicht vier Wege.

- **Start:** der gewaehlte Startpunkt bzw. der Knoten, an dem die Quelle angeschlossen ist
  (angeschlossenes Netz in der RIB, sonst EPG-/BD-Subnetz einer gelesenen ACI-VRF).
- **Je Hop:** eine Policy-Route bzw. SD-WAN-Regel, die sicher passt, geht vor; sonst das
  laengste Praefix der RIB (ECMP verzweigt) - in der VRF des Eingangs (FortiGate; ohne
  Eingang die Standard-VRF 0, nie die Management-Tabelle).
- **Naechster Knoten:** dem der Next-Hop gehoert (Interface-IP der FortiGate inkl.
  Sekundaer-IPs, SVI/Interface eines L3Outs). Gehoert er keinem gelesenen Geraet, endet der
  Weg dort - kein Raten ueber ein gemeinsames Transfernetz (0.17.1: das fuehrte bei mehreren
  Firewalls am selben Aussenrouter zu Schleifen).
- **Anbindung nach aussen** (0.18.0, von Hand erfasst, `TopoExit`): gehoert der Next-Hop
  keinem gelesenen Geraet, aber passt eine Anbindung (Next-Hop-IP, sonst Interface bzw. Zone
  der Firewall), endet der Weg dort („Internet“, „MPLS …“) - oder geht nach ihren manuellen
  Routen beim gelesenen Knoten weiter (z. B. ueber MPLS zur Firewall eines anderen Standorts).
  0.20.0: fuer eine oder mehrere Firewalls (z. B. beide Session-Sync-Partner); erkannte
  Anbindungen mit allen Routern in `store/exits.py`.
- **Ende:** Ziel angeschlossen, Anbindung, Default-Route ohne gelesenen Nachbarn
  („extern“), Next-Hop ohne gelesenes Geraet, keine Route, Schleife oder Hop-Grenze -
  Next-Hops mit dem Namen aus dem IPAM.
- **Redundante Wege** (0.19.0): neben den aktiven Wegen (ECMP verzweigt, parallele Firewalls
  eines Verbunds) die Backup-Wege - je Weg eine Abweichung an einem Hop ueber einen
  BGP-Pfad bzw. eine statische Route fuer dasselbe Praefix mit anderem Next-Hop (schwebende
  Route, Backup-Provider). Gezeigt werden Backup-Wege, die ein Ziel erreichen und hoechstens
  `slack` Hops laenger sind als der kuerzeste aktive Weg („gleiche oder fast gleiche Laenge“).
- **Regeln auf dem Weg** (0.22.0, `store/policy_check.py`): je FortiGate-Hop die Policies, die
  auf Ein-/Ausgang, Quelle, Ziel und Dienst passen, mit dem Urteil; je Weg das Gesamturteil.
"""

import ipaddress
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    InvDevice,
    InvInterface,
    InvSubnet,
    SourceSystem,
    TopoContext,
    TopoExit,
    TopoL3Out,
    TopoPolicyRoute,
    TopoRoute,
)
from app.store import references
from app.store.topology import MGMT, Table, ip_hex, matches, table_of

MAX_HOPS = 12
MAX_PATHS = 16  # aktive Wege
MAX_BACKUP = 16  # Backup-Wege (vor dem Filter nach Laenge)
REACHED = ("connected", "fabric", "exit", "external")  # Ende = Ziel erreicht bzw. nach aussen
_CONNECTED = ("connected", "local")
Key = tuple[int, str]  # (Quellsystem, Kontext: VDOM bzw. Tenant:VRF)
__all__ = ["MGMT", "Graph", "trace"]

EXIT_LABELS = {
    "internet": "Internet",
    "mpls": "MPLS",
    "wan": "WAN",
    "partner": "Partner",
    "other": "Anbindung",
}


@dataclass
class Node:
    source_system_id: int
    context: str
    kind: str  # firewall | vdom | vrf
    label: str
    source: str
    members: list[str] = field(default_factory=list)  # Hostname bzw. Border-Leafs
    missing: list[str] = field(default_factory=list)
    group: str | None = None  # Verbund paralleler Firewalls (ADR 0020)

    @property
    def key(self) -> str:
        return f"{self.source_system_id}|{self.context}"

    def out(self) -> dict:
        return {
            "key": self.key,
            "source_system_id": self.source_system_id,
            "source": self.source,
            "context": self.context,
            "kind": self.kind,
            "label": self.label,
            "members": self.members,
            "missing": self.missing,
            "group": self.group,
        }


def context_label(kind: str, source: str, node: str, context: str, single: bool) -> tuple[str, str]:
    """(Art, Bezeichnung) eines gelesenen Kontexts: FortiGate nur mit `root` = die Firewall,
    sonst „fw · VDOM x“; ACI je Leaf „leaf-101 · VRF T:V“."""
    if kind == "vdom":
        return ("firewall", source) if single else ("vdom", f"{source} · VDOM {context}")
    return "vrf", f"{node} · VRF {context}"


class Graph:
    """Knoten, Adressen der Knoten (Interfaces, L3Out-SVIs) und IPAM-Namen eines Mandanten."""

    def __init__(self, session: Session, tenant_id: int):
        self.session = session
        self.tenant_id = tenant_id
        names, groups = {}, {}
        for sid, name, group in session.execute(
            select(SourceSystem.id, SourceSystem.name, SourceSystem.parallel_group).where(
                SourceSystem.tenant_id == tenant_id
            )
        ):
            names[sid], groups[sid] = name, group
        contexts = list(
            session.scalars(
                select(TopoContext)
                .where(TopoContext.tenant_id == tenant_id)
                .order_by(TopoContext.source_system_id, TopoContext.context, TopoContext.node)
            )
        )
        vdoms = Counter(c.source_system_id for c in contexts if c.kind == "vdom")
        self.nodes: dict[Key, Node] = {}
        self.labels: dict[tuple[int, str, str], tuple[str, str]] = {}
        for c in contexts:
            source = names.get(c.source_system_id, f"#{c.source_system_id}")
            single = vdoms[c.source_system_id] == 1
            self.labels[(c.source_system_id, c.node, c.context)] = context_label(
                c.kind, source, c.node, c.context, single
            )
            key = (c.source_system_id, c.context)
            node = self.nodes.get(key)
            if node is None:
                kind, label = context_label(c.kind, source, c.node, c.context, single)
                if c.kind == "vrf":
                    label = f"{source} · VRF {c.context}"
                node = self.nodes[key] = Node(c.source_system_id, c.context, kind, label, source)
                node.group = groups.get(c.source_system_id)
            node.members.append(c.node)
            node.missing += [m for m in c.missing or [] if m not in node.missing]
        self.refs = references.context(session, tenant_id)
        self._owners: dict[str, list[tuple[Key, str | None, str | None]]] | None = None
        self._zones: dict[tuple[int, str], str] = {}
        self._tables: dict[Key, dict[str, Table]] = {}
        self.exits = list(
            session.scalars(
                select(TopoExit).where(TopoExit.tenant_id == tenant_id).order_by(TopoExit.id)
            )
        )

    # -- Bezeichnungen --------------------------------------------------------------

    def label(self, source_system_id: int, node: str, context: str) -> tuple[str, str]:
        return self.labels.get((source_system_id, node, context), ("", f"{node} · {context}"))

    def names(self, ip: str | None) -> list[str]:
        """DNS-Namen bzw. Namen der IP aus dem IPAM (fuehrende Quelle und Anreicherung)."""
        return references.dns_names(self.refs, ip) if ip else []

    def network(self, ip: str | None) -> str | None:
        """Ohne Namen der IP: das kleinste IPAM-Netz mit Namen bzw. Beschreibung."""
        if not ip:
            return None
        for idx in [self.refs.primary, *self.refs.enrichments]:
            if idx is None:
                continue
            for r in idx.containing_prefixes(ip):
                text = r.name or r.description
                if text and not r.value.endswith("/0"):
                    return f"{text} ({r.value})"
        return None

    def resolve(self, ip: str | None) -> dict:
        """Namen (IPAM) und - ohne Namen - das Netz; fuer Next-Hops und Nachbarn."""
        names = self.names(ip)
        net = None if names or not ip else self.network(ip)
        return {k: v for k, v in (("names", names), ("network", net)) if v}

    # -- Adressen der Knoten ------------------------------------------------------------

    def owners(self) -> dict[str, list[tuple[Key, str | None, str | None]]]:
        """IP -> (Knoten, Interface, Zone): FortiGate-Interfaces und L3Out-SVIs/-Interfaces."""
        if self._owners is not None:
            return self._owners
        out: dict[str, list[tuple[Key, str | None, str | None]]] = defaultdict(list)
        sources = {k[0] for k in self.nodes}
        single = defaultdict(list)
        for key in self.nodes:
            single[key[0]].append(key[1])
        if sources:
            for i in self.session.scalars(
                select(InvInterface).where(InvInterface.source_system_id.in_(sources))
            ):
                if i.zone:
                    self._zones[(i.source_system_id, i.name)] = i.zone
                ctxs = single[i.source_system_id]
                context = i.vdom or (ctxs[0] if len(ctxs) == 1 else None)
                key = (i.source_system_id, context or "")
                if key not in self.nodes:
                    continue
                for ip in i.ips or []:
                    out[str(ip).split("/")[0]].append((key, i.name, i.zone))
            for o in self.session.scalars(
                select(TopoL3Out).where(TopoL3Out.source_system_id.in_(sources))
            ):
                key = (o.source_system_id, o.vrf or "")
                if key not in self.nodes:
                    continue
                for path in o.paths or []:
                    for entry in path.get("ips") or []:
                        ip = str(entry.get("ip") or "").split("/")[0]
                        if ip:
                            out[ip].append((key, path.get("interface"), None))
        self._owners = out
        return out

    def owner_label(self, ip: str | None) -> str | None:
        found = self.owners().get(ip or "", [])
        return self.nodes[found[0][0]].label if found else None

    def zone(self, source_system_id: int, interface: str | None) -> str | None:
        self.owners()  # laedt die Zonen
        return self._zones.get((source_system_id, interface or ""))

    # -- Routen -------------------------------------------------------------------------

    def _routes(self, ip, key: Key | None = None, connected: bool = False) -> list[TopoRoute]:
        hx = ip_hex(ip)
        stmt = select(TopoRoute).where(
            TopoRoute.tenant_id == self.tenant_id,
            TopoRoute.family == ip.version,
            TopoRoute.table == "rib",
            TopoRoute.best.is_(True),
            TopoRoute.lo <= hx,
            TopoRoute.hi >= hx,
        )
        if key is not None:
            stmt = stmt.where(TopoRoute.source_system_id == key[0], TopoRoute.context == key[1])
        if connected:
            stmt = stmt.where(TopoRoute.protocol.in_(_CONNECTED))
        return list(self.session.scalars(stmt))

    def route(self, key: Key, ip, table: Table = 0) -> dict | None:
        """Wirksame Route des Knotens in der VRF `table`: laengstes Praefix, kleinste Distanz;
        Next-Hops aller Mitglieder (Border-Leafs) zusammen."""
        rows = [r for r in self._routes(ip, key) if table_of(r) == table]
        if not rows:
            return None
        length = max(r.prefix_len for r in rows)
        rows = [r for r in rows if r.prefix_len == length]
        distance = min((r.distance or 0) for r in rows)
        rows = [r for r in rows if (r.distance or 0) == distance]
        hops: list[dict] = []
        for r in rows:
            for h in r.next_hops or []:
                if h not in hops:
                    hops.append(h)
        first = rows[0]
        return {
            "prefix": first.prefix,
            "prefix_len": first.prefix_len,
            "protocol": first.protocol,
            "distance": first.distance,
            "next_hops": hops,
            "members": sorted({r.node for r in rows}),
        }

    def connected_nodes(self, ip) -> list[tuple[Key, str | None, Table]]:
        """Knoten mit einem angeschlossenen Netz, das die IP enthaelt (laengstes je Knoten, bei
        gleicher Laenge die Standard-VRF): Knoten, Interface, VRF."""
        best: dict[Key, TopoRoute] = {}
        for r in self._routes(ip, connected=True):
            key = (r.source_system_id, r.context)
            if key not in self.nodes:
                continue
            rank = (r.prefix_len, table_of(r) == 0)
            if key not in best or rank > (best[key].prefix_len, table_of(best[key]) == 0):
                best[key] = r
        out = []
        for key, r in best.items():
            ifaces = [h.get("interface") for h in r.next_hops or [] if h.get("interface")]
            out.append((key, ifaces[0] if ifaces else None, table_of(r)))
        return out

    def backups(self, key: Key, ip, table: Table, route: dict) -> list[dict]:
        """Backup-Next-Hops fuer das Praefix der aktiven Route: BGP-Pfade und eingeschaltete
        statische Routen derselben VRF mit einem anderen Next-Hop (z. B. schwebende Route mit
        hoeherer Distanz, zweiter Provider)."""
        hx = ip_hex(ip)
        active = {h.get("ip") for h in route.get("next_hops") or []}
        out: list[dict] = []
        for r in self.session.scalars(
            select(TopoRoute)
            .where(
                TopoRoute.source_system_id == key[0],
                TopoRoute.context == key[1],
                TopoRoute.family == ip.version,
                TopoRoute.table.in_(("bgp", "static")),
                TopoRoute.prefix == route["prefix"],
                TopoRoute.lo <= hx,
                TopoRoute.hi >= hx,
            )
            .order_by(TopoRoute.table, TopoRoute.distance, TopoRoute.id)
        ):
            if table_of(r) != table or (r.table == "static" and not r.best):
                continue  # andere VRF bzw. abgeschaltete statische Route
            for h in r.next_hops or []:
                nh = h.get("ip")
                if not nh or nh in active or any(o["next_hop"]["ip"] == nh for o in out):
                    continue
                attrs = r.attrs or {}
                if r.table == "bgp":
                    path = f" (AS-Pfad {attrs['as_path']})" if attrs.get("as_path") else ""
                    reason = f"BGP-Backup{path}"
                else:
                    reason = "statische Backup-Route" + (
                        f" (Distanz {r.distance})" if r.distance is not None else ""
                    )
                hop = dict(h)
                if not hop.get("interface"):  # BGP-Pfade ohne Interface: das Netz des Next-Hops
                    hop["interface"] = self.interface_to(key, nh, table)
                out.append({"next_hop": hop, "prefix": r.prefix, "protocol": r.protocol,
                            "distance": r.distance, "reason": reason})  # fmt: skip
        return out

    def interface_to(self, key: Key, ip_text: str, table: Table) -> str | None:
        """Interface, ueber das der Knoten eine IP direkt erreicht (angeschlossenes Netz)."""
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            return None
        rows = [r for r in self._routes(ip, key, connected=True) if table_of(r) == table]
        rows.sort(key=lambda r: -r.prefix_len)
        for r in rows:
            for h in r.next_hops or []:
                if h.get("interface"):
                    return h["interface"]
        return None

    def table(self, key: Key, interface: str | None) -> Table:
        """VRF eines Interfaces - aus seinen angeschlossenen Netzen (fehlt = 0)."""
        if key not in self._tables:
            tables: dict[str, Table] = {}
            for r in self.session.scalars(
                select(TopoRoute).where(
                    TopoRoute.source_system_id == key[0],
                    TopoRoute.context == key[1],
                    TopoRoute.table == "rib",
                    TopoRoute.protocol.in_(_CONNECTED),
                )
            ):
                for h in r.next_hops or []:
                    if h.get("interface"):
                        tables.setdefault(h["interface"], table_of(r))
            self._tables[key] = tables
        return self._tables[key].get(interface or "", 0)

    def fabric(self, ip, key: Key | None = None) -> list[tuple[Key, str]]:
        """ACI-VRF-Knoten, deren EPG- bzw. BD-Subnetz die IP enthaelt, mit dem Ort im Fabric
        (`Tenant|AP|EPG` bzw. BD); laengstes Subnetz je Knoten."""
        best: dict[Key, tuple[int, str]] = {}
        stmt = select(InvSubnet).where(
            InvSubnet.tenant_id == self.tenant_id, InvSubnet.context.in_(("bd", "epg"))
        )
        if key is not None:
            stmt = stmt.where(InvSubnet.source_system_id == key[0], InvSubnet.vrf == key[1])
        for s in self.session.scalars(stmt):
            try:
                net = ipaddress.ip_network(s.prefix, strict=False)
            except ValueError:
                continue
            nkey = (s.source_system_id, s.vrf or "")
            if ip not in net or nkey not in self.nodes:
                continue
            where = (
                f"EPG {s.aci_tenant}|{s.ap}|{s.epg}"
                if s.epg and s.ap
                else f"BD {s.aci_tenant}|{s.bd or s.context_name}"
            )
            text = f"{where} ({s.prefix})"
            if nkey not in best or net.prefixlen > best[nkey][0]:
                best[nkey] = (net.prefixlen, text)
        return [(k, v[1]) for k, v in best.items()]

    def exit_for(
        self, key: Key, interface: str | None, zone: str | None, ip: str | None
    ) -> TopoExit | None:
        """Anbindung fuer einen Ausgang: zuerst ueber die Next-Hop-IP, dann ueber Interface
        bzw. Zone - Anbindungen dieser Firewall (auch mehrerer, z. B. beider Session-Sync-
        Partner) vor solchen fuer alle."""
        ports = {p for p in (interface, zone) if p}
        own = [e for e in self.exits if key[0] in (e.source_system_ids or [])]
        ranked = own + [e for e in self.exits if not e.source_system_ids]
        if ip:
            for e in ranked:
                if ip in (e.next_hops or []):
                    return e
        for e in ranked:
            if ports & set(e.interfaces or []):
                return e
        return None

    def policies(self, key: Key, src, dst, ingress: str | None) -> list[dict]:
        """Policy-Routen bzw. SD-WAN-Regeln des Knotens, die fuer den Verkehr gelten (koennen):
        `certain` = Quelle, Ziel und Eingangs-Interface passen sicher."""
        out = []
        for p in self.session.scalars(
            select(TopoPolicyRoute)
            .where(
                TopoPolicyRoute.source_system_id == key[0],
                TopoPolicyRoute.context == key[1],
                TopoPolicyRoute.enabled.is_(True),
            )
            .order_by(TopoPolicyRoute.kind, TopoPolicyRoute.seq)
        ):
            m = p.match or {}
            devices = m.get("input_device") or []
            checks = [
                matches(p.kind, m, "dst", dst),
                matches(p.kind, m, "src", src),
                True if not devices else (ingress in devices if ingress else None),
            ]
            if False in checks:
                continue
            out.append(
                {
                    "kind": p.kind,
                    "seq": p.seq,
                    "name": p.name,
                    "gateway": p.gateway,
                    "output_device": p.output_device or [],
                    "certain": all(c is True for c in checks),
                }
            )
        return out


def parse_key(value: str | None) -> Key | None:
    if not value or "|" not in value:
        return None
    sid, _, context = value.partition("|")
    return (int(sid), context) if sid.isdigit() else None


def trace(
    session: Session,
    tenant_id: int,
    dst_text: str,
    src_text: str | None = None,
    start: str | None = None,
    slack: int = 1,
    service: tuple[str, int | None] | None = None,
) -> dict:
    """Weg von der Quelle (bzw. dem Startpunkt) zum Ziel: je Pfad die Hops mit Ein- und
    Ausgang (Interface, Zone), Route bzw. Policy-Route und Next-Hop, dazu das Ende - die
    aktiven Wege und die Backup-Wege mit hoechstens `slack` Hops mehr; je FortiGate-Hop die
    passenden Policies (Dienst optional, z. B. `("tcp", 443)`)."""
    from app.store import policy_check  # Regeln je Hop - spaet importiert (Regelindex)

    dst = ipaddress.ip_address(dst_text.strip())
    src = ipaddress.ip_address(src_text.strip()) if src_text and src_text.strip() else None
    g = Graph(session, tenant_id)
    notes: list[str] = []
    starts: list[tuple[Key, str | None, str]] = []
    chosen = parse_key(start)
    if start and chosen not in g.nodes:
        notes.append("Startpunkt unbekannt – neu einlesen bzw. einen anderen wählen")
    elif chosen is not None:
        # Eingang (und damit VRF) am Startpunkt: das angeschlossene Netz der Quelle, falls bekannt
        found = g.connected_nodes(src) if src else []
        iface = next((i for k, i, _ in found if k == chosen), None)
        starts = [(chosen, iface, "gewählt")]
    elif src is not None:
        starts = [(k, iface, "Quelle angeschlossen") for k, iface, _ in g.connected_nodes(src)]
        if not starts:
            starts = [(k, None, f"Quelle im Fabric: {text}") for k, text in g.fabric(src)]
        if not starts:
            notes.append(
                f"Kein gelesenes Gerät hat {src} angeschlossen – Startpunkt wählen bzw. das "
                "Gerät mit diesem Netz einlesen."
            )
    paths: list[dict] = []
    backups_found: list[dict] = []
    # (Hops, Knoten, Eingang, wie erreicht, VRF, Abweichung) - aktive Wege zuerst
    active = [([], key, iface, via, g.table(key, iface), None) for key, iface, via in starts]
    backup: list[tuple] = []
    cut = False
    while active or backup:
        if active and len(paths) >= MAX_PATHS or not active and len(backups_found) >= MAX_BACKUP:
            cut = True
            break
        hops, key, ingress, via, table, dev = (active or backup).pop(0)
        out = paths if dev is None else backups_found

        node = g.nodes[key]
        hop = {**node.out(), "via": via, "ingress": _port(g, key, ingress)}
        if table != 0:
            hop["vrf"] = table
        if any((h["key"], h.get("vrf", 0)) == (node.key, table) for h in hops):
            _record(out, hops, hop, "loop", f"Schleife: {node.label} ist schon auf dem Weg", dev)
            continue
        if len(hops) >= MAX_HOPS:
            _record(out, hops, hop, "max_hops", f"mehr als {MAX_HOPS} Hops – abgebrochen", dev)
            continue
        policies = g.policies(key, src, dst, ingress)
        hop["policy_routes"] = policies
        follow = next(
            (p for p in policies if p["certain"] and (p["gateway"] or p["output_device"])), None
        )
        route = g.route(key, dst, table)
        hop["route"] = route
        inside = g.fabric(dst, key) if node.kind == "vrf" else []
        if follow is not None:
            hop["policy"] = follow
            devices = follow["output_device"] or [None]
            next_hops = [{"ip": follow["gateway"], "interface": devices[0]}]
        elif inside:
            _record(out, hops, hop, "fabric", f"Ziel im Fabric: {inside[0][1]}", dev)
            continue
        elif route is None:
            text = "keine Route zum Ziel" + (f" (VRF {table})" if table != 0 else "")
            if node.missing:
                text = "keine Route bekannt – Routing-Tabelle nicht (ganz) lesbar"
            _record(out, hops, hop, "no_route", text, dev)
            continue
        elif route["protocol"] in _CONNECTED or not any(h.get("ip") for h in route["next_hops"]):
            iface = next((h.get("interface") for h in route["next_hops"]), None)
            hop["egress"] = _port(g, key, iface)
            where = f" ({iface})" if iface else ""
            _record(out, hops, hop, "connected", f"Ziel angeschlossen an {node.label}{where}", dev)
            continue
        else:
            next_hops = route["next_hops"]
        branches = [(nh, hop, dev) for nh in next_hops]
        # eine Abweichung je Weg: Backup-Next-Hops dieses Knotens (nicht bei Policy-Routen)
        if dev is None and follow is None and route is not None:
            for b in g.backups(key, dst, table, route):
                info = {k: b[k] for k in ("prefix", "protocol", "distance", "reason")}
                deviation = {"hop": len(hops), "label": node.label, "reason": b["reason"]}
                branches.append((b["next_hop"], {**hop, "backup": info}, deviation))
        for nh, base, deviation in branches:
            queue = active if deviation is None else backup
            target = paths if deviation is None else backups_found
            ip = nh.get("ip")
            egress = {**_port(g, key, nh.get("interface")), "next_hop": ip, **g.resolve(ip)}
            branch = {**base, "egress": egress}

            nxt = _neighbors(g, key, ip)
            for nkey, niface, how in nxt:
                queue.append(([*hops, branch], nkey, niface, how, g.table(nkey, niface), deviation))
            if nxt:
                continue
            ex = g.exit_for(key, nh.get("interface"), egress.get("zone"), ip)
            if ex is not None:
                egress["exit"] = {"id": ex.id, "name": ex.name, "kind": ex.kind}
                target_key = _exit_target(ex, dst)
                if target_key is not None and target_key in g.nodes:
                    via_exit = f"über {_exit_label(ex)} (manuelle Route)"
                    queue.append(([*hops, branch], target_key, None, via_exit, 0, deviation))
                    continue
                text = _exit_text(ex, dst, ip, g.names(ip))
                _record(target, hops, branch, "exit", text, deviation)
                continue
            known = g.names(ip) or ([egress["network"]] if egress.get("network") else [])
            name = f" ({', '.join(known)})" if known else ""
            if not ip:
                kind, text = "unknown", f"weiter über {nh.get('interface') or '?'} ohne Next-Hop"
            elif route is not None and route["prefix_len"] == 0 and follow is None:
                kind, text = "external", f"weiter nach extern über {ip}{name} – Default-Route"
            else:
                kind, text = "unknown", f"Next-Hop {ip}{name} gehört keinem gelesenen Gerät"
            _record(target, hops, branch, kind, text, deviation)
    if cut:
        notes.append(f"mehr als {MAX_PATHS} Wege – gekürzt")
    primary = _unique(paths)
    for p in primary:
        p["kind"] = "active"
    _mark_parallel(primary)
    shown, hidden = _backups(primary, _unique(backups_found), slack)
    if hidden:
        notes.append(
            f"{hidden} weitere Backup-Wege ausgeblendet (mehr als {slack} Hop länger bzw. "
            "ohne Ziel)"
        )
    _mark_length(primary + shown)
    policy_check.check_paths(session, tenant_id, primary + shown, src, dst, service)
    return {
        "start": [g.nodes[k].out() | {"via": via} for k, _, via in starts],
        "paths": primary + shown,
        "notes": notes,
        "slack": slack,
        "service": _service_text(service),
    }


def _service_text(service: tuple[str, int | None] | None) -> str | None:
    if service is None:
        return None
    return f"{service[0]}/{service[1]}" if service[1] else service[0]


def _signature(p: dict) -> tuple:
    return tuple(
        (h["key"], *((h.get("egress") or {}).get(k) for k in ("next_hop", "interface")))
        for h in p["hops"]
    ) + (p["end"]["kind"],)


def _mark_parallel(primary: list[dict]) -> None:
    """Aktive Wege, die sich nur in Firewalls desselben Verbunds unterscheiden, sind redundant
    ueber den Verbund (z. B. ECMP auf zwei Cluster mit Session-Sync)."""
    seen: dict[tuple, list[dict]] = defaultdict(list)
    for p in primary:
        sig = tuple(h.get("group") or h["key"] for h in p["hops"]) + (p["end"]["kind"],)
        seen[sig].append(p)
    for same in seen.values():
        keys = {tuple(h["key"] for h in p["hops"]) for p in same}
        if len(keys) > 1:
            groups = sorted({h["group"] for p in same for h in p["hops"] if h.get("group")})
            for p in same:
                p["parallel"] = groups


def _mark_length(paths: list[dict]) -> None:
    """Laenge im Vergleich (0.20.0): `extra` = Hops mehr als der kuerzeste Weg, der ein Ziel
    erreicht (0 = gleich lang); bei Wegen ohne Ziel `None`."""
    reached = [len(p["hops"]) for p in paths if p["end"]["kind"] in REACHED]
    shortest = min(reached) if reached else None
    for p in paths:
        ok = shortest is not None and p["end"]["kind"] in REACHED
        p["extra"] = len(p["hops"]) - shortest if ok else None


def _backups(primary: list[dict], found: list[dict], slack: int) -> tuple[list[dict], int]:
    """Backup-Wege, die ein Ziel erreichen und hoechstens `slack` Hops laenger sind als der
    kuerzeste aktive Weg; ohne doppelte zu aktiven Wegen. Rueckgabe: (gezeigt, ausgeblendet)."""
    reached = [len(p["hops"]) for p in primary if p["end"]["kind"] in REACHED]
    shortest = min(reached or [len(p["hops"]) for p in primary] or [0])
    known = {_signature(p) for p in primary}
    shown, hidden = [], 0
    for p in found:
        if _signature(p) in known:
            continue
        if p["end"]["kind"] not in REACHED or (primary and len(p["hops"]) > shortest + slack):
            hidden += 1
            continue
        p["kind"] = "backup"
        shown.append(p)
    shown.sort(key=lambda p: len(p["hops"]))
    return shown, hidden


def _exit_target(ex: TopoExit, dst) -> Key | None:
    """Manuelle Route der Anbindung mit dem laengsten passenden Praefix -> Knoten."""
    best: tuple[int, Key] | None = None
    for r in ex.routes or []:
        try:
            net = ipaddress.ip_network(r.get("prefix") or "", strict=False)
        except ValueError:
            continue
        key = parse_key(r.get("target"))
        if key is not None and dst in net and (best is None or net.prefixlen > best[0]):
            best = (net.prefixlen, key)
    return best[1] if best else None


def _exit_label(ex: TopoExit) -> str:
    """„MPLS Kunde A“ - die Art nur, wenn der Name sie nicht schon nennt."""
    label = EXIT_LABELS.get(ex.kind, "Anbindung")
    return ex.name if ex.name.lower().startswith(label.lower()) else f"{label} {ex.name}"


def _exit_text(ex: TopoExit, dst, ip: str | None, names: list[str]) -> str:
    text = f"weiter über {_exit_label(ex)}"
    if names:
        text += f" ({', '.join(names)})"
    routers = ex.next_hops or []
    if ip in routers and len(routers) > 1:
        text += f" – Router {routers.index(ip) + 1} von {len(routers)}"
    nets = []
    for p in ex.prefixes or []:
        try:
            nets.append(ipaddress.ip_network(p, strict=False))
        except ValueError:
            continue
    if nets and not any(dst in n for n in nets):
        text += " – Ziel liegt nicht in den Netzen der Anbindung"
    return text


def _port(g: Graph, key: Key, interface: str | None) -> dict:
    out: dict = {"interface": interface}
    zone = g.zone(key[0], interface)
    if zone:
        out["zone"] = zone
    return out


def _neighbors(g: Graph, key: Key, ip: str | None) -> list[tuple[Key, str | None, str]]:
    """Knoten hinter dem Next-Hop: dem die Adresse gehoert (Interface, L3Out-SVI). Ein
    gemeinsames Transfernetz allein reicht nicht - dort haengt meist ein Aussenrouter."""
    exact: list[tuple[Key, str | None, str]] = []
    for nkey, iface, _zone in g.owners().get(ip or "", []):
        if nkey != key and all(nkey != e[0] for e in exact):
            exact.append((nkey, iface, "Adresse des Nachbarn"))
    return exact


def _end(hops: list, hop: dict, kind: str, text: str) -> dict:
    return {"hops": [*hops, hop], "end": {"kind": kind, "text": text}}


def _record(out: list, hops: list, hop: dict, kind: str, text: str, deviation) -> None:
    """Weg mit seinem Ende ablegen; Backup-Wege mit der Abweichung (Hop, Grund)."""
    out.append({**_end(hops, hop, kind, text), **({"deviation": deviation} if deviation else {})})


def _unique(paths: list[dict]) -> list[dict]:
    """Gleiche Wege (z. B. ueber zwei Next-Hops zum selben Nachbarn) nur einmal."""
    seen, out = set(), []
    for p in paths:
        sig = _signature(p)
        if sig not in seen:
            seen.add(sig)
            out.append(p)
    return out


def device_names(session: Session, source_ids: set[int]) -> dict[tuple[int, str], str]:
    """ACI: Knoten-ID -> Leaf-Name (Inventar), fuer L3Out-Pfade."""
    if not source_ids:
        return {}
    return {
        (d.source_system_id, d.node_id): d.name
        for d in session.scalars(
            select(InvDevice).where(
                InvDevice.source_system_id.in_(source_ids), InvDevice.node_id.is_not(None)
            )
        )
    }
