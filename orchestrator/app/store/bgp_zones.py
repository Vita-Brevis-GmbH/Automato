"""BGP-Filter je Zone ableiten: Soll je Nachbar aus den Zonen dahinter (0.114.0, ADR 0041 Stufe 2).

Anwender 2026-10-28: „BGP-Filter aus den Netzen und Ausschluessen je Zone ableiten“ - als
Vorschlag bzw. Export, kein Schreibweg. Gewaehlt 2026-10-29: alle Zonen, die ueber den Nachbarn
erreicht werden.

- **Zone am Nachbarn:** die Zone der gelesenen FortiGate bzw. des VRFs mit der IP des Nachbarn,
  sonst die andere Seite des Uebergangs (Zonenplan, `zone_struct`) mit dem Netz des Nachbarn an
  dieser Firewall, sonst die Nachbarzone hinter dem Interface zum Nachbarn.
- **Zonen dahinter:** im Zonenplan alle Zonen, deren kuerzester Weg von der Zone der Firewall
  ueber die Zone am Nachbarn fuehrt (wie `zone_exits`).
- **Soll:** ihre Netze (wie erfasst bzw. abgeleitet, groessere umfassen kleinere) als `permit
  … le 32`, die Ausschluesse davor als `deny … le 32` - je Familie eine Prefix-Liste.
- **Vergleich** mit den gelesenen Eingangsfiltern: Netz der Zonen gesperrt („fehlt im Filter“),
  Ausschluss erlaubt, Regel ausserhalb der Zonen bzw. „alles“ („zu offen“), ohne Eingangsfilter.
- **CLI-Vorschlag** (FortiOS `config router prefix-list`/`prefix-list6`) zum Kopieren - nur
  Text, Automato schreibt nichts auf die Firewall.

Ohne Zonen mit Geraeten, ohne Zone der Firewall bzw. ohne Zone am Nachbarn: kein Soll (mit
Grund). Nur lesend.
"""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict, deque

from sqlalchemy.orm import Session

from app.i18n import L
from app.models import TopoPeer
from app.store import bgp_filters, zone_struct
from app.store import zones as zone_store

Net = ipaddress.IPv4Network | ipaddress.IPv6Network
MAX_NETS = 200  # Netze je Liste
SHOWN = 20  # Abweichungen je Art in Befund und Anzeige


def _net(value) -> Net | None:
    try:
        return ipaddress.ip_network(str(value).strip(), strict=False)
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


def _collapse(nets: dict[Net, set[str]]) -> dict[Net, set[str]]:
    """Groessere Netze umfassen kleinere (deren Zonen kommen dazu)."""
    out: dict[Net, set[str]] = {}
    for net in sorted(nets, key=lambda n: (n.version, n.prefixlen, int(n.network_address))):
        parent = next((p for p in out if p.version == net.version and net.subnet_of(p)), None)
        if parent is None:
            out[net] = set(nets[net])
        else:
            out[parent] |= nets[net]
    return out


def list_name(zone) -> str:
    """Name der Prefix-Liste: PL_<Code bzw. Name der Zone am Nachbarn>_IN (FortiOS: 35 Zeichen)."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", (zone.code or zone.name or "ZONE")).strip("_").upper()
    return f"PL_{base[:26]}_IN"


def cli(name: str, family: int, entries: list[dict], peer_ip: str, bind: bool) -> str:
    """FortiOS-CLI der Prefix-Liste (Ausschluesse zuerst) - Vorschlag, nur Text; ohne
    Kommentarzeilen, damit er sich so in die Konsole einfuegen laesst."""
    six = "6" if family == 6 else ""
    lines = [
        f"config router prefix-list{six}",
        f'    edit "{name}"',
        "        config rule",
    ]
    for i, e in enumerate(entries, start=1):
        net = _net(e["prefix"])
        lines += [f"            edit {i}", f"                set action {e['action']}",
                  f"                set prefix{six} {net}"]  # fmt: skip
        if net is not None and net.prefixlen < net.max_prefixlen and net.prefixlen > 0:
            lines.append(f"                set le {net.max_prefixlen}")
        lines.append("            next")
    lines += ["        end", "    next", "end"]
    if bind:
        lines += ["config router bgp", "    config neighbor", f'        edit "{peer_ip}"',
                  f'            set prefix-list-in{six} "{name}"', "        next", "    end",
                  "end"]  # fmt: skip
    return "\n".join(lines)


class ZoneSoll:
    """Soll je BGP-Nachbar aus dem Zonenplan; Zonenplan und Filter je Mandant einmal."""

    def __init__(self, session: Session, tenant_id: int,
                 filters: bgp_filters.Filters | None = None):  # fmt: skip
        self.session, self.tenant_id = session, tenant_id
        self.filters = filters or bgp_filters.Filters(session, tenant_id)
        self.items = zone_store.load(session, tenant_id)
        self.zones = {z.id: z for z in self.items}
        self.st = (zone_struct.build(session, tenant_id, self.items)
                   if zone_struct.has_nodes(self.items) else None)  # fmt: skip
        self.adj: dict[int, set[int]] = defaultdict(set)
        if self.st is not None:
            for a, b in self.st.transitions:
                if isinstance(a, int) and isinstance(b, int):
                    self.adj[a].add(b)
                    self.adj[b].add(a)
        self._nets: dict[int, tuple[dict[Net, set[str]], list[Net]]] = {}

    # -- Zonen ----------------------------------------------------------------------------

    def _neighbor_zone(self, key: tuple[int, str], home: int, peer_ip: str) -> int | None:
        st = self.st
        g = st.graph
        for k, *_ in g.owners().get(peer_ip, []):  # Nachbar ist ein gelesenes Geraet
            z = st.node_zone.get(k)
            if k != key and z is not None and z != home:
                return z
        try:
            ip = ipaddress.ip_address(peer_ip)
        except ValueError:
            return None
        me = zone_struct.node_id(key)
        iface = g.interface_to(key, peer_ip, 0)
        for (a, b), t in st.transitions.items():
            other = b if a == home else a if b == home else None
            if not isinstance(other, int):
                continue
            for info in t.networks:
                legs = [x for x in info["legs"] if x["node"] == me]
                net = _net(info["network"])
                inside = net is not None and net.version == ip.version and ip in net
                if legs and (inside or any(x.get("interface") == iface for x in legs if iface)):
                    return other
        return None

    def behind(self, home: int, neighbor: int) -> list[int]:
        """Zonen, deren kuerzester Weg von `home` ueber `neighbor` fuehrt (inkl. `neighbor`)."""
        dh = _distances(self.adj, home)
        dn = _distances(self.adj, neighbor)
        adjacent = dh.get(neighbor) == 1
        out = []
        for z in self.zones:
            if z == home or z not in dn:
                continue
            if (dh.get(z) == dn[z] + 1) if adjacent else (dn[z] < dh.get(z, dn[z] + 1)):
                out.append(z)
        return sorted(out, key=lambda z: (dn[z], self.zones[z].name.lower()))

    def nets(self, zid: int) -> tuple[dict[Net, set[str]], list[Net]]:
        """Netze der Zone (wie erfasst, dazu die abgeleiteten, die keines umfasst) und ihre
        Ausschluesse."""
        if zid not in self._nets:
            z = self.zones[zid]
            name = z.name
            entered = [n for s in z.selectors if s.kind == "prefix" and not s.exclude
                       if (n := _net(s.value)) is not None]  # fmt: skip
            excluded = [n for s in z.selectors if s.kind == "prefix" and s.exclude
                        if (n := _net(s.value)) is not None]  # fmt: skip
            found: dict[Net, set[str]] = {n: {name} for n in entered}
            derived = self.st.nets(zid)[1] if self.st is not None else {}
            for n in derived:
                net = _net(n)
                if net is None or any(net.version == e.version and net.subnet_of(e)
                                      for e in entered):  # fmt: skip
                    continue
                found.setdefault(net, {name})
            self._nets[zid] = (found, excluded)
        return self._nets[zid]

    # -- je Nachbar -----------------------------------------------------------------------

    def for_peer(self, p: TopoPeer) -> dict:
        """Soll aus den Zonen dahinter, Vergleich mit dem Eingangsfilter und CLI-Vorschlag."""
        if self.st is None:
            text = L(
                "keine Zonen mit Geräten im Netzwerkdesign – kein Soll aus den Zonen",
                "no zones with devices in the network design – no target from the zones",
            )
            return {"state": "no_zones", "text": text}
        key = (p.source_system_id, p.context)
        home = self.st.node_zone.get(key)
        if home is None:
            return {"state": "no_zone", "text": L(
                "Die Firewall gehört zu keiner Zone (Netzwerkdesign → Zonen → Geräte)",
                "The firewall belongs to no zone (network design → zones → devices)")}  # fmt: skip
        neighbor = self._neighbor_zone(key, home, p.peer_ip)
        base = {"home": self._zone(home)}
        if neighbor is None:
            return {**base, "state": "no_neighbor_zone", "text": L(
                f"Zone am Nachbarn {p.peer_ip} unbekannt – kein Übergang mit seinem Netz im "
                "Zonenplan",
                f"Zone at neighbor {p.peer_ip} unknown – no transition with its network in the "
                "zone plan")}  # fmt: skip
        zones = self.behind(home, neighbor)
        permit: dict[Net, set[str]] = defaultdict(set)
        excluded: dict[Net, set[str]] = defaultdict(set)
        for z in zones:
            found, out = self.nets(z)
            for n, names in found.items():
                permit[n] |= names
            for n in out:
                excluded[n].add(self.zones[z].name)
        permit = _collapse(permit)
        excluded = {n: v for n, v in excluded.items()
                    if any(n.version == q.version and n.subnet_of(q) for q in permit)}  # fmt: skip
        conf = (p.attrs or {}).get("config") or {}
        cfg = self.filters.configs.get((p.source_system_id, p.node, p.context))
        lists = []
        for family in sorted({n.version for n in permit}):
            lists.append(self._family(family, permit, excluded, conf, cfg, p.peer_ip,
                                      self.zones[neighbor]))  # fmt: skip
        diffs = sum(len(x["missing"]) + len(x["too_open"]) + len(x["excluded_allowed"])
                    + (1 if x["open"] else 0) for x in lists)  # fmt: skip
        if not lists:
            state, text = "no_nets", L("Die Zonen dahinter haben keine Netze",
                                       "The zones behind have no networks")  # fmt: skip
        elif diffs:
            state, text = "differs", L(f"{diffs} Abweichungen zu den Zonen dahinter",
                                       f"{diffs} deviations from the zones behind")  # fmt: skip
        else:
            state, text = "ok", L("Eingangsfilter passt zu den Zonen dahinter",
                                  "Inbound filter matches the zones behind")  # fmt: skip
        return {**base, "neighbor_zone": self._zone(neighbor),
                "zones": [self._zone(z) for z in zones], "lists": lists,
                "state": state, "text": text}  # fmt: skip

    def _zone(self, zid: int) -> dict:
        z = self.zones[zid]
        return {"id": z.id, "name": z.name, "code": z.code}

    def _family(self, family, permit, excluded, conf, cfg, peer_ip, neighbor) -> dict:
        fin = bgp_filters._ways(conf, family)[0] if cfg is not None else {}
        mine = sorted((n for n in permit if n.version == family),
                      key=lambda n: (int(n.network_address), n.prefixlen))[:MAX_NETS]  # fmt: skip
        out_nets = sorted((n for n in excluded if n.version == family),
                          key=lambda n: (int(n.network_address), n.prefixlen))  # fmt: skip

        def verdict(net: Net) -> str | None:
            if cfg is None:
                return None
            return cfg.verdict(fin, net) if fin else bgp_filters.PERMIT

        entries = [{"prefix": str(n), "action": "deny", "zones": sorted(excluded[n]),
                    "verdict": verdict(n)} for n in out_nets]  # fmt: skip
        entries += [{"prefix": str(n), "action": "permit", "zones": sorted(permit[n]),
                     "verdict": verdict(n)} for n in mine]  # fmt: skip
        missing = [e["prefix"] for e in entries
                   if e["action"] == "permit" and e["verdict"] == bgp_filters.DENY]  # fmt: skip
        allowed = [e["prefix"] for e in entries if fin and e["action"] == "deny"
                   and e["verdict"] == bgp_filters.PERMIT]  # fmt: skip
        too_open = []
        if cfg is not None and fin:
            everything = any(n.prefixlen == 0 for n in mine)
            for kind, rm in fin.items():  # Route-Map mit permit ohne Bedingung: alles
                rules = cfg.rules("route_map", None, rm) if kind == "route_map" else None
                if not everything and any((r.get("action") or "permit") == "permit"
                                          and not r.get("match") for r in rules or []):  # fmt: skip
                    too_open.append({"prefix": "any", "filter": rm, "all": True})
            for e in cfg.entries(fin, family):
                base = _net(e.get("prefix")) if e.get("prefix") != "any" else None
                if e.get("all"):
                    if not everything:
                        too_open.append({"prefix": e.get("prefix"), "filter": e["filter"],
                                         "all": True})  # fmt: skip
                    continue
                inside = base is not None and any(base.subnet_of(n) for n in mine)
                if not inside:
                    too_open.append({"prefix": str(base or e.get("prefix")),
                                     "filter": e["filter"]})  # fmt: skip
        name = list_name(neighbor)
        return {
            "family": family,
            "name": name,
            "filters": fin,
            "open": cfg is not None and not fin,
            "entries": entries,
            "missing": missing[:SHOWN],
            "missing_total": len(missing),
            "too_open": too_open[:SHOWN],
            "excluded_allowed": allowed[:SHOWN],
            "cli": cli(name, family, entries, peer_ip, bind=not fin),
        }


def for_peer(session: Session, tenant_id: int, p: TopoPeer,
             filters: bgp_filters.Filters | None = None) -> dict:  # fmt: skip
    return ZoneSoll(session, tenant_id, filters).for_peer(p)
