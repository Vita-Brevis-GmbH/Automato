"""Persistenz + Matching der Collector-Ergebnisse (M7-Durchstich, ADR 0004).

CollectorResult (normalisierte Regeln) -> Regeln/Endpunkte/Services im Rule &
Service Store, Adress-Matching gegen den SoT-Adapter, Findings für nicht
auflösbare Objekte. Idempotent je Quellsystem: dessen Regeln werden bei jedem
Lauf ersetzt; Adress-/Service-Objekte und Findings akkumulieren (mit last_seen).

Endpunkt-Rohwerte (DTO `sources`/`destinations`) sind entweder IP/CIDR
(→ wertebasiertes Matching, search_by_ip) oder Objektnamen (→ Namenskonvention +
get_by_name). `all`/`any` → Endpunkt-Kind ANY.
"""

import ipaddress
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import naming
from app.models import (
    EndpointKind,
    EndpointRole,
    Family,
    Finding,
    FindingType,
    MatchStatus,
    NetworkObject,
    ObjectClass,
    Proto,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    ServiceObject,
    SourceOfRecord,
    SubjectKind,
    Zone,
)
from app.schemas.collector import CollectorResult, NormalizedRule

_ANY_VALUES = {"all", "any", "*"}
# Typ-praefixe, die der Collector fuer nicht-IP-aufloesbare Objekte setzt.
_TYPED_KINDS = {"fqdn", "geo", "mac", "dynamic", "range"}
_DOMAIN_BY_PLATFORM = {
    "fortigate": RuleDomain.FIREWALL,
    "cisco_aci": RuleDomain.ACI_CONTRACT,
}
_KIND_BY_CLASS = {
    ObjectClass.HOST: EndpointKind.HOST,
    ObjectClass.VIP: EndpointKind.HOST,
    ObjectClass.EXTERNAL: EndpointKind.HOST,
    ObjectClass.NETWORK: EndpointKind.NETWORK,
    ObjectClass.RANGE: EndpointKind.NETWORK,
    ObjectClass.UNKNOWN: EndpointKind.HOST,
}
_SOURCE_BY_ADAPTER = {
    "netbox": SourceOfRecord.NETBOX,
    "infoblox": SourceOfRecord.INFOBLOX,
}
_PROTO_BY_NAME = {
    "tcp": Proto.TCP,
    "udp": Proto.UDP,
    "icmp": Proto.ICMP,
    "icmpv6": Proto.ICMPV6,
}


def persist_result(session: Session, run, result: CollectorResult, sot) -> None:
    """Schreibt `result` in den Store. `run` muss tenant_id + source_system_id tragen."""
    # Regeln des Quellsystems ersetzen (aktueller Stand); ORM-Delete kaskadiert Endpunkte
    for old in session.scalars(select(Rule).where(Rule.source_system_id == run.source_system_id)):
        session.delete(old)
    # Findings sind abgeleiteter Zustand -> pro Lauf neu aufbauen, sonst bleiben
    # veraltete Befunde (z. B. frueher als unmatched eingestufte fqdn/geo-Objekte)
    # stehen. TODO Multi-Source: auf das Quellsystem scopen bzw. "stale" aufloesen,
    # sobald mehrere Quellen je Mandant und ein Quittierungs-Workflow existieren.
    for old_f in session.scalars(select(Finding).where(Finding.tenant_id == run.tenant_id)):
        session.delete(old_f)
    session.flush()

    for normalized in result.rules:
        _persist_rule(session, run, normalized, sot)


def _persist_rule(session: Session, run, nr: NormalizedRule, sot) -> None:
    rule = Rule(
        tenant_id=run.tenant_id,
        source_system_id=run.source_system_id,
        domain=_DOMAIN_BY_PLATFORM.get(nr.source_platform, RuleDomain.FIREWALL),
        external_id=nr.external_id,
        name=nr.name,
        action=RuleAction.ALLOW if nr.action == "allow" else RuleAction.DENY,
        src_zone_id=_zone_id(session, run.tenant_id, nr.src_zone),
        dst_zone_id=_zone_id(session, run.tenant_id, nr.dst_zone),
        last_seen=datetime.now(UTC),
        collector_run_id=run.id,
        raw=nr.raw,
    )
    for raw in nr.sources:
        rule.endpoints.append(_endpoint(session, run.tenant_id, EndpointRole.SRC, raw, sot))
    for raw in nr.destinations:
        rule.endpoints.append(_endpoint(session, run.tenant_id, EndpointRole.DST, raw, sot))
    for svc in nr.services:
        rule.services.append(_service(session, svc))
    session.add(rule)
    session.flush()


# -- Endpunkte + Matching -----------------------------------------------------


def _endpoint(session: Session, tenant_id: int, role: EndpointRole, raw: str, sot) -> RuleEndpoint:
    value = raw.strip()
    if value.lower() in _ANY_VALUES:
        return RuleEndpoint(role=role, kind=EndpointKind.ANY, inline_value=value, matched=False)

    kind_tag, sep, rest = value.partition(":")
    if sep and kind_tag in _TYPED_KINDS:
        obj = _typed_object(session, tenant_id, kind_tag, rest or value, sot)
    else:
        net = _as_ip_or_net(value)
        if net is not None:
            obj = _object_by_value(session, tenant_id, value, net, sot)
        else:
            obj = _object_by_name(session, tenant_id, value, sot)

    return RuleEndpoint(
        role=role,
        kind=_KIND_BY_CLASS.get(obj.object_class, EndpointKind.HOST),
        network_object_id=obj.id,
        matched=obj.match_status is MatchStatus.MATCHED,
    )


def _typed_object(session: Session, tenant_id: int, kind_tag: str, rest: str, sot) -> NetworkObject:
    """Nicht-IP-Typen vom Collector (fqdn/geo/mac/dynamic/range) einordnen."""
    if kind_tag == "fqdn":
        obj = _get_or_create_object(
            session, tenant_id, name=rest, object_class=ObjectClass.EXTERNAL, fqdn=rest
        )
        obj.match_status = MatchStatus.EXPECTED_EXTERNAL  # extern erwartet, kein Finding
        obj.last_seen = datetime.now(UTC)
        session.flush()
        return obj

    if kind_tag == "range":
        obj = _get_or_create_object(
            session, tenant_id, name=rest, object_class=ObjectClass.RANGE, value=rest
        )
        start_ip = rest.split("-")[0].strip()
        found = _safe_search_by_ip(sot, start_ip)  # Treffer ueber umschliessendes Prefix
        _apply_match(session, tenant_id, obj, found)
        return obj

    # geo / mac / dynamic -> kein IPAM-Objekt -> not_applicable, kein Finding
    obj = _get_or_create_object(session, tenant_id, name=rest, object_class=ObjectClass.UNKNOWN)
    obj.match_status = MatchStatus.NOT_APPLICABLE
    obj.last_seen = datetime.now(UTC)
    session.flush()
    return obj


def _object_by_value(session: Session, tenant_id: int, value: str, net, sot) -> NetworkObject:
    is_host = getattr(net, "num_addresses", 1) == 1
    obj = _get_or_create_object(
        session,
        tenant_id,
        name=value,
        object_class=ObjectClass.HOST if is_host else ObjectClass.NETWORK,
        value=value,
        family=Family.V6 if net.version == 6 else Family.V4,
    )
    host_ip = value.split("/")[0]
    found = _safe_search_by_ip(sot, host_ip)
    _apply_match(session, tenant_id, obj, found)
    return obj


def _object_by_name(session: Session, tenant_id: int, value: str, sot) -> NetworkObject:
    parsed = naming.parse_object_name(value)
    obj = _get_or_create_object(
        session,
        tenant_id,
        name=value,
        object_class=parsed.object_class,
        fqdn=parsed.fqdn,
        domain=parsed.domain,
    )

    if parsed.object_class is ObjectClass.EXTERNAL:
        # H-EXT_: bewusst extern, kein IPAM-Match erwartet -> kein Finding
        obj.match_status = MatchStatus.EXPECTED_EXTERNAL
        obj.last_seen = datetime.now(UTC)
        session.flush()
        return obj

    if parsed.object_class is ObjectClass.UNKNOWN:
        _finding(
            session,
            tenant_id,
            FindingType.INVALID_NAME,
            obj.name,
            {"hint": "Name passt zu keinem Konventions-Praefix"},
        )

    found = _safe_get_by_name(sot, value)
    _apply_match(session, tenant_id, obj, [found] if found else [])
    return obj


def _apply_match(session: Session, tenant_id: int, obj: NetworkObject, found: list) -> None:
    if found:
        first = found[0]
        obj.match_status = MatchStatus.MATCHED
        obj.source_of_record = _SOURCE_BY_ADAPTER.get(getattr(first, "source", ""))
        if not obj.value and getattr(first, "value", None):
            obj.value = first.value
        zone_id = _zone_from_tags(session, tenant_id, getattr(first, "tags", []))
        if zone_id is not None:
            obj.zone_id = zone_id
    elif obj.match_status is not MatchStatus.EXPECTED_EXTERNAL:
        obj.match_status = MatchStatus.UNMATCHED
        _finding(
            session,
            tenant_id,
            FindingType.UNMATCHED_OBJECT,
            obj.name,
            {"value": obj.value, "object_class": obj.object_class.value},
        )
    obj.last_seen = datetime.now(UTC)
    session.flush()


# -- Objekte / Zonen / Services / Findings (get-or-create) --------------------


def _get_or_create_object(session: Session, tenant_id: int, name: str, **fields) -> NetworkObject:
    obj = session.scalars(
        select(NetworkObject).where(
            NetworkObject.tenant_id == tenant_id, NetworkObject.name == name
        )
    ).first()
    if obj is None:
        obj = NetworkObject(tenant_id=tenant_id, name=name, **fields)
        session.add(obj)
        session.flush()
    else:
        for key, val in fields.items():
            if val is not None:
                setattr(obj, key, val)
    return obj


def _zone_id(session: Session, tenant_id: int, name: str | None) -> int | None:
    if not name:
        return None
    zone = session.scalars(
        select(Zone).where(Zone.tenant_id == tenant_id, Zone.name == name)
    ).first()
    if zone is None:
        zone = Zone(tenant_id=tenant_id, name=name)
        session.add(zone)
        session.flush()
    return zone.id


def _zone_from_tags(session: Session, tenant_id: int, tags: list[str]) -> int | None:
    for tag in tags:
        if tag.startswith("zone-"):
            return _zone_id(session, tenant_id, tag.removeprefix("zone-"))
    return None


def _service(session: Session, raw: str) -> ServiceObject:
    proto, port_start, port_end, canonical = _parse_service(raw)
    svc = session.scalars(
        select(ServiceObject).where(ServiceObject.canonical_name == canonical)
    ).first()
    if svc is None:
        svc = ServiceObject(
            proto=proto, port_start=port_start, port_end=port_end, canonical_name=canonical
        )
        session.add(svc)
        session.flush()
    return svc


def _parse_service(raw: str) -> tuple[Proto, int | None, int | None, str]:
    s = raw.strip().lower()
    if "/" in s:
        proto_s, _, port_s = s.partition("/")
        proto = _PROTO_BY_NAME.get(proto_s)
        if proto in (Proto.TCP, Proto.UDP) and port_s:
            if "-" in port_s:
                lo, _, hi = port_s.partition("-")
                if lo.isdigit() and hi.isdigit():
                    return proto, int(lo), int(hi), f"{proto_s}/{lo}-{hi}"
            elif port_s.isdigit():
                port = int(port_s)
                return proto, port, port, f"{proto_s}/{port}"
        if proto in (Proto.ICMP, Proto.ICMPV6):
            return proto, None, None, f"{proto_s}/{port_s or 'any'}"
    # Fallback: Vendor-Name (z. B. "HTTPS", "ALL") unkanonisiert ablegen
    return Proto.IP, None, None, s


def _finding(
    session: Session,
    tenant_id: int,
    ftype: FindingType,
    subject_id: str,
    detail: dict,
) -> None:
    existing = session.scalars(
        select(Finding).where(
            Finding.tenant_id == tenant_id,
            Finding.type == ftype,
            Finding.subject_id == subject_id,
        )
    ).first()
    now = datetime.now(UTC)
    if existing is None:
        session.add(
            Finding(
                tenant_id=tenant_id,
                type=ftype,
                subject_kind=SubjectKind.NETWORK_OBJECT,
                subject_id=subject_id,
                detail=detail,
                first_seen=now,
                last_seen=now,
            )
        )
    else:
        existing.last_seen = now
    session.flush()


# -- Helpers ------------------------------------------------------------------


def _as_ip_or_net(value: str):
    try:
        if "/" in value:
            return ipaddress.ip_network(value, strict=False)
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _safe_search_by_ip(sot, ip: str) -> list:
    try:
        return sot.search_by_ip(ip)
    except Exception:  # noqa: BLE001 - SoT-Fehler: als "nicht gefunden" behandeln, nie crashen
        return []


def _safe_get_by_name(sot, name: str):
    try:
        return sot.get_by_name(name)
    except Exception:  # noqa: BLE001
        return None
