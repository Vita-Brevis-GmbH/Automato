"""Namen der Gegenstellen in der Applikations-Doku (0.95.0, ADR 0012 Ergaenzung 0.95.0).

Firewall-Regeln nennen ihre Adressen als Werte - der Collector loest die Objektnamen der
FortiGate zu IPs bzw. Netzen auf. In der Doku nach Kategorien steht bei Gegenstellen ausserhalb
der Applikation (anderer Tenant, anderes Netz, extern) deshalb oft nur die Adresse. Ist am
Mandanten `appdoc_foreign_names` gesetzt (nur der Admin), steht der Name davor:
„db01.example.ch (10.30.0.5)“. Der Name kommt - in dieser Reihenfolge - aus dem IPAM (DNS-Name
der IP bzw. Netz mit Name/Beschreibung), der VM (vCenter), der EPG (Endpunkt bzw. Subnetz der
EPG, nur eindeutig) oder dem Firewall-Objekt, ueber das die Regel die Adresse nennt.

Seit 0.101.0 (Anwender 2026-10-28: „bei den unspezifischen Zugriffen sehe ich nur die IPs der
Source“) nennen Netze und Bereiche zuerst das Objekt, wie es in der Regel steht: die Gruppe der
Firewall mit ihren Netzen in einer Zeile („G_clients (10.8.0.0/16, 10.9.0.0/16)“) statt einer
Zeile je Netz. Einzelne Hosts behalten ihren Namen aus IPAM bzw. VM.

Nur fuer Betreuer: Kunden-Benutzer sehen unabhaengig von der Einstellung weiter nur die
Adressen (`enabled`, fail-closed; die Anonymisierung `customer_alias` bleibt). Reine Anzeige -
nichts wird gespeichert, verglichen oder fuer Befunde verwendet; Gegenstellen mit eigenem Namen
(H_web01, EPG, any → …) bleiben, wie sie sind.
"""

import ipaddress
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DeviceObject,
    Epg,
    EpgEndpoint,
    EpgSubnet,
    NetworkObject,
    ObjectCategory,
    Rule,
    RuleDomain,
    RuleEndpoint,
    RuleObjectUsage,
    Tenant,
    Vm,
    VmIp,
)
from app.store import appdoc, references
from app.store import objects as objects_store
from app.store.epgs import inventory_label

SHOWN = 3  # Netze je Gruppe in einer Zeile


def enabled(session: Session, tenant_id: int | None, customer: bool) -> bool:
    """Namen zeigen? Nur fuer Betreuer und nur mit der Einstellung des Mandanten."""
    if customer or tenant_id is None:
        return False
    tenant = session.get(Tenant, tenant_id)
    return bool(tenant is not None and tenant.appdoc_foreign_names)


def tenant_of(session: Session, rows: list[dict]) -> int | None:
    """Mandant der Zeilen (Doku je System ohne gewaehlten Mandanten): der ihrer Regeln."""
    rid = next((r.get(appdoc.RULE) for r in rows if r.get(appdoc.RULE) is not None), None)
    return session.scalar(select(Rule.tenant_id).where(Rule.id == rid)) if rid else None


def _bare(obj: NetworkObject) -> bool:
    """Werte-Objekt ohne eigenen Namen (die Regel kennt nur die Adresse)."""
    return bool(obj.value) and obj.name == obj.value


def _host(value: str) -> str | None:
    try:
        net = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None
    return str(net.network_address) if net.num_addresses == 1 else None


def _net(value: str) -> str | None:
    try:
        return str(ipaddress.ip_network(value, strict=False))
    except ValueError:
        return None


def _unique(values) -> str | None:
    found = list(dict.fromkeys(v for v in values if v))
    return found[0] if len(found) == 1 else None


class _Names:
    """Namen je Adresse aus IPAM, VMs, EPGs und Firewall-Objekten - einmal geladen."""

    def __init__(self, session: Session, tenant_id: int, eps: dict[int, list]):
        values = {obj.value for items in eps.values() for _role, obj in items}
        self.hosts = {v: ip for v in values if (ip := _host(v))}
        self.nets = {v: n for v in values if v not in self.hosts and (n := _net(v))}
        self.ctx = references.context(session, tenant_id)
        ips = set(self.hosts.values())
        self.vms: dict[str, set[str]] = defaultdict(set)
        self.epg_ips: dict[str, set[str]] = defaultdict(set)
        self.epg_nets: dict[str, set[str]] = defaultdict(set)
        if ips:
            for ip, name in session.execute(
                select(VmIp.ip, Vm.name)
                .join(Vm, Vm.id == VmIp.vm_id)
                .where(VmIp.tenant_id == tenant_id, VmIp.ip.in_(ips), Vm.template.is_(False))
            ):
                self.vms[ip].add(name)
            for ip, epg in session.execute(
                select(EpgEndpoint.ip, Epg)
                .join(Epg, Epg.id == EpgEndpoint.epg_id)
                .where(Epg.tenant_id == tenant_id, Epg.kind == "epg", EpgEndpoint.ip.in_(ips))
            ):
                self.epg_ips[ip].add(inventory_label(epg))
        if self.nets:
            wanted = set(self.nets.values())
            for ip, epg in session.execute(
                select(EpgSubnet.ip, Epg)
                .join(Epg, Epg.id == EpgSubnet.epg_id)
                .where(Epg.tenant_id == tenant_id, Epg.kind == "epg")
            ):
                try:
                    net = str(ipaddress.ip_interface(ip).network)
                except ValueError:
                    continue
                if net in wanted:
                    self.epg_nets[net].add(inventory_label(epg))
        # Firewall-Objekte der Regel je (Regel, Seite, Werte-Objekt) - direkt vor Gruppen-
        # Mitgliedern; ohne Verwendung ein Adressobjekt des Mandanten mit genau diesem Wert
        obj_ids = {obj.id for items in eps.values() for _role, obj in items}
        self.used: dict[tuple[int, str, int], str] = {}
        rows = session.execute(
            select(RuleObjectUsage.rule_id, RuleObjectUsage.role, RuleObjectUsage.direct,
                   DeviceObject.name, DeviceObject.network_object_id)
            .join(DeviceObject, DeviceObject.id == RuleObjectUsage.device_object_id)
            .where(RuleObjectUsage.rule_id.in_(set(eps)),
                   RuleObjectUsage.role.in_(("src", "dst")),
                   DeviceObject.network_object_id.in_(obj_ids))
        ).all()  # fmt: skip
        for rid, role, _direct, name, oid in sorted(rows, key=lambda r: (not r[2], r[3])):
            self.used.setdefault((rid, role, oid), name)
        # Gruppen, wie sie in der Regel stehen (0.101.0): je (Regel, Seite) Name und Werte
        self.groups: dict[tuple[int, str], list[tuple[str, set[str]]]] = defaultdict(list)
        found = session.execute(
            select(RuleObjectUsage.rule_id, RuleObjectUsage.role, DeviceObject)
            .join(DeviceObject, DeviceObject.id == RuleObjectUsage.device_object_id)
            .where(RuleObjectUsage.rule_id.in_(set(eps)), RuleObjectUsage.direct.is_(True),
                   RuleObjectUsage.role.in_(("src", "dst")),
                   DeviceObject.category == ObjectCategory.ADDRESS)
        ).all()  # fmt: skip
        found = [(rid, role, o) for rid, role, o in found if o.members]
        if found:
            directory = objects_store.Directory(session, {o.source_system_id for *_, o in found})
            for rid, role, o in sorted(found, key=lambda x: x[2].name):
                values = {str(v) for m in directory.expand(o) for v in m.values or []}
                self.groups[(rid, role)].append((o.name, values))
        self.any_object: dict[int, str] = {}
        for oid, name in session.execute(
            select(DeviceObject.network_object_id, DeviceObject.name)
            .where(DeviceObject.tenant_id == tenant_id,
                   DeviceObject.category == ObjectCategory.ADDRESS,
                   DeviceObject.network_object_id.in_(obj_ids))
            .order_by(DeviceObject.name)
        ):  # fmt: skip
            self.any_object.setdefault(oid, name)

    def _ipam(self, value: str) -> str | None:
        if value in self.hosts:
            names = references.dns_names(self.ctx, self.hosts[value])
            return names[0] if names else None
        if value in self.nets:
            for idx in [self.ctx.primary, *self.ctx.enrichments]:
                rec = idx.prefix(self.nets[value]) if idx is not None else None
                if rec is not None and (rec.name or rec.description):
                    return rec.name or rec.description
        return None

    def group(self, rule_id: int, role: str, value: str) -> str | None:
        """Gruppe der Regel, in der ein Netz bzw. Bereich steht (Hosts behalten ihren Namen)."""
        if value in self.hosts:
            return None
        return next((n for n, vals in self.groups.get((rule_id, role), ()) if value in vals), None)

    def name(self, rule_id: int, role: str, obj: NetworkObject) -> str | None:
        value = obj.value or ""
        ip, net = self.hosts.get(value), self.nets.get(value)
        candidates = [
            self._ipam(value),
            _unique(self.vms.get(ip, ())) if ip else None,
            _unique(self.epg_ips.get(ip, ())) if ip else None,
            _unique(self.epg_nets.get(net, ())) if net else None,
            self.used.get((rule_id, role, obj.id)) or self.any_object.get(obj.id),
        ]
        return next((n for n in candidates if n and n not in (value, ip, net)), None)


def apply(session: Session, tenant_id: int | None, rows: list[dict]) -> int:
    """Gegenstellen der Firewall-Regeln in den Zeilen der Kategorien (`appdoc.PEER`, schon
    verteilt: auch Source/Destination) mit Namen; aendert die Zeilen, liefert ihre Zahl.
    Aufrufer pruefen vorher `enabled`."""
    if tenant_id is None:
        return 0
    rule_ids = {r.get(appdoc.RULE) for r in rows if r.get(appdoc.PEER)} - {None}
    if not rule_ids:
        return 0
    eps: dict[int, list[tuple[str, NetworkObject]]] = defaultdict(list)
    for rid, role, obj in session.execute(
        select(RuleEndpoint.rule_id, RuleEndpoint.role, NetworkObject)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .join(NetworkObject, NetworkObject.id == RuleEndpoint.network_object_id)
        .where(RuleEndpoint.rule_id.in_(rule_ids), Rule.domain == RuleDomain.FIREWALL)
    ):
        if _bare(obj):
            eps[rid].append((role.value, obj))
    if not eps:
        return 0
    names = _Names(session, tenant_id, eps)
    changed = 0
    for row in rows:
        rid, side = row.get(appdoc.RULE), row.get(appdoc.SIDE)
        mapping: dict[str, str] = {}
        grouped: dict[str, list[str]] = defaultdict(list)  # Gruppe -> ihre Netze in der Zelle
        for role, obj in eps.get(rid, ()):
            if role == side or obj.value in mapping:
                continue
            group = names.group(rid, role, obj.value)
            if group:
                grouped[group].append(obj.value)
                mapping[obj.value] = group
                continue
            name = names.name(rid, role, obj)
            if name:
                mapping[obj.value] = f"{name} ({obj.value})"
        for group, values in grouped.items():  # eine Zeile je Gruppe: „G_x (a, b, c +2)“
            more = f" +{len(values) - SHOWN}" if len(values) > SHOWN else ""
            line = f"{group} ({', '.join(values[:SHOWN])}{more})"
            for v in values:
                mapping[v] = line
        if not mapping:
            continue
        text = row.get(appdoc.PEER) or ""
        new = appdoc.lines(mapping.get(line, line) for line in text.split("\n"))
        if new != text:
            row[appdoc.PEER] = new
            if appdoc.SOURCE in row:  # schon verteilt (Doku je System)
                appdoc.place(row)
            changed += 1
    return changed
