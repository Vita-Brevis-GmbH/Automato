"""Abgleich: Findings als abgeleiteter Zustand des Mandanten (ADR 0010).

Nach jedem Lauf eines Quellsystems (und auf Anforderung) werden alle Befunde des Mandanten
aus dem aktuellen Stand neu berechnet - unabhaengig davon, welches Quellsystem zuletzt lief.
Bestehende Befunde behalten Triage-Status und Notiz; Befunde, die nicht mehr zutreffen,
fallen weg. Nur lesend gegenueber allen Quellsystemen.

Pruefungen (Inventar ↔ Konvention):
- unmatched_object      Regel-Endpunkt ohne Treffer im IPAM
- invalid_name          Objektname passt zu keinem Praefix der Namenskonvention
- name_type_mismatch    Praefix sagt Host, Wert ist ein Netz (oder umgekehrt)
- duplicate_value       gleicher Wert in mehreren Adressobjekten derselben Firewall
- unused_object         Firewall-Objekt in keiner Regel und keiner Gruppe
Referenz-Pruefungen (IPAM, DDI ↔ NetBox, ACI-Subnetze) ergaenzt `references.checks`.
"""

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import naming
from app.models import (
    DeviceObject,
    Finding,
    FindingType,
    MatchStatus,
    NetworkObject,
    ObjectCategory,
    ObjectClass,
    Rule,
    RuleEndpoint,
    RuleObjectRef,
    Severity,
    SourceSystem,
    SubjectKind,
)
from app.store import naming_profiles as profile_store


@dataclass(frozen=True)
class Issue:
    type: FindingType
    subject_kind: SubjectKind
    subject_id: str
    source_system_id: int | None = None
    severity: Severity = Severity.WARNING
    detail: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def key(self) -> tuple:
        return (self.type, self.subject_kind, self.subject_id[:300], self.source_system_id)


def reconcile_tenant(session: Session, tenant_id: int | None) -> dict[str, int]:
    """Alle Befunde des Mandanten neu berechnen; Rueckgabe: Anzahl je Befund-Typ."""
    if tenant_id is None:
        return {}
    session.flush()
    from app.store import references  # Referenz-Ebene (IPAM), spaet importiert

    ctx = references.context(session, tenant_id)
    references.rematch(session, tenant_id, ctx)  # zuerst: Werte gegen den aktuellen Stand
    prefixes = profile_store.prefixes_for_tenant(session, tenant_id)
    issues: list[Issue] = []
    issues += _rule_endpoint_issues(session, tenant_id)
    issues += _device_object_issues(session, tenant_id, prefixes)
    issues += references.checks(session, tenant_id, prefixes, ctx)
    _sync_findings(session, tenant_id, issues)
    counts: dict[str, int] = defaultdict(int)
    for issue in {i.key: i for i in issues}.values():
        counts[issue.type.value] += 1
    return dict(counts)


# -- Regel-Endpunkte (kanonische Werte-Ebene) ---------------------------------


def _rule_endpoint_issues(session: Session, tenant_id: int) -> list[Issue]:
    """unmatched_object / invalid_name fuer Objekte, die eine Regel des Mandanten nutzt."""
    rows = session.execute(
        select(NetworkObject, Rule.source_system_id)
        .join(RuleEndpoint, RuleEndpoint.network_object_id == NetworkObject.id)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .where(Rule.tenant_id == tenant_id)
    ).all()
    out: list[Issue] = []
    seen: set[int] = set()
    for obj, _source_id in rows:
        if obj.id in seen:
            continue
        seen.add(obj.id)
        if obj.match_status is MatchStatus.UNMATCHED:
            out.append(
                Issue(
                    FindingType.UNMATCHED_OBJECT,
                    SubjectKind.NETWORK_OBJECT,
                    obj.name,
                    detail={"value": obj.value, "object_class": obj.object_class.value},
                )
            )
        if (
            obj.object_class is ObjectClass.UNKNOWN
            and not obj.value
            and obj.match_status not in (MatchStatus.NOT_APPLICABLE, MatchStatus.EXPECTED_EXTERNAL)
        ):
            out.append(
                Issue(
                    FindingType.INVALID_NAME,
                    SubjectKind.NETWORK_OBJECT,
                    obj.name,
                    detail={"hint": "Name passt zu keinem Konventions-Praefix"},
                )
            )
    return out


# -- Firewall-Objekte (Inventar-Ebene) ----------------------------------------


def _device_object_issues(session: Session, tenant_id: int, prefixes) -> list[Issue]:
    objects = list(session.scalars(select(DeviceObject).where(DeviceObject.tenant_id == tenant_id)))
    if not objects:
        return []
    source_names = {
        s.id: s.name
        for s in session.scalars(select(SourceSystem).where(SourceSystem.tenant_id == tenant_id))
    }
    used = _used_object_ids(session, tenant_id, objects)
    out: list[Issue] = []
    by_value: dict[tuple[int, str], list[str]] = defaultdict(list)

    for o in objects:
        base = {
            "source_system_id": o.source_system_id,
            "source": source_names.get(o.source_system_id),
        }
        detail = {**base, "category": o.category.value, "name": o.name}
        # Konvention gilt fuer Adressen und VIPs (fuer Gruppen kennt sie kein Praefix)
        if o.category in _CONVENTION_CATEGORIES:
            parsed = naming.parse_object_name(o.name, prefixes)
            if parsed.object_class is ObjectClass.UNKNOWN and o.obj_type not in _NO_CONVENTION:
                out.append(_issue(FindingType.INVALID_NAME, o, detail))
            elif o.category is ObjectCategory.ADDRESS:
                shape = _value_shape(o)
                if (parsed.object_class, shape) in _SHAPE_MISMATCH:
                    out.append(
                        _issue(
                            FindingType.NAME_TYPE_MISMATCH,
                            o,
                            {**detail, "expected": parsed.object_class.value, "value": _first(o)},
                        )
                    )
        if o.category is ObjectCategory.ADDRESS and _first(o):
            by_value[(o.source_system_id, _first(o))].append(o.name)
        if o.id not in used and o.category is not ObjectCategory.IPPOOL:
            out.append(_issue(FindingType.UNUSED_OBJECT, o, detail, Severity.INFO))

    for (source_id, value), names in by_value.items():
        if len(names) > 1:
            out.append(
                Issue(
                    FindingType.DUPLICATE_VALUE,
                    SubjectKind.DEVICE_OBJECT,
                    value,
                    source_id,
                    detail={
                        "source_system_id": source_id,
                        "source": source_names.get(source_id),
                        "value": value,
                        "names": sorted(names),
                    },
                )
            )
    return out


_CONVENTION_CATEGORIES = (ObjectCategory.ADDRESS, ObjectCategory.VIP)
# FortiGate-Typen ohne Namenskonvention (Geo-/MAC-/dynamische Objekte, FQDN-Wildcards)
_NO_CONVENTION = {"geography", "mac", "dynamic", "wildcard-fqdn"}
_SHAPE_MISMATCH = {(ObjectClass.HOST, "network"), (ObjectClass.NETWORK, "host")}


def _issue(ftype: FindingType, o: DeviceObject, detail: dict, sev=Severity.WARNING) -> Issue:
    return Issue(ftype, SubjectKind.DEVICE_OBJECT, o.name, o.source_system_id, sev, detail)


def _first(o: DeviceObject) -> str | None:
    values = o.values or []
    return values[0] if len(values) == 1 else None


def _value_shape(o: DeviceObject) -> str | None:
    """host | network fuer IP-/CIDR-Werte; typisierte Werte (fqdn:, range:, ...) -> None."""
    value = _first(o)
    try:
        net = ipaddress.ip_network(value, strict=False) if value else None
    except ValueError:
        return None
    if net is None:
        return None
    return "host" if net.num_addresses == 1 else "network"


def _used_object_ids(session: Session, tenant_id: int, objects: list[DeviceObject]) -> set[int]:
    """In einer Regel referenziert oder Mitglied einer (genutzten oder nicht) Gruppe."""
    used = set(
        session.scalars(
            select(RuleObjectRef.device_object_id)
            .join(Rule, Rule.id == RuleObjectRef.rule_id)
            .where(Rule.tenant_id == tenant_id, RuleObjectRef.device_object_id.is_not(None))
        )
    )
    by_source_name: dict[tuple[int, str], list[DeviceObject]] = defaultdict(list)
    for o in objects:
        by_source_name[(o.source_system_id, o.name)].append(o)
    for o in objects:
        for member in o.members or []:
            for m in by_source_name.get((o.source_system_id, member), []):
                used.add(m.id)
    return used


# -- Findings synchronisieren (Triage bleibt erhalten) ------------------------


def _sync_findings(session: Session, tenant_id: int, issues: list[Issue]) -> None:
    now = datetime.now(UTC)
    wanted = {i.key: i for i in issues}
    existing: dict[tuple, Finding] = {}
    for f in session.scalars(select(Finding).where(Finding.tenant_id == tenant_id)):
        key = (f.type, f.subject_kind, f.subject_id, f.source_system_id)
        if key in wanted and key not in existing:
            existing[key] = f
        else:
            session.delete(f)  # trifft nicht mehr zu (oder Dublette)
    session.flush()
    for key, issue in wanted.items():
        f = existing.get(key)
        if f is None:
            session.add(
                Finding(
                    tenant_id=tenant_id,
                    type=issue.type,
                    severity=issue.severity,
                    subject_kind=issue.subject_kind,
                    subject_id=issue.subject_id[:300],
                    source_system_id=issue.source_system_id,
                    detail=issue.detail or None,
                    first_seen=now,
                    last_seen=now,
                )
            )
        else:
            f.severity = issue.severity
            f.detail = issue.detail or None
            f.last_seen = now
    session.flush()
