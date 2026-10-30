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

from sqlalchemy.orm import Session

from app.i18n import L
from app.models import TopoL3Out, TopoPeer
from app.store import bgp_filters, zone_struct
from app.store import zones as zone_store

Net = ipaddress.IPv4Network | ipaddress.IPv6Network
MAX_NETS = 200  # Netze je Liste
SHOWN = 20  # Abweichungen je Art in Befund und Anzeige
MAX_ANNOUNCED = 5000  # angekuendigte Routen je Nachbar fuer den Vergleich (Ausgang)
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


WAYS = ("in", "out")


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
        neighbor = self._neighbor_zone(key, home, p.peer_ip)
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
        lists = []
        for family in sorted({n.version for n in permit}):
            lists.append(self._family(family, permit, excluded, conf, cfg, p.peer_ip,
                                      self.zones[neighbor], way, announced))  # fmt: skip
        diffs = sum(len(x["missing"]) + len(x["too_open"]) + len(x["excluded_allowed"])
                    + len(x.get("announced_outside") or []) + (1 if x["open"] else 0)
                    for x in lists)  # fmt: skip
        if not lists:
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
        return {**base, "neighbor_zone": self._zone(neighbor),
                "zones": [self._zone(z) for z in zones], "lists": lists, "public_only": public_only,
                "state": state, "text": text}  # fmt: skip

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

    def _announced(self, p: TopoPeer) -> list[dict]:
        """Was die Firewall dem Nachbarn heute ankuendigt (berechnet, `bgp_filters.report`)."""
        try:
            return self.filters.report(p, limit=MAX_ANNOUNCED)["out"]["announced"]
        except KeyError:
            return []

    def _zone(self, zid: int) -> dict:
        z = self.zones[zid]
        return {"id": z.id, "name": z.name, "code": z.code}

    def _family(self, family, permit, excluded, conf, cfg, peer_ip, neighbor, way="in",
                announced=()) -> dict:  # fmt: skip
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
        # Ausgang (0.116.0): heute angekuendigte Routen ausserhalb der Zonen diesseits;
        # default-originate ist gewollt und keine Abweichung
        outside = []
        if way == "out":
            for a in announced:
                net = _net(a.get("prefix"))
                if net is None or net.version != family or a.get("origin") == "default-originate":
                    continue
                if not any(net.subnet_of(n) for n in mine) and not any(
                        net.subnet_of(n) for n in out_nets):  # fmt: skip
                    outside.append(str(net))
        name = list_name(neighbor, way)
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
            elif not any(net.version == n.version and net.subnet_of(n) for n in mine):
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
                "proposal": aci_proposal(o, entries, excluded),
                "state": state, "text": text}  # fmt: skip


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
