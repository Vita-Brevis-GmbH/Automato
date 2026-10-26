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
        self.by_short: dict[str, list[Ref]] = defaultdict(list)  # erstes Label (web01)
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
                    if r.kind in _IP_KIND_VALUES:
                        self.by_short[_short(name)].append(r)
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

    def lookup_name(self, name: str | None, prefixes=None) -> Ref | None:
        """Eintrag zu einem Namen. Im IPAM steht der Server ohne Konventions-Praefix und oft
        als FQDN, auf Firewall/ACI mit Praefix (H_web01): exakt, dann ohne Praefix, dann ueber
        das erste Label - dieses nur, wenn es eindeutig auf eine IP zeigt."""
        if not name or _norm_net(name) is not None:  # IP/Netz: kein Namens-Lookup
            return None
        from app import naming  # spaet: naming ist frei von DB-Abhaengigkeiten

        candidates = [name, naming.server_name(name, prefixes)]
        for n in dict.fromkeys(c.strip().lower().rstrip(".") for c in candidates if c):
            recs = self.by_name.get(n) or self.by_name.get(f"{n}.")
            if recs:
                return recs[0]
        short = _short(candidates[1])
        recs = self.by_short.get(short, []) if short else []
        if recs and len({r.value for r in recs}) == 1:
            return recs[0]
        return None


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


# -- Anzeige einer einzelnen IP (NAT, VIP) ------------------------------------


def ip_info(ctx: Context, value: str | None) -> list[dict]:
    """IPAM-Sicht auf eine IP bzw. einen Bereich `a-b` (Startadresse): DNS-Namen aller
    Eintraege und das umschliessende Netz (Beschreibung, VLAN, Site) - je Referenzquelle,
    fuehrend zuerst. Leer, wenn keine Quelle etwas weiss."""
    if not value:
        return []
    ip = value.split("-", 1)[0].strip()
    out: list[dict] = []
    for role, idx in [("primary", ctx.primary), *(("enrichment", e) for e in ctx.enrichments)]:
        if idx is None:
            continue
        records = idx.ip_records(ip)
        best, prefix = idx.lookup_value(value)
        names = sorted({r.dns_name or r.name for r in records if r.dns_name or r.name})
        if not names and prefix is None:
            continue
        out.append(
            {
                "source": idx.source.name,
                "role": role,
                "dns_names": names,
                "records": [
                    {"kind": r.kind, "dns_name": r.dns_name, "name": r.name} for r in records[:10]
                ],
                "prefix": (
                    {
                        "value": prefix.value,
                        "description": prefix.description or prefix.name,
                        "vlan": prefix.vlan_vid,
                        "site": prefix.site,
                        "vrf": prefix.vrf,
                    }
                    if prefix is not None
                    else None
                ),
                "url": best.url if best is not None else None,
            }
        )
    return out


def dns_names(ctx: Context, value: str | None) -> list[str]:
    """Alle DNS-Namen einer IP (auch a.b.c.d/32 bzw. Start eines Bereichs a-b) ueber die
    Referenzquellen - fuer Listen und Suche."""
    if not value:
        return []
    ip = value.split("-", 1)[0].strip()
    if "/" in ip:
        net = _norm_net(ip)
        if net is None or net.num_addresses != 1:
            return []
        ip = str(net.network_address)
    names: list[str] = []
    for idx in [ctx.primary, *ctx.enrichments]:
        if idx is None:
            continue
        for r in idx.ip_records(ip):
            for n in (r.dns_name, r.name):
                if n and n not in names:
                    names.append(n)
    return names


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


def rematch(session: Session, tenant_id: int, ctx: Context, prefixes=None) -> None:
    """Werte-Objekte des Mandanten gegen das fuehrende IPAM (+ Anreicherung) neu abgleichen.

    Ohne Referenzquelle bleibt der Stand des Ingests (Live-Adapter, Rueckfall) unveraendert.
    Objekte ohne Wert werden ueber den Namen gesucht - ohne Konventions-Praefix (`prefixes`
    aus dem Namensprofil), so wie der Server im IPAM steht."""
    if ctx.primary is None and not ctx.enrichments:
        return
    from app.store import ingest  # spaet: ingest importiert reconcile

    for obj in session.scalars(
        select(NetworkObjectRow).where(NetworkObjectRow.tenant_id == tenant_id)
    ):
        if obj.match_status is MatchStatus.NOT_APPLICABLE:
            continue
        best, prefix = _lookup(ctx.primary, obj, prefixes)
        enrichment = next(
            (b for b in (_lookup(idx, obj, prefixes)[0] for idx in ctx.enrichments) if b), None
        )
        obj.reference_id = best.id if best else None
        obj.reference_prefix_id = prefix.id if prefix else None
        obj.enrichment_id = enrichment.id if enrichment else None
        if ctx.primary is None or obj.match_status is MatchStatus.EXPECTED_EXTERNAL:
            continue
        if best is not None:
            obj.match_status = MatchStatus.MATCHED
            obj.source_of_record = _SOURCE_OF_RECORD.get(ctx.primary.platform)
            zone_id = ingest.zone_from_tags(session, tenant_id, list((prefix or best).tags or []))
            if zone_id is not None:
                obj.zone_id = zone_id
        else:
            obj.match_status = MatchStatus.UNMATCHED
            obj.source_of_record = None
    session.flush()


def _lookup(
    index: ReferenceIndex | None, obj: NetworkObjectRow, prefixes=None
) -> tuple[Ref | None, Ref | None]:
    if index is None:
        return None, None
    if obj.value:
        best, prefix = index.lookup_value(obj.value)
        if best is not None:
            return best, prefix
    by_name = index.lookup_name(obj.fqdn, prefixes) or index.lookup_name(obj.name, prefixes)
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


# -- Pruefungen (Abgleich) ---------------------------------------------------


def checks(session: Session, tenant_id: int, prefixes, ctx: Context | None = None) -> list:
    """Referenz-Pruefungen: Name ↔ DNS-Name, Subnetz nicht im IPAM, VLAN abweichend."""
    from app.store.reconcile import Issue  # spaet: reconcile importiert dieses Modul

    ctx = ctx or context(session, tenant_id)
    if ctx.primary is None:
        return []
    names = dict(
        session.execute(
            select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
        ).all()
    )
    out: list = []
    out += _name_mismatches(session, tenant_id, prefixes, ctx, names, Issue)
    out += _subnet_checks(session, tenant_id, ctx, names, Issue)
    return out


def _short(name: str | None) -> str:
    return (name or "").strip().rstrip(".").lower().split(".", 1)[0]


def _name_mismatches(session, tenant_id, prefixes, ctx: Context, names, Issue) -> list:
    """Host-Objekt (Konvention H_) mit IPAM-Eintrag, dessen DNS-Name nicht zum Namen passt."""
    from app import naming
    from app.models import DeviceObject, FindingType, ObjectCategory, ObjectClass, SubjectKind

    out = []
    rows = session.execute(
        select(DeviceObject, ReferenceRecordRow)
        .join(NetworkObjectRow, NetworkObjectRow.id == DeviceObject.network_object_id)
        .join(ReferenceRecordRow, ReferenceRecordRow.id == NetworkObjectRow.reference_id)
        .where(
            DeviceObject.tenant_id == tenant_id,
            DeviceObject.category == ObjectCategory.ADDRESS,
            ReferenceRecordRow.dns_name.is_not(None),
        )
    ).all()
    for obj, ref in rows:
        parsed = naming.parse_object_name(obj.name, prefixes)
        if parsed.object_class is not ObjectClass.HOST or not parsed.remainder:
            continue
        if _short(parsed.remainder) != _short(ref.dns_name):
            out.append(
                Issue(
                    FindingType.NAME_MISMATCH,
                    SubjectKind.DEVICE_OBJECT,
                    obj.name,
                    obj.source_system_id,
                    detail={
                        "source_system_id": obj.source_system_id,
                        "source": names.get(obj.source_system_id),
                        "name": obj.name,
                        "value": (obj.values or [None])[0],
                        "dns_name": ref.dns_name,
                        "reference": ctx.primary.source.name,
                        "compared": [_short(parsed.remainder), _short(ref.dns_name)],
                    },
                )
            )
    return out


def _subnet_checks(session, tenant_id, ctx: Context, names, Issue) -> list:
    """Reale Subnetze (FortiGate-Interfaces, ACI-BD/-EPG) gegen das IPAM pruefen."""
    from app.models import FindingType, InvSubnet, SubjectKind

    out = []
    for sub in session.scalars(select(InvSubnet).where(InvSubnet.tenant_id == tenant_id)):
        net = _norm_net(sub.prefix)
        if net is None or net.is_link_local or net.is_loopback:
            continue
        detail = {
            "source_system_id": sub.source_system_id,
            "source": names.get(sub.source_system_id),
            "prefix": sub.prefix,
            "gateway": sub.gateway,
            "context": sub.context,
            "context_name": sub.context_name,
            "epg": sub.epg,
            "vrf": sub.vrf,
            "vlan": sub.vlan,
            "reference": ctx.primary.source.name,
        }
        ref = ctx.primary.prefix(sub.prefix)
        if ref is None:
            out.append(
                Issue(
                    FindingType.SUBNET_NOT_IN_IPAM,
                    SubjectKind.SUBNET,
                    sub.prefix,
                    sub.source_system_id,
                    detail=detail,
                )
            )
            continue
        if sub.vlan is not None and ref.vlan_vid is not None and sub.vlan != ref.vlan_vid:
            out.append(
                Issue(
                    FindingType.VLAN_MISMATCH,
                    SubjectKind.SUBNET,
                    sub.prefix,
                    sub.source_system_id,
                    detail={**detail, "ipam_vlan": ref.vlan_vid},
                )
            )
    return out


# -- Vergleich fuehrendes IPAM ↔ Anreicherung (DDI ↔ NetBox) ------------------

COMPARE_STATUSES = ("missing", "different", "only_enrichment")


def _ip_view(index: ReferenceIndex) -> dict[str, Ref]:
    """Je IP der aussagekraeftigste Eintrag (Host/IP-Adresse vor A/PTR/DHCP)."""
    return {ip: recs[0] for ip, recs in index.by_ip.items() if recs}


def compare_sources(
    session: Session,
    tenant_id: int,
    status: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    keep=None,
) -> dict:
    """Fuehrendes IPAM gegen die Anreicherung vergleichen (nur Felder mit DDI-Hoheit:
    Existenz von Netzen/Ranges/IPs und der DNS-Name je IP). `keep` (0.76.0, Kunde gewaehlt):
    Wert (IP, Netz, Range) -> im Ausschnitt?"""
    ctx = context(session, tenant_id)
    if ctx.primary is None or not ctx.enrichments:
        return {"available": False}
    primary, enrichment = ctx.primary, ctx.enrichments[0]
    rows: list[dict] = []

    def add(state: str, kind: str, value: str, p: Ref | None, e: Ref | None, diffs=()):
        rows.append(
            {
                "status": state,
                "kind": kind,
                "value": value,
                "primary": _brief(p),
                "enrichment": _brief(e),
                "diffs": list(diffs),
            }
        )

    for kind, pmap, emap in (
        ("prefix", primary.prefix_by_value, enrichment.prefix_by_value),
        (
            "range",
            {r.value: r for *_, r in primary.ranges},
            {r.value: r for *_, r in enrichment.ranges},
        ),
    ):
        for value, p in pmap.items():
            if value not in emap:
                add("missing", kind, value, p, None)
        for value, e in emap.items():
            if value not in pmap:
                add("only_enrichment", kind, value, None, e)
    pips, eips = _ip_view(primary), _ip_view(enrichment)
    for ip, p in pips.items():
        e = eips.get(ip)
        if e is None:
            add("missing", "ip", ip, p, None)
        elif (p.dns_name or "").rstrip(".").lower() != (e.dns_name or "").rstrip(".").lower():
            add("different", "ip", ip, p, e, ["dns_name"])
    for ip, e in eips.items():
        if ip not in pips:
            add("only_enrichment", "ip", ip, None, e)

    if keep is not None:
        rows = [r for r in rows if keep(r["value"])]
    summary = {s: 0 for s in COMPARE_STATUSES}
    for r in rows:
        summary[r["status"]] += 1
    values = [*pips, *primary.prefix_by_value, *(r.value for *_, r in primary.ranges)]
    equal = (
        sum(1 for v in values if keep is None or keep(v))
        - summary["missing"]
        - summary["different"]
    )
    if q:
        ql = q.lower()
        rows = [
            r
            for r in rows
            if ql in r["value"].lower()
            or any(
                ql in (x or {}).get("dns_name", "").lower() for x in (r["primary"], r["enrichment"])
            )
        ]
    if status:
        rows = [r for r in rows if r["status"] == status]
    order = {s: i for i, s in enumerate(COMPARE_STATUSES)}
    kinds = {"prefix": 0, "range": 1, "ip": 2}  # Netze vor einzelnen IPs
    rows.sort(key=lambda r: (order[r["status"]], kinds[r["kind"]], _sort_key(r["value"])))
    return {
        "available": True,
        "primary": {
            "id": primary.source.id,
            "name": primary.source.name,
            "platform": primary.platform,
        },
        "enrichment": {
            "id": enrichment.source.id,
            "name": enrichment.source.name,
            "platform": enrichment.platform,
        },
        "summary": {**summary, "equal": max(equal, 0)},
        "total": len(rows),
        "rows": rows[offset : offset + limit],
    }


def _brief(r: Ref | None) -> dict | None:
    if r is None:
        return None
    return {
        "kind": r.kind,
        "value": r.value,
        "dns_name": r.dns_name or "",
        "description": r.description,
        "vlan": r.vlan_vid,
        "site": r.site,
        "url": r.url,
    }


def _sort_key(value: str):
    first = value.split("-")[0].split("/")[0]
    try:
        addr = ipaddress.ip_address(first)
        return (addr.version, int(addr), value)
    except ValueError:
        return (9, 0, value)
