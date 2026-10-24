"""Zugehoerigkeit der Kunden ableiten (ADR 0018, Schritt 1).

Ein Kunde ist ein Ausschnitt der Umgebung (Mandant), beschrieben durch Selektoren
(`models/customer.py`). Daraus folgt je Kunde, was ihm gehoert:

- **EPGs:** ACI-Tenant, VRF (aufgeloest wie im APIC: eigener Tenant, sonst `common`),
  Application Profile, EPG (DN bzw. `Tenant/AP/EPG`), Bridge Domain bzw. das Kuerzel in
  Tenant, VRF oder AP (`TE_<kuerzel>`).
- **Netze:** Praefixe von Hand, die Subnetze der EPGs bzw. ihrer Bridge Domains (ohne die
  Subnetze externer EPGs, z. B. 0.0.0.0/0) und die Netze an den Interfaces zugeordneter
  VDOMs, Zonen bzw. Interfaces. Ausgeschlossene Praefixe werden herausgeschnitten.
- **Werte-Objekte:** deren IP, Netz bzw. Bereich ganz in einem Netz des Kunden liegt, bzw.
  mit dem Kuerzel im Namen.
- **Regeln:** ACI-Contracts mit einer EPG des Kunden; Firewall-Regeln in einem VDOM des
  Kunden, mit einer Zone bzw. einem Interface des Kunden oder deren Quelle bzw. Ziel ein Netz
  des Kunden beruehrt (ohne `any`).
- **VMs:** an einer Portgruppe einer EPG des Kunden, mit einer IP in seinen Netzen bzw. mit
  dem Kuerzel im Namen.
- **IPAM-Merkmale** (0.31.0): Netze und Adressen des Referenzstands mit NetBox-Tenant, VRF bzw.
  DDI-Netzansicht oder einem Tag bzw. Attribut `key=value` zaehlen als Netze des Kunden.
- **NAT** (0.31.0): interne bzw. externe IP in einem Netz des Kunden.
- **Findings** (0.31.0): ueber ihr Subjekt - Regel, Objekt, VM, Portgruppe (Tenant im Namen)
  bzw. Subnetz des Kunden.

Ausschliessende Selektoren nehmen ihre Treffer wieder heraus. Ein Element darf mehreren
Kunden gehoeren. Die Zugehoerigkeit ist abgeleiteter Zustand: nach jedem Lauf und bei
geaenderten Selektoren neu berechnet (`derive`). Nur lesend gegenueber den Quellsystemen.
- **Zonen** (0.53.0): die logischen Zonen des Kunden (`logical_zone.customer_id`) zaehlen mit
  ihren Netzen und EPGs dazu - die Selektoren des Kunden bleiben fuer Ausnahmen.

Noch nicht: Relevanz nach Pfad (ADR 0019).
"""

import bisect
import ipaddress
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    CustomerMember,
    Epg,
    EpgSubnet,
    Finding,
    InvInterface,
    LogicalZone,
    NatRule,
    NetworkObject,
    ReferenceRecordRow,
    Rule,
    RuleDomain,
    RuleEndpoint,
    RuleIpRange,
    SourceSystem,
    SubjectKind,
    TopoExit,
    Vm,
    VmIp,
    VmNic,
    Zone,
)
from app.store import aci_relevance, virtual
from app.store import epgs as epg_store
from app.store.topology import ip_hex

_TOKEN = re.compile(r"[_\-|/.:\s]+")
SAMPLES = 5  # Beispiele je Art in der Vorschau
# Tenants der Fabric selbst - keine Kunden-Vorschlaege
_FABRIC_TENANTS = {"common", "infra", "mgmt"}

Net = ipaddress.IPv4Network | ipaddress.IPv6Network


def tokens(*names: str | None) -> set[str]:
    """Teile von Namen (`TE_ka`, `web01-007-ka`) in Kleinbuchstaben."""
    return {t for n in names if n for t in _TOKEN.split(n.casefold()) if t}


def _net(value: str | None) -> Net | None:
    try:
        return ipaddress.ip_network((value or "").strip(), strict=False)
    except ValueError:
        return None


def _bounds(value: str | None) -> tuple[int, int, int] | None:
    """(Familie, erste, letzte) Adresse eines Werts: IP, Netz oder Bereich a-b."""
    text = (value or "").strip()
    try:
        if "-" in text:
            a, _, b = text.partition("-")
            lo, hi = ipaddress.ip_address(a.strip()), ipaddress.ip_address(b.strip())
        else:
            n = ipaddress.ip_network(text, strict=False)
            lo, hi = n.network_address, n.broadcast_address
    except ValueError:
        return None
    return (lo.version, int(lo), int(hi)) if lo.version == hi.version else None


@dataclass
class Selector:
    kind: str
    value: str
    source_system_id: int | None = None
    exclude: bool = False


@dataclass
class Scope:
    """Was die Selektoren eines Kunden erfassen: je Art id -> Grund (Selektor)."""

    epg: dict[int, str] = field(default_factory=dict)
    net: dict[str, str] = field(default_factory=dict)  # CIDR -> Grund
    object: dict[int, str] = field(default_factory=dict)
    rule: dict[int, str] = field(default_factory=dict)
    vm: dict[int, str] = field(default_factory=dict)
    nat: dict[int, str] = field(default_factory=dict)
    finding: dict[int, str] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {k: len(getattr(self, k)) for k in KINDS}


KINDS = ("epg", "net", "object", "rule", "vm", "nat", "finding")
IPAM_KINDS = ("ipam_tenant", "ipam_vrf", "ipam_tag")


def _label(s: Selector) -> str:
    names = {
        "aci_tenant": "Tenant",
        "aci_vrf": "VRF",
        "aci_ap": "AP",
        "aci_epg": "EPG",
        "aci_bd": "BD",
        "fw_vdom": "VDOM",
        "fw_zone": "Zone",
        "fw_interface": "Interface",
        "prefix": "Netz",
        "code": "Kürzel",
        "ipam_tenant": "IPAM-Tenant",
        "ipam_vrf": "IPAM-VRF/Netzansicht",
        "ipam_tag": "IPAM-Merkmal",
        "exit": "Anbindung",
    }
    return f"{names.get(s.kind, s.kind)} {s.value}"


class _Data:
    """Was die Auswertung braucht - einmal je Mandant geladen (auch fuer mehrere Kunden)."""

    def __init__(self, session: Session, tenant_id: int):
        self.session = session
        self.tenant_id = tenant_id
        self.classifier = aci_relevance.Classifier(session, tenant_id)
        self.epgs = list(session.scalars(select(Epg).where(Epg.tenant_id == tenant_id)))
        self.vrf = {e.id: self.classifier.vrf_of(e) for e in self.epgs}
        self.epg_nets: dict[int, list[Net]] = defaultdict(list)
        ids = [e.id for e in self.epgs]
        if ids:
            for s in session.scalars(
                select(EpgSubnet).where(
                    EpgSubnet.epg_id.in_(ids), EpgSubnet.origin.in_(("epg", "bd"))
                )
            ):
                net = _net(s.ip)
                if net is not None:
                    self.epg_nets[s.epg_id].append(net)
        self.interfaces = list(
            session.execute(
                select(
                    InvInterface.source_system_id,
                    InvInterface.name,
                    InvInterface.vdom,
                    InvInterface.zone,
                    InvInterface.ips,
                ).where(InvInterface.tenant_id == tenant_id, InvInterface.vdom.is_not(None))
            )
        )
        self.zones = dict(
            session.execute(select(Zone.id, Zone.name).where(Zone.tenant_id == tenant_id)).all()
        )

    # -- IPAM (Referenzstand, 0.31.0) ---------------------------------------------------

    @cached_property
    def references(self) -> list:
        """Aktueller Referenzstand der IPAM-Quellen: Netze, Bereiche und Adressen mit Tenant,
        VRF bzw. Netzansicht, Tags und Attributen."""
        return list(
            self.session.execute(
                select(
                    ReferenceRecordRow.kind,
                    ReferenceRecordRow.value,
                    ReferenceRecordRow.vrf,
                    ReferenceRecordRow.tenant_name,
                    ReferenceRecordRow.tags,
                    ReferenceRecordRow.attrs,
                )
                .join(SourceSystem, SourceSystem.id == ReferenceRecordRow.source_system_id)
                .where(
                    ReferenceRecordRow.tenant_id == self.tenant_id,
                    ReferenceRecordRow.collector_run_id == SourceSystem.reference_run_id,
                    ReferenceRecordRow.kind.in_(_IPAM_NET_KINDS),
                )
            )
        )

    def ipam_nets(self, s: Selector) -> set[Net]:
        """Netze bzw. Adressen aus dem IPAM mit dem Merkmal des Selektors."""
        v = s.value.strip().casefold()
        out: set[Net] = set()
        for kind, value, vrf, tenant, tags, attrs in self.references:
            if s.kind == "ipam_tenant":
                hit = (tenant or "").casefold() == v
            elif s.kind == "ipam_vrf":
                hit = (vrf or "").casefold() == v
            else:
                pairs = {f"{k}={x}".casefold() for k, x in (attrs or {}).items()}
                hit = v in {str(t).casefold() for t in tags or []} | pairs
            if hit:
                out |= _record_nets(kind, value)
        return out

    def selector_nets(self, s: Selector) -> set[Net]:
        """Netze, die ein Selektor direkt nennt (ohne die Subnetze seiner EPGs)."""
        if s.kind == "prefix":
            net = _net(s.value)
            return {net} if net is not None else set()
        if s.kind in IPAM_KINDS:
            return self.ipam_nets(s)
        if s.kind in FW_KINDS:
            return self.interface_nets(s)
        if s.kind == "exit":
            return self.exit_nets(s)
        return set()

    @cached_property
    def exits(self) -> list:
        return list(
            self.session.scalars(select(TopoExit).where(TopoExit.tenant_id == self.tenant_id))
        )

    def exit_nets(self, s: Selector) -> set[Net]:
        """Netze hinter einer Anbindung (Name bzw. `#id`) - z. B. 0.0.0.0/0 fuer Internet."""
        v = s.value.strip().casefold()
        out: set[Net] = set()
        for e in self.exits:
            if (e.name or "").casefold() == v or f"#{e.id}" == v:
                out |= {n for p in e.prefixes or [] if (n := _net(str(p))) is not None}
        return out

    # -- EPGs --------------------------------------------------------------------------

    def epg_matches(self, s: Selector) -> set[int]:
        v = s.value.strip().casefold()
        out = set()
        for e in self.epgs:
            t, ap, bd = (e.aci_tenant or "").casefold(), (e.ap or ""), (e.bd or "")
            vrf = (self.vrf.get(e.id) or "").casefold()
            if s.kind == "aci_tenant":
                hit = t == v
            elif s.kind == "aci_vrf":
                hit = vrf == v if ":" in v else vrf.split(":")[-1] == v
            elif s.kind == "aci_ap":
                hit = bool(ap) and f"{t}/{ap.casefold()}" == v
            elif s.kind == "aci_epg":
                label = epg_store.inventory_label(e).casefold()
                hit = (e.dn or "").casefold() == v or label == v
            elif s.kind == "aci_bd":
                hit = bool(bd) and f"{t}/{bd.casefold()}" == v
            elif s.kind == "code":
                hit = v in tokens(e.aci_tenant, vrf, e.ap)
            else:
                hit = False
            if hit:
                out.add(e.id)
        return out

    # -- Firewall ----------------------------------------------------------------------

    def _fw_hit(self, s: Selector, sid: int, vdom: str | None, names: Iterable[str]) -> bool:
        if s.source_system_id is not None and s.source_system_id != sid:
            return False
        v = s.value.strip().casefold()
        if s.kind == "fw_vdom":
            return (vdom or "root").casefold() == v
        return v in {n.casefold() for n in names if n}

    def interface_nets(self, s: Selector) -> set[Net]:
        out = set()
        for sid, name, vdom, zone, ips in self.interfaces:
            names = [zone] if s.kind == "fw_zone" else [name]
            if self._fw_hit(s, sid, vdom, names):
                for ip in ips or []:
                    try:
                        out.add(ipaddress.ip_interface(ip).network)
                    except ValueError:
                        continue
        return out

    def fw_rules(self, s: Selector) -> set[int]:
        out = set()
        rows = self.session.execute(
            select(
                Rule.id,
                Rule.source_system_id,
                Rule.vdom,
                Rule.src_zone_id,
                Rule.dst_zone_id,
                Rule.raw,
            ).where(
                Rule.tenant_id == self.tenant_id, Rule.domain == RuleDomain.FIREWALL
            )  # fmt: skip
        )
        for rid, sid, vdom, src_z, dst_z, raw in rows:
            if s.kind == "fw_zone":
                names = [self.zones.get(src_z), self.zones.get(dst_z)]
            elif s.kind == "fw_interface":
                raw = raw if isinstance(raw, dict) else {}
                names = [
                    str(i.get("name"))
                    for key in ("srcintf", "dstintf")
                    for i in raw.get(key) or []
                    if isinstance(i, dict)
                ]
            else:
                names = []
            if self._fw_hit(s, sid, vdom, [n for n in names if n]):
                out.add(rid)
        return out


_IPAM_NET_KINDS = ("prefix", "range", "ip_address", "host", "fixed_address", "a_record")


def _record_nets(kind: str, value: str | None) -> set[Net]:
    """Netz eines IPAM-Eintrags: Praefix, Bereich (zusammengefasst) bzw. Adresse als /32."""
    text = (value or "").strip()
    try:
        if kind == "prefix":
            return {ipaddress.ip_network(text, strict=False)}
        if kind == "range" and "-" in text:
            a, _, b = text.partition("-")
            lo, hi = ipaddress.ip_address(a.strip()), ipaddress.ip_address(b.strip())
            return set(ipaddress.summarize_address_range(lo, hi))
        ip = ipaddress.ip_interface(text).ip
        return {ipaddress.ip_network(ip)}
    except (ValueError, TypeError):
        return set()


def _cut(nets: dict[Net, str], removed: list[Net]) -> dict[Net, str]:
    """Ausgeschlossene Praefixe aus den Netzen herausschneiden."""
    out = dict(nets)
    for x in removed:
        for net in list(out):
            if net.version != x.version or not net.overlaps(x):
                continue
            why = out.pop(net)
            if not net.subnet_of(x):
                for rest in net.address_exclude(x):
                    out[rest] = why
    return out


class _Ranges:
    """Nicht ueberlappende Netze je Familie fuer schnelles „liegt ganz darin“."""

    def __init__(self, nets: Iterable[Net]):
        self.nets: dict[int, list[Net]] = {}
        self.starts: dict[int, list[int]] = {}
        for version in (4, 6):
            merged = list(ipaddress.collapse_addresses(n for n in nets if n.version == version))
            self.nets[version] = merged
            self.starts[version] = [int(n.network_address) for n in merged]

    def covers(self, family: int, lo: int, hi: int) -> bool:
        starts, nets = self.starts.get(family, []), self.nets.get(family, [])
        i = bisect.bisect_right(starts, lo) - 1
        return i >= 0 and hi <= int(nets[i].broadcast_address)

    def items(self) -> Iterable[Net]:
        for merged in self.nets.values():
            yield from merged


ACI_KINDS = ("aci_tenant", "aci_vrf", "aci_ap", "aci_epg", "aci_bd", "code")
FW_KINDS = ("fw_vdom", "fw_zone", "fw_interface")


def nets_of(data: _Data, selectors: list[Selector]) -> tuple[dict[int, str], dict[Net, str]]:
    """EPGs und Netze der Selektoren, je mit Grund: Netze von Hand, aus dem IPAM, an den
    Interfaces der Firewall-Selektoren, die Subnetze der EPGs bzw. Bridge Domains und - nur
    fuer logische Zonen (0.51.0) - hinter einer Anbindung (`exit`); ausgeschlossene
    herausgeschnitten. Auch fuer die logischen Zonen (`store/zones.py`)."""
    include = [s for s in selectors if not s.exclude]
    exclude = [s for s in selectors if s.exclude]

    # EPGs: eingeschlossene ohne ausgeschlossene
    epgs: dict[int, str] = {}
    dropped = set().union(*(data.epg_matches(s) for s in exclude if s.kind in ACI_KINDS))
    for s in include:
        if s.kind in ACI_KINDS:
            for eid in data.epg_matches(s) - dropped:
                epgs.setdefault(eid, _label(s))

    nets: dict[Net, str] = {}
    for s in include:
        for net in data.selector_nets(s):
            nets.setdefault(net, _label(s))
    by_id = {e.id: e for e in data.epgs}
    for eid in epgs:
        for net in data.epg_nets.get(eid, []):
            nets.setdefault(net, f"EPG {epg_store.inventory_label(by_id[eid])}")
    removed = [n for s in exclude for n in data.selector_nets(s)]
    return epgs, _cut(nets, removed)


def owned_zones(session: Session, tenant_id: int,
                customer_id: int | None) -> list[tuple[str, list[Selector]]]:  # fmt: skip
    """Logische Zonen des Kunden (0.53.0, ADR 0018 Schritt 4) mit ihren Selektoren."""
    if customer_id is None:
        return []
    zones = session.scalars(
        select(LogicalZone)
        .where(LogicalZone.tenant_id == tenant_id, LogicalZone.customer_id == customer_id)
        .order_by(LogicalZone.position, LogicalZone.name)
    )
    return [
        (z.name, [Selector(s.kind, s.value, s.source_system_id, s.exclude) for s in z.selectors])
        for z in zones
    ]


def evaluate(session: Session, tenant_id: int, selectors: list[Selector],
             data: _Data | None = None,
             zones: list[tuple[str, list[Selector]]] = ()) -> Scope:  # fmt: skip
    """Was die Selektoren erfassen (siehe Modul) - ohne zu speichern (Vorschau). Seit 0.53.0
    zaehlen die Netze und EPGs der Zonen des Kunden dazu (`zones`, Grund „Zone …“); die
    ausschliessenden Selektoren des Kunden gelten auch fuer sie."""
    data = data or _Data(session, tenant_id)
    scope = Scope()
    include = [s for s in selectors if not s.exclude]
    exclude = [s for s in selectors if s.exclude]
    scope.epg, nets = nets_of(data, selectors)
    if zones:
        dropped = set().union(*(data.epg_matches(s) for s in exclude if s.kind in ACI_KINDS))
        for name, zone_selectors in zones:
            z_epgs, z_nets = nets_of(data, zone_selectors)
            for eid in z_epgs:
                if eid not in dropped:
                    scope.epg.setdefault(eid, f"Zone {name}")
            for net in _cut(z_nets, [n for s in exclude for n in data.selector_nets(s)]):
                nets.setdefault(net, f"Zone {name}")
    by_id = {e.id: e for e in data.epgs}
    scope.net = {str(n): why for n, why in sorted(nets.items(), key=lambda x: _net_key(x[0]))}
    ranges = _Ranges(nets)
    codes_in = {s.value.strip().casefold() for s in include if s.kind == "code"}
    codes_out = {s.value.strip().casefold() for s in exclude if s.kind == "code"}

    # Werte-Objekte: ganz in einem Netz des Kunden bzw. mit dem Kuerzel im Namen
    for oid, name, value in session.execute(
        select(NetworkObject.id, NetworkObject.name, NetworkObject.value).where(
            NetworkObject.tenant_id == tenant_id
        )
    ):
        names = tokens(name)
        if names & codes_out:
            continue
        b = _bounds(value)
        if b is not None and ranges.covers(*b):
            scope.object[oid] = "Netz des Kunden"
        elif names & codes_in:
            scope.object[oid] = f"Kürzel {sorted(names & codes_in)[0]}"

    # Regeln: Contracts ueber EPGs, Firewall-Regeln ueber VDOM/Zone/Interface bzw. Netze
    if scope.epg:
        for rid, eid in session.execute(
            select(RuleEndpoint.rule_id, RuleEndpoint.epg_id)
            .join(Rule, Rule.id == RuleEndpoint.rule_id)
            .where(Rule.tenant_id == tenant_id, RuleEndpoint.epg_id.in_(list(scope.epg)))
        ):
            scope.rule.setdefault(rid, f"EPG {epg_store.inventory_label(by_id[eid])}")
    fw_out = set().union(*(data.fw_rules(s) for s in exclude if s.kind in FW_KINDS))
    for s in include:
        if s.kind in FW_KINDS:
            for rid in data.fw_rules(s) - fw_out:
                scope.rule.setdefault(rid, _label(s))
    for net in ranges.items():
        for (rid,) in session.execute(
            select(RuleIpRange.rule_id)
            .where(
                RuleIpRange.tenant_id == tenant_id,
                RuleIpRange.family == net.version,
                RuleIpRange.is_any.is_(False),
                RuleIpRange.lo <= ip_hex(net.broadcast_address),
                RuleIpRange.hi >= ip_hex(net.network_address),
            )
            .distinct()
        ):
            if rid not in fw_out:
                scope.rule.setdefault(rid, f"Netz {net}")

    # VMs: Portgruppe einer EPG, IP in einem Netz, Kuerzel im Namen
    if scope.epg:
        groups = virtual.PortgroupIndex.load(session, tenant_id)  # gleichnamige getrennt
        for vid, sid, nic in session.execute(
            select(Vm.id, Vm.source_system_id, VmNic)
            .join(VmNic, VmNic.vm_id == Vm.id)
            .where(Vm.tenant_id == tenant_id)
        ):
            pg = groups.of(sid, nic)
            if pg is not None and pg.epg_id in scope.epg:
                scope.vm.setdefault(vid, f"Portgruppe {nic.portgroup}")
    for vid, ip in session.execute(select(VmIp.vm_id, VmIp.ip).where(VmIp.tenant_id == tenant_id)):
        b = _bounds(ip)
        if b is not None and ranges.covers(*b):
            scope.vm.setdefault(vid, "IP im Netz des Kunden")
    for vid, name in session.execute(select(Vm.id, Vm.name).where(Vm.tenant_id == tenant_id)):
        names = tokens(name)
        if names & codes_out:
            scope.vm.pop(vid, None)
        elif names & codes_in:
            scope.vm.setdefault(vid, f"Kürzel {sorted(names & codes_in)[0]}")

    # NAT (0.31.0): interne bzw. externe IP im Netz des Kunden
    for nid, ext, mapped in session.execute(
        select(NatRule.id, NatRule.ext_ip, NatRule.mapped_ip).where(NatRule.tenant_id == tenant_id)
    ):
        for value, why in ((mapped, "interne IP im Netz"), (ext, "externe IP im Netz")):
            b = _bounds((value or "").split(",")[0])
            if b is not None and ranges.covers(*b):
                scope.nat.setdefault(nid, why)
                break
    _findings(session, tenant_id, scope, ranges, codes_in, data)
    return scope


_SUFFIX = re.compile(r" \((IPv6|VDOM [^)]*)\)")


def _findings(session: Session, tenant_id: int, scope: Scope, ranges: "_Ranges",
              codes: set[str], data: _Data) -> None:  # fmt: skip
    """Findings ueber ihr Subjekt (0.31.0): Regel, Werte- bzw. Firewall-Objekt, VM,
    Portgruppe (Tenant der EPG im Namen) bzw. Subnetz des Kunden."""
    objects = set(
        session.scalars(select(NetworkObject.name).where(NetworkObject.id.in_(list(scope.object))))
    ) if scope.object else set()  # fmt: skip
    vms = (
        set(session.scalars(select(Vm.name).where(Vm.id.in_(list(scope.vm)))))
        if scope.vm
        else set()
    )
    tenants = {(e.aci_tenant or "").casefold() for e in data.epgs if e.id in scope.epg}
    for fid, kind, subject, detail in session.execute(
        select(Finding.id, Finding.subject_kind, Finding.subject_id, Finding.detail).where(
            Finding.tenant_id == tenant_id
        )
    ):
        detail = detail if isinstance(detail, dict) else {}
        why = None
        if kind is SubjectKind.RULE:
            rid = detail.get("rule_id") or (int(subject) if subject.isdigit() else None)
            why = "Regel des Kunden" if rid in scope.rule else None
        elif kind in (SubjectKind.NETWORK_OBJECT, SubjectKind.DEVICE_OBJECT):
            name = _SUFFIX.sub("", subject)
            if name in objects:
                why = "Objekt des Kunden"
            elif tokens(name) & codes:
                why = "Kürzel im Namen"
        elif kind is SubjectKind.VM:
            why = "VM des Kunden" if subject in vms else None
        elif kind is SubjectKind.PORTGROUP:
            parts = virtual.epg_from_name(subject)
            if parts is not None and parts[0].casefold() in tenants:
                why = "Portgruppe im Tenant des Kunden"
        elif kind is SubjectKind.SUBNET:
            b = _bounds(subject)
            why = "Subnetz des Kunden" if b is not None and ranges.covers(*b) else None
        if why:
            scope.finding[fid] = why


def _net_key(n: Net) -> tuple:
    return (n.version, int(n.network_address), n.prefixlen)


def selectors_of(c: Customer) -> list[Selector]:
    return [Selector(s.kind, s.value, s.source_system_id, s.exclude) for s in c.selectors]


def derive(session: Session, tenant_id: int, customer_id: int | None = None) -> int:
    """Zugehoerigkeit der Kunden des Mandanten (bzw. eines Kunden) neu ableiten; Anzahl der
    Eintraege."""
    stmt = select(Customer).where(Customer.tenant_id == tenant_id)
    if customer_id is not None:
        stmt = stmt.where(Customer.id == customer_id)
    customers = list(session.scalars(stmt))
    if not customers:
        return 0
    data = _Data(session, tenant_id)
    total = 0
    now = datetime.now(UTC)
    for c in customers:
        session.execute(delete(CustomerMember).where(CustomerMember.customer_id == c.id))
        scope = evaluate(session, tenant_id, selectors_of(c), data,
                         owned_zones(session, tenant_id, c.id))  # fmt: skip
        base = {"tenant_id": tenant_id, "customer_id": c.id}
        rows = [
            {**base, "kind": kind, "ref_id": ref, "value": None, "via": why[:200]}
            for kind in ("epg", "object", "rule", "vm", "nat", "finding")
            for ref, why in getattr(scope, kind).items()
        ] + [
            {**base, "kind": "net", "ref_id": None, "value": net, "via": why[:200]}
            for net, why in scope.net.items()
        ]
        if rows:
            session.execute(CustomerMember.__table__.insert(), rows)
        c.derived_at = now
        total += len(rows)
    session.flush()
    return total


def counts(session: Session, customer_ids: list[int]) -> dict[int, dict[str, int]]:
    """Gespeicherte Zugehoerigkeit je Kunde und Art."""
    out: dict[int, dict[str, int]] = {cid: dict.fromkeys(KINDS, 0) for cid in customer_ids}
    if customer_ids:
        for cid, kind, n in session.execute(
            select(CustomerMember.customer_id, CustomerMember.kind, func.count())
            .where(CustomerMember.customer_id.in_(customer_ids))
            .group_by(CustomerMember.customer_id, CustomerMember.kind)
        ):
            out[cid][kind] = n
    return out


def samples(session: Session, tenant_id: int, scope: Scope, n: int = SAMPLES) -> dict[str, list]:
    """Erste Beispiele je Art mit Namen (Vorschau)."""
    out: dict[str, list] = {"net": list(scope.net)[:n]}
    pick = {k: list(getattr(scope, k))[:n] for k in ("epg", "object", "rule", "vm")}  # Namen
    if pick["epg"]:
        out["epg"] = [
            epg_store.inventory_label(e)
            for e in session.scalars(select(Epg).where(Epg.id.in_(pick["epg"])).order_by(Epg.id))
        ]
    if pick["object"]:
        out["object"] = list(
            session.scalars(
                select(NetworkObject.name)
                .where(NetworkObject.id.in_(pick["object"]))
                .order_by(NetworkObject.id)
            )
        )
    if pick["rule"]:
        out["rule"] = [
            name or f"#{ext}"
            for name, ext in session.execute(
                select(Rule.name, Rule.external_id)
                .where(Rule.id.in_(pick["rule"]))
                .order_by(Rule.id)
            )
        ]
    if pick["vm"]:
        out["vm"] = list(
            session.scalars(select(Vm.name).where(Vm.id.in_(pick["vm"])).order_by(Vm.id))
        )
    for kind, model, col in (("nat", NatRule, NatRule.name), ("finding", Finding,
                                                              Finding.subject_id)):  # fmt: skip
        ids = list(getattr(scope, kind))[:n]
        if ids:
            out[kind] = list(
                session.scalars(select(col).where(model.id.in_(ids)).order_by(model.id))
            )
    return {k: out.get(k, []) for k in KINDS}


def unassigned(session: Session, tenant_id: int, n: int = SAMPLES) -> dict[str, dict]:
    """Was keinem Kunden (auch keinem geteilten) gehoert: EPGs, Regeln, VMs - Anzahl und
    die ersten Beispiele."""
    member = select(CustomerMember.ref_id).where(CustomerMember.tenant_id == tenant_id)
    out: dict[str, dict] = {}
    for kind, model, name_col, extra in (
        ("epg", Epg, Epg.name, [Epg.kind == "epg"]),
        ("rule", Rule, Rule.name, []),
        ("vm", Vm, Vm.name, []),
    ):
        cond = [
            model.tenant_id == tenant_id,
            *extra,
            model.id.not_in(member.where(CustomerMember.kind == kind)),
        ]
        total = session.scalar(select(func.count(model.id)).where(*cond)) or 0
        if kind == "epg":
            first = [
                epg_store.inventory_label(e)
                for e in session.scalars(select(Epg).where(*cond).order_by(Epg.id).limit(n))
            ]
        else:
            first = [x or "?" for x in session.scalars(
                select(name_col).where(*cond).order_by(model.id).limit(n))]  # fmt: skip
        out[kind] = {"count": total, "samples": first}
    return out


def code_of(aci_tenant: str) -> str | None:
    """Kuerzel aus der Namenskonvention: `TE_<kuerzel>` -> kuerzel."""
    _, sep, rest = aci_tenant.partition("_")
    return rest or None if sep else None


def suggestions(session: Session, tenant_id: int) -> list[dict]:
    """Je ACI-Tenant ohne Kunden ein Vorschlag (Kuerzel aus `TE_<kuerzel>`); `common` als
    geteilter Teil, solange es keinen geteilten Kunden gibt."""
    customers = list(session.scalars(select(Customer).where(Customer.tenant_id == tenant_id)))
    covered = {
        s.value.casefold() for c in customers for s in c.selectors
        if s.kind == "aci_tenant" and not s.exclude
    }  # fmt: skip
    names = {c.name.casefold() for c in customers}
    found: dict[str, int] = {}
    for t, n in session.execute(
        select(Epg.aci_tenant, func.count(Epg.id))
        .where(Epg.tenant_id == tenant_id, Epg.kind == "epg", Epg.aci_tenant.is_not(None))
        .group_by(Epg.aci_tenant)
        .order_by(Epg.aci_tenant)
    ):
        found[t] = n
    out = []
    for t, n in found.items():
        if t.casefold() in covered or t.casefold() in names:
            continue
        shared = t.casefold() == "common"
        if t.casefold() in _FABRIC_TENANTS and not shared:
            continue
        if shared and any(c.shared for c in customers):
            continue
        out.append({
            "name": "Geteilt" if shared else t,
            "code": None if shared else code_of(t),
            "shared": shared,
            "epgs": n,
            "selectors": [{"kind": "aci_tenant", "value": t, "source_system_id": None,
                           "exclude": False}],
        })  # fmt: skip
    return out
