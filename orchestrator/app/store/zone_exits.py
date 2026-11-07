"""Netze der Zonen gegen das Routing pruefen (0.105.0, seit 0.106.0 ohne Ausgaenge von Hand,
ADR 0035).

Anwender 2026-10-28: „Findings, wenn ich die Netzwerke nicht von dort lerne, wo ich sie erwarte
gemaess Netzwerk-Zone“ und „aus den Daten muesste ersichtlich sein, ueber welches L3O die Routen
und die Zone dahinter feststehen, wenn ich den Zonen Geraete zuweise“.

Die Erwartung kommt aus dem Zonenplan (`zone_struct`): eine Firewall bzw. VDOM, die einer Zone A
zugewiesen ist (Geraete der Zone), liegt mit ihren Interfaces an den Uebergaengen zu den
Nachbarzonen. Ein Netz einer anderen Zone Z erwartet sie ueber die Interfaces zu den Nachbarzonen
auf dem kuerzesten Weg von A nach Z im Zonenplan. Je Netz zaehlt die wirksame Route mit dem
laengsten Praefix, das das Netz ganz enthaelt (RIB, Standard-VRF):

- Next-Hop ueber ein erwartetes Interface: in Ordnung;
- sonst Befund `zone_net_exit` - mit dem Interface, ueber das die Firewall das Netz lernt, und
  den Zonen dahinter; nur ueber die Default-Route bzw. gar keine Route: ebenso.

Ohne Interface der Firewall Richtung Z (der Weg fuehrt durch andere Knoten der Zone A) bzw. ohne
Weg im Zonenplan prueft Automato nichts. Rein lesend.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict, deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SourceSystem, TopoRoute
from app.store import topology, zone_struct
from app.store import zones as zone_store

FIREWALL_KINDS = ("firewall", "vdom")
MAX_NETS = 200  # Netze je Zone


def _net(value: str):
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return None


def _distances(adj: dict[int, set[int]], start: int) -> dict[int, int]:
    dist = {start: 0}
    todo = deque([start])
    while todo:
        z = todo.popleft()
        for n in adj.get(z, ()):
            if n not in dist:
                dist[n] = dist[z] + 1
                todo.append(n)
    return dist


def checks(session: Session, tenant_id: int) -> list[dict]:
    """Befunde je Zone, Netz und Firewall (siehe oben)."""
    items = zone_store.load(session, tenant_id)
    if not zone_struct.has_nodes(items):
        return []
    st = zone_struct.build(session, tenant_id, items)
    g = st.graph
    fws = {k: z for k, z in st.node_zone.items()
           if k in g.nodes and g.nodes[k].kind in FIREWALL_KINDS}  # fmt: skip
    if not fws:
        return []
    by_id = {zone_struct.node_id(k): k for k in fws}
    # Zonenplan: Nachbarzonen und je Firewall die Interfaces zu jeder Nachbarzone
    adj: dict[int, set[int]] = defaultdict(set)
    faces: dict[tuple, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for (a, b), t in st.transitions.items():
        if isinstance(a, int) and isinstance(b, int):
            adj[a].add(b)
            adj[b].add(a)
        for info in t.networks:
            for leg in info["legs"]:
                key = by_id.get(leg["node"])
                if key is None or not leg.get("interface"):
                    continue
                own = fws[key]
                other = b if own == a else a if own == b else None
                if isinstance(other, int):
                    faces[key][other].add(leg["interface"])
    if not faces:
        return []
    names = {z.id: z.name for z in items}
    sources = dict(session.execute(
        select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
    ).all())  # fmt: skip
    routes: dict[tuple, list[tuple]] = defaultdict(list)
    for r in session.scalars(
        select(TopoRoute).where(
            TopoRoute.tenant_id == tenant_id, TopoRoute.table == "rib",
            TopoRoute.best.is_(True),
            TopoRoute.source_system_id.in_({k[0] for k in faces}),
        )
    ):  # fmt: skip
        net = _net(r.prefix if "/" in r.prefix else f"{r.prefix}/{r.prefix_len}")
        if net is None or topology.table_of(r) != 0 or (r.source_system_id, r.context) not in faces:
            continue
        hops = {h.get("interface") for h in r.next_hops or [] if h.get("interface")}
        routes[(r.source_system_id, r.context)].append((net, hops))

    issues = []
    for z in items:
        nets = [n for n in (_net(str(x)) for x in st.nets(z.id)[1]) if n is not None][:MAX_NETS]
        if not nets:
            continue
        dist = _distances(adj, z.id)
        for key in sorted(faces, key=lambda k: (k[0], k[1])):
            home = fws[key]
            if home == z.id or home not in dist:
                continue
            toward = sorted(n for n in adj[home] if dist.get(n) == dist[home] - 1)
            expected = set().union(*(faces[key].get(n, set()) for n in toward)) if toward else set()
            if not expected:
                continue  # der Weg fuehrt nicht ueber ein Interface dieser Firewall
            table = routes.get(key, [])
            for net in nets:
                best = max((r for r in table if r[0].version == net.version
                            and net.subnet_of(r[0])), key=lambda r: r[0].prefixlen,
                           default=None)  # fmt: skip
                if best is not None and best[1] & expected:
                    continue
                via = sorted(best[1]) if best else []
                behind = sorted(names[o] for o, ifaces in faces[key].items()
                                if o in names and ifaces & set(via))  # fmt: skip
                issues.append({
                    "type": "zone_net_exit", "subject": f"{z.name} · {net}",
                    "source_system_id": key[0], "severity": "warning",
                    "zone_id": z.id, "zone": z.name, "net": str(net),
                    "source": sources.get(key[0]), "context": key[1], "via": via,
                    "expected": sorted(expected), "toward": [names[n] for n in toward],
                    "others": behind, "default": best is not None and best[0].prefixlen == 0,
                    "missing": best is None,
                })  # fmt: skip
    return issues
