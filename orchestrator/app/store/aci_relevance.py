"""Relevanz von ACI-Contracts ueber externe EPGs (0.28.0, ADR 0019 Punkt 6).

Ein System ausserhalb des Fabrics (z. B. hinter der Firewall) erreicht EPGs ueber einen
L3Out. Das ACI ordnet seinen Verkehr in der VRF einer externen EPG zu, und es gelten deren
Contracts:

- Es zaehlen die Subnetze externer EPGs mit Scope `import-security` (APIC-Standard, auch ohne
  gelesenen Scope). Die uebrigen (`export-rtctrl` …) steuern nur das Routing.
- Je VRF gewinnt das laengste passende Subnetz - die Zuordnung gilt fuer die VRF, nicht je
  L3Out. Contracts externer EPGs mit kuerzerem Subnetz gelten fuer die IP nicht.
- Liegt die IP im Fabric der VRF (BD- bzw. EPG-Subnetz), gehoert sie zu ihrer EPG, nicht zu
  einer externen.
- Gleich lange Subnetze in mehreren L3Outs (typisch 0.0.0.0/0): moeglich ist die EPG des
  L3Outs, ueber den die VRF die IP leitet; fuer die anderen bleibt es unbekannt (nicht
  ausgeschlossen).
- **Firewall fuehrend (ADR 0034):** Steht hinter dem L3Out der externen EPG eine Firewall
  (`store/l3out_firewall.py`, abgeleitet bzw. von Hand), entscheidet ihr Routing, ueber welchen
  L3Out die IP kommt - nicht das (meist weit offene) Subnetz im ACI: es gewinnt der L3Out,
  dessen Firewall die IP am genauesten nach aussen routet; leitet sie die IP zurueck ins ACI,
  kommt sie nicht ueber diesen L3Out. Innerhalb der gewaehlten L3Outs gilt weiter das laengste
  Subnetz. Ohne Firewall bzw. ohne Aussage bleibt die Zuordnung im ACI (Rueckfall).

Ein Netz (Weg-Suche, 0.39.0) zaehlt wie eine IP, wenn das Subnetz es ganz enthaelt.

Ergebnis je externer EPG und IP: `possible`, `impossible` (mit Begruendung) oder `unknown`;
None, wenn kein Subnetz der EPG die IP erfasst. Nur lesend - es zaehlen die gelesenen Daten.
Noch nicht: Shared Services zwischen VRFs (`shared-security`), ESGs mit IP-Selektoren.
"""

import ipaddress
from collections import defaultdict
from collections.abc import Callable
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import Epg, EpgEndpoint, EpgSubnet, InvSubnet, InvVrf, TopoContext, TopoL3Out
from app.store import epgs as epg_store
from app.store.paths import Graph
from app.store.topology import aci_vrf, is_net, overlaps, within

POSSIBLE, IMPOSSIBLE, UNKNOWN = "possible", "impossible", "unknown"
RANK = {POSSIBLE: 0, UNKNOWN: 1, IMPOSSIBLE: 2}

CLASSIFY = "import-security"
# Scope eines Contracts in Worten (Regel-Detail, Begruendungen)
SCOPE_LABEL = {"application-profile": "AP", "context": "VRF", "tenant": "Tenant",
               "global": "global"}  # fmt: skip

Net = ipaddress.IPv4Network | ipaddress.IPv6Network


def classifies(scope: str | None) -> bool:
    """Ordnet das Subnetz Verkehr der externen EPG zu? Ohne Scope: APIC-Standard."""
    return scope is None or CLASSIFY in {s.strip() for s in scope.split(",")}


def _net(value: str) -> Net | None:
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None


def ext_label(epg: Epg) -> str:
    return f"{epg.l3out}|{epg.name}" if epg.l3out else epg.name


class Classifier:
    """Externe EPGs je APIC und VRF mit ihren Subnetzen; einmal je Mandant geladen."""

    def __init__(self, session: Session, tenant_id: int, graph: Callable[[], Graph] | None = None):
        self.session = session
        self.tenant_id = tenant_id
        self._graph_of = graph  # Graph der Wege teilen (Doku: einmal je Mandant)
        self._graph: Graph | None = None
        self._cache: dict[tuple, tuple[str, str] | None] = {}

    @property
    def graph(self) -> Graph:
        if self._graph is None:
            self._graph = (
                self._graph_of() if self._graph_of else Graph(self.session, self.tenant_id)
            )
        return self._graph

    @cached_property
    def locator(self):
        """EPG einer IP nach der Konfiguration (statischer Endpunkt, Portgruppe der VM)."""
        from app.store import epg_locate  # spaet: epg_locate importiert dieses Modul

        return epg_locate.Locator(self.session, self.tenant_id, self)

    @cached_property
    def behind(self):
        """Firewalls hinter den L3Outs (ADR 0034): ihr Routing entscheidet, ueber welchen L3Out
        eine IP kommt; teilt den Graph."""
        from app.store import l3out_firewall  # spaet: l3out_firewall -> topo_model -> paths

        return l3out_firewall.Behind(self.session, self.tenant_id, lambda: self.graph)

    @cached_property
    def _l3outs(self) -> list[TopoL3Out]:
        return list(
            self.session.scalars(select(TopoL3Out).where(TopoL3Out.tenant_id == self.tenant_id))
        )

    @cached_property
    def _vrfs(self) -> dict[int | None, set[str]]:
        """Bekannte VRFs `Tenant:VRF` je APIC (Inventar, Routing-Kontexte)."""
        out: dict[int | None, set[str]] = defaultdict(set)
        for sid, name in self.session.execute(
            select(InvVrf.source_system_id, InvVrf.name).where(InvVrf.tenant_id == self.tenant_id)
        ):
            out[sid].add(name)
        for sid, name in self.session.execute(
            select(TopoContext.source_system_id, TopoContext.context).where(
                TopoContext.tenant_id == self.tenant_id, TopoContext.kind == "vrf"
            )
        ):
            out[sid].add(name)
        return out

    def vrf_of(self, epg: Epg) -> str | None:
        """VRF `Tenant:VRF` der EPG: bei externen die des L3Outs, sonst im eigenen ACI-Tenant
        bzw. wie der APIC in `common`."""
        if epg.kind == "external":
            for o in self._l3outs:
                if (o.source_system_id, o.aci_tenant, o.name) == (
                    epg.source_system_id,
                    epg.aci_tenant,
                    epg.l3out,
                ) and o.vrf:
                    return o.vrf
        if not epg.vrf:
            return None
        if ":" in epg.vrf:
            return epg.vrf
        if epg.vrf_tenant:  # aufgeloest im Collector (`fvRsCtx.tDn`, 0.44.0)
            return f"{epg.vrf_tenant}:{epg.vrf}"
        if epg.kind == "vzany":  # vzAny haengt an der VRF seines Tenants
            return f"{epg.aci_tenant}:{epg.vrf}"
        own = f"{epg.aci_tenant}:{epg.vrf}"
        known = self._vrfs.get(epg.source_system_id, set())
        return own if own in known or not known else f"common:{epg.vrf}"

    def scope_allows(self, scope: str | None, own: Epg, other: Epg) -> bool:
        """Erlaubt ein Contract mit diesem Scope Verkehr zwischen den beiden EPGs? (Anwender,
        2026-10-23: der Scope legt fest, was der Contract verbindet.)

        - `application-profile`: nur innerhalb des AP (gleicher Tenant und AP). vzAny steht fuer
          alle EPGs seiner VRF - mit einer EPG derselben VRF verbindet es die EPGs ihres AP.
          Externe EPGs liegen in keinem AP.
        - `context`: nur innerhalb der VRF (und des APIC).
        - `tenant`: innerhalb des Tenants, auch ueber mehrere seiner VRFs (vzAny bzw. externe
          EPG einer VRF aus `common`: gleiche VRF).
        - `global` bzw. ohne Scope: alle verbundenen EPGs.

        Das gilt auch fuer Contracts aus `common` - mit Scope VRF verbinden sie nur EPGs
        derselben VRF, auch wenn EPGs vieler Tenants sie nutzen (Dev-Befund 0.43.0). Ist eine
        VRF unbekannt, bleibt die Gegenstelle (nicht ausgeschlossen). Zwischen verschiedenen
        VRFs braucht es zusaetzlich den Leak der Subnetze (`aci_shared`, 0.44.0)."""
        return self.scope_reason(scope, own, other) is None

    def _same_vrf(self, own: Epg, other: Epg) -> bool | None:
        a, b = self.vrf_of(own), self.vrf_of(other)
        if a is None or b is None:
            return None
        return (own.source_system_id, a) == (other.source_system_id, b)

    def scope_reason(self, scope: str | None, own: Epg, other: Epg) -> str | None:
        """Warum der Scope die beiden nicht verbindet - None, wenn er sie verbindet."""
        if not scope or scope == "global":
            return None
        a, b = epg_store.inventory_label(own), epg_store.inventory_label(other)
        same_vrf = self._same_vrf(own, other)
        if scope == "application-profile":
            if "vzany" in (own.kind, other.kind):
                return None if same_vrf is not False else L(
                    f"Scope AP: vzAny gilt nur in seiner VRF ({a} – {b})",
                    f"Scope AP: vzAny applies only in its VRF ({a} – {b})")  # fmt: skip
            if own.ap is not None and (own.aci_tenant, own.ap) == (other.aci_tenant, other.ap):
                return None
            return L(f"Scope AP: {a} und {b} liegen nicht im selben AP",
                     f"Scope AP: {a} and {b} are not in the same AP")  # fmt: skip
        regular = own.kind in ("epg", "esg") and other.kind in ("epg", "esg")
        if scope == "tenant" and (regular or "common" not in {own.aci_tenant, other.aci_tenant}):
            if own.aci_tenant == other.aci_tenant:
                return None
            ta, tb = own.aci_tenant, other.aci_tenant
            return L(f"Scope Tenant: {a} (Tenant {ta}) und {b} (Tenant {tb}) liegen in "
                     "verschiedenen Tenants",
                     f"Scope Tenant: {a} (Tenant {ta}) and {b} (Tenant {tb}) are in different "
                     "tenants")  # fmt: skip
        if same_vrf is not False:
            return None
        name, va, vb = SCOPE_LABEL.get(scope, scope), self.vrf_of(own), self.vrf_of(other)
        return L(
            f"Scope {name}: {a} (VRF {va}) und {b} (VRF {vb}) liegen in verschiedenen VRFs",
            f"Scope {name}: {a} (VRF {va}) and {b} (VRF {vb}) are in different VRFs",
        )

    def scope_pairs(
        self, scope: str | None, consumers: list[Epg], providers: list[Epg]
    ) -> tuple[list[tuple[Epg, Epg]], set[int]]:
        """Consumer -> Provider, die der Scope verbindet, und die EPG-IDs ohne Gegenstelle im
        Scope (fuer Regel-Detail und Befund)."""
        pairs = [(c, p) for c in consumers for p in providers
                 if c.id != p.id and self.scope_allows(scope, c, p)]  # fmt: skip
        paired = {e.id for pair in pairs for e in pair}
        return pairs, {e.id for e in [*consumers, *providers]} - paired

    @cached_property
    def _ext(self) -> dict[tuple[int | None, str], list[tuple[Epg, Net, str | None]]]:
        """(APIC, VRF) -> Subnetze externer EPGs mit Scope."""
        epgs = {
            e.id: e
            for e in self.session.scalars(
                select(Epg).where(Epg.tenant_id == self.tenant_id, Epg.kind == "external")
            )
        }
        out: dict[tuple[int | None, str], list[tuple[Epg, Net, str | None]]] = defaultdict(list)
        if not epgs:
            return out
        for s in self.session.scalars(
            select(EpgSubnet).where(EpgSubnet.epg_id.in_(list(epgs)), EpgSubnet.origin == "ext")
        ):
            epg, net = epgs[s.epg_id], _net(s.ip)
            vrf = self.vrf_of(epg)
            if net is not None and vrf:
                out[(epg.source_system_id, vrf)].append((epg, net, s.scope))
        return out

    def externals(self, ip) -> list[Epg]:
        """Externe EPGs, deren Subnetze die IP erfassen (jeder Scope)."""
        seen: dict[int, Epg] = {}
        for rows in self._ext.values():
            for epg, net, _scope in rows:
                if within(ip, net):
                    seen.setdefault(epg.id, epg)
        return sorted(seen.values(), key=lambda e: (e.aci_tenant or "", e.l3out or "", e.name))

    def overlapping(self, sid: int | None, vrf: str, net) -> list[tuple[Epg, Net]]:
        """Zuordnende Subnetze externer EPGs der VRF, die einen Teil des Netzes erfassen
        (Weg-Suche mit einem Netz, 0.39.0)."""
        return [(e, n) for e, n, s in self._ext.get((sid, vrf), [])
                if classifies(s) and overlaps(net, n)]  # fmt: skip

    def subnet_for(self, epg: Epg, ip) -> Net | None:
        """Laengstes Subnetz der externen EPG, das die IP erfasst (zuordnende zuerst)."""
        rows = self._ext.get((epg.source_system_id, self.vrf_of(epg) or ""), [])
        own = [(classifies(s), n.prefixlen, n) for e, n, s in rows
               if e.id == epg.id and within(ip, n)]  # fmt: skip
        return max(own, key=lambda x: x[:2])[2] if own else None

    def inside(self, epg: Epg, ip) -> str | None:
        """Ort im Fabric der VRF der EPG, wenn die IP dort liegt (dann zaehlt ihre EPG)."""
        return self._fabric(epg.source_system_id, self.vrf_of(epg), ip)

    def check(self, epg: Epg, ip) -> tuple[str, str] | None:
        """Relevanz der externen EPG fuer die IP (siehe Modul); None ohne passendes Subnetz."""
        key = (epg.id, str(ip))
        if key not in self._cache:
            self._cache[key] = self._decide(epg, ip)
        return self._cache[key]

    def _decide(self, epg: Epg, ip) -> tuple[str, str] | None:
        vrf = self.vrf_of(epg)
        rows = self._ext.get((epg.source_system_id, vrf or ""), [])
        own = [(n, s) for e, n, s in rows if e.id == epg.id and within(ip, n)]
        if not own:
            return None
        label = ext_label(epg)
        mine = [n for n, s in own if classifies(s)]
        if not mine:
            net, scope = own[0]
            return IMPOSSIBLE, L(
                f"Subnetz {net} der externen EPG {label} steuert nur das Routing (Scope {scope}) "
                "– es ordnet keinen Verkehr zu",
                f"Subnet {net} of external EPG {label} only controls routing (scope {scope}) "
                "– it does not classify traffic",
            )
        inside = self._fabric(epg.source_system_id, vrf, ip)
        if inside:
            return IMPOSSIBLE, L(
                f"{ip} liegt in VRF {vrf} im Fabric ({inside}) – Verkehr kommt aus der EPG, nicht "
                f"über {epg.l3out or 'einen L3Out'}",
                f"{ip} is in the fabric of VRF {vrf} ({inside}) – traffic comes from the EPG, not "
                f"via {epg.l3out or 'an L3Out'}",
            )
        by_firewall = self._by_firewall(epg, ip, vrf, rows, mine)
        if by_firewall is not None:
            return by_firewall
        best = max(n.prefixlen for n in mine)
        cover = [(e, n) for e, n, s in rows if classifies(s) and within(ip, n)]
        top = max(n.prefixlen for _e, n in cover)
        mine_net = next(n for n in mine if n.prefixlen == best)
        if best < top:
            winners = ", ".join(
                f"{ext_label(e)} ({n})" for e, n in cover if n.prefixlen == top and e.id != epg.id
            )
            return IMPOSSIBLE, L(
                f"{ip} gehört in VRF {vrf} zur externen EPG {winners} – genauer als {mine_net} "
                f"von {label}",
                f"{ip} belongs to external EPG {winners} in VRF {vrf} – more specific than "
                f"{mine_net} of {label}",
            )
        others = sorted({ext_label(e) for e, n in cover if n.prefixlen == top and e.id != epg.id})
        if not others:
            return POSSIBLE, ""
        # gleich lange Subnetze in mehreren externen EPGs (z. B. 0.0.0.0/0): nach dem Routing
        routed = self.routed_l3outs(epg.source_system_id, vrf, ip)
        if routed and epg.l3out in routed:
            return POSSIBLE, ""
        listed = ", ".join(others)
        also = L(f"{mine_net} auch bei {listed}", f"{mine_net} also at {listed}")
        if routed is None:
            return UNKNOWN, L(f"{also} – Route der VRF {vrf} zu {ip} nicht bekannt",
                              f"{also} – route of VRF {vrf} to {ip} not known")  # fmt: skip
        via = ", ".join(routed) if routed else L("keinen gelesenen L3Out", "no L3Out read")
        return UNKNOWN, L(f"{also}; die VRF {vrf} leitet {ip} über {via}",
                          f"{also}; VRF {vrf} routes {ip} via {via}")  # fmt: skip

    def _by_firewall(self, epg: Epg, ip, vrf: str | None, rows, mine) -> tuple[str, str] | None:
        """Firewall fuehrend (ADR 0034): der L3Out der IP nach dem Routing der Firewalls hinter
        den L3Outs der VRF. None ohne Aussage fuer den L3Out dieser EPG (keine Firewall, keine
        Route) - dann gilt die Zuordnung im ACI."""
        name = epg.l3out
        d = self.behind.decide(epg.source_system_id, vrf, ip) if name else None
        if d is None or name not in d.names or name in d.unknown:
            return None
        label = ext_label(epg)
        if name in d.best:
            # innerhalb der L3Outs der Firewall: das laengste zuordnende Subnetz
            cover = [(e, n) for e, n, s in rows
                     if classifies(s) and within(ip, n) and e.l3out in d.best]  # fmt: skip
            top = max(n.prefixlen for _e, n in cover)
            best = max(n.prefixlen for n in mine)
            if best < top:
                mine_net = next(n for n in mine if n.prefixlen == best)
                winners = ", ".join(f"{ext_label(e)} ({n})" for e, n in cover
                                    if n.prefixlen == top and e.id != epg.id)  # fmt: skip
                return IMPOSSIBLE, L(
                    f"{ip} gehört in VRF {vrf} zur externen EPG {winners} – genauer als "
                    f"{mine_net} von {label}",
                    f"{ip} belongs to external EPG {winners} in VRF {vrf} – more specific than "
                    f"{mine_net} of {label}",
                )
            reason = d.reasons[name]
            return POSSIBLE, L(f"über {name}: {reason}", f"via {name}: {reason}")
        if d.best:
            via, why = ", ".join(d.best), d.reason()
            return IMPOSSIBLE, L(f"{ip} kommt über {via}: {why} – nicht über {name}",
                                 f"{ip} comes via {via}: {why} – not via {name}")  # fmt: skip
        if name in d.back:
            why = d.back[name]
            return IMPOSSIBLE, L(f"{why} – nicht über {name}", f"{why} – not via {name}")
        return None

    def l3out_of(self, sid: int | None, vrf: str | None, ip) -> list[str] | None:
        """L3Outs der IP nach dem Routing der Firewalls (ADR 0034) - None ohne Aussage."""
        d = self.behind.decide(sid, vrf, ip)
        return list(d.best) if d is not None and d.best else None

    def _fabric(self, sid: int | None, vrf: str | None, ip) -> str | None:
        """Ort im Fabric der VRF (EPG bzw. BD), wenn ein BD-/EPG-Subnetz die IP enthaelt, die
        Portgruppe ihrer VM bzw. ein statischer Endpunkt sie einer EPG zuordnet (0.83.1: BD ohne
        Subnetz, Gateway auf der Firewall - das ACI lernt dort keine IPs) oder ein gelernter
        Endpunkt sie hat."""
        if not vrf:
            return None
        for s in self._fabric_subnets.get((sid, vrf), []):
            if within(ip, s[0]):
                return s[1]
        if not is_net(ip):
            for epg, how in self.locator.configured(str(ip), sid, vrf):
                return f"{how}, EPG {epg_store.inventory_label(epg).replace('/', '|')}"
        for epg in self.session.scalars(
            select(Epg)
            .join(EpgEndpoint, EpgEndpoint.epg_id == Epg.id)
            .where(Epg.tenant_id == self.tenant_id, Epg.kind == "epg", EpgEndpoint.ip == str(ip))
        ):
            if epg.source_system_id == sid and self.vrf_of(epg) == vrf:
                name = epg_store.inventory_label(epg).replace("/", "|")
                return L(f"Endpunkt der EPG {name}", f"endpoint of EPG {name}")
        return None

    @cached_property
    def _fabric_subnets(self) -> dict[tuple[int | None, str], list[tuple[Net, str]]]:
        """(APIC, VRF) -> BD-/EPG-Subnetze mit Ort (Inventar und EPGs der Contracts); laengste
        zuerst."""
        out: dict[tuple[int | None, str], list[tuple[Net, str]]] = defaultdict(list)
        known = {(sid, v) for sid, names in self._vrfs.items() for v in names}
        for s in self.session.scalars(
            select(InvSubnet).where(
                InvSubnet.tenant_id == self.tenant_id, InvSubnet.context.in_(("bd", "epg"))
            )
        ):
            net = _net(s.prefix)
            if net is None or not s.vrf:
                continue
            where = (
                f"EPG {s.aci_tenant}|{s.ap}|{s.epg}"
                if s.epg and s.ap
                else f"BD {s.aci_tenant}|{s.bd or s.context_name}"
            )
            vrf = aci_vrf(s.vrf, s.source_system_id, known)  # VRF aus common (0.83.1)
            out[(s.source_system_id, vrf)].append((net, f"{where} {s.prefix}"))
        epgs = {
            e.id: e
            for e in self.session.scalars(
                select(Epg).where(Epg.tenant_id == self.tenant_id, Epg.kind == "epg")
            )
        }
        if epgs:
            for s in self.session.scalars(
                select(EpgSubnet).where(
                    EpgSubnet.epg_id.in_(list(epgs)), EpgSubnet.origin.in_(("epg", "bd"))
                )
            ):
                epg, net = epgs[s.epg_id], _net(s.ip)
                vrf = self.vrf_of(epg)
                if net is not None and vrf:
                    where = f"EPG {epg_store.inventory_label(epg).replace('/', '|')}"
                    out[(epg.source_system_id, vrf)].append((net, f"{where} {net}"))
        for rows in out.values():
            rows.sort(key=lambda r: -r[0].prefixlen)
        return out

    def routed_l3outs(self, sid: int | None, vrf: str | None, ip) -> list[str] | None:
        """L3Outs, ueber die die VRF (alle Border-Leafs) die IP leitet: Next-Hop im Netz einer
        L3Out-SVI bzw. Adresse eines Peers oder einer statischen Route. None ohne Route."""
        key = (sid, vrf or "")
        if sid is None or key not in self.graph.nodes:
            return None
        route = self.graph.route(key, ip, 0)
        if route is None:
            return None
        hops = [h["ip"] for h in route["next_hops"] or [] if h.get("ip")]
        out: list[str] = []
        for o in self._l3outs:
            if (o.source_system_id, o.vrf) != (sid, vrf):
                continue
            if any(_reaches(o, h) for h in hops) and o.name not in out:
                out.append(o.name)
        return sorted(out)


def _reaches(l3out: TopoL3Out, hop: str) -> bool:
    """Next-Hop gehoert zum L3Out: im Netz einer SVI bzw. eines Interfaces, Peer oder Next-Hop
    einer statischen Route."""
    try:
        ip = ipaddress.ip_address(hop)
    except ValueError:
        return False
    if any(p.get("ip") == hop for p in l3out.peers or []):
        return True
    for n in l3out.nodes or []:
        for r in n.get("static_routes") or []:
            if hop in (r.get("next_hops") or []):
                return True
    for path in l3out.paths or []:
        for a in path.get("ips") or []:
            try:
                net = ipaddress.ip_interface(a.get("ip") or "").network
            except ValueError:
                continue
            if net.version == ip.version and ip in net:
                return True
    return False
