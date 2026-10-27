"""Ausschnitt eines Kunden auf der Infrastruktur (0.76.0, ADR 0018 Ergaenzung).

Anwender: „oben den Kunden waehlen, und alles filtert nach dem Kunden – keine zusaetzlichen
Infos“. Bis 0.75.0 filterte der Umschalter nur die Doku-Listen (Regeln, Objekte, NAT, Findings,
VMs, Applikationen). Hier steht, was ein Kunde in den uebrigen Ansichten sieht - Routing,
Topologie, Weg-Suche, Inventar, Verkabelung, Abgleich - und was davon **geteilt** ist (Entscheid:
eigenes plus geteiltes, das geteilte markiert):

- **Kontexte** (VDOM bzw. ACI-VRF `Tenant:VRF`): die VDOMs seiner Firewall-Selektoren und seiner
  Regeln, die VRFs seiner EPGs. *Eigen*, wenn kein anderer Kunde dort Regeln bzw. EPGs hat und
  der Kontext nicht nur ueber den geteilten Teil (z. B. Tenant `common`) dazukommt; sonst
  *geteilt*. Router bzw. L3-Switches (ADR 0032, Art `l3`) sind immer *geteilt* - im Ausschnitt,
  wenn sie ein Netz des Kunden routen (Route ausser der Default-Route, die eines seiner Netze
  enthaelt oder darin liegt). In einem geteilten Kontext zeigt das Routing nur Routen, die ein
  Netz des Kunden enthalten oder darin liegen (auch die Default-Route) - keine Routen anderer
  Kunden.
- **Netze**: seine (eigen) bzw. die des geteilten Teils (geteilt), `customer_member` kind `net`.
- **EPGs** als `Tenant/AP/EPG` (Schreibweise im Inventar), **VLANs** (der EPGs, der Netze des
  Kunden, der Portgruppen seiner VMs), **Ports** (mit seinen EPGs, in seinen VDOMs, mit IPs in
  seinen Netzen bzw. seinen VLANs) und **Geraete** (an diesen Ports, Knoten seiner Kontexte,
  ESXi-Hosts seiner VMs).

Nur fuer Betreuer mit gewaehltem Kunden; Kunden-Benutzer erreichen diese Ansichten nicht
(Freigabeliste `auth/customer_routes.py`).
"""

import ipaddress
from collections import defaultdict
from functools import cached_property

from sqlalchemy import and_, false, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    CustomerMember,
    Epg,
    InvInterface,
    InvSubnet,
    InvVlan,
    Portgroup,
    Rule,
    RuleDomain,
    TopoContext,
    TopoRoute,
    Vm,
    VmNic,
)
from app.store import aci_relevance
from app.store.customer_scope import View
from app.store.topology import ip_hex

OWN, SHARED = "own", "shared"


def _net(value: str | None):
    try:
        return ipaddress.ip_network((value or "").strip(), strict=False)
    except ValueError:
        return None


def _mark(*marks: str | None) -> str | None:
    """Eigen schlaegt geteilt."""
    present = [m for m in marks if m]
    return OWN if OWN in present else (SHARED if present else None)


class Infra:
    """Was ein Kunde auf der Infrastruktur sieht, je Element eigen bzw. geteilt."""

    def __init__(self, session: Session, view: View):
        self.session = session
        self.view = view
        self.tenant_id = view.tenant_id
        self.own = set(view.own)

    # -- Grundlagen --------------------------------------------------------------------

    @cached_property
    def _members(self) -> dict[str, dict[int, set[int]]]:
        """Art -> ref_id -> Kunden (alle Kunden des Mandanten, fuer „andere Kunden“)."""
        out: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
        for cid, kind, ref in self.session.execute(
            select(CustomerMember.customer_id, CustomerMember.kind, CustomerMember.ref_id).where(
                CustomerMember.tenant_id == self.tenant_id,
                CustomerMember.kind.in_(("epg", "rule")),
                CustomerMember.ref_id.is_not(None),
            )
        ):
            out[kind][ref].add(cid)
        return out

    def _whose(self, kind: str, ref: int) -> str | None:
        """Eigen (ein eigener Kunde), geteilt (nur der geteilte Teil) bzw. nicht im Ausschnitt."""
        cids = self._members[kind].get(ref, set())
        if cids & self.own:
            return OWN
        if cids & set(self.view.customer_ids):
            return SHARED
        return None

    def _others(self, kind: str, ref: int) -> bool:
        """Gehoert das Element (auch) einem Kunden ausserhalb der Sicht bzw. dem geteilten Teil?"""
        return bool(self._members[kind].get(ref, set()) - self.own)

    @cached_property
    def epgs(self) -> dict[int, str]:
        out = {}
        for ref in self._members["epg"]:
            mark = self._whose("epg", ref)
            if mark:
                out[ref] = mark
        return out

    @cached_property
    def _epg_rows(self) -> list[Epg]:
        return list(self.session.scalars(select(Epg).where(Epg.tenant_id == self.tenant_id)))

    @cached_property
    def epg_paths(self) -> dict[str, str]:
        """`Tenant/AP/EPG` (Inventar, Ports) -> eigen/geteilt."""
        out: dict[str, str] = {}
        for e in self._epg_rows:
            mark = self.epgs.get(e.id)
            if mark and e.aci_tenant and e.ap:
                key = f"{e.aci_tenant}/{e.ap}/{e.name}"
                out[key] = _mark(out.get(key), mark) or mark
        return out

    @cached_property
    def nets(self) -> list[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]]:
        """Netze des Kunden (eigen) und des geteilten Teils (geteilt), zusammengefasst."""
        own: list = []
        shared: list = []
        for cid, value in self.session.execute(
            select(CustomerMember.customer_id, CustomerMember.value).where(
                CustomerMember.customer_id.in_(self.view.customer_ids),
                CustomerMember.kind == "net",
            )
        ):
            n = _net(value)
            if n is not None:
                (own if cid in self.own else shared).append(n)
        out = []
        for nets, mark in ((own, OWN), (shared, SHARED)):
            for version in (4, 6):
                out += [
                    (n, mark)
                    for n in ipaddress.collapse_addresses(x for x in nets if x.version == version)
                ]
        return out

    def net_mark(self, value: str | None) -> str | None:
        """Liegt die IP bzw. das Netz ganz in einem Netz des Kunden? (eigen vor geteilt)"""
        n = _net((value or "").split("-")[0])
        if n is None:
            return None
        marks = [mark for net, mark in self.nets if net.version == n.version and n.subnet_of(net)]
        return _mark(*marks)

    def overlaps(self, value: str | None) -> bool:
        """Enthaelt das Netz ein Netz des Kunden oder liegt es darin?"""
        n = _net(value)
        if n is None:
            return False
        return any(
            net.version == n.version and (n.subnet_of(net) or net.subnet_of(n))
            for net, _ in self.nets
        )

    def overlap_clause(self, family_col, lo_col, hi_col):
        """SQL: Bereich [lo, hi] ueberschneidet ein Netz des Kunden (Routen)."""
        parts = [
            and_(
                family_col == n.version,
                lo_col <= ip_hex(n.broadcast_address),
                hi_col >= ip_hex(n.network_address),
            )
            for n, _ in self.nets
        ]
        return or_(*parts) if parts else false()

    # -- Kontexte ----------------------------------------------------------------------

    @cached_property
    def _selectors(self) -> list:
        from app.store import customers  # spaet: customers -> relevance -> paths

        out = []
        for c in self.session.scalars(
            select(Customer).where(Customer.id.in_(self.view.customer_ids))
        ):
            for s in customers.selectors_of(c):
                if not s.exclude:
                    out.append((c.id, s))
        return out

    @cached_property
    def contexts(self) -> dict[tuple[int, str], str]:
        """(Quellsystem, Kontext) -> eigen/geteilt. Ein VDOM ist *eigen* nur ueber einen
        VDOM-Selektor des Kunden, eine VRF nur im eigenen ACI-Tenant - und nur, wenn kein
        anderer Kunde dort Regeln bzw. EPGs hat. VDOMs, in denen der Kunde nur Regeln hat
        (z. B. `root` fuer alle), und VRFs aus `common` sind *geteilt*."""
        dedicated: set[tuple[int, str]] = set()  # eigen moeglich
        used: set[tuple[int, str]] = set()  # im Ausschnitt (eigen bzw. geteilt)
        foreign: set[tuple[int, str]] = set()  # dort hat auch ein anderer Kunde etwas
        # VDOMs der Firewall-Selektoren (ohne Quellsystem: alle Firewalls mit diesem VDOM)
        fw_contexts = list(
            self.session.execute(
                select(TopoContext.source_system_id, TopoContext.context)
                .where(TopoContext.tenant_id == self.tenant_id, TopoContext.kind == "vdom")
                .distinct()
            )
        )
        for cid, s in self._selectors:
            if s.kind != "fw_vdom":
                continue
            for sid, ctx in fw_contexts:
                if ctx == s.value.strip() and s.source_system_id in (None, sid):
                    used.add((sid, ctx))
                    if cid in self.own:
                        dedicated.add((sid, ctx))
        # VDOMs der Regeln
        for rid, sid, vdom in self.session.execute(
            select(Rule.id, Rule.source_system_id, Rule.vdom).where(
                Rule.tenant_id == self.tenant_id, Rule.domain == RuleDomain.FIREWALL
            )
        ):
            key = (sid, vdom or "root")
            if self._whose("rule", rid):
                used.add(key)
            if self._others("rule", rid):
                foreign.add(key)
        # VRFs der EPGs: eigen nur im ACI-Tenant einer eigenen EPG
        classifier = aci_relevance.Classifier(self.session, self.tenant_id)
        own_tenants = {
            e.aci_tenant for e in self._epg_rows if self.epgs.get(e.id) == OWN and e.aci_tenant
        }
        for e in self._epg_rows:
            vrf = classifier.vrf_of(e)
            if not vrf or e.source_system_id is None:
                continue
            key = (e.source_system_id, vrf)
            mark = self.epgs.get(e.id)
            if mark:
                used.add(key)
            if mark == OWN and vrf.split(":", 1)[0] in own_tenants:
                dedicated.add(key)
            if self._others("epg", e.id):
                foreign.add(key)
        used |= self._routers()
        return {k: OWN if k in dedicated and k not in foreign else SHARED for k in used}

    def _routers(self) -> set[tuple[int, str]]:
        """Router-Kontexte (ADR 0032), die ein Netz des Kunden routen - immer geteilt."""
        routers = set(
            self.session.execute(
                select(TopoContext.source_system_id, TopoContext.context).where(
                    TopoContext.tenant_id == self.tenant_id, TopoContext.kind == "l3"
                )
            ).tuples()
        )
        if not routers or not self.nets:
            return set()
        hits = self.session.execute(
            select(TopoRoute.source_system_id, TopoRoute.context)
            .where(
                TopoRoute.tenant_id == self.tenant_id,
                TopoRoute.table == "rib",
                TopoRoute.prefix_len > 0,
                TopoRoute.source_system_id.in_({sid for sid, _ in routers}),
                self.overlap_clause(TopoRoute.family, TopoRoute.lo, TopoRoute.hi),
            )
            .distinct()
        ).tuples()
        return {k for k in hits if k in routers}

    def context_mark(self, source_system_id: int, context: str | None) -> str | None:
        return self.contexts.get((source_system_id, context or "root"))

    @cached_property
    def nodes(self) -> dict[tuple[int, str], str]:
        """(Quellsystem, Knoten = Firewall bzw. Border-Leaf) seiner Kontexte."""
        out: dict[tuple[int, str], str] = {}
        for sid, node, ctx in self.session.execute(
            select(TopoContext.source_system_id, TopoContext.node, TopoContext.context).where(
                TopoContext.tenant_id == self.tenant_id
            )
        ):
            mark = self.context_mark(sid, ctx)
            if mark:
                out[(sid, node)] = _mark(out.get((sid, node)), mark) or mark
        return out

    # -- L2: VLANs, Ports, Geraete ------------------------------------------------------

    @cached_property
    def vlans(self) -> dict[int, str]:
        """VLAN-IDs: der EPGs (Inventar), der Netze des Kunden und der Portgruppen seiner EPGs."""
        out: dict[int, str] = {}

        def add(vid, mark):
            if vid is not None and mark:
                out[vid] = _mark(out.get(vid), mark) or mark

        for vid, epgs in self.session.execute(
            select(InvVlan.vid, InvVlan.epgs).where(InvVlan.tenant_id == self.tenant_id)
        ):
            add(vid, _mark(*(self.epg_paths.get(p) for p in epgs or [])))
        for vid, prefix in self.session.execute(
            select(InvSubnet.vlan, InvSubnet.prefix).where(
                InvSubnet.tenant_id == self.tenant_id, InvSubnet.vlan.is_not(None)
            )
        ):
            add(vid, self.net_mark(prefix))
        for vid, epg_id in self.session.execute(
            select(Portgroup.vlan, Portgroup.epg_id).where(
                Portgroup.tenant_id == self.tenant_id, Portgroup.epg_id.is_not(None)
            )
        ):
            add(vid, self.epgs.get(epg_id))
        return out

    def port_mark(self, port: InvInterface) -> str | None:
        marks = [self.epg_paths.get(p) for p in port.epgs or []]
        if port.vdom:
            marks.append(self.context_mark(port.source_system_id, port.vdom))
        marks += [self.net_mark(ip) for ip in port.ips or []]
        for vid in [port.untagged_vlan, port.native_vlan, *(port.tagged_vlans or [])]:
            if isinstance(vid, int):
                marks.append(self.vlans.get(vid))
        return _mark(*marks)

    @cached_property
    def ports(self) -> dict[tuple[int, str, str], str]:
        out = {}
        for p in self.session.scalars(
            select(InvInterface).where(InvInterface.tenant_id == self.tenant_id)
        ):
            mark = self.port_mark(p)
            if mark:
                out[(p.source_system_id, p.device, p.name)] = mark
        return out

    @cached_property
    def vm_hosts(self) -> dict[str, str]:
        """ESXi-Hosts der VMs des Kunden (VMs nur eigene, ADR 0018)."""
        out: dict[str, str] = {}
        vm_ids = self.view.ids("vm")
        for (host,) in self.session.execute(
            select(Vm.host).where(Vm.id.in_(vm_ids), Vm.host.is_not(None)).distinct()
        ):
            out[host] = OWN
        return out

    @cached_property
    def devices(self) -> dict[tuple[int, str], str]:
        """(Quellsystem, Geraet): an seinen Ports bzw. Knoten seiner Kontexte."""
        out: dict[tuple[int, str], str] = dict(self.nodes)
        for (sid, device, _), mark in self.ports.items():
            out[(sid, device)] = _mark(out.get((sid, device)), mark) or mark
        return out

    def device_mark(self, source_system_id: int | None, name: str | None) -> str | None:
        if not name:
            return None
        if source_system_id is not None and (source_system_id, name) in self.devices:
            return self.devices[(source_system_id, name)]
        hit = [m for (_, n), m in self.devices.items() if n == name]
        return _mark(*hit, self.vm_hosts.get(name))

    @cached_property
    def macs(self) -> set[str]:
        """MACs der NICs seiner VMs."""
        vm_ids = self.view.ids("vm")
        return {
            (m or "").lower()
            for (m,) in self.session.execute(select(VmNic.mac).where(VmNic.vm_id.in_(vm_ids)))
            if m
        }


def of(session: Session, view: View | None) -> Infra | None:
    return Infra(session, view) if view is not None else None
