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
    Epg,
    Finding,
    FindingType,
    MatchStatus,
    NetworkObject,
    ObjectCategory,
    ObjectClass,
    Portgroup,
    Rule,
    RuleEndpoint,
    RuleObjectRef,
    Severity,
    SourceSystem,
    SubjectKind,
    Vm,
    VmIp,
)
from app.store import naming_profiles as profile_store
from app.store import virtual


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
    prefixes = profile_store.prefixes_for_tenant(session, tenant_id)
    references.rematch(session, tenant_id, ctx, prefixes)  # zuerst: Werte gegen den Stand
    virtual.map_epgs(session, tenant_id)  # Portgruppen -> EPGs (EPGs aendern sich je APIC-Lauf)
    issues: list[Issue] = []
    issues += _rule_endpoint_issues(session, tenant_id, ctx, prefixes)
    issues += _rule_incomplete_issues(session, tenant_id)
    issues += _device_object_issues(session, tenant_id, prefixes)
    issues += references.checks(session, tenant_id, prefixes, ctx)
    issues += _virtual_issues(session, tenant_id, ctx)
    _sync_findings(session, tenant_id, issues)
    counts: dict[str, int] = defaultdict(int)
    for issue in {i.key: i for i in issues}.values():
        counts[issue.type.value] += 1
    return dict(counts)


# -- Regel-Endpunkte (kanonische Werte-Ebene) ---------------------------------


def _rule_endpoint_issues(session: Session, tenant_id: int, ctx=None, prefixes=None) -> list[Issue]:
    """unmatched_object / invalid_name fuer Objekte, die eine Regel des Mandanten nutzt.

    Das Detail sagt genau, was fehlt: Wert, Referenzquelle, welche Firewall-Objekte und Regeln
    den Wert nutzen und - falls das IPAM den Server unter seinem Namen (ohne Praefix) mit
    einer anderen IP kennt - diese IP."""
    rows = session.execute(
        select(NetworkObject, Rule.id, Rule.name, Rule.external_id, Rule.source_system_id)
        .join(RuleEndpoint, RuleEndpoint.network_object_id == NetworkObject.id)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .where(Rule.tenant_id == tenant_id)
    ).all()
    objs: dict[int, NetworkObject] = {}
    rules_of: dict[int, dict[int, str]] = defaultdict(dict)
    sources_of: dict[int, set[int]] = defaultdict(set)
    for obj, rule_id, rule_name, ext_id, source_id in rows:
        objs[obj.id] = obj
        rules_of[obj.id][rule_id] = rule_name or f"#{ext_id}"
        if source_id is not None:
            sources_of[obj.id].add(source_id)
    unmatched = [o for o in objs.values() if o.match_status is MatchStatus.UNMATCHED]
    fw_names: dict[int, list[str]] = defaultdict(list)
    if unmatched:
        for oid, name in session.execute(
            select(DeviceObject.network_object_id, DeviceObject.name)
            .where(
                DeviceObject.tenant_id == tenant_id,
                DeviceObject.network_object_id.in_([o.id for o in unmatched]),
            )
            .order_by(DeviceObject.name)
        ):
            if name not in fw_names[oid]:
                fw_names[oid].append(name)
    source_names = _source_names(session, tenant_id)
    primary = ctx.primary if ctx is not None else None
    out: list[Issue] = []
    for obj in objs.values():
        if obj.match_status is MatchStatus.UNMATCHED:
            rules = sorted(rules_of[obj.id].values())
            detail = {
                "value": obj.value,
                "object_class": obj.object_class.value,
                "reference": primary.source.name if primary else None,
                "no_reference": primary is None or None,
                "sources": sorted(source_names.get(s, f"#{s}") for s in sources_of[obj.id]),
                "firewall_objects": fw_names.get(obj.id, [])[:10],
                "rules": len(rules),
                "rule_names": rules[:5],
            }
            if not obj.value:
                detail["server_name"] = naming.server_name(obj.name, prefixes)
                detail["ipam_candidates"] = _name_candidates(primary, detail["server_name"])
            elsewhere = _ipam_elsewhere(primary, obj, fw_names.get(obj.id, []), prefixes)
            if elsewhere:
                detail["ipam_elsewhere"] = elsewhere
            out.append(
                Issue(
                    FindingType.UNMATCHED_OBJECT,
                    SubjectKind.NETWORK_OBJECT,
                    obj.name,
                    detail={k: v for k, v in detail.items() if v not in (None, [], "")},
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
                    detail={
                        "hint": "Name passt zu keinem Konventions-Praefix",
                        "prefixes": _prefix_names(prefixes),
                    },
                )
            )
    return out


def _rule_incomplete_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Regel ohne Quelle bzw. Ziel - ACI: Contract ohne Consumer/Provider, nicht wirksam."""
    roles: dict[int, set[str]] = defaultdict(set)
    for rule_id, role in session.execute(
        select(RuleEndpoint.rule_id, RuleEndpoint.role)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .where(Rule.tenant_id == tenant_id)
        .distinct()
    ):
        roles[rule_id].add(role.value)
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    for r in session.scalars(select(Rule).where(Rule.tenant_id == tenant_id)):
        missing = [side for side in ("src", "dst") if side not in roles.get(r.id, set())]
        if not missing:
            continue
        aci = r.domain.value == "aci_contract"
        raw = r.raw if isinstance(r.raw, dict) else {}
        label = r.name or f"#{r.external_id}"
        subject = f"{raw.get('tenant')}/{label}" if aci and raw.get("tenant") else label
        out.append(
            Issue(
                FindingType.RULE_INCOMPLETE,
                SubjectKind.RULE,
                subject,
                r.source_system_id,
                Severity.INFO if aci else Severity.WARNING,
                detail={
                    "rule_id": r.id,
                    "external_id": r.external_id,
                    "domain": r.domain.value,
                    "missing": missing,
                    "source_system_id": r.source_system_id,
                    "source": names.get(r.source_system_id),
                    "name": label,
                    "tenant": raw.get("tenant") if aci else None,
                },
            )
        )
    return out


def _ipam_elsewhere(primary, obj: NetworkObject, fw_names: list[str], prefixes) -> dict | None:
    """Kennt das IPAM den Server (Name ohne Praefix, H_web01 -> web01) unter einer anderen IP?"""
    if primary is None or obj.object_class is not ObjectClass.HOST:
        return None
    for name in fw_names:
        ref = primary.lookup_name(name, prefixes)
        if ref is not None and ref.value and ref.value != (obj.value or "").split("/")[0]:
            return {"name": ref.dns_name or ref.name, "value": ref.value, "object": name}
    return None


def _name_candidates(primary, name: str) -> list[str]:
    """Eintraege mit demselben Kurznamen (dup -> dup.example.ch, dup.other.ch): warum der
    Name nicht eindeutig zugeordnet werden konnte."""
    if primary is None:
        return []
    recs = primary.by_short.get(naming.short_name(name), [])
    out = [f"{r.dns_name or r.name} → {r.value}" for r in recs]
    return list(dict.fromkeys(out))[:5]


def _prefix_names(prefixes) -> list[str]:
    return [p for p, _ in (prefixes or naming.prefixes_from_entries([]))]


def _source_names(session: Session, tenant_id: int) -> dict[int, str]:
    return {
        s.id: s.name
        for s in session.scalars(select(SourceSystem).where(SourceSystem.tenant_id == tenant_id))
    }


# -- Firewall-Objekte (Inventar-Ebene) ----------------------------------------


def _device_object_issues(session: Session, tenant_id: int, prefixes) -> list[Issue]:
    objects = list(session.scalars(select(DeviceObject).where(DeviceObject.tenant_id == tenant_id)))
    if not objects:
        return []
    source_names = _source_names(session, tenant_id)
    used = _used_object_ids(session, tenant_id, objects)
    services = _ServiceConvention(session, tenant_id)
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
                known = {"prefixes": _prefix_names(prefixes)}
                out.append(_issue(FindingType.INVALID_NAME, o, {**detail, **known}))
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
        predefined = _predefined_service(o)
        if o.category is ObjectCategory.SERVICE and not predefined:
            out += _service_issues(o, detail, services)
        if o.category is ObjectCategory.ADDRESS and _first(o):
            by_value[(o.source_system_id, _first(o))].append(o.name)
        derived = o.category is ObjectCategory.VIP_TARGET or bool((o.attrs or {}).get("vip_of"))
        if (
            o.id not in used
            and o.category is not ObjectCategory.IPPOOL
            and not predefined
            and not derived  # VIP-Ziel bzw. Objekt hinter einer VIP: von der VIP verwendet
        ):
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


class _ServiceConvention:
    """Praefixe (Woerterbuch `service_prefix`, sonst FT_) und Soll-Ports (Woerterbuch
    `service`, z. B. FT_https -> tcp/443) - ohne Woerterbuch greift das Schema FT_<proto><port>."""

    def __init__(self, session: Session, tenant_id: int):
        own = profile_store.entries_for_tenant(session, tenant_id, "service_prefix")
        self.prefixes = tuple(own) or naming.SERVICE_PREFIXES
        self.catalog = {
            k.lower(): v
            for k, v in profile_store.entries_for_tenant(session, tenant_id, "service").items()
        }

    def expected(self, name: str) -> tuple[list[str], str] | None:
        if name.lower() in self.catalog:
            return naming.service_tokens(self.catalog[name.lower()]), "Wörterbuch"
        tokens = naming.expected_service(name)
        return (tokens, "Namensschema") if tokens else None


def _bare_name(o: DeviceObject) -> str:
    """ACI-Filter heissen `<Tenant>/<FT_name>` - geprueft wird der Filtername."""
    return o.name.split("/", 1)[1] if o.obj_type == "vzFilter" and "/" in o.name else o.name


def _predefined_service(o: DeviceObject) -> bool:
    return (
        o.category in (ObjectCategory.SERVICE, ObjectCategory.SERVICE_GROUP)
        and o.obj_type != "vzFilter"
        and o.name.lower() in naming.FORTIOS_PREDEFINED_SERVICES
    )


def _service_issues(o: DeviceObject, detail: dict, conv: _ServiceConvention) -> list[Issue]:
    """Service/Filter nach Namensschema: Praefix FT_ und Ports passend zum Namen."""
    name = _bare_name(o)
    base = {**detail, "category": "service"}
    if not name.startswith(conv.prefixes):
        return [_issue(FindingType.INVALID_NAME, o, {**base, "prefixes": list(conv.prefixes)})]
    expected = conv.expected(name)
    if expected is None:
        return []
    tokens, rule = expected
    actual = naming.normalize_tokens(o.values or [])
    if naming.normalize_tokens(tokens) == actual:
        return []
    return [
        _issue(
            FindingType.SERVICE_MISMATCH,
            o,
            {
                **base,
                "expected": sorted(naming.normalize_tokens(tokens)),
                "values": sorted(actual),
                "rule": rule,
            },
        )
    ]


_CONVENTION_CATEGORIES = (ObjectCategory.ADDRESS, ObjectCategory.VIP)
# FortiGate-Typen ohne Namenskonvention (Geo-/MAC-/dynamische Objekte, FQDN-Wildcards)
_NO_CONVENTION = {"geography", "mac", "dynamic", "wildcard-fqdn"}
_SHAPE_MISMATCH = {(ObjectClass.HOST, "network"), (ObjectClass.NETWORK, "host")}


def subject(o: DeviceObject) -> str:
    """Befund-Bezeichnung eines Firewall-Objekts. IPv6-Adressobjekte tragen die Familie im
    Namen - auf der FortiGate darf ein IPv4- und ein IPv6-Objekt gleich heissen."""
    if o.family == "v6" and o.category in _FAMILY_CATEGORIES:
        return f"{o.name} (IPv6)"
    return o.name


_FAMILY_CATEGORIES = (ObjectCategory.ADDRESS, ObjectCategory.ADDRESS_GROUP, ObjectCategory.VIP)


def _issue(ftype: FindingType, o: DeviceObject, detail: dict, sev=Severity.WARNING) -> Issue:
    if o.family and o.category in _FAMILY_CATEGORIES:
        detail = {**detail, "family": o.family}
    return Issue(ftype, SubjectKind.DEVICE_OBJECT, subject(o), o.source_system_id, sev, detail)


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
    """In einer Regel referenziert oder Mitglied einer (genutzten oder nicht) Gruppe - Mitglieder
    in der Familie der Gruppe (IPv4-Gruppe -> IPv4-Objekt, auch bei gleichem Namen in IPv6)."""
    from app.store.objects import Directory  # spaet: objects importiert Modelle/Schemas

    used = set(
        session.scalars(
            select(RuleObjectRef.device_object_id)
            .join(Rule, Rule.id == RuleObjectRef.rule_id)
            .where(Rule.tenant_id == tenant_id, RuleObjectRef.device_object_id.is_not(None))
        )
    )
    directory = Directory(session, {o.source_system_id for o in objects})
    for o in objects:
        for member in o.members or []:
            m = directory.member(o, member)
            if m is not None:
                used.add(m.id)
    return used


# -- VMware (ADR 0017) -------------------------------------------------------


def _virtual_issues(session: Session, tenant_id: int, ctx) -> list[Issue]:
    """VM-IPs ohne Eintrag im fuehrenden IPAM; Portgruppen nach ACI-Schema ohne EPG."""
    out: list[Issue] = []
    names = _source_names(session, tenant_id)
    primary = ctx.primary if ctx is not None else None
    if primary is not None:
        missing: dict[int, list[str]] = defaultdict(list)
        vms: dict[int, Vm] = {}
        for ip, vm in session.execute(
            select(VmIp.ip, Vm)
            .join(Vm, Vm.id == VmIp.vm_id)
            .where(VmIp.tenant_id == tenant_id, Vm.template.is_(False))
            .order_by(Vm.name, VmIp.ip)
        ):
            if not primary.ip_records(ip):
                missing[vm.id].append(ip)
                vms[vm.id] = vm
        for vm_id, ips in missing.items():
            vm = vms[vm_id]
            out.append(
                Issue(
                    FindingType.VM_NOT_IN_IPAM,
                    SubjectKind.VM,
                    vm.name,
                    vm.source_system_id,
                    detail={
                        "source": names.get(vm.source_system_id),
                        "ips": ips,
                        "reference": primary.source.name,
                        "cluster": vm.cluster,
                        "hostname": vm.hostname,
                    },
                )
            )
    has_aci = session.scalar(select(Epg.id).where(Epg.tenant_id == tenant_id).limit(1))
    if has_aci:
        for pg in session.scalars(
            select(Portgroup).where(Portgroup.tenant_id == tenant_id, Portgroup.epg_id.is_(None))
        ):
            parts = virtual.epg_from_name(pg.name)
            if parts is None:
                continue
            out.append(
                Issue(
                    FindingType.PORTGROUP_NO_EPG,
                    SubjectKind.PORTGROUP,
                    pg.name,
                    pg.source_system_id,
                    Severity.INFO,
                    detail={
                        "source": names.get(pg.source_system_id),
                        "tenant": parts[0],
                        "ap": parts[1],
                        "epg": parts[2],
                        "vlan": pg.vlan,
                        "vms": pg.vm_count,
                    },
                )
            )
    return out


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
