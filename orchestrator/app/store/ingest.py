"""Persistenz + Matching der Collector-Ergebnisse (M7-Durchstich, ADR 0004).

CollectorResult (normalisierte Regeln) -> Regeln/Endpunkte/Services im Rule &
Service Store, Adress-Matching gegen den SoT-Adapter, Findings für nicht
auflösbare Objekte. Idempotent je Quellsystem: dessen Regeln werden bei jedem
Lauf ersetzt; Adress-/Service-Objekte und Findings akkumulieren (mit last_seen).

Endpunkt-Rohwerte (DTO `sources`/`destinations`) sind entweder IP/CIDR
(→ wertebasiertes Matching, search_by_ip) oder Objektnamen (→ Namenskonvention +
get_by_name). `all`/`any` → Endpunkt-Kind ANY.

Inventar-Ebene (ADR 0010): die Firewall-Objekte des Quellsystems (`device_object`) werden je
Lauf ersetzt, Regeln verweisen zusaetzlich per Objektname darauf (`rule_object_ref`).
Findings entstehen nicht mehr hier, sondern im Abgleich (`reconcile`) am Ende des Ingests.
"""

import ipaddress
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app import naming
from app.models import (
    INVENTORY_MODELS,
    SERVICE_CATEGORIES,
    DeviceObject,
    EndpointKind,
    EndpointRole,
    Epg,
    EpgEndpoint,
    EpgSubnet,
    Family,
    InvDevice,
    InvInterface,
    InvSubnet,
    InvVlan,
    InvVrf,
    MatchStatus,
    NatRule,
    NetworkObject,
    ObjectCategory,
    ObjectClass,
    Proto,
    RefRole,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    RuleObjectRef,
    ServiceObject,
    SourceOfRecord,
    Zone,
)
from app.naming import PrefixList
from app.schemas.collector import (
    CollectorResult,
    NetworkInventory,
    NormalizedNat,
    NormalizedObject,
    NormalizedRule,
)
from app.store import naming_profiles as profile_store
from app.store import reconcile

_ANY_VALUES = {"all", "any", "*"}
# Typ-praefixe, die der Collector fuer nicht-IP-aufloesbare Objekte setzt.
_TYPED_KINDS = {"fqdn", "geo", "mac", "dynamic", "range", "vip"}
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
    # NAT-/VIP-Regeln des Quellsystems ebenfalls ersetzen (aktueller Stand)
    for old_nat in session.scalars(
        select(NatRule).where(NatRule.source_system_id == run.source_system_id)
    ):
        session.delete(old_nat)
    session.flush()

    _apply_epgs(session, run.tenant_id, result.epgs)  # EPGs vor den Regeln (Endpunkt-Link)
    prefixes = _resolve_prefixes(session, run.tenant_id)  # Namensprofil des Mandanten
    device_objects = _replace_device_objects(session, run, result.objects, sot)
    for normalized in result.rules:
        _persist_rule(session, run, normalized, sot, prefixes, device_objects)
    _apply_object_catalog(session, run.tenant_id, result.objects)
    for nat in result.nat_rules:
        _persist_nat(session, run, nat)
    if result.inventory is not None:  # aeltere Collectors liefern kein Inventar
        _replace_inventory(session, run, result.inventory)
    # Findings = abgeleiteter Zustand des ganzen Mandanten (alle Quellsysteme)
    reconcile.reconcile_tenant(session, run.tenant_id)


def _apply_epgs(session: Session, tenant_id: int, epgs) -> None:
    """EPGs (ACI) idempotent anlegen/aktualisieren; Subnetze + gelernte Endpunkte je EPG
    ersetzen; EPG an den zugehoerigen Server (Host-Objekt) knuepfen, wenn aufloesbar.

    Ein APIC meldet dieselbe IP teils mehrfach je EPG (mehrere fvCEp, z. B. nach einem
    Umzug) - je EPG zaehlt eine IP einmal (erste MAC gewinnt)."""
    for e in epgs:
        epg = session.scalars(
            select(Epg).where(Epg.tenant_id == tenant_id, Epg.name == e.name)
        ).first()
        if epg is None:
            epg = Epg(tenant_id=tenant_id, name=e.name)
            session.add(epg)
        epg.ap = e.ap
        epg.bd = e.bd
        session.flush()
        for old in session.scalars(select(EpgSubnet).where(EpgSubnet.epg_id == epg.id)):
            session.delete(old)
        for old in session.scalars(select(EpgEndpoint).where(EpgEndpoint.epg_id == epg.id)):
            session.delete(old)
        # DELETE vor INSERT erzwingen: der Unit of Work fuegt sonst zuerst ein, und ein
        # erneut gelernter Endpunkt kollidiert mit seinem alten Eintrag (epg_id, ip)
        session.flush()
        for ip in dict.fromkeys(e.subnets):
            session.add(EpgSubnet(epg_id=epg.id, ip=ip, family="v6" if ":" in ip else "v4"))
        macs: dict[str, str | None] = {}
        for ep in e.endpoints:
            macs.setdefault(ep.ip, ep.mac)
        for ip, mac in macs.items():
            session.add(EpgEndpoint(epg_id=epg.id, ip=ip, mac=mac))
        # EPG <-> Host: ersten gelernten Endpunkt, der auf ein Host-Objekt zeigt, verknuepfen
        host = _host_by_ips(session, tenant_id, list(macs))
        if host is not None:
            epg.network_object_id = host.id
    session.flush()


def _host_by_ips(session: Session, tenant_id: int, ips) -> NetworkObject | None:
    for ip in ips:
        host = session.scalars(
            select(NetworkObject).where(
                NetworkObject.tenant_id == tenant_id,
                NetworkObject.object_class == ObjectClass.HOST,
                NetworkObject.value == ip,
            )
        ).first()
        if host is not None:
            return host
    return None


def _resolve_prefixes(session: Session, tenant_id: int) -> PrefixList | None:
    """Praefix-Liste aus dem Namensprofil des Mandanten (siehe profile_store)."""
    return profile_store.prefixes_for_tenant(session, tenant_id)


_DEVICE_OBJECTS = dict[tuple[str, str], DeviceObject]  # (art, name) -> Objekt; art = addr|svc


def _replace_device_objects(
    session: Session, run, objects: list[NormalizedObject], sot
) -> _DEVICE_OBJECTS:
    """Firewall-Objekte des Quellsystems ersetzen (Inventar, ADR 0010)."""
    if run.source_system_id is None or run.tenant_id is None:
        return {}
    for old in session.scalars(
        select(DeviceObject).where(DeviceObject.source_system_id == run.source_system_id)
    ):
        session.delete(old)
    session.flush()  # DELETE vor INSERT (Unique je Quellsystem/Kategorie/Name)
    now = datetime.now(UTC)
    out: _DEVICE_OBJECTS = {}
    for o in objects:
        category = ObjectCategory(o.category)
        art = "svc" if category in SERVICE_CATEGORIES else "addr"
        if (art, o.name) in out:
            continue  # gleicher Name in IPv4 und IPv6: erstes Objekt gilt
        values = list(dict.fromkeys(o.values or ([o.value] if o.value else [])))
        obj = DeviceObject(
            tenant_id=run.tenant_id,
            source_system_id=run.source_system_id,
            category=category,
            name=o.name,
            obj_type=o.obj_type,
            family=o.family,
            values=values or None,
            members=o.members or None,
            fqdn=o.fqdn,
            comment=o.comment,
            color=o.color,
            interface=o.interface,
            collector_run_id=run.id,
            last_seen=now,
        )
        if category is ObjectCategory.ADDRESS and len(values) == 1:
            obj.network_object_id = _canonical_object_id(session, run.tenant_id, values[0], sot)
        session.add(obj)
        out[(art, o.name)] = obj
    session.flush()
    return out


def _replace_inventory(session: Session, run, inv: NetworkInventory) -> None:
    """Netzinventar des Quellsystems ersetzen (Geraete, Ports, VLANs, Subnetze, VRFs)."""
    if run.source_system_id is None or run.tenant_id is None:
        return
    for model in INVENTORY_MODELS:
        session.execute(delete(model).where(model.source_system_id == run.source_system_id))
    base = {"tenant_id": run.tenant_id, "source_system_id": run.source_system_id}
    rows: list = [InvDevice(**base, **d.model_dump()) for d in inv.devices]
    for i in inv.interfaces:
        data = i.model_dump()
        for key in ("tagged_vlans", "ips", "epgs"):
            data[key] = data[key] or None
        rows.append(InvInterface(**base, **data))
    rows += [InvVlan(**base, **{**v.model_dump(), "epgs": v.epgs or None}) for v in inv.vlans]
    rows += [InvSubnet(**base, **s.model_dump()) for s in inv.subnets]
    rows += [InvVrf(**base, name=name) for name in dict.fromkeys(inv.vrfs)]
    session.add_all(rows)
    session.flush()


def _canonical_object_id(session: Session, tenant_id: int, value: str, sot) -> int | None:
    """Werte-Objekt (IP/CIDR) zu einem Firewall-Adressobjekt - fuer Matching/Referenzdaten."""
    net = _as_ip_or_net(value)
    if net is None:
        return None
    return _object_by_value(session, tenant_id, value, net, sot).id


def _apply_object_catalog(session: Session, tenant_id: int, objects) -> None:
    """Farbcode (Signal) auf bereits angelegte Adressobjekte uebertragen (Name oder Wert)."""
    for o in objects:
        if o.color is None or getattr(o, "category", "address") != "address":
            continue
        conds = [NetworkObject.name == o.name]
        if o.value:
            conds.append(NetworkObject.value == o.value)
        for obj in session.scalars(
            select(NetworkObject).where(NetworkObject.tenant_id == tenant_id, or_(*conds))
        ):
            obj.color = o.color
    session.flush()


def _persist_nat(session: Session, run, nat: NormalizedNat) -> None:
    session.add(
        NatRule(
            tenant_id=run.tenant_id,
            source_system_id=run.source_system_id,
            name=nat.name or nat.external_id,
            external_id=nat.external_id,
            nat_type=nat.nat_type,
            ext_ip=nat.ext_ip,
            ext_port=nat.ext_port,
            mapped_ip=nat.mapped_ip,
            mapped_port=nat.mapped_port,
            protocol=nat.protocol,
            portforward=nat.portforward,
            collector_run_id=run.id,
            last_seen=datetime.now(UTC),
            raw=nat.raw,
        )
    )
    session.flush()


def _persist_rule(
    session: Session,
    run,
    nr: NormalizedRule,
    sot,
    prefixes: PrefixList | None = None,
    device_objects: _DEVICE_OBJECTS | None = None,
) -> None:
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
        protections=nr.protections or None,
        aci_scope=nr.aci_scope,
        raw=nr.raw,
    )
    for raw in nr.sources:
        rule.endpoints.append(
            _endpoint(session, run.tenant_id, EndpointRole.SRC, raw, sot, prefixes)
        )
    for raw in nr.destinations:
        rule.endpoints.append(
            _endpoint(session, run.tenant_id, EndpointRole.DST, raw, sot, prefixes)
        )
    for svc in nr.services:
        rule.services.append(_service(session, svc))
    objs = device_objects or {}
    for role, art, names in (
        (RefRole.SRC, "addr", nr.source_refs),
        (RefRole.DST, "addr", nr.destination_refs),
        (RefRole.SERVICE, "svc", nr.service_refs),
    ):
        for position, name in enumerate(names):
            dobj = objs.get((art, name))
            rule.refs.append(
                RuleObjectRef(
                    role=role,
                    position=position,
                    name=name,
                    device_object_id=dobj.id if dobj is not None else None,
                )
            )
    session.add(rule)
    session.flush()


# -- Endpunkte + Matching -----------------------------------------------------


def _endpoint(
    session: Session,
    tenant_id: int,
    role: EndpointRole,
    raw: str,
    sot,
    prefixes: PrefixList | None = None,
) -> RuleEndpoint:
    value = raw.strip()
    if value.lower() in _ANY_VALUES:
        return RuleEndpoint(role=role, kind=EndpointKind.ANY, inline_value=value, matched=False)

    # ACI-EPG (Model 1): typisierter Endpunkt ohne IPAM-Aufloesung/Finding; ist die EPG
    # bekannt (epg-Tabelle), wird sie referenziert, sonst nur inline gefuehrt.
    if value.startswith("epg:"):
        epg_name = value[4:]
        epg = session.scalars(
            select(Epg).where(Epg.tenant_id == tenant_id, Epg.name == epg_name)
        ).first()
        return RuleEndpoint(
            role=role,
            kind=EndpointKind.EPG,
            epg_id=epg.id if epg else None,
            inline_value=epg_name,
            matched=epg is not None,
        )

    kind_tag, sep, rest = value.partition(":")
    if sep and kind_tag in _TYPED_KINDS:
        obj = _typed_object(session, tenant_id, kind_tag, rest or value, sot)
    else:
        net = _as_ip_or_net(value)
        if net is not None:
            obj = _object_by_value(session, tenant_id, value, net, sot)
        else:
            obj = _object_by_name(session, tenant_id, value, sot, prefixes)

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
        found = _safe_search_by_ip(sot, start_ip)  # Treffer ueber umschliessendes Prefix/Range
        _apply_match(session, tenant_id, obj, found)
        return obj

    if kind_tag == "vip":
        # Virtual IP (DNAT): "vip:<name>=<extip>". Ueber die externe IP matchen,
        # den VIP-Namen als Objektname behalten (publizierter Dienst).
        vip_name, _, extip = rest.partition("=")
        obj = _get_or_create_object(
            session, tenant_id, name=vip_name, object_class=ObjectClass.VIP, value=extip or None
        )
        if extip:
            _apply_match(session, tenant_id, obj, _safe_search_by_ip(sot, extip))
        else:
            obj.match_status = MatchStatus.NOT_APPLICABLE
            obj.last_seen = datetime.now(UTC)
            session.flush()
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


def _object_by_name(
    session: Session, tenant_id: int, value: str, sot, prefixes: PrefixList | None = None
) -> NetworkObject:
    parsed = naming.parse_object_name(value, prefixes)
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
        obj.match_status = MatchStatus.UNMATCHED  # Finding entsteht im Abgleich
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
