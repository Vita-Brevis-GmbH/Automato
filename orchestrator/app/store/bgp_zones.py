"""BGP-Filter je Zone ableiten: Soll je Nachbar aus den Zonen dahinter (0.114.0, ADR 0041 Stufe 2).

Anwender 2026-10-28: „BGP-Filter aus den Netzen und Ausschluessen je Zone ableiten“ - als
Vorschlag bzw. Export, kein Schreibweg. Gewaehlt 2026-10-29: alle Zonen, die ueber den Nachbarn
erreicht werden.

- **Zone am Nachbarn:** die Zone der gelesenen FortiGate bzw. des VRFs mit der IP des Nachbarn,
  sonst die andere Seite des Uebergangs (Zonenplan, `zone_struct`) mit dem Netz des Nachbarn an
  dieser Firewall, sonst die Nachbarzone hinter dem Interface zum Nachbarn.
- **Zonen dahinter:** im Zonenplan alle Zonen, deren kuerzester Weg von der Zone der Firewall
  ueber die Zone am Nachbarn fuehrt (wie `zone_exits`). Seit 0.128.0 (Anwender: „Man muss
  einstellen können wo die Zonen übergänge bei Dreiecksbeziehungen sein soll“, Entscheid: nur
  „die Verbindungen zu gleichen Zonen unterschiedlicher Tenants“, Vorgabe aus): eine Zone ist
  kein Durchgang zwischen zwei Zonen desselben Zonentyps (z. B. die Zone der Internet-Firewall
  zwischen den Zonen zweier Kunden), ausser ihr Zonentyp hat im Grundschema „verbindet gleiche
  Zonen“ - die Zonen, die deshalb wegfallen, nennt die Begruendung (`chain.not_via`).
- **Soll:** ihre Netze (wie erfasst bzw. abgeleitet, groessere umfassen kleinere) als `permit
  … le 32`, die Ausschluesse davor als `deny … le 32` - je Familie eine Prefix-Liste.
- **Vergleich** mit den gelesenen Eingangsfiltern: Netz der Zonen gesperrt („fehlt im Filter“),
  Ausschluss erlaubt, Regel ausserhalb der Zonen bzw. „alles“ („zu offen“), ohne Eingangsfilter.
- **CLI-Vorschlag** (FortiOS `config router prefix-list`/`prefix-list6`) zum Kopieren - nur
  Text, Automato schreibt nichts auf die Firewall.

Stufe 3 (0.116.0, Roadmap Version 1 Nr. 3 Rest):

- **Ausgangsfilter je Zone:** was die Firewall dem Nachbarn ankuendigen soll - die Netze der
  Zonen, die der Nachbar ueber diese Firewall erreicht (kuerzester Weg von der Zone am Nachbarn
  ueber die Zone der Firewall, inkl. ihr); an einen Nachbarn in einer Zone der Art `external`
  nur oeffentliche Netze. Vergleich mit dem Ausgangsfilter (gesperrt, zu offen, Ausschluss
  erlaubt, ohne Filter) und mit dem, was die Firewall heute ankuendigt (`bgp_filters.report`):
  Routen ausserhalb der Zonen; default-originate zaehlt nicht als Abweichung. CLI
  `PL_<Zone am Nachbarn>_OUT` mit `prefix-list-out`.
- **ACI import-rtctrl aus den Zonen:** je L3Out (Zone der VRF, Zone am Peer des L3Outs) die
  Netze der Zonen dahinter gegen die Subnetze mit Import Route Control: nicht erzwungen, fehlt,
  zu offen (ausserhalb der Zonen bzw. 0.0.0.0/0 aggregate), Ausschluss erlaubt; Vorschlag als
  Liste der Subnetze (Aggregate Import fuer Netze groesser als ein Host).

Ohne Zonen mit Geraeten, ohne Zone der Firewall bzw. ohne Zone am Nachbarn: kein Soll (mit
Grund). Nur lesend.
"""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict, deque

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import TopoL3Out, TopoPeer
from app.store import bgp_filters, zone_struct
from app.store import zones as zone_store

Net = ipaddress.IPv4Network | ipaddress.IPv6Network
MAX_NETS = 200  # Netze je Liste
SHOWN = 50  # Abweichungen je Art in Befund und Anzeige (0.131.0, vorher 20)
MAX_ANNOUNCED = 5000  # angekuendigte Routen je Nachbar fuer den Vergleich (Ausgang)
KNOWN_MAX = 5000  # bekannte Netze je Netz von Hand (0.131.0)
# intern = nie an einen Nachbarn ausserhalb (RFC 1918, CGNAT, Link-local, Loopback, ULA)
INTERNAL = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16",
    "127.0.0.0/8", "fc00::/7", "fe80::/10", "::1/128"))  # fmt: skip


def public(net: Net) -> bool:
    return not any(net.version == i.version and net.subnet_of(i) for i in INTERNAL)


def _net(value) -> Net | None:
    try:
        return ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError:
        return None


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


WAYS = ("in", "out")


def _pieces(entered: list[Net], excluded: list[Net]) -> set[Net]:
    """Stuecke der Netze von Hand ohne die Ausschluesse (wie `customers._cut`) - in den
    abgeleiteten Netzen der Zone stehen sie fuer das Netz von Hand selbst."""
    from app.store import customers

    return set(customers._cut({e: "" for e in entered}, excluded)) | set(entered)


def list_name(zone, way: str = "in") -> str:
    """Name der Prefix-Liste: PL_<Code bzw. Name der Zone am Nachbarn>_IN bzw. _OUT (FortiOS:
    35 Zeichen)."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", (zone.code or zone.name or "ZONE")).strip("_").upper()
    return f"PL_{base[:26]}_{way.upper()}"


def cli(name: str, family: int, entries: list[dict], peer_ip: str, bind: bool,
        way: str = "in") -> str:  # fmt: skip
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
                  f'            set prefix-list-{way}{six} "{name}"', "        next", "    end",
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
        # 0.128.0: Zonentyp je Zone und Zonen, die gleiche Zonen nicht verbinden (Grundschema)
        from app.store import zone_types  # spaet: zone_types -> zone_detect -> ...

        schema = zone_types.schema_for(session, tenant_id)
        self.types = zone_types.types(session, tenant_id, self.items, schema)
        self.joins = {
            zid
            for zid, t in self.types.items()
            if t and (sz := schema.zone(t)) is not None and sz.joins_same
        }
        self.adj: dict[int, set[int]] = defaultdict(set)
        if self.st is not None:
            for a, b in self.st.transitions:
                if isinstance(a, int) and isinstance(b, int):
                    self.adj[a].add(b)
                    self.adj[b].add(a)
        self._nets: dict[int, tuple[dict[Net, set[str]], list[Net]]] = {}
        # 0.131.0: Netze von Hand je Zone (Rahmen), Rahmen ohne bekanntes Netz darin, Transfernetze
        self._scope: dict[int, list[Net]] = {}
        self._empty: dict[int, list[Net]] = {}
        self._known: dict[Net, list[Net]] = {}
        self._transfers = sorted(self.st.transfer_nets, key=lambda n: n.prefixlen) \
            if self.st is not None else []  # fmt: skip
        self._owners: list[tuple[Net, int]] | None = None
        self._node_ids: dict[str, int] | None = None

    # -- Zonen ----------------------------------------------------------------------------

    def _neighbor_zone(self, key: tuple[int, str], home: int, peer_ip: str) -> int | None:
        return self._neighbor(key, home, peer_ip)[0]

    def _neighbor(self, key: tuple[int, str], home: int, peer_ip: str) -> tuple[int | None, dict]:
        """Zone am Nachbarn und woher sie kommt (0.127.0): `device` = der Nachbar ist ein
        gelesenes Geraet dieser Zone, `network` = Netz des Uebergangs mit seiner IP bzw. am
        Interface zu ihm."""
        st = self.st
        g = st.graph
        for k, *_ in g.owners().get(peer_ip, []):  # Nachbar ist ein gelesenes Geraet
            z = st.node_zone.get(k)
            if k != key and z is not None and z != home:
                return z, {"device": True}
        try:
            ip = ipaddress.ip_address(peer_ip)
        except ValueError:
            return None, {}
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
                    return other, {
                        "network": info["network"],
                        **({"interface": iface} if iface and not inside else {}),
                    }
        return None, {}

    def _blocked(self, prev: int, cur: int, nxt: int) -> bool:
        """Durchgang prev -> cur -> nxt gesperrt: prev und nxt haben denselben Zonentyp und cur
        verbindet gleiche Zonen nicht (0.128.0, Vorgabe)."""
        if cur in self.joins:
            return False
        a, b = self.types.get(prev), self.types.get(nxt)
        return a is not None and a == b

    def dist(self, start: int, first: int | None = None, strict: bool = True) -> dict[int, int]:
        """Abstand (Uebergaenge) von `start` je Zone; `first` = erster Schritt fest (ueber diese
        Zone); `strict=False` ohne die Sperre gleicher Zonen (Vergleich fuer die Begruendung)."""
        return self.walk(start, first, strict)[0]

    def walk(self, start: int, first: int | None = None,
             strict: bool = True) -> tuple[dict[int, int], dict[int, list[int]]]:  # fmt: skip
        """Abstand je Zone und der Weg dorthin (Zonen ab `start`). Je Schritt zaehlt der Weg, auf
        dem er liegt (0.130.0): eine Zone, die frueh ueber einen gesperrten Durchgang und spaeter
        ueber einen Umweg erreicht wird, gibt den Umweg mit seiner Laenge weiter."""
        dist, path = {start: 0}, {start: [start]}
        parent: dict[tuple, tuple | None] = {}
        if first is not None:
            if first not in self.adj.get(start, ()):
                return dist, path
            begin = (first, start)
            parent[begin] = (start, None)
            parent[(start, None)] = None
            todo = deque([(begin, 1)])
        else:
            begin = (start, None)
            parent[begin] = None
            todo = deque([(begin, 0)])

        def trail(state) -> list[int]:
            out = []
            while state is not None:
                out.append(state[0])
                state = parent.get(state)
            return out[::-1]

        if first is not None:
            dist[first], path[first] = 1, [start, first]
        while todo:
            (cur, prev), d = todo.popleft()
            for nxt in self.adj.get(cur, ()):
                state = (nxt, cur)
                if nxt == prev or state in parent:
                    continue
                if strict and prev is not None and self._blocked(prev, cur, nxt):
                    continue
                parent[state] = (cur, prev)
                if nxt not in dist:
                    dist[nxt], path[nxt] = d + 1, trail(state)
                todo.append((state, d + 1))
        return dist, path

    def behind(self, home: int, neighbor: int, strict: bool = True) -> list[int]:
        """Zonen, deren kuerzester Weg von `home` ueber `neighbor` fuehrt (inkl. `neighbor`)."""
        dh = self.dist(home, strict=strict)
        adjacent = neighbor in self.adj.get(home, ())
        if adjacent:  # kuerzester Weg mit dem ersten Schritt zum Nachbarn
            via = self.dist(home, neighbor, strict=strict)
            out = [z for z in self.zones if z != home and z in via and via[z] == dh.get(z)]
        else:
            via = self.dist(neighbor, strict=strict)
            out = [z for z in self.zones
                   if z != home and z in via and via[z] < dh.get(z, via[z] + 1)]  # fmt: skip
        return sorted(out, key=lambda z: (via[z], self.zones[z].name.lower()))

    def entered(self, zid: int) -> tuple[list[Net], list[Net]]:
        """Netze der Zone von Hand (Netzwerkdesign, Selektoren „Netz“) und ihre Ausschluesse."""
        z = self.zones[zid]
        entered = [n for sel in z.selectors if sel.kind == "prefix" and not sel.exclude
                   if (n := _net(sel.value)) is not None]  # fmt: skip
        excluded = [n for sel in z.selectors if sel.kind == "prefix" and sel.exclude
                    if (n := _net(sel.value)) is not None]  # fmt: skip
        return entered, excluded

    def nets(self, zid: int) -> tuple[dict[Net, set[str]], list[Net]]:
        """Netze der Zone fuer die BGP-Filter und ihre Ausschluesse (0.131.0, Anwender: „in dem
        Netzwerk design Kategorien habe ich den groben Filter angewendet, der sollte aber nicht
        auf die Routing Filter angesetzt werden sondern nur Filtern, welche Netzwerke dort
        dahinter sein könnten“ und „die Transfer Netzwerke nicht mit eingerechnet“):

        - die Netze an den Knoten der Zone und aus ihren uebrigen Selektoren (IPAM, EPG,
          Interface) ohne Transfernetze;
        - die Netze von Hand sind nur der Rahmen: darin die bekannten Netze aus den
          Routing-Tabellen (ohne Transfernetze, Netze einer anderen Zone und Sammelrouten ueber
          Netze einer anderen Zone); ist darin keines
          bekannt, steht das Netz von Hand in `frames` (Hinweis), nicht im Filter. 0.0.0.0/0
          bzw. ::/0 von Hand gilt als Ganzes (alles).
        """
        if zid not in self._nets:
            name = self.zones[zid].name
            entered, excluded = self.entered(zid)
            found: dict[Net, set[str]] = {}
            for net in self._derived(zid):
                found.setdefault(net, {name})
            empty = []
            for e in entered:
                if e.prefixlen == 0:
                    found.setdefault(e, {name})
                    continue
                known = [k for k in self._known_in(e) if self._owner(k) == zid
                         and not self._is_transfer(k) and not self._covers_other(k, zid)
                         and not self._attached_elsewhere(k, zid)
                         and not any(k.version == x.version and k.subnet_of(x)
                                     for x in excluded)]  # fmt: skip
                for k in known:
                    found.setdefault(k, {name})
                if not known and not any(f.version == e.version and f.subnet_of(e)
                                         for f in found):  # fmt: skip
                    empty.append(e)
            self._nets[zid] = (found, excluded)
            self._scope[zid] = entered
            self._empty[zid] = empty
        return self._nets[zid]

    def _derived(self, zid: int) -> list[Net]:
        """Netze der Zone aus Knoten und Selektoren ausser den Netzen von Hand, ohne
        Transfernetze."""
        if self.st is None:
            return []
        entered, excluded = self.entered(zid)
        pieces = _pieces(entered, excluded)
        explicit = self.st.explicit.get(zid, ({}, {}))[1]
        out = []
        for n in explicit:
            net = _net(n)
            if net is not None and n not in self.st.transition_nets and net not in pieces \
                    and not self._is_transfer(net):  # fmt: skip
                out.append(net)
        for n in self.st.internal.get(zid, {}):  # nicht, was nur der Rahmen einer Zone mit
            net = _net(n)  # Geraeten an einem Geraet einer anderen Zone dazunimmt
            if net is not None and net not in pieces and not self._is_transfer(net) \
                    and not self._attached_elsewhere(net, zid):  # fmt: skip
                out.append(net)
        return list(dict.fromkeys(out))

    def _attached_elsewhere(self, net: Net, zid: int) -> bool:
        """Netz an Interfaces gelesener Geraete, keines davon in der Zone (die Zone hat selbst
        Geraete): es haengt an einer anderen Zone bzw. an einem Kontext ohne Zone und liegt
        nicht hinter dieser (0.131.0)."""
        if self.st is None:
            return False
        ids = self._node_zone_ids()
        if zid not in set(ids.values()):
            return False
        nodes = self.st.net_nodes.get(net)
        return bool(nodes) and not any(ids.get(n) == zid for n in nodes)

    def _node_zone_ids(self) -> dict[str, int]:
        """Zone je Knoten (Knoten-ID wie an den Beinen der Netze)."""
        if self._node_ids is None:
            self._node_ids = {zone_struct.node_id(k): z for k, z in self.st.node_zone.items()}
        return self._node_ids

    def frames(self, zones: list[int]) -> list[dict]:
        """Netze von Hand ohne bekanntes Netz darin (0.131.0): nicht im Filter, als Hinweis."""
        out = []
        for z in zones:
            self.nets(z)
            out += [{"prefix": str(e), "zone": self.zones[z].name} for e in self._empty[z]]
        return out

    def _is_transfer(self, net: Net) -> bool:
        return any(net.version == t.version and net.subnet_of(t) for t in self._transfers)

    def _known_in(self, e: Net) -> list[Net]:
        """Bekannte Netze in einem Netz von Hand: Routen der Routing-Tabellen (ohne lokale
        Adressen und Host-Routen), gleich bzw. genauer als das Netz selbst."""
        if e not in self._known:
            from sqlalchemy import select

            from app.models import TopoRoute
            from app.store.topology import ip_hex

            rows = self.session.scalars(
                select(TopoRoute.prefix).distinct()
                .where(TopoRoute.tenant_id == self.tenant_id, TopoRoute.family == e.version,
                       TopoRoute.table == "rib", TopoRoute.lo >= ip_hex(e.network_address),
                       TopoRoute.hi <= ip_hex(e.broadcast_address),
                       TopoRoute.prefix_len >= e.prefixlen,
                       TopoRoute.prefix_len < e.max_prefixlen,
                       or_(TopoRoute.protocol.is_(None), TopoRoute.protocol != "local"))
                .limit(KNOWN_MAX)
            )  # fmt: skip
            self._known[e] = sorted(
                {n for r in rows if (n := _net(r)) is not None},
                key=lambda n: (int(n.network_address), n.prefixlen),
            )
        return self._known[e]

    def _covers_other(self, net: Net, zid: int) -> bool:
        """Route umfasst ein Netz einer anderen Zone (Sammelroute, zeigt nicht nur in diese
        Zone) - kein bekanntes Netz der Zone."""
        self._owner(net)
        return any(z != zid and e != net and e.version == net.version and e.subnet_of(net)
                   for e, z in self._owners or [])  # fmt: skip

    def _owner(self, net: Net) -> int | None:
        """Zone eines bekannten Netzes: die mit dem genauesten Netz darum - Netze von Hand und
        die abgeleiteten Netze der Zonen (ein Netz an einem Knoten einer anderen Zone gehoert
        dorthin)."""
        if self._owners is None:
            pairs = [(e, zid) for zid in self.zones for e in self.entered(zid)[0]]
            pairs += [(n, zid) for zid in self.zones for n in self._derived(zid)]
            if self.st is not None:  # Netze an Interfaces der Geraete einer Zone
                ids = self._node_zone_ids()
                for n, nodes in self.st.net_nodes.items():
                    if not self._is_transfer(n):
                        pairs += [(n, z) for z in sorted({ids.get(x) for x in nodes} - {None})]
            self._owners = sorted(pairs, key=lambda x: -x[0].prefixlen)  # stabil: Reihenfolge
        return next((zid for e, zid in self._owners
                     if e.version == net.version and net.subnet_of(e)), None)  # fmt: skip

    # -- je Nachbar -----------------------------------------------------------------------

    def for_peer(self, p: TopoPeer, way: str = "in") -> dict:
        """Soll aus den Zonen, Vergleich mit dem Filter der Richtung und CLI-Vorschlag: `in` =
        Zonen dahinter (Eingangsfilter), `out` = Zonen diesseits (Ausgangsfilter, 0.116.0)."""
        if self.st is None:
            text = L(
                "keine Zonen mit Geräten im Netzwerkdesign – kein Soll aus den Zonen",
                "no zones with devices in the network design – no target from the zones",
            )
            return {"state": "no_zones", "way": way, "text": text}
        key = (p.source_system_id, p.context)
        home = self.st.node_zone.get(key)
        if home is None:
            return {"state": "no_zone", "way": way, "text": L(
                "Die Firewall gehört zu keiner Zone (Netzwerkdesign → Zonen → Geräte)",
                "The firewall belongs to no zone (network design → zones → devices)")}  # fmt: skip
        neighbor, how = self._neighbor(key, home, p.peer_ip)
        base = {"home": self._zone(home), "way": way}
        if neighbor is None:
            return {**base, "state": "no_neighbor_zone", "text": L(
                f"Zone am Nachbarn {p.peer_ip} unbekannt – kein Übergang mit seinem Netz im "
                "Zonenplan",
                f"Zone at neighbor {p.peer_ip} unknown – no transition with its network in the "
                "zone plan")}  # fmt: skip
        out = way == "out"
        zones = self.behind(neighbor, home) if out else self.behind(home, neighbor)
        # an einen Nachbarn ausserhalb (Provider) nur oeffentliche Netze ankuendigen
        public_only = out and self.zones[neighbor].kind == "external"
        permit, excluded = self._soll(zones, public_only)
        conf = (p.attrs or {}).get("config") or {}
        cfg = self.filters.configs.get((p.source_system_id, p.node, p.context))
        announced = self._announced(p) if out and cfg is not None else []
        # 0.131.0: nur die Adressfamilien der Sitzung (am Nachbarn ausgeschaltete nicht)
        key3 = (p.source_system_id, p.node, p.context)
        received = self.filters.received(key3, p.peer_ip) if cfg is not None else []
        families = bgp_filters.session_families(p.peer_ip, conf, received)
        scope = self._scope_of(zones, public_only)
        lists = []
        for family in sorted({n.version for n in permit} & set(families)):
            x = self._family(family, permit, excluded, conf, cfg, p.peer_ip,
                             self.zones[neighbor], way, announced, scope)  # fmt: skip
            if not out:  # was heute vom Nachbarn ankommt (0.131.0: z. B. nur die Default-Route)
                got = [n for prefix, _best in received
                       if (n := _net(prefix)) is not None and n.version == family]  # fmt: skip
                x["received_total"] = len(got)
                x["received_default_only"] = bool(got) and all(n.prefixlen == 0 for n in got)
            lists.append(x)
        diffs = sum(len(x["missing"]) + len(x["too_open"]) + len(x["excluded_allowed"])
                    + len(x.get("announced_outside") or []) + (1 if x["open"] else 0)
                    for x in lists)  # fmt: skip
        if not lists and permit:
            state = "no_nets"
            fams = ", ".join(f"IPv{f}" for f in families) or "–"
            text = L(f"Die Zonen haben keine Netze in den Adressfamilien der Sitzung ({fams})",
                     f"The zones have no networks in the address families of the session "
                     f"({fams})")  # fmt: skip
        elif not lists:
            state = "no_nets"
            text = (L("Die Zonen diesseits haben keine (öffentlichen) Netze",
                      "The zones on this side have no (public) networks") if out
                    else L("Die Zonen dahinter haben keine Netze",
                           "The zones behind have no networks"))  # fmt: skip
        elif diffs:
            state = "differs"
            text = (L(f"{diffs} Abweichungen zu den Zonen diesseits",
                      f"{diffs} deviations from the zones on this side") if out
                    else L(f"{diffs} Abweichungen zu den Zonen dahinter",
                           f"{diffs} deviations from the zones behind"))  # fmt: skip
        else:
            state = "ok"
            text = (L("Ausgangsfilter passt zu den Zonen diesseits",
                      "Outbound filter matches the zones on this side") if out
                    else L("Eingangsfilter passt zu den Zonen dahinter",
                           "Inbound filter matches the zones behind"))  # fmt: skip
        # Begruendung der Zonen (0.127.0): Zone am Nachbarn woher, je Zone dahinter der Abstand
        # (Uebergaenge im Zonenplan) von der Zone der Firewall, diesseits vom Nachbarn aus
        start = neighbor if out else home
        dist = self.dist(start)
        # Zonen, die nur ohne die Sperre gleicher Zonen dazu gehoerten (0.128.0)
        wide = (self.behind(neighbor, home, strict=False) if out
                else self.behind(home, neighbor, strict=False))  # fmt: skip
        dropped = [z for z in wide if z not in set(zones)]
        paths = self._paths(home, neighbor, out, p.peer_ip)
        chain = {
            "home": self.zones[home].name,
            "neighbor": self.zones[neighbor].name,
            **how,
            # 0.130.0: je Zone der Weg mit dem Netz je Uebergang (woher eine Zone kommt)
            "zones": [
                {"name": self.zones[z].name, "hops": dist.get(z), "path": paths.get(z, [])}
                for z in zones
            ],  # fmt: skip
            # 0.128.0: ueber eine Zone, die gleiche Zonen nicht verbindet, nicht erreicht
            "not_via": [self.zones[z].name for z in dropped],
        }
        # Netze von Hand ohne bekanntes Netz darin: nicht im Filter, als Hinweis (0.131.0)
        frames = [f for f in self.frames(zones)
                  if (n := _net(f["prefix"])) is not None and n.version in families
                  and (not public_only or public(n))]  # fmt: skip
        return {**base, "neighbor_zone": self._zone(neighbor),
                "zones": [self._zone(z) for z in zones], "lists": lists, "public_only": public_only,
                "families": families, "frames": frames, "state": state, "text": text,
                "chain": chain}  # fmt: skip

    def _paths(self, home: int, neighbor: int, out: bool,
               peer_ip: str | None = None) -> dict[int, list[dict]]:  # fmt: skip
        """Weg je Zone (0.130.0, Anwender: „Es zeigt mir nun auch andere Zonen an … Session
        Links, welche dort nicht hingehören“): ab der Zone der Firewall ueber die Zone am
        Nachbarn (Ausgang: ab dem Nachbarn ueber die Firewall), je Schritt die Zone und das Netz
        des Uebergangs - so ist sichtbar, welcher Uebergang eine Zone dazu bringt. Zwischen
        Firewall und Nachbar zuerst das Netz mit der Adresse des Nachbarn."""
        try:
            peer = ipaddress.ip_address(bgp_filters._bare(peer_ip)) if peer_ip else None
        except ValueError:
            peer = None

        def has_peer(text: str) -> bool:
            net = _net(text)
            return peer is not None and net is not None and net.version == peer.version and (
                peer in net)  # fmt: skip

        start, via = (neighbor, home) if out else (home, neighbor)
        if via in self.adj.get(start, ()):
            _dist, trails = self.walk(start, via)
        else:  # Zone am Nachbarn ohne Uebergang im Plan: ab ihr
            _dist, trails = self.walk(via)
        result: dict[int, list[dict]] = {}
        for z, trail in trails.items():
            steps = [{"zone": self.zones[trail[0]].name, "network": None}]
            for a, b in zip(trail, trail[1:], strict=False):
                t = self.st.transitions.get(tuple(sorted((a, b)))) if self.st else None
                nets = list(dict.fromkeys(i["network"] for i in (t.networks if t else [])))
                if {a, b} == {home, neighbor}:
                    nets.sort(key=lambda n: not has_peer(n))
                steps.append({"zone": self.zones[b].name, "network": nets[0] if nets else None,
                              "more": max(len(nets) - 1, 0)})  # fmt: skip
            result[z] = steps
        return result

    def _soll(self, zones: list[int], public_only: bool = False):
        """Netze (groessere umfassen kleinere) und Ausschluesse der Zonen."""
        permit: dict[Net, set[str]] = defaultdict(set)
        excluded: dict[Net, set[str]] = defaultdict(set)
        for z in zones:
            found, out = self.nets(z)
            for n, names in found.items():
                if not public_only or public(n):
                    permit[n] |= names
            for n in out:
                excluded[n].add(self.zones[z].name)
        permit = _collapse(permit)
        excluded = {n: v for n, v in excluded.items()
                    if any(n.version == q.version and n.subnet_of(q) for q in permit)}  # fmt: skip
        return permit, excluded

    def _scope_of(self, zones: list[int], public_only: bool = False) -> list[Net]:
        """Rahmen (0.131.0): Netze von Hand der Zonen und die Transfernetze - Filterregeln darin
        sind nicht zu offen; an einen Nachbarn ausserhalb nur die oeffentlichen."""
        out: list[Net] = []
        for z in zones:
            self.nets(z)
            out += self._scope.get(z, [])
        out += self._transfers
        return [n for n in out if not public_only or public(n)]

    @staticmethod
    def _tolerated(net: Net, scope: list[Net]) -> bool:
        """Regel im Rahmen der Zonen bzw. fuer ein Transfernetz - nicht „zu offen“ (0.131.0)."""
        return any(net.version == s.version and net.subnet_of(s) for s in scope)

    def _announced(self, p: TopoPeer) -> list[dict]:
        """Was die Firewall dem Nachbarn heute ankuendigt (berechnet, `bgp_filters.report`)."""
        try:
            return self.filters.report(p, limit=MAX_ANNOUNCED)["out"]["announced"]
        except KeyError:
            return []

    def zones_of(self, net: Net) -> list[str]:
        """Zonen, deren Netze das Netz enthalten bzw. in ihm liegen (0.127.0)."""
        out = []
        for zid, z in self.zones.items():
            found, _ = self.nets(zid)
            if any(n.version == net.version and (net.subnet_of(n) or n.subnet_of(net))
                   for n in [*found, *self._scope.get(zid, [])]):  # fmt: skip
                out.append(z.name)
        return sorted(out, key=str.lower)

    def _zone(self, zid: int) -> dict:
        z = self.zones[zid]
        return {"id": z.id, "name": z.name, "code": z.code}

    def _family(self, family, permit, excluded, conf, cfg, peer_ip, neighbor, way="in",
                announced=(), scope=()) -> dict:  # fmt: skip
        ways = bgp_filters._ways(conf, family) if cfg is not None else ({}, {})
        fin = ways[WAYS.index(way)]
        mine = sorted((n for n in permit if n.version == family),
                      key=lambda n: (int(n.network_address), n.prefixlen))[:MAX_NETS]  # fmt: skip
        out_nets = sorted((n for n in excluded if n.version == family),
                          key=lambda n: (int(n.network_address), n.prefixlen))  # fmt: skip

        def verdict(net: Net) -> str | None:
            if cfg is None:
                return None
            return cfg.verdict(fin, net) if fin else bgp_filters.PERMIT

        def decided(net: Net) -> list[dict]:  # 0.127.0: entscheidende Regel je Filter
            return cfg.decide(fin, net) if cfg is not None and fin else []

        entries = [{"prefix": str(n), "action": "deny", "zones": sorted(excluded[n]),
                    "verdict": verdict(n), "decided": decided(n)} for n in out_nets]  # fmt: skip
        entries += [{"prefix": str(n), "action": "permit", "zones": sorted(permit[n]),
                     "verdict": verdict(n), "decided": decided(n)} for n in mine]  # fmt: skip
        missing = [e["prefix"] for e in entries
                   if e["action"] == "permit" and e["verdict"] == bgp_filters.DENY]  # fmt: skip
        allowed = [e["prefix"] for e in entries if fin and e["action"] == "deny"
                   and e["verdict"] == bgp_filters.PERMIT]  # fmt: skip
        too_open = []
        if cfg is not None and fin:
            everything = any(n.prefixlen == 0 for n in mine)
            for kind, rm in fin.items():  # Route-Map mit permit ohne Bedingung: alles
                rules = cfg.rules("route_map", None, rm) if kind == "route_map" else None
                free = [
                    r
                    for r in rules or []
                    if (r.get("action") or "permit") == "permit" and not r.get("match")
                ]
                if not everything and free:
                    too_open.append({"prefix": "any", "filter": rm, "all": True,
                                     "rule": bgp_filters.rule_text(free[0])})  # fmt: skip
            for e in cfg.entries(fin, family):
                base = _net(e.get("prefix")) if e.get("prefix") != "any" else None
                if e.get("all"):
                    if not everything:
                        too_open.append({"prefix": e.get("prefix"), "filter": e["filter"],
                                         "all": True,
                                         "rule": bgp_filters.rule_text(e)})  # fmt: skip
                    continue
                # 0.131.0: im Rahmen der Zonen (Netz von Hand) bzw. Transfernetz: nicht zu offen
                inside = base is not None and (any(base.subnet_of(n) for n in mine)
                                               or self._tolerated(base, scope))  # fmt: skip
                if not inside:
                    # 0.127.0: zu welcher Zone das Netz gehoert (nicht hinter diesem Nachbarn)
                    too_open.append({"prefix": str(base or e.get("prefix")),
                                     "filter": e["filter"], "rule": bgp_filters.rule_text(e),
                                     "zones": self.zones_of(base) if base else []})  # fmt: skip
        # Ausgang (0.116.0): heute angekuendigte Routen ausserhalb der Zonen diesseits;
        # default-originate ist gewollt und keine Abweichung
        outside = []
        if way == "out":
            for a in announced:
                net = _net(a.get("prefix"))
                if net is None or net.version != family or a.get("origin") == "default-originate":
                    continue
                if not any(net.subnet_of(n) for n in mine) and not any(
                        net.subnet_of(n) for n in out_nets) and not self._tolerated(
                        net, scope):  # fmt: skip
                    outside.append(str(net))
        name = list_name(neighbor, way)
        # Begruendung je Abweichung (0.127.0, Anwender: „ich sehe nur, dass es nicht passen
        # soll“): Netz mit Zone und der entscheidenden Regel bzw. den erlaubten Teilen darin
        reasons: list[dict] = []
        opened = cfg.entries(fin, family) if cfg is not None and fin else []
        for e in entries:
            if e["action"] == "permit" and e["verdict"] == bgp_filters.DENY:
                net = _net(e["prefix"])
                by = [d for d in e["decided"] if d["verdict"] == bgp_filters.DENY]
                parts = [str(x.get("prefix")) for x in opened
                         if (b := _net(x.get("prefix"))) is not None and b != net
                         and b.version == net.version and b.subnet_of(net)]  # fmt: skip
                reasons.append({"kind": "missing", "prefix": e["prefix"], "zones": e["zones"],
                                "by": by, "parts": list(dict.fromkeys(parts))[:5]})  # fmt: skip
            elif e["action"] == "deny" and fin and e["verdict"] == bgp_filters.PERMIT:
                by = [d for d in e["decided"] if d["verdict"] == bgp_filters.PERMIT]
                reasons.append({"kind": "excluded_allowed", "prefix": e["prefix"],
                                "zones": e["zones"], "by": by})  # fmt: skip
        reasons += [{"kind": "too_open", **t} for t in too_open]
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
            "reasons": reasons[: SHOWN * 2],
            **(
                {"announced_outside": outside[:SHOWN], "announced_outside_total": len(outside)}
                if way == "out"
                else {}
            ),
            "cli": cli(name, family, entries, peer_ip, bind=not fin, way=way),
        }

    # -- ACI: Import Route Control der L3Outs (0.116.0) ------------------------------------

    def for_l3out(self, o: TopoL3Out) -> dict:
        """Soll der Subnetze mit Import Route Control aus den Zonen hinter dem L3Out."""
        if self.st is None:
            text = L(
                "keine Zonen mit Geräten im Netzwerkdesign – kein Soll aus den Zonen",
                "no zones with devices in the network design – no target from the zones",
            )
            return {"state": "no_zones", "text": text}
        key = (o.source_system_id, o.vrf or "")
        home = self.st.node_zone.get(key)
        if home is None:
            text = L(
                f"Die VRF {o.vrf} gehört zu keiner Zone (Netzwerkdesign → Zonen → Geräte)",
                f"The VRF {o.vrf} belongs to no zone (network design → zones → devices)",
            )
            return {"state": "no_zone", "text": text}
        neighbor = next((z for peer in o.peers or [] if peer.get("ip")
                         if (z := self._neighbor_zone(key, home, bgp_filters._bare(peer["ip"])))
                         is not None), None)  # fmt: skip
        base = {"home": self._zone(home)}
        if neighbor is None:
            return {**base, "state": "no_neighbor_zone", "text": L(
                "Zone am Peer des L3Outs unbekannt – kein Übergang mit seinem Netz im Zonenplan",
                "Zone at the peer of the L3Out unknown – no transition with its network in the "
                "zone plan")}  # fmt: skip
        zones = self.behind(home, neighbor)
        permit, excluded = self._soll(zones)
        # 0.131.0: nur die Adressfamilien der BGP-Peers des L3Outs
        families = self._l3out_families(o)
        permit = {n: v for n, v in permit.items() if n.version in families}
        excluded = {n: v for n, v in excluded.items() if n.version in families}
        scope = self._scope_of(zones)
        rc = o.route_control or {}
        enforced = "import" in (rc.get("enforce") or ["export"])
        imports = [e for e in rc.get("import") or [] if _net(e.get("prefix")) is not None]
        mine = sorted(permit, key=lambda n: (n.version, int(n.network_address), n.prefixlen))
        mine = mine[:MAX_NETS]

        def covered(net: Net) -> bool:
            return any(bgp_filters.aci_entry_covers(e, net) for e in imports)

        entries = [{"prefix": str(n), "action": "permit", "zones": sorted(permit[n]),
                    "aggregate": n.prefixlen < n.max_prefixlen,
                    "verdict": (bgp_filters.PERMIT if covered(n) else bgp_filters.DENY)
                    if enforced else bgp_filters.PERMIT} for n in mine]  # fmt: skip
        missing = [e["prefix"] for e in entries if e["verdict"] == bgp_filters.DENY]
        too_open = []
        for e in imports:
            net = _net(e["prefix"])
            if net.prefixlen == 0 and e.get("aggregate"):
                if not any(n.prefixlen == 0 for n in mine):
                    too_open.append({"prefix": str(net), "epg": e.get("epg"), "all": True})
            elif not any(net.version == n.version and net.subnet_of(n) for n in mine) and (
                    not self._tolerated(net, scope)):  # fmt: skip
                too_open.append({"prefix": str(net), "epg": e.get("epg")})
        allowed = [str(n) for n in excluded if enforced and covered(n)]
        diffs = len(missing) + len(too_open) + len(allowed) + (0 if enforced else 1)
        if not entries:
            state, text = "no_nets", L("Die Zonen dahinter haben keine Netze",
                                       "The zones behind have no networks")  # fmt: skip
        elif diffs:
            state, text = "differs", L(f"{diffs} Abweichungen zu den Zonen dahinter",
                                       f"{diffs} deviations from the zones behind")  # fmt: skip
        else:
            state, text = "ok", L("Import Route Control passt zu den Zonen dahinter",
                                  "Import route control matches the zones behind")  # fmt: skip
        return {**base, "neighbor_zone": self._zone(neighbor),
                "zones": [self._zone(z) for z in zones], "import_enforced": enforced,
                "entries": entries, "missing": missing[:SHOWN], "missing_total": len(missing),
                "too_open": too_open[:SHOWN], "excluded_allowed": allowed[:SHOWN],
                "excluded": [{"prefix": str(n), "zones": sorted(v)} for n, v in excluded.items()],
                "proposal": aci_proposal(o, entries, excluded), "families": families,
                "frames": [f for f in self.frames(zones)
                           if (n := _net(f["prefix"])) is not None and n.version in families],
                "state": state, "text": text}  # fmt: skip

    @staticmethod
    def _l3out_families(o: TopoL3Out) -> list[int]:
        """Adressfamilien der BGP-Peers eines L3Outs; ohne Peer-Adresse beide."""
        out = set()
        for peer in o.peers or []:
            try:
                out.add(ipaddress.ip_address(bgp_filters._bare(peer.get("ip") or "")).version)
            except ValueError:
                continue
        return sorted(out) or [4, 6]


def aci_proposal(o: TopoL3Out, entries: list[dict], excluded: dict) -> str:
    """Subnetze der externen EPG mit Import Route Control - Vorschlag als Text (APIC: L3Out →
    externe EPG → Subnetz, Scope „Import Route Control Subnet“, bei Netzen „Aggregate Import“).
    Ausschluesse lassen sich so nicht ausdruecken (Route-Map mit deny im Route Control Profile)."""
    head = L(
        f"L3Out {o.name} (VRF {o.vrf}): Route Control Enforcement „Import“ setzen",
        f"L3Out {o.name} (VRF {o.vrf}): set route control enforcement “import”",
    )
    lines = [head]
    for e in entries:
        flags = "import-rtctrl" + (", aggregate import" if e["aggregate"] else "")
        lines.append(f"  {e['prefix']:<20} {flags}")
    if excluded:
        lines.append(L("Ausschlüsse (nur über ein Route Control Profile mit deny): ",
                       "Exclusions (only via a route control profile with deny): ")
                     + ", ".join(str(n) for n in excluded))  # fmt: skip
    return "\n".join(lines)


def for_peer(session: Session, tenant_id: int, p: TopoPeer,
             filters: bgp_filters.Filters | None = None, way: str = "in") -> dict:  # fmt: skip
    return ZoneSoll(session, tenant_id, filters).for_peer(p, way)


def for_l3out(session: Session, tenant_id: int, o: TopoL3Out) -> dict:
    return ZoneSoll(session, tenant_id).for_l3out(o)
