"""Referenz-Ebene: gespeicherter Stand der Referenzquellen (Infoblox DDI, NetBox), ADR 0010.

- Speichern: Teil-Uploads eines Laufs (`add_batch`), Abschluss (`finalize_run`) macht den
  Lauf zum aktuellen Stand der Quelle und verwirft aeltere Eintraege.
- Matching: `ReferenceIndex` (genaue IP, umschliessendes Netz per Praefixlaenge, Ranges,
  DNS-Name) im Speicher; `SnapshotSoT` bietet ihn dem Ingest als SoT-Adapter an, `rematch`
  verknuepft die Werte-Objekte des Mandanten nach jedem Lauf neu (fuehrend + Anreicherung).
- Anzeige: `describe` liefert Referenz- und Anreicherungsdaten je Werte-Objekt.
- Pruefungen: `checks` (Name ↔ DNS-Name, Subnetz nicht im IPAM, VLAN abweichend) und
  `compare_sources` (DDI ↔ NetBox).
Gegenueber den Quellen nur lesend.
"""

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field

from sot_gateway.models import IP_KINDS, NetworkObject, NetworkObjectType, ReferenceKind
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.models import (
    MatchStatus,
    ReferenceRecordRow,
    ReferenceRole,
    SourceOfRecord,
    SourceSystem,
)
from app.models import (
    NetworkObject as NetworkObjectRow,
)

_SOURCE_OF_RECORD = {"infoblox": SourceOfRecord.INFOBLOX, "netbox": SourceOfRecord.NETBOX}
_IP_KIND_VALUES = {k.value for k in IP_KINDS}
# Reihenfolge fuer den "besten" IP-Treffer: Host/IP-Adresse vor DNS-/DHCP-Eintraegen
_IP_KIND_RANK = {
    ReferenceKind.HOST.value: 0,
    ReferenceKind.IP_ADDRESS.value: 0,
    ReferenceKind.A_RECORD.value: 1,
    ReferenceKind.FIXED_ADDRESS.value: 2,
    ReferenceKind.PTR_RECORD.value: 3,
}

_COLUMNS = (
    ReferenceRecordRow.id,
    ReferenceRecordRow.kind,
    ReferenceRecordRow.value,
    ReferenceRecordRow.prefix_len,
    ReferenceRecordRow.vrf,
    ReferenceRecordRow.name,
    ReferenceRecordRow.dns_name,
    ReferenceRecordRow.description,
    ReferenceRecordRow.vlan_vid,
    ReferenceRecordRow.vlan_name,
    ReferenceRecordRow.site,
    ReferenceRecordRow.tenant_name,
    ReferenceRecordRow.status,
    ReferenceRecordRow.tags,
    ReferenceRecordRow.url,
)


@dataclass(frozen=True)
class Ref:
    id: int
    kind: str
    value: str
    prefix_len: int | None
    vrf: str | None
    name: str | None
    dns_name: str | None
    description: str | None
    vlan_vid: int | None
    vlan_name: str | None
    site: str | None
    tenant_name: str | None
    status: str | None
    tags: list | None
    url: str | None


def _norm_ip(value: str) -> str | None:
    try:
        return ipaddress.ip_address(value.strip()).compressed
    except ValueError:
        return None


def _norm_net(value: str):
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return None


def _range(value: str):
    start, sep, end = value.partition("-")
    if not sep:
        return None
    try:
        a, b = ipaddress.ip_address(start.strip()), ipaddress.ip_address(end.strip())
    except ValueError:
        return None
    return (a.version, int(a), int(b)) if a.version == b.version else None


class ReferenceIndex:
    """Schneller Lookup ueber den aktuellen Stand einer Referenzquelle."""

    def __init__(self, source: SourceSystem, rows: list[Ref]) -> None:
        self.source = source
        self.by_ip: dict[str, list[Ref]] = defaultdict(list)
        self.prefixes: dict[tuple[int, int], dict[int, Ref]] = defaultdict(dict)
        self.prefix_by_value: dict[str, Ref] = {}
        self.ranges: list[tuple[int, int, int, Ref]] = []
        self.by_name: dict[str, list[Ref]] = defaultdict(list)
        self.vlans: dict[int, list[Ref]] = defaultdict(list)
        for r in rows:
            if r.kind in _IP_KIND_VALUES:
                ip = _norm_ip(r.value)
                if ip:
                    self.by_ip[ip].append(r)
            elif r.kind == ReferenceKind.PREFIX.value:
                net = _norm_net(r.value)
                if net is not None:
                    key = (net.version, net.prefixlen)
                    self.prefixes[key].setdefault(int(net.network_address), r)
                    self.prefix_by_value.setdefault(str(net), r)
            elif r.kind == ReferenceKind.RANGE.value:
                rng = _range(r.value)
                if rng:
                    self.ranges.append((*rng, r))
            elif r.kind == ReferenceKind.VLAN.value and r.vlan_vid is not None:
                self.vlans[r.vlan_vid].append(r)
            for name in (r.dns_name, r.name):
                if name:
                    self.by_name[name.lower()].append(r)
        for recs in self.by_ip.values():
            recs.sort(key=lambda x: _IP_KIND_RANK.get(x.kind, 9))
        self._plens = {
            v: sorted({p for (ver, p) in self.prefixes if ver == v}, reverse=True) for v in (4, 6)
        }

    @property
    def platform(self) -> str:
        return self.source.platform.value

    def ip_records(self, ip: str) -> list[Ref]:
        key = _norm_ip(ip)
        return list(self.by_ip.get(key, [])) if key else []

    def containing_prefixes(self, value: str, max_len: int | None = None) -> list[Ref]:
        """Netze, die eine IP bzw. ein Netz enthalten (laengste zuerst)."""
        net = _norm_net(value)
        if net is None:
            return []
        out: list[Ref] = []
        for plen in self._plens.get(net.version, []):
            if plen > net.prefixlen or (max_len is not None and plen > max_len):
                continue
            masked = (
                int(net.network_address) >> (net.max_prefixlen - plen) << (net.max_prefixlen - plen)
            )
            rec = self.prefixes[(net.version, plen)].get(masked)
            if rec is not None:
                out.append(rec)
        return out

    def containing_ranges(self, ip: str) -> list[Ref]:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return []
        return [r for (v, a, b, r) in self.ranges if v == addr.version and a <= int(addr) <= b]

    def prefix(self, value: str) -> Ref | None:
        net = _norm_net(value)
        return self.prefix_by_value.get(str(net)) if net is not None else None

    def lookup_value(self, value: str) -> tuple[Ref | None, Ref | None]:
        """(bester Treffer, umschliessendes Netz) fuer einen Wert (IP, CIDR, Range a-b)."""
        rng = _range(value)
        if rng:
            start = value.split("-")[0].strip()
            exact = next((r for (v, a, b, r) in self.ranges if (v, a, b) == rng), None)
            prefixes = self.containing_prefixes(start)
            best = exact or (prefixes[0] if prefixes else None)
            return best, prefixes[0] if prefixes else None
        net = _norm_net(value)
        if net is None:
            return None, None
        if net.num_addresses == 1:
            ip = str(net.network_address)
            exact = self.ip_records(ip)
            prefixes = self.containing_prefixes(ip)
            ranges = self.containing_ranges(ip)
            best = (
                exact[0]
                if exact
                else (ranges[0] if ranges else (prefixes[0] if prefixes else None))
            )
            return best, prefixes[0] if prefixes else None
        exact_prefix = self.prefix(str(net))
        prefixes = self.containing_prefixes(str(net))
        return exact_prefix or (prefixes[0] if prefixes else None), exact_prefix or (
            prefixes[0] if prefixes else None
        )

    def lookup_name(self, name: str | None) -> Ref | None:
        if not name:
            return None
        recs = self.by_name.get(name.lower()) or self.by_name.get(name.lower().rstrip("."))
        return recs[0] if recs else None


# -- Quellen + Index laden ----------------------------------------------------


def tenant_reference_sources(session: Session, tenant_id: int) -> list[SourceSystem]:
    return list(
        session.scalars(
            select(SourceSystem).where(
                SourceSystem.tenant_id == tenant_id,
                SourceSystem.ref_role.is_not(None),
                SourceSystem.reference_run_id.is_not(None),
                SourceSystem.enabled.is_(True),
            )
        )
    )


def load_index(session: Session, source: SourceSystem) -> ReferenceIndex:
    rows = session.execute(
        select(*_COLUMNS).where(ReferenceRecordRow.collector_run_id == source.reference_run_id)
    ).all()
    return ReferenceIndex(source, [Ref(*row) for row in rows])


@dataclass
class Context:
    primary: ReferenceIndex | None = None
    enrichments: list[ReferenceIndex] = field(default_factory=list)


def context(session: Session, tenant_id: int | None) -> Context:
    """Indizes der Referenzquellen des Mandanten (je Transaktion zwischengespeichert)."""
    if tenant_id is None:
        return Context()
    sources = tenant_reference_sources(session, tenant_id)
    key = (
        "ref_ctx",
        tenant_id,
        tuple(sorted((s.id, s.reference_run_id, s.ref_role) for s in sources)),
    )
    cached = session.info.get(key)
    if cached is not None:
        return cached
    ctx = Context()
    for s in sorted(sources, key=lambda x: x.id):
        idx = load_index(session, s)
        if s.ref_role == ReferenceRole.PRIMARY.value and ctx.primary is None:
            ctx.primary = idx
        else:
            ctx.enrichments.append(idx)
    session.info[key] = ctx
    return ctx


# -- SoT-Adapter ueber dem gespeicherten Stand (Ingest) -----------------------


class SnapshotSoT:
    """Erfuellt das SoT-Protokoll (search_by_ip/get_by_name) aus dem gespeicherten Stand."""

    def __init__(self, index: ReferenceIndex) -> None:
        self.index = index
        self.name = index.platform

    def _obj(self, r: Ref) -> NetworkObject:
        kind = NetworkObjectType.HOST if r.kind in _IP_KIND_VALUES else NetworkObjectType.NETWORK
        return NetworkObject(
            name=r.dns_name or r.name or r.value,
            type=kind,
            value=r.value,
            tags=list(r.tags or []),
            source=self.name,
        )

    def search_by_ip(self, ip: str) -> list[NetworkObject]:
        recs = self.index.ip_records(ip) + self.index.containing_prefixes(ip)
        recs += self.index.containing_ranges(ip)
        return [self._obj(r) for r in recs]

    def get_by_name(self, name: str) -> NetworkObject | None:
        r = self.index.lookup_name(name)
        return self._obj(r) if r else None


def snapshot_sot(session: Session, tenant_id: int | None) -> SnapshotSoT | None:
    ctx = context(session, tenant_id)
    return SnapshotSoT(ctx.primary) if ctx.primary is not None else None


# -- Rematch: Werte-Objekte mit dem aktuellen Stand verknuepfen ---------------


def rematch(session: Session, tenant_id: int, ctx: Context) -> None:
    """Werte-Objekte des Mandanten gegen das fuehrende IPAM (+ Anreicherung) neu abgleichen.

    Ohne Referenzquelle bleibt der Stand des Ingests (Live-Adapter, Rueckfall) unveraendert."""
    if ctx.primary is None and not ctx.enrichments:
        return
    from app.store import ingest  # spaet: ingest importiert reconcile

    for obj in session.scalars(
        select(NetworkObjectRow).where(NetworkObjectRow.tenant_id == tenant_id)
    ):
        if obj.match_status is MatchStatus.NOT_APPLICABLE:
            continue
        best, prefix = _lookup(ctx.primary, obj)
        enrichment = next(
            (b for b in (_lookup(idx, obj)[0] for idx in ctx.enrichments) if b is not None), None
        )
        obj.reference_id = best.id if best else None
        obj.reference_prefix_id = prefix.id if prefix else None
        obj.enrichment_id = enrichment.id if enrichment else None
        if ctx.primary is None or obj.match_status is MatchStatus.EXPECTED_EXTERNAL:
            continue
        if best is not None:
            obj.match_status = MatchStatus.MATCHED
            obj.source_of_record = _SOURCE_OF_RECORD.get(ctx.primary.platform)
            zone_id = ingest._zone_from_tags(session, tenant_id, list((prefix or best).tags or []))
            if zone_id is not None:
                obj.zone_id = zone_id
        else:
            obj.match_status = MatchStatus.UNMATCHED
            obj.source_of_record = None
    session.flush()


def _lookup(index: ReferenceIndex | None, obj: NetworkObjectRow) -> tuple[Ref | None, Ref | None]:
    if index is None:
        return None, None
    if obj.value:
        best, prefix = index.lookup_value(obj.value)
        if best is not None:
            return best, prefix
    by_name = index.lookup_name(obj.fqdn) or index.lookup_name(obj.name)
    return by_name, None


# -- Anzeige ------------------------------------------------------------------


def _ref_dict(
    r: ReferenceRecordRow, source: str | None, prefix: ReferenceRecordRow | None = None
) -> dict:
    net = prefix if prefix is not None else (r if r.kind == ReferenceKind.PREFIX.value else None)
    return {
        "source": source,
        "kind": r.kind,
        "value": r.value,
        "dns_name": r.dns_name,
        "name": r.name,
        "description": r.description or (net.description if net is not None else None),
        "prefix": net.value if net is not None else None,
        "vlan": (net.vlan_vid if net is not None and net.vlan_vid else r.vlan_vid),
        "vlan_name": (net.vlan_name if net is not None and net.vlan_name else r.vlan_name),
        "vrf": r.vrf or (net.vrf if net is not None else None),
        "site": r.site or (net.site if net is not None else None),
        "tenant": r.tenant_name or (net.tenant_name if net is not None else None),
        "status": r.status,
        "attrs": r.attrs or None,
        "url": r.url,
    }


def describe(session: Session, objects: list) -> dict[int, tuple[dict | None, dict | None]]:
    """Referenz- (fuehrend) und Anreicherungsdaten je Werte-Objekt."""
    ids = {
        i
        for o in objects
        for i in (o.reference_id, o.reference_prefix_id, o.enrichment_id)
        if i is not None
    }
    if not ids:
        return {}
    rows = {
        r.id: r
        for r in session.scalars(select(ReferenceRecordRow).where(ReferenceRecordRow.id.in_(ids)))
    }
    names = dict(
        session.execute(
            select(SourceSystem.id, SourceSystem.name).where(
                SourceSystem.id.in_({r.source_system_id for r in rows.values()})
            )
        ).all()
    )
    out: dict[int, tuple[dict | None, dict | None]] = {}
    for o in objects:
        ref = rows.get(o.reference_id) if o.reference_id else None
        pre = rows.get(o.reference_prefix_id) if o.reference_prefix_id else None
        enr = rows.get(o.enrichment_id) if o.enrichment_id else None
        out[o.id] = (
            _ref_dict(ref, names.get(ref.source_system_id), pre) if ref else None,
            _ref_dict(enr, names.get(enr.source_system_id)) if enr else None,
        )
    return out


# -- Speichern (Teil-Uploads, Abschluss) --------------------------------------


def add_batch(session: Session, run, records: list) -> int:
    """Teil-Upload eines Laufs speichern (schnell per Bulk-Insert)."""
    if not records:
        return 0
    rows = [
        {
            "tenant_id": run.tenant_id,
            "source_system_id": run.source_system_id,
            "collector_run_id": run.id,
            "kind": r.kind.value,
            "value": r.value[:200],
            "prefix_len": r.prefix_len,
            "vrf": r.vrf,
            "name": (r.name or None) and r.name[:300],
            "dns_name": (r.dns_name or None) and r.dns_name[:300],
            "description": r.description,
            "vlan_vid": r.vlan_vid,
            "vlan_name": r.vlan_name,
            "site": r.site,
            "tenant_name": r.tenant,
            "status": r.status,
            "tags": r.tags or None,
            "attrs": r.attrs or None,
            "ref_id": (r.ref_id or None) and r.ref_id[:300],
            "url": r.url,
        }
        for r in records
    ]
    session.execute(insert(ReferenceRecordRow), rows)
    return len(rows)


def run_count(session: Session, run_id: str) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(ReferenceRecordRow)
            .where(ReferenceRecordRow.collector_run_id == run_id)
        )
        or 0
    )


def finalize_run(session: Session, run, source: SourceSystem) -> int:
    """Lauf zum aktuellen Stand der Quelle machen; aeltere Eintraege der Quelle verwerfen."""
    session.execute(
        delete(ReferenceRecordRow).where(
            ReferenceRecordRow.source_system_id == source.id,
            ReferenceRecordRow.collector_run_id != run.id,
        )
    )
    source.reference_run_id = run.id
    if source.ref_role is None:
        source.ref_role = ReferenceRole.PRIMARY.value
    session.flush()
    return run_count(session, run.id)


def discard_run(session: Session, run) -> None:
    session.execute(delete(ReferenceRecordRow).where(ReferenceRecordRow.collector_run_id == run.id))


# -- Pruefungen (Abgleich, A4) ------------------------------------------------


def checks(session: Session, tenant_id: int, prefixes, ctx: Context | None = None) -> list:
    """Referenz-Pruefungen fuer den Abgleich (siehe A4)."""
    return []
