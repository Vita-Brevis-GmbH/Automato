"""Firewall-Objekte lesen und anzeigen (Inventar-Ebene, ADR 0010).

Bereitet Objekte so auf, wie sie auf dem Geraet stehen: Name, Typ, Kommentar, Gruppen mit
Mitgliedern, aufgeloeste Werte - und je Wert den Abgleich gegen das IPAM (Match-Status,
Zone, Referenz- und Anreicherungsdaten). Genutzt von Regel-Detail und Objekt-Ansicht.
"""

from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    SERVICE_CATEGORIES,
    DeviceObject,
    Finding,
    MatchStatus,
    NetworkObject,
    RefRole,
    Rule,
    SubjectKind,
    Zone,
)
from app.schemas.rules import RefMemberOut, RefOut, RefValueOut

# Typ-Praefixe der Collector-Werte (siehe ingest._TYPED_KINDS)
_TYPED = ("fqdn", "geo", "mac", "dynamic", "range")


def object_key(value: str) -> str:
    """Name des kanonischen Werte-Objekts zu einem Collector-Wert."""
    tag, sep, rest = value.partition(":")
    if sep and tag in _TYPED:
        return rest
    if sep and tag == "vip":
        return rest.partition("=")[0]
    return value


def display_value(value: str) -> str:
    tag, sep, rest = value.partition(":")
    if sep and tag in _TYPED:
        return rest
    if sep and tag == "vip":
        name, _, ip = rest.partition("=")
        return ip or name
    return value


def value_infos(session: Session, tenant_id: int, values: Iterable[str]) -> dict[str, RefValueOut]:
    """Je Wert: Anzeige + Abgleich (Match-Status, Zone, Referenzdaten)."""
    from app.store import references, virtual  # spaet: references nutzt Modelle dieses Moduls

    values = list(dict.fromkeys(values))
    keys = {object_key(v) for v in values}
    objs = (
        {
            o.name: o
            for o in session.scalars(
                select(NetworkObject).where(
                    NetworkObject.tenant_id == tenant_id, NetworkObject.name.in_(keys)
                )
            )
        }
        if keys
        else {}
    )
    zone_ids = {o.zone_id for o in objs.values() if o.zone_id}
    zones = (
        {z.id: z.name for z in session.scalars(select(Zone).where(Zone.id.in_(zone_ids)))}
        if zone_ids
        else {}
    )
    refs = references.describe(session, list(objs.values()))
    vms = virtual.Lookup(session, tenant_id, {display_value(v).split("/")[0] for v in values})
    out: dict[str, RefValueOut] = {}
    for v in values:
        obj = objs.get(object_key(v))
        reference, enrichment = refs.get(obj.id, (None, None)) if obj else (None, None)
        out[v] = RefValueOut(
            value=display_value(v),
            match_status=obj.match_status.value if obj else None,
            zone=zones.get(obj.zone_id) if obj and obj.zone_id else None,
            source_of_record=obj.source_of_record.value if obj and obj.source_of_record else None,
            reference=reference,
            enrichment=enrichment,
            vm=vms.info(display_value(v)),
        )
    return out


def aggregate_status(values: Iterable[RefValueOut]) -> str | None:
    statuses = [v.match_status for v in values if v.match_status]
    if not statuses:
        return None
    for status in (MatchStatus.UNMATCHED, MatchStatus.AMBIGUOUS):
        if status.value in statuses:
            return status.value
    if all(s == MatchStatus.MATCHED.value for s in statuses):
        return MatchStatus.MATCHED.value
    return statuses[0]


def art(o: DeviceObject) -> str:
    """svc fuer Services/Service-Gruppen, sonst addr (Adressen, Gruppen, VIPs, Pools)."""
    return "svc" if o.category in SERVICE_CATEGORIES else "addr"


class Directory:
    """Firewall-Objekte von Quellsystemen nach Name. FortiGate fuehrt IPv4 (address/addrgrp)
    und IPv6 (address6/addrgrp6) getrennt - gleiche Namen sind verschiedene Objekte; Gruppen
    loesen ihre Mitglieder in der eigenen Familie auf."""

    def __init__(
        self, session: Session, source_ids: Iterable[int], names: Iterable[str] | None = None
    ):
        ids = set(source_ids)
        stmt = select(DeviceObject).where(DeviceObject.source_system_id.in_(ids))
        if names is not None:  # nur diese Namen (z. B. Mitglieder einer Gruppe)
            stmt = stmt.where(DeviceObject.name.in_(set(names)))
        rows = session.scalars(stmt) if ids else []
        self.by_id: dict[int, DeviceObject] = {}
        self._by_name: dict[tuple[int, str, str], list[DeviceObject]] = {}
        for o in rows:
            self.by_id[o.id] = o
            self._by_name.setdefault((o.source_system_id, art(o), o.name), []).append(o)

    def find(
        self, source_id: int, name: str, kind: str = "addr", family: str | None = None
    ) -> DeviceObject | None:
        """Objekt nach Name; bei gleichnamigen IPv4/IPv6-Objekten das der Familie (ohne Angabe
        IPv4 vor familienlosen vor IPv6)."""
        found = self._by_name.get((source_id, kind, name))
        if not found:
            return None
        order = {family: 0} if family else {"v4": 0, None: 1}
        return min(found, key=lambda o: order.get(o.family, 2))

    def member(self, group: DeviceObject, name: str) -> DeviceObject | None:
        return self.find(group.source_system_id, name, art(group), group.family)

    def expand(self, obj: DeviceObject, seen: set[int] | None = None) -> list[DeviceObject]:
        """Objekt mit allen (verschachtelten) Gruppenmitgliedern derselben Familie."""
        seen = seen if seen is not None else set()
        if obj.id in seen:
            return []
        seen.add(obj.id)
        out = [obj]
        for name in obj.members or []:
            member = self.member(obj, name)
            if member is not None:
                out += self.expand(member, seen)
        return out


def rule_refs(session: Session, rule: Rule) -> list[RefOut]:
    """Verweise einer Regel mit Objekt-Details, Gruppenmitgliedern und Abgleich."""
    if not rule.refs:
        return []
    directory = Directory(session, {rule.source_system_id})
    by_id = directory.by_id

    # alle Werte (inkl. Mitglieder) in einem Rutsch abgleichen
    address_values: list[str] = []
    for ref in rule.refs:
        o = by_id.get(ref.device_object_id) if ref.device_object_id else None
        if o is None or ref.role is RefRole.SERVICE:
            continue
        address_values += o.values or []
        for m in o.members or []:
            member = directory.member(o, m)
            address_values += member.values or [] if member else []
    infos = value_infos(session, rule.tenant_id, address_values)

    def values_of(o: DeviceObject, service: bool) -> list[RefValueOut]:
        if service:
            return [RefValueOut(value=v) for v in o.values or []]
        return [infos.get(v) or RefValueOut(value=display_value(v)) for v in o.values or []]

    out: list[RefOut] = []
    for ref in rule.refs:
        service = ref.role is RefRole.SERVICE
        o = by_id.get(ref.device_object_id) if ref.device_object_id else None
        if o is None:
            out.append(RefOut(role=ref.role.value, name=ref.name, family=ref.family))
            continue
        members = []
        for m in o.members or []:
            member = directory.member(o, m)
            members.append(
                RefMemberOut(
                    name=m,
                    category=member.category.value if member else None,
                    obj_type=member.obj_type if member else None,
                    comment=member.comment if member else None,
                    values=values_of(member, service) if member else [],
                )
            )
        values = values_of(o, service)
        out.append(
            RefOut(
                role=ref.role.value,
                name=ref.name,
                family=o.family or ref.family,
                category=o.category.value,
                obj_type=o.obj_type,
                comment=o.comment,
                color=o.color,
                values=values,
                members=members,
                match_status=None if service else aggregate_status(values),
            )
        )
    return out


def object_issues(session: Session, tid: int, objects: list[DeviceObject]) -> dict[int, list[str]]:
    """Befunde je Objekt (aus dem Abgleich) und Werte ohne Treffer im IPAM - ohne die teure
    Aufbereitung der Referenzdaten (fuer den Filter "nur Abweichungen" ueber alle Objekte)."""
    from app.store import reconcile  # spaet: reconcile nutzt dieses Modul

    by_subject: dict[tuple[int | None, str], list[str]] = defaultdict(list)
    for sid, subject, ftype in session.execute(
        select(Finding.source_system_id, Finding.subject_id, Finding.type).where(
            Finding.tenant_id == tid, Finding.subject_kind == SubjectKind.DEVICE_OBJECT
        )
    ):
        by_subject[(sid, subject)].append(ftype.value)
    bad = set(
        session.scalars(
            select(NetworkObject.name).where(
                NetworkObject.tenant_id == tid,
                NetworkObject.match_status.in_([MatchStatus.UNMATCHED, MatchStatus.AMBIGUOUS]),
            )
        )
    )
    out: dict[int, list[str]] = {}
    for o in objects:
        found = list(by_subject.get((o.source_system_id, reconcile.subject(o)), []))
        service = o.category in SERVICE_CATEGORIES
        if not service and any(object_key(v) in bad for v in o.values or []):
            found.append("unmatched_object")
        out[o.id] = sorted(set(found))
    return out
