"""Weg einer Verbindung durchs Netz (ADR 0019 Stufe 3, erste Fassung 0.17.0).

Knoten sind Routing-Instanzen: ein FortiGate-VDOM (bzw. die Firewall, wenn sie nur `root`
hat) oder der Rand einer ACI-VRF - alle Border-Leafs der VRF zusammen, vier Anbindungen sind
ein Knoten und nicht vier Wege.

- **Start:** der gewaehlte Startpunkt bzw. der Knoten, an dem die Quelle angeschlossen ist
  (angeschlossenes Netz in der RIB, sonst EPG-/BD-Subnetz einer gelesenen ACI-VRF).
- **Je Hop:** eine Policy-Route bzw. SD-WAN-Regel, die sicher passt, geht vor; sonst das
  laengste Praefix der RIB (ECMP verzweigt).
- **Naechster Knoten:** dem der Next-Hop gehoert (Interface-IP der FortiGate, SVI/Interface
  eines L3Outs); sonst ein Knoten mit diesem Transfernetz (vermutet).
- **Ende:** Ziel angeschlossen, Default-Route ohne gelesenen Nachbarn („extern/Internet“),
  Next-Hop ohne gelesenes Geraet, keine Route, Schleife oder Hop-Grenze - Next-Hops mit dem
  Namen aus dem IPAM.
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
    TopoL3Out,
    TopoPolicyRoute,
    TopoRoute,
)
from app.store import references
from app.store.topology import ip_hex, matches

MAX_HOPS = 12
MAX_PATHS = 16
_CONNECTED = ("connected", "local")

Key = tuple[int, str]  # (Quellsystem, Kontext: VDOM bzw. Tenant:VRF)


@dataclass
class Node:
    source_system_id: int
    context: str
    kind: str  # firewall | vdom | vrf
    label: str
    source: str
    members: list[str] = field(default_factory=list)  # Hostname bzw. Border-Leafs
    missing: list[str] = field(default_factory=list)

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
        names = dict(
            session.execute(
                select(SourceSystem.id, SourceSystem.name).where(
                    SourceSystem.tenant_id == tenant_id
                )
            ).all()
        )
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
            node.members.append(c.node)
            node.missing += [m for m in c.missing or [] if m not in node.missing]
        self.refs = references.context(session, tenant_id)
        self._owners: dict[str, list[tuple[Key, str | None, str | None]]] | None = None
        self._zones: dict[tuple[int, str], str] = {}

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

    def route(self, key: Key, ip) -> dict | None:
        """Wirksame Route des Knotens: laengstes Praefix, kleinste Distanz; Next-Hops aller
        Mitglieder (Border-Leafs) zusammen."""
        rows = self._routes(ip, key)
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

    def connected_nodes(self, ip, exclude: Key | None = None) -> list[tuple[Key, str | None]]:
        """Knoten mit einem angeschlossenen Netz, das die IP enthaelt (laengstes je Knoten)."""
        best: dict[Key, TopoRoute] = {}
        for r in self._routes(ip, connected=True):
            key = (r.source_system_id, r.context)
            if key == exclude or key not in self.nodes:
                continue
            if key not in best or r.prefix_len > best[key].prefix_len:
                best[key] = r
        out = []
        for key, r in best.items():
            ifaces = [h.get("interface") for h in r.next_hops or [] if h.get("interface")]
            out.append((key, ifaces[0] if ifaces else None))
        return out

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
) -> dict:
    """Weg von der Quelle (bzw. dem Startpunkt) zum Ziel: je Pfad die Hops mit Ein- und
    Ausgang (Interface, Zone), Route bzw. Policy-Route und Next-Hop, dazu das Ende."""
    dst = ipaddress.ip_address(dst_text.strip())
    src = ipaddress.ip_address(src_text.strip()) if src_text and src_text.strip() else None
    g = Graph(session, tenant_id)
    notes: list[str] = []
    starts: list[tuple[Key, str | None, str]] = []
    chosen = parse_key(start)
    if start and chosen not in g.nodes:
        notes.append("Startpunkt unbekannt – neu einlesen bzw. einen anderen wählen")
    elif chosen is not None:
        # Eingang am Startpunkt: das angeschlossene Netz der Quelle, falls bekannt
        iface = next((i for k, i in g.connected_nodes(src) if k == chosen), None) if src else None
        starts = [(chosen, iface, "gewählt")]
    elif src is not None:
        starts = [(k, iface, "Quelle angeschlossen") for k, iface in g.connected_nodes(src)]
        if not starts:
            starts = [(k, None, f"Quelle im Fabric: {text}") for k, text in g.fabric(src)]
        if not starts:
            notes.append(
                f"Kein gelesenes Gerät hat {src} angeschlossen – Startpunkt wählen bzw. das "
                "Gerät mit diesem Netz einlesen."
            )
    paths: list[dict] = []
    queue = [([], key, iface, via) for key, iface, via in starts]
    while queue and len(paths) < MAX_PATHS:
        hops, key, ingress, via = queue.pop(0)
        node = g.nodes[key]
        hop = {**node.out(), "via": via, "ingress": _port(g, key, ingress)}
        if any(h["key"] == node.key for h in hops):
            paths.append(_end(hops, hop, "loop", f"Schleife: {node.label} ist schon auf dem Weg"))
            continue
        if len(hops) >= MAX_HOPS:
            paths.append(_end(hops, hop, "max_hops", f"mehr als {MAX_HOPS} Hops – abgebrochen"))
            continue
        policies = g.policies(key, src, dst, ingress)
        hop["policy_routes"] = policies
        follow = next(
            (p for p in policies if p["certain"] and (p["gateway"] or p["output_device"])), None
        )
        route = g.route(key, dst)
        hop["route"] = route
        inside = g.fabric(dst, key) if node.kind == "vrf" else []
        if follow is not None:
            hop["policy"] = follow
            devices = follow["output_device"] or [None]
            next_hops = [{"ip": follow["gateway"], "interface": devices[0]}]
        elif inside:
            paths.append(_end(hops, hop, "fabric", f"Ziel im Fabric: {inside[0][1]}"))
            continue
        elif route is None:
            text = "keine Route zum Ziel"
            if node.missing:
                text = "keine Route bekannt – Routing-Tabelle nicht (ganz) lesbar"
            paths.append(_end(hops, hop, "no_route", text))
            continue
        elif route["protocol"] in _CONNECTED or not any(h.get("ip") for h in route["next_hops"]):
            iface = next((h.get("interface") for h in route["next_hops"]), None)
            hop["egress"] = _port(g, key, iface)
            where = f" ({iface})" if iface else ""
            paths.append(_end(hops, hop, "connected", f"Ziel angeschlossen an {node.label}{where}"))
            continue
        else:
            next_hops = route["next_hops"]
        for nh in next_hops:
            ip = nh.get("ip")
            egress = {**_port(g, key, nh.get("interface")), "next_hop": ip, **g.resolve(ip)}
            branch = {**hop, "egress": egress}
            nxt = _neighbors(g, key, ip)
            for nkey, niface, how in nxt:
                queue.append(([*hops, branch], nkey, niface, how))
            if nxt:
                continue
            known = g.names(ip) or ([egress["network"]] if egress.get("network") else [])
            name = f" ({', '.join(known)})" if known else ""
            if not ip:
                kind, text = "unknown", f"weiter über {nh.get('interface') or '?'} ohne Next-Hop"
            elif route is not None and route["prefix_len"] == 0 and follow is None:
                kind, text = "external", f"weiter nach extern über {ip}{name} – Default-Route"
            else:
                kind, text = "unknown", f"Next-Hop {ip}{name} gehört keinem gelesenen Gerät"
            paths.append(_end(hops, branch, kind, text))
    if queue:
        notes.append(f"mehr als {MAX_PATHS} Wege – gekürzt")
    return {
        "start": [g.nodes[k].out() | {"via": via} for k, _, via in starts],
        "paths": _unique(paths),
        "notes": notes,
    }


def _port(g: Graph, key: Key, interface: str | None) -> dict:
    out: dict = {"interface": interface}
    zone = g.zone(key[0], interface)
    if zone:
        out["zone"] = zone
    return out


def _neighbors(g: Graph, key: Key, ip: str | None) -> list[tuple[Key, str | None, str]]:
    """Knoten hinter dem Next-Hop: dem die Adresse gehoert, sonst mit diesem Transfernetz."""
    if not ip:
        return []
    exact: list[tuple[Key, str | None, str]] = []
    for nkey, iface, _zone in g.owners().get(ip, []):
        if nkey != key and all(nkey != e[0] for e in exact):
            exact.append((nkey, iface, "Adresse des Nachbarn"))
    if exact:
        return exact
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return []
    return [(k, iface, "Transfernetz (vermutet)") for k, iface in g.connected_nodes(addr, key)]


def _end(hops: list, hop: dict, kind: str, text: str) -> dict:
    return {"hops": [*hops, hop], "end": {"kind": kind, "text": text}}


def _unique(paths: list[dict]) -> list[dict]:
    """Gleiche Wege (z. B. ueber zwei Next-Hops zum selben Nachbarn) nur einmal."""
    seen, out = set(), []
    for p in paths:
        sig = tuple(
            (h["key"], *((h.get("egress") or {}).get(k) for k in ("next_hop", "interface")))
            for h in p["hops"]
        ) + (p["end"]["kind"],)
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
