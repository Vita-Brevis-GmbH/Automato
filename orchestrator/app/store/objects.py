"""Firewall-Objekte lesen und anzeigen (Inventar-Ebene, ADR 0010).

Bereitet Objekte so auf, wie sie auf dem Geraet stehen: Name, Typ, Kommentar, Gruppen mit
Mitgliedern, aufgeloeste Werte - und je Wert den Abgleich gegen das IPAM (Match-Status,
Zone, Referenz- und Anreicherungsdaten). Genutzt von Regel-Detail und Objekt-Ansicht.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    SERVICE_CATEGORIES,
    DeviceObject,
    MatchStatus,
    NetworkObject,
    RefRole,
    Rule,
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
    from app.store import references  # spaet: references nutzt Modelle dieses Moduls

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


def _objects_by_source(session: Session, source_ids: set[int]) -> dict[tuple, DeviceObject]:
    if not source_ids:
        return {}
    rows = session.scalars(
        select(DeviceObject).where(DeviceObject.source_system_id.in_(source_ids))
    )
    out: dict[tuple, DeviceObject] = {}
    for o in rows:
        art = "svc" if o.category in SERVICE_CATEGORIES else "addr"
        out[(o.source_system_id, art, o.name)] = o
    return out


def rule_refs(session: Session, rule: Rule) -> list[RefOut]:
    """Verweise einer Regel mit Objekt-Details, Gruppenmitgliedern und Abgleich."""
    if not rule.refs:
        return []
    objs = _objects_by_source(session, {rule.source_system_id})
    by_id = {o.id: o for o in objs.values()}

    def find(art: str, name: str) -> DeviceObject | None:
        return objs.get((rule.source_system_id, art, name))

    # alle Werte (inkl. Mitglieder) in einem Rutsch abgleichen
    address_values: list[str] = []
    for ref in rule.refs:
        o = by_id.get(ref.device_object_id) if ref.device_object_id else None
        if o is None or ref.role is RefRole.SERVICE:
            continue
        address_values += o.values or []
        for m in o.members or []:
            member = find("addr", m)
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
            out.append(RefOut(role=ref.role.value, name=ref.name))
            continue
        members = []
        for m in o.members or []:
            member = find("svc" if service else "addr", m)
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
