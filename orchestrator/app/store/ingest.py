"""Persistenz + Matching der Collector-Ergebnisse (M7-Durchstich, ADR 0004).

CollectorResult (normalisierte Regeln) -> Regeln/Endpunkte/Services im Rule &
Service Store, Adress-Matching gegen den SoT-Adapter, Findings für nicht
auflösbare Objekte. Idempotent je Quellsystem: dessen Regeln werden bei jedem
Lauf ersetzt; Adress-/Service-Objekte und Findings akkumulieren (mit last_seen).

Endpunkt-Rohwerte (DTO `sources`/`destinations`) sind entweder IP/CIDR
(→ wertebasiertes Matching, search_by_ip) oder Objektnamen (→ Namenskonvention +
get_by_name). `all`/`any` → Endpunkt-Kind ANY.

Inventar-Ebene (ADR 0010): die Firewall-Objekte des Quellsystems (`device_object`) werden je
Lauf ersetzt, Regeln verweisen zusaetzlich per Objektname darauf (`rule_object_ref`) - im
VDOM der Regel (ADR 0019: gleiche Namen in zwei VDOMs sind zwei Objekte).
Findings entstehen nicht mehr hier, sondern im Abgleich (`reconcile`) am Ende des Ingests.

Leistung (Dev-Befund grosse Regelwerke): Werte-Objekte, Zonen, Services und IPAM-Treffer
werden je Lauf einmal geladen bzw. nachgeschlagen (`_Cache`), neue Objekte gesammelt
angelegt; danach baut `rule_index` den Such-/Listenindex des Quellsystems.
"""

import ipaddress
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, select, true
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
from app.store import customers, reconcile, rule_index, topology, virtual
from app.store import naming_profiles as profile_store
from app.store import rules as rules_store

_ANY_VALUES = {"all", "any", "*"}
# Typ-praefixe, die der Collector fuer nicht-IP-aufloesbare Objekte setzt.
_TYPED_KINDS = {"fqdn", "geo", "mac", "dynamic", "range", "vip", "isdb", "user", "usergroup"}
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
    # Regeln und NAT des Quellsystems ersetzen (aktueller Stand) - per SQL, ohne jede Regel
    # mit ihren Kindern einzeln zu laden
    rules_store.delete_rules(session, Rule.source_system_id == run.source_system_id)
    session.execute(
        delete(NatRule)
        .where(NatRule.source_system_id == run.source_system_id)
        .execution_options(synchronize_session="fetch")
    )
    session.flush()

    _apply_epgs(session, run, result.epgs)  # EPGs vor den Regeln (Endpunkt-Link)
    prefixes = _resolve_prefixes(session, run.tenant_id)  # Namensprofil des Mandanten
    cache = _Cache(session, run.tenant_id, sot, prefixes)
    # Werte-Objekte aller Endpunkte und Firewall-Objekte zuerst, gesammelt angelegt
    for o in result.objects:
        values = o.values or ([o.value] if o.value else [])
        if ObjectCategory(o.category) is ObjectCategory.ADDRESS and len(values) == 1:
            cache.value_object(values[0])
    for nr in result.rules:
        for raw in [*nr.sources, *nr.destinations]:
            cache.endpoint(raw)
    session.flush()
    device_objects = _replace_device_objects(session, run, result.objects, cache)
    rules = []
    for i, normalized in enumerate(result.rules, 1):
        rules.append(_persist_rule(session, run, normalized, cache, device_objects))
        if i % 500 == 0:
            session.flush()
    session.flush()
    _apply_object_catalog(cache, result.objects)
    for nat in result.nat_rules:
        _persist_nat(session, run, nat)
    if result.nat_rules:
        session.flush()
        _vip_targets(session, run, device_objects, cache)
    if result.inventory is not None:  # aeltere Collectors liefern kein Inventar
        _replace_inventory(session, run, result.inventory)
    if result.virtual is not None:  # vCenter: VMs und Portgruppen (ADR 0017)
        virtual.replace(session, run, result.virtual)
    if result.routing is not None:  # Routing, BGP, HA, L3Out (ADR 0019 Stufe 2)
        topology.replace(session, run, result.routing)
    session.flush()
    # Findings = abgeleiteter Zustand des ganzen Mandanten (alle Quellsysteme); danach der
    # Regelindex des Quellsystems (Abgleich kann Werte aus dem IPAM ergaenzen) - nach einem
    # vCenter-Lauf der des ganzen Mandanten (VM-Namen im Suchtext)
    reconcile.reconcile_tenant(session, run.tenant_id)
    if result.virtual is not None:
        rule_index.rebuild(session, run.tenant_id)
    else:
        rule_index.rebuild(
            session, run.tenant_id, source_system_id=run.source_system_id, rules=rules
        )
    # Kunden (ADR 0018): Zugehoerigkeit ist abgeleitet - nach jedem Lauf neu
    customers.derive(session, run.tenant_id)


class _Cache:
    """Was ein Lauf immer wieder nachschlaegt - einmal geladen: Werte-Objekte des Mandanten nach
    Name, Zonen, kanonische Services, EPGs, IPAM-Treffer je IP/Name und das Ergebnis je
    Endpunkt-Rohwert. Neue Objekte werden ohne Einzel-Flush angelegt."""

    def __init__(self, session: Session, tenant_id: int, sot, prefixes: PrefixList | None):
        self.session = session
        self.tenant_id = tenant_id
        self.sot = sot
        self.prefixes = prefixes
        self.objects: dict[str, NetworkObject] = {
            o.name: o
            for o in session.scalars(
                select(NetworkObject)
                .where(NetworkObject.tenant_id == tenant_id)
                .order_by(NetworkObject.id.desc())  # bei Dubletten gilt das aelteste
            )
        }
        # VIPs je Name und externer IP: gleiche VIP-Namen in zwei VDOMs (bzw. Firewalls) mit
        # anderer IP sind verschiedene Werte-Objekte (ADR 0019)
        self.vips: dict[tuple[str, str | None], NetworkObject] = {
            (o.name, o.value): o for o in self.objects.values() if o.object_class is ObjectClass.VIP
        }
        self.zones: dict[str, Zone] = {
            z.name: z for z in session.scalars(select(Zone).where(Zone.tenant_id == tenant_id))
        }
        self.services: dict[str, ServiceObject] = {
            svc.canonical_name: svc for svc in session.scalars(select(ServiceObject))
        }
        self.epgs: dict[str, Epg | None] = {}
        self._by_ip: dict[str, list] = {}
        self._by_name: dict[str, object] = {}
        self._endpoints: dict[str, tuple] = {}
        self._by_value: dict[str, list[NetworkObject]] | None = None

    # -- Werte-Objekte ------------------------------------------------------------------

    def get_or_create(self, name: str, **fields) -> NetworkObject:
        obj = self.objects.get(name)
        if obj is None:
            obj = NetworkObject(tenant_id=self.tenant_id, name=name, **fields)
            self.session.add(obj)
            self.objects[name] = obj
            self._by_value = None
        else:
            for key, val in fields.items():
                if val is not None:
                    setattr(obj, key, val)
        return obj

    def vip(self, name: str, extip: str | None) -> NetworkObject:
        """Werte-Objekt eines VIPs mit seiner externen IP; gleicher Name mit anderer IP (anderer
        VDOM) = eigenes Objekt."""
        obj = self.vips.get((name, extip))
        if obj is None:
            same = self.objects.get(name)
            if same is not None and same.object_class is ObjectClass.VIP and not same.value:
                obj = same  # VIP ohne IP (aelterer Lauf): IP ergaenzen
                obj.value = extip
            elif same is None or same.object_class is not ObjectClass.VIP:
                obj = self.get_or_create(name, object_class=ObjectClass.VIP, value=extip)
            else:
                obj = NetworkObject(
                    tenant_id=self.tenant_id, name=name, object_class=ObjectClass.VIP, value=extip
                )
                self.session.add(obj)
                self._by_value = None
            self.vips[(name, extip)] = obj
        return obj

    def by_value(self, value: str) -> list[NetworkObject]:
        if self._by_value is None:
            self._by_value = defaultdict(list)
            for o in self.objects.values():
                if o.value:
                    self._by_value[o.value].append(o)
        return self._by_value.get(value, [])

    def search_by_ip(self, ip: str) -> list:
        if ip not in self._by_ip:
            self._by_ip[ip] = _safe_search_by_ip(self.sot, ip)
        return self._by_ip[ip]

    def get_by_name(self, name: str):
        if name not in self._by_name:
            self._by_name[name] = _safe_get_by_name(self.sot, name)
        return self._by_name[name]

    def value_object(self, value: str) -> NetworkObject | None:
        """Werte-Objekt (IP/CIDR) zu einem Firewall-Adressobjekt - fuer Matching/Referenz."""
        net = _as_ip_or_net(value)
        if net is None:
            return None
        return self.endpoint(value)[1]

    # -- Endpunkte ----------------------------------------------------------------------

    def endpoint(self, raw: str) -> tuple:
        """(Art, Werte-Objekt | None, EPG | None, inline) je Rohwert - einmal je Lauf."""
        value = raw.strip()
        if value not in self._endpoints:
            self._endpoints[value] = self._resolve(value)
        return self._endpoints[value]

    def _resolve(self, value: str) -> tuple:
        if value.lower() in _ANY_VALUES:
            return EndpointKind.ANY, None, None, value
        # ACI-EPG (Model 1): typisierter Endpunkt ohne IPAM-Aufloesung/Finding; ist die EPG
        # bekannt (epg-Tabelle), wird sie referenziert, sonst nur inline gefuehrt.
        if value.startswith("epg:"):
            ref = value[4:]  # DN (uni/tn-...) bzw. bei aelteren Collectors der EPG-Name
            return EndpointKind.EPG, None, self.epg(ref), ref[:300]
        kind_tag, sep, rest = value.partition(":")
        if sep and kind_tag in _TYPED_KINDS:
            obj = _typed_object(self, kind_tag, rest or value)
        else:
            net = _as_ip_or_net(value)
            if net is not None:
                obj = _object_by_value(self, value, net)
            else:
                obj = _object_by_name(self, value)
        return _KIND_BY_CLASS.get(obj.object_class, EndpointKind.HOST), obj, None, None

    def epg(self, ref: str) -> Epg | None:
        if ref not in self.epgs:
            cond = Epg.dn == ref if ref.startswith("uni/") else Epg.name == ref
            self.epgs[ref] = self.session.scalars(
                select(Epg).where(Epg.tenant_id == self.tenant_id, cond)
            ).first()
        return self.epgs[ref]

    # -- Zonen / Services ---------------------------------------------------------------

    def zone_id(self, name: str | None) -> int | None:
        if not name:
            return None
        zone = self.zones.get(name)
        if zone is None:
            zone = Zone(tenant_id=self.tenant_id, name=name)
            self.session.add(zone)
            self.session.flush()
            self.zones[name] = zone
        return zone.id

    def service(self, raw: str) -> ServiceObject:
        proto, port_start, port_end, canonical = _parse_service(raw)
        svc = self.services.get(canonical)
        if svc is None:
            svc = self.session.scalars(
                select(ServiceObject).where(ServiceObject.canonical_name == canonical)
            ).first()
            if svc is None:
                svc = ServiceObject(
                    proto=proto, port_start=port_start, port_end=port_end, canonical_name=canonical
                )
                self.session.add(svc)
                self.session.flush()
            self.services[canonical] = svc
        return svc


def _apply_epgs(session: Session, run, epgs) -> None:
    """EPGs (ACI) idempotent anlegen/aktualisieren; Subnetze + gelernte Endpunkte je EPG
    ersetzen; EPG an den zugehoerigen Server (Host-Objekt) knuepfen, wenn aufloesbar.

    Identitaet ist die DN (gleiche EPG-Namen in vielen ACI-Tenants); aeltere Collectors
    liefern nur den Namen. Ein APIC meldet dieselbe IP teils mehrfach je EPG (mehrere fvCEp,
    z. B. nach einem Umzug) - je EPG zaehlt eine IP einmal (erste MAC gewinnt). EPGs dieses
    APICs, die nicht mehr gemeldet werden und an keiner Regel mehr haengen, fallen weg."""
    tenant_id = run.tenant_id
    seen: set[int] = set()
    for e in epgs:
        cond = Epg.dn == e.dn if e.dn else (Epg.name == e.name) & Epg.dn.is_(None)
        epg = session.scalars(select(Epg).where(Epg.tenant_id == tenant_id, cond)).first()
        if epg is None:
            epg = Epg(tenant_id=tenant_id, name=e.name, dn=e.dn)
            session.add(epg)
        epg.name = e.name
        epg.kind = e.kind or "epg"
        epg.aci_tenant, epg.ap, epg.bd = e.tenant, e.ap, e.bd
        epg.vrf, epg.l3out = e.vrf, e.l3out
        epg.vrf_tenant = e.vrf_tenant  # aufgeloester Tenant der VRF (0.44.0)
        epg.selectors = e.selectors or None
        epg.pref_group = e.pref_group
        epg.source_system_id = run.source_system_id
        session.flush()
        seen.add(epg.id)
        _clear_epg_children(session, [epg.id])
        # DELETE vor INSERT erzwingen: der Unit of Work fuegt sonst zuerst ein, und ein
        # erneut gelernter Endpunkt kollidiert mit seinem alten Eintrag (epg_id, ip)
        session.flush()
        own = {"external": "ext", "esg": "esg"}.get(epg.kind, "epg")  # ESG: IP-Selektoren
        subnets = {(ip, own): None for ip in e.subnets}
        subnets.update({(ip, "bd"): None for ip in e.bd_subnets if (ip, own) not in subnets})
        for ip, origin in subnets:
            fam = "v6" if ":" in ip else "v4"
            # Scope: externe EPG import-security; EPG/BD shared = zwischen VRFs geteilt (0.44.0)
            scope = (e.bd_subnet_scopes if origin == "bd" else e.subnet_scopes).get(ip)
            session.add(EpgSubnet(epg_id=epg.id, ip=ip, family=fam, origin=origin, scope=scope))
        macs: dict[str, str | None] = {}
        origins: dict[str, str | None] = {}
        for ep in e.endpoints:
            macs.setdefault(ep.ip, ep.mac)
            origins.setdefault(ep.ip, ep.origin)  # static | vmm | learned (0.46.0)
        for ip, mac in macs.items():
            session.add(EpgEndpoint(epg_id=epg.id, ip=ip, mac=mac, origin=origins.get(ip)))
        # EPG <-> Host: ersten gelernten Endpunkt, der auf ein Host-Objekt zeigt, verknuepfen
        host = _host_by_ips(session, tenant_id, list(macs))
        if host is not None:
            epg.network_object_id = host.id
    session.flush()
    if any(e.dn for e in epgs):  # nur mit vollstaendigem Stand (DN) aufraeumen
        _drop_stale_epgs(session, run, seen)


def _clear_epg_children(session: Session, epg_ids: list[int]) -> None:
    session.execute(delete(EpgSubnet).where(EpgSubnet.epg_id.in_(epg_ids)))
    session.execute(delete(EpgEndpoint).where(EpgEndpoint.epg_id.in_(epg_ids)))


def _drop_stale_epgs(session: Session, run, seen: set[int]) -> None:
    """EPGs dieses APICs (bzw. Altbestand ohne DN/Quelle), die der Lauf nicht mehr meldet und
    auf die keine Regel mehr verweist (Regeln des Quellsystems sind bereits ersetzt)."""
    referenced = select(RuleEndpoint.epg_id).where(RuleEndpoint.epg_id.is_not(None))
    stale = list(
        session.scalars(
            select(Epg.id).where(
                Epg.tenant_id == run.tenant_id,
                (Epg.source_system_id == run.source_system_id) | Epg.source_system_id.is_(None),
                Epg.id.not_in(seen) if seen else true(),
                Epg.id.not_in(referenced),
            )
        )
    )
    if stale:
        _clear_epg_children(session, stale)
        session.execute(delete(Epg).where(Epg.id.in_(stale)))
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


# (VDOM, art, name) -> Objekte (IPv4 und IPv6 duerfen gleich heissen); art = addr | svc
_DEVICE_OBJECTS = dict[tuple[str | None, str, str], list[DeviceObject]]


def _replace_device_objects(
    session: Session, run, objects: list[NormalizedObject], cache: "_Cache"
) -> _DEVICE_OBJECTS:
    """Firewall-Objekte des Quellsystems ersetzen (Inventar, ADR 0010)."""
    if run.source_system_id is None or run.tenant_id is None:
        return {}
    session.execute(
        delete(DeviceObject)
        .where(DeviceObject.source_system_id == run.source_system_id)
        .execution_options(synchronize_session="fetch")
    )
    session.flush()  # DELETE vor INSERT (Unique je Quellsystem/Kategorie/Familie/Name)
    now = datetime.now(UTC)
    out: _DEVICE_OBJECTS = defaultdict(list)
    seen: set[tuple] = set()
    for o in objects:
        category = ObjectCategory(o.category)
        if (o.vdom, category, o.family, o.name) in seen:
            continue  # doppelt geliefert: erstes Objekt gilt
        seen.add((o.vdom, category, o.family, o.name))
        art = "svc" if category in SERVICE_CATEGORIES else "addr"
        values = list(dict.fromkeys(o.values or ([o.value] if o.value else [])))
        obj = DeviceObject(
            tenant_id=run.tenant_id,
            source_system_id=run.source_system_id,
            category=category,
            name=o.name,
            obj_type=o.obj_type,
            family=o.family,
            vdom=o.vdom,
            values=values or None,
            members=o.members or None,
            fqdn=o.fqdn,
            comment=o.comment,
            color=o.color,
            interface=o.interface,
            attrs=o.attrs or None,
            collector_run_id=run.id,
            last_seen=now,
        )
        if category is ObjectCategory.ADDRESS and len(values) == 1:
            canonical = cache.value_object(values[0])
            obj.network_object_id = canonical.id if canonical is not None else None
        session.add(obj)
        out[(o.vdom, art, o.name)].append(obj)
    session.flush()
    return out


def _device_object(
    objs: _DEVICE_OBJECTS, art: str, name: str, family: str | None, vdom: str | None = None
) -> DeviceObject | None:
    """Verweis -> Objekt im VDOM des Verweises: IPv6-Felder auf IPv6-Objekte, sonst IPv4 vor
    familienlosen (VIPs, Services) - aeltere Collectors ohne getrennte IPv6-Felder fallen auf
    irgendeines zurueck."""
    found = objs.get((vdom, art, name))
    if not found:
        return None
    order = {family: 0} if family else {"v4": 0, None: 1}
    return min(found, key=lambda o: order.get(o.family, 2))


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
    leaks: dict[str, list[dict]] = {}  # Route-Leaking je VRF (ACI leakRoutes, 0.44.0)
    for leak in inv.vrf_leaks:
        if leak.get("vrf") and leak.get("prefix"):
            leaks.setdefault(leak["vrf"], []).append(
                {k: leak.get(k) for k in ("prefix", "to", "external")}
            )
    rows += [
        InvVrf(**base, name=name, leaks=leaks.get(name) or None)
        for name in dict.fromkeys([*inv.vrfs, *leaks])
    ]
    session.add_all(rows)
    session.flush()


def _apply_object_catalog(cache: "_Cache", objects) -> None:
    """Farbcode (Signal) auf bereits angelegte Adressobjekte uebertragen (Name oder Wert)."""
    for o in objects:
        if o.color is None or getattr(o, "category", "address") != "address":
            continue
        targets = [cache.objects[o.name]] if o.name in cache.objects else []
        targets += cache.by_value(o.value) if o.value else []
        for obj in targets:
            obj.color = o.color


def vip_target_list(nat: NatRule) -> list[dict]:
    """Interne Ziele einer VIP: Real Server (IP bzw. Adressobjekt), FQDN-VIP (`mapped-addr`)
    oder die Mapped-IP. Je Ziel `ip` oder `address`, dazu Port und Status."""
    raw = nat.raw if isinstance(nat.raw, dict) else {}
    if nat.targets:
        return [t for t in nat.targets if t.get("ip") or t.get("address")]
    mapped_addr = raw.get("mapped-addr")
    if isinstance(mapped_addr, list):  # manche FortiOS-Versionen: [{"name": ...}]
        mapped_addr = next((m.get("name") for m in mapped_addr if isinstance(m, dict)), None)
    if mapped_addr:
        return [{"address": str(mapped_addr)}]
    if nat.mapped_ip:
        return [{"ip": nat.mapped_ip, "port": nat.mapped_port}]
    return []


def _vip_targets(session: Session, run, device_objects: _DEVICE_OBJECTS, cache: "_Cache") -> None:
    """VIP-Ziele (Dev-Befund 0.12.0): ein Adressobjekt hinter einer VIP (per Name oder mit
    genau der Ziel-IP) bekommt `vip_of`, ein Ziel ohne Objekt wird ein eigenes Objekt der
    Kategorie `vip_target`. Beides zaehlt die Regeln der VIP mit (Regelindex)."""
    now = datetime.now(UTC)
    seen: set[tuple] = set()
    created: list[tuple[DeviceObject, object]] = []
    by_value: dict[tuple[str | None, str | None, str], list[DeviceObject]] = defaultdict(list)
    for objs in device_objects.values():
        for o in objs:
            if o.category is ObjectCategory.ADDRESS and len(o.values or []) == 1:
                value = str(o.values[0]).removesuffix("/32").removesuffix("/128")
                by_value[(o.vdom, o.family, value)].append(o)

    def mark(obj: DeviceObject, vip: str) -> None:
        attrs = dict(obj.attrs or {})
        attrs["vip_of"] = sorted({*attrs.get("vip_of", []), vip})
        obj.attrs = attrs

    nats = session.scalars(
        select(NatRule).where(
            NatRule.source_system_id == run.source_system_id, NatRule.nat_type == "dnat_vip"
        )
    )
    for nat in nats:
        for t in vip_target_list(nat):
            if t.get("address"):
                obj = _device_object(
                    device_objects, "addr", str(t["address"]), nat.family, nat.vdom
                )
                if obj is not None:
                    mark(obj, nat.name)
                continue
            ip = str(t["ip"])
            existing = by_value.get((nat.vdom, nat.family, ip), [])
            if existing:  # Adressobjekt mit genau dieser IP: das ist das Ziel
                for obj in existing:
                    mark(obj, nat.name)
                continue
            port = str(t["port"]) if t.get("port") else None
            name = f"{nat.name} → {ip}{f':{port}' if port else ''}"[:300]
            if (nat.vdom, nat.family, name) in seen:
                continue
            seen.add((nat.vdom, nat.family, name))
            target = DeviceObject(
                tenant_id=run.tenant_id,
                source_system_id=run.source_system_id,
                category=ObjectCategory.VIP_TARGET,
                name=name,
                obj_type="realserver" if nat.targets else "mapped",
                family=nat.family,
                vdom=nat.vdom,
                values=[ip],
                comment=f"Internes Ziel von VIP {nat.name}",
                attrs={
                    k: v
                    for k, v in {"vip": nat.name, "port": port, "status": t.get("status")}.items()
                    if v
                },
                collector_run_id=run.id,
                last_seen=now,
            )
            session.add(target)
            created.append((target, cache.value_object(ip.split("-")[0])))
    if created:
        session.flush()  # neue Werte-Objekte (Ziel-IPs) haben jetzt eine ID
        for target, canonical in created:
            target.network_object_id = canonical.id if canonical is not None else None


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
            vip_type=nat.vip_type,
            family=nat.family,
            vdom=nat.vdom,
            targets=nat.targets or None,
            attrs=nat.attrs or None,
            collector_run_id=run.id,
            last_seen=datetime.now(UTC),
            raw=nat.raw,
        )
    )


def _persist_rule(
    session: Session,
    run,
    nr: NormalizedRule,
    cache: "_Cache",
    device_objects: _DEVICE_OBJECTS | None = None,
) -> Rule:
    rule = Rule(
        tenant_id=run.tenant_id,
        source_system_id=run.source_system_id,
        domain=_DOMAIN_BY_PLATFORM.get(nr.source_platform, RuleDomain.FIREWALL),
        external_id=nr.external_id,
        name=nr.name,
        action=RuleAction.ALLOW if nr.action == "allow" else RuleAction.DENY,
        src_zone_id=cache.zone_id(nr.src_zone),
        dst_zone_id=cache.zone_id(nr.dst_zone),
        last_seen=datetime.now(UTC),
        collector_run_id=run.id,
        protections=nr.protections or None,
        aci_scope=nr.aci_scope,
        ip_family=nr.family or _values_family([*nr.sources, *nr.destinations]),
        vdom=nr.vdom,
        raw=nr.raw,
    )
    for role, values in ((EndpointRole.SRC, nr.sources), (EndpointRole.DST, nr.destinations)):
        for raw in values:
            kind, obj, epg, inline = cache.endpoint(raw)
            rule.endpoints.append(
                RuleEndpoint(
                    role=role,
                    kind=kind,
                    network_object_id=obj.id if obj is not None else None,
                    epg_id=epg.id if epg is not None else None,
                    inline_value=inline,
                    matched=(obj.match_status is MatchStatus.MATCHED)
                    if obj is not None
                    else epg is not None,
                )
            )
    # Je Regel jeden Service einmal: verschiedene Eintraege landen oft beim selben kanonischen
    # Service (ACI-Filter `https` und `443`, Eintraege nur mit anderem Quell-Port, FortiGate-
    # Gruppe + Einzelservice) - doppelt verletzt das sonst den Schluessel von rule_service
    for svc in {s.id: s for s in (cache.service(raw) for raw in nr.services)}.values():
        rule.services.append(svc)
    objs = device_objects or {}
    split = bool(nr.source_refs6 or nr.destination_refs6 or nr.family)  # IPv6 getrennt
    for role, art, groups in (
        (RefRole.SRC, "addr", ((nr.source_refs, None), (nr.source_refs6, "v6"))),
        (RefRole.DST, "addr", ((nr.destination_refs, None), (nr.destination_refs6, "v6"))),
        (RefRole.SERVICE, "svc", ((nr.service_refs, None),)),
    ):
        position = 0
        for names, family in groups:
            for name in names:
                fam = family or ("v4" if split else None)
                dobj = _device_object(objs, art, name, fam, nr.vdom)
                rule.refs.append(
                    RuleObjectRef(
                        role=role,
                        position=position,
                        name=name,
                        family=family or (dobj.family if dobj is not None else None),
                        device_object_id=dobj.id if dobj is not None else None,
                    )
                )
                position += 1
    session.add(rule)
    return rule


def _values_family(values: list[str]) -> str | None:
    """v4 | v6 | dual aus den Endpunkt-Werten (aeltere Collectors, ACI)."""
    versions = set()
    for v in values:
        tag, sep, rest = v.partition(":")
        net = _as_ip_or_net(v) if not sep or tag not in _TYPED_KINDS else None
        if tag == "range" and sep:
            net = _as_ip_or_net(rest.split("-")[0].strip())
        if net is not None:
            versions.add(net.version)
    if versions == {4, 6}:
        return "dual"
    return {frozenset({4}): "v4", frozenset({6}): "v6"}.get(frozenset(versions))


# -- Endpunkte + Matching -----------------------------------------------------


def _typed_object(cache: "_Cache", kind_tag: str, rest: str) -> NetworkObject:
    """Nicht-IP-Typen vom Collector (fqdn/geo/mac/dynamic/range) einordnen."""
    now = datetime.now(UTC)
    if kind_tag == "isdb":
        # FortiGate Internet Service (z. B. Microsoft-Office365): bewusst extern
        obj = cache.get_or_create(rest, object_class=ObjectClass.EXTERNAL)
        obj.match_status = MatchStatus.EXPECTED_EXTERNAL
        obj.last_seen = now
        return obj

    if kind_tag == "fqdn":
        obj = cache.get_or_create(rest, object_class=ObjectClass.EXTERNAL, fqdn=rest)
        obj.match_status = MatchStatus.EXPECTED_EXTERNAL  # extern erwartet, kein Finding
        obj.last_seen = now
        return obj

    if kind_tag == "range":
        obj = cache.get_or_create(rest, object_class=ObjectClass.RANGE, value=rest)
        start_ip = rest.split("-")[0].strip()
        found = cache.search_by_ip(start_ip)  # Treffer ueber umschliessendes Prefix/Range
        _apply_match(cache, obj, found)
        return obj

    if kind_tag == "vip":
        # Virtual IP (DNAT): "vip:<name>=<extip>". Ueber die externe IP matchen,
        # den VIP-Namen als Objektname behalten (publizierter Dienst).
        vip_name, _, extip = rest.partition("=")
        obj = cache.vip(vip_name, extip or None)
        if extip:
            _apply_match(cache, obj, cache.search_by_ip(extip))
        else:
            obj.match_status = MatchStatus.NOT_APPLICABLE
            obj.last_seen = now
        return obj

    # geo / mac / dynamic / user / usergroup -> kein IPAM-Objekt -> not_applicable, kein Finding
    obj = cache.get_or_create(rest, object_class=ObjectClass.UNKNOWN)
    obj.match_status = MatchStatus.NOT_APPLICABLE
    obj.last_seen = now
    return obj


def _object_by_value(cache: "_Cache", value: str, net) -> NetworkObject:
    is_host = getattr(net, "num_addresses", 1) == 1
    obj = cache.get_or_create(
        value,
        object_class=ObjectClass.HOST if is_host else ObjectClass.NETWORK,
        value=value,
        family=Family.V6 if net.version == 6 else Family.V4,
    )
    _apply_match(cache, obj, cache.search_by_ip(value.split("/")[0]))
    return obj


def _object_by_name(cache: "_Cache", value: str) -> NetworkObject:
    parsed = naming.parse_object_name(value, cache.prefixes)
    obj = cache.get_or_create(
        value,
        object_class=parsed.object_class,
        fqdn=parsed.fqdn,
        domain=parsed.domain,
    )

    if parsed.object_class is ObjectClass.EXTERNAL:
        # H-EXT_: bewusst extern, kein IPAM-Match erwartet -> kein Finding
        obj.match_status = MatchStatus.EXPECTED_EXTERNAL
        obj.last_seen = datetime.now(UTC)
        return obj

    # Im IPAM steht der Server ohne Praefix (H_web01 -> web01[.domain])
    found = cache.get_by_name(value)
    if found is None and parsed.remainder and parsed.remainder != value:
        found = cache.get_by_name(parsed.remainder)
    _apply_match(cache, obj, [found] if found else [])
    return obj


def _apply_match(cache: "_Cache", obj: NetworkObject, found: list) -> None:
    if found:
        first = found[0]
        obj.match_status = MatchStatus.MATCHED
        obj.source_of_record = _SOURCE_BY_ADAPTER.get(getattr(first, "source", ""))
        if not obj.value and getattr(first, "value", None):
            obj.value = first.value
        zone_id = _zone_from_tags(cache, getattr(first, "tags", []))
        if zone_id is not None:
            obj.zone_id = zone_id
    elif obj.match_status is not MatchStatus.EXPECTED_EXTERNAL:
        obj.match_status = MatchStatus.UNMATCHED  # Finding entsteht im Abgleich
    obj.last_seen = datetime.now(UTC)


# -- Zonen / Services ---------------------------------------------------------


def _zone_from_tags(cache: "_Cache", tags: list[str]) -> int | None:
    for tag in tags:
        if tag.startswith("zone-"):
            return cache.zone_id(tag.removeprefix("zone-"))
    return None


def zone_from_tags(session: Session, tenant_id: int, tags: list[str]) -> int | None:
    """Zone aus IPAM-Tags `zone-<name>` ausserhalb eines Laufs (Abgleich gegen den Stand)."""
    for tag in tags:
        if tag.startswith("zone-"):
            name = tag.removeprefix("zone-")
            zone = session.scalars(
                select(Zone).where(Zone.tenant_id == tenant_id, Zone.name == name)
            ).first()
            if zone is None:
                zone = Zone(tenant_id=tenant_id, name=name)
                session.add(zone)
                session.flush()
            return zone.id
    return None


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
