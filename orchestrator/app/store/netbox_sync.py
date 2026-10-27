"""NetBox-Sync (ADR 0010): Soll-Stand aus Automato -> Vorschau -> Uebernahme per Klick.

Einziger Schreibweg der Plattform. Ablauf:

1. **Soll** (`build_wants`): aus dem Automato-Bestand eines Mandanten
   - Netzinventar (Abschnitt `inventory`): Geraete mit Ports (Modus, VLANs, LAG, Parent,
     Interface-IPs), VLANs je Quellsystem (VLAN-Gruppe), reale Subnetze (inkl. ACI
     BD/EPG/VRF), VRFs - Automato ist hier fuehrend;
   - DDI-Spiegel (Abschnitt `mirror`): Netze, Ranges und IPs mit DNS-Name aus dem
     fuehrenden IPAM (z. B. Infoblox), wenn die Ziel-NetBox nicht selbst fuehrend ist;
   - Virtualisierung (Abschnitt `virtualization`, ADR 0017): VMs aus dem vCenter mit Cluster,
     Cluster-Gruppe (Datacenter), VM-Interfaces und ihren IPs;
   - Verkabelung (Abschnitt `cabling`, ADR 0031): gelesene Switches/Router mit Ports und Kabel
     zwischen zwei Ports, die beide Seiten per CDP/LLDP melden;
   - Applikationen (Abschnitt `applications`, ADR 0031): je Applikation ein Tag mit Praefix
     `automato-app-` an den VMs und IP-Adressen ihrer Systeme.
   Die beiden letzten nur, wenn ausdruecklich gewaehlt (`DEFAULT_SECTIONS`).
2. **Ist** (`read_state`): NetBox lesen (nur GET); danach `resolve_after_read` (VLANs der
   Switch-Ports, Kabel und Tags nur an Objekten, die es in NetBox gibt bzw. die der Sync anlegt).
3. **Vorschau** (`diff`): je Objekt anlegen | aendern | unveraendert | Konflikt | loeschen.
   Geaendert/geloescht wird nur, was das Tag `automato` traegt; von Hand gepflegte Objekte
   werden hoechstens referenziert - weichen sie ab, ist das ein *Konflikt* (bleibt, wie es
   ist). Loeschen nur mit `allow_delete`, sonst als *veraltet* angezeigt.
4. **Uebernahme** (`apply`): Plan neu berechnen, Fingerabdruck muss zur Vorschau passen,
   dann Bulk-Schreiben in Abhaengigkeitsreihenfolge; Loeschen in umgekehrter Reihenfolge.

Sprache (ADR 0028): Der Plan wird immer deutsch berechnet - was nach NetBox geht, Bezeichnungen,
Hinweise und Meldungen sind am Plan gespeichert. Die Anzeige uebersetzt bekannte Formen erst bei
der Ausgabe (`kind_label`, `show_label`, `show_warning`, `shown`).
"""

import hashlib
import ipaddress
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.i18n import L, lang
from app.models import (
    Application,
    Epg,
    InvDevice,
    InvInterface,
    InvSubnet,
    InvVlan,
    InvVrf,
    Platform,
    Site,
    SourceSystem,
    Tenant,
    Vm,
)
from app.store import cabling as cabling_store
from app.store import references
from app.store import sites as site_store
from app.store import virtual as virtual_store
from app.store.epgs import inventory_label

TAG = "automato"
SECTIONS = ("inventory", "mirror", "virtualization", "cabling", "applications")
# ohne Angabe die bisherigen Abschnitte; Verkabelung/Applikationen nur ausdruecklich (ADR 0031)
DEFAULT_SECTIONS = ("inventory", "mirror", "virtualization")
INVENTORY_PLATFORMS = (Platform.FORTIGATE, Platform.CISCO_ACI)
CABLING_PLATFORMS = (Platform.CISCO_SWITCH,)  # Switches & Router (ADR 0025)
# Applikations-Tags (ADR 0031): `automato-app-<Mandant>-<Kuerzel bzw. Name>`; das Praefix gehoert
# Automato - nur solche Tags setzt bzw. entfernt der Sync, fremde Tags bleiben
APP_TAG_PREFIX = "automato-app-"
WRITE_ACTIONS = ("create", "update", "delete")
ACTIONS = ("create", "update", "unchanged", "conflict", "delete", "obsolete")


@dataclass(frozen=True)
class Kind:
    name: str
    endpoint: str
    label: str
    managed: tuple[str, ...]  # Felder, die der Sync aktuell haelt (Aenderung/Konflikt)
    scope: str = ""  # "" = nie loeschen | tenant | group | device | vm | app (eigene App-Tags)
    # nur an eigenen Objekten (Tag automato) gehalten; an von Hand gepflegten kein Konflikt
    owned: tuple[str, ...] = ()


# Reihenfolge = Abhaengigkeiten (Anlegen vorwaerts, Loeschen rueckwaerts). Tags tragen selbst
# keinen Tag: eigen sind die Applikations-Tags des Mandanten (Praefix, `read_state`).
KINDS = (
    Kind("tag", "extras/tags", "Tag", (), "app", owned=("name", "color", "description")),
    Kind("tenant", "tenancy/tenants", "Tenant", ("name",)),
    Kind("site_group", "dcim/site-groups", "Standort-Gruppe", ("name", "description")),
    Kind("site", "dcim/sites", "Standort", ("name",), owned=("group", "comments")),
    Kind("manufacturer", "dcim/manufacturers", "Hersteller", ()),
    Kind("device_role", "dcim/device-roles", "Geräterolle", ()),
    Kind("device_type", "dcim/device-types", "Gerätetyp", ()),
    Kind("cluster_type", "virtualization/cluster-types", "Cluster-Typ", ()),
    Kind(
        "cluster_group",
        "virtualization/cluster-groups",
        "Cluster-Gruppe",
        ("name", "description"),
        "group",
    ),
    Kind(
        "cluster",
        "virtualization/clusters",
        "Cluster",
        ("tenant",),
        "tenant",
        owned=("type", "group", "status", "description"),
    ),
    Kind("vrf", "ipam/vrfs", "VRF", ("tenant",), "tenant"),
    Kind("vlan_group", "ipam/vlan-groups", "VLAN-Gruppe", ("name", "description"), "group"),
    Kind("vlan", "ipam/vlans", "VLAN", ("name", "tenant", "description", "status"), "tenant"),
    Kind(
        "prefix",
        "ipam/prefixes",
        "Netz",
        ("vrf", "vlan", "tenant", "description", "status"),
        "tenant",
    ),
    Kind("ip_range", "ipam/ip-ranges", "Range", ("tenant", "description", "status"), "tenant"),
    Kind(
        "device",
        "dcim/devices",
        "Gerät",
        ("device_type", "role", "site", "tenant", "serial", "comments", "status"),
        "tenant",
    ),
    Kind(
        "interface",
        "dcim/interfaces",
        "Port",
        (
            "type",
            "enabled",
            "mode",
            "untagged_vlan",
            "tagged_vlans",
            "lag",
            "parent",
            "description",
        ),
        "device",
    ),
    # Kabel zwischen zwei Ports (Abschnitt Verkabelung, ADR 0031); Schluessel = beide Enden
    Kind("cable", "dcim/cables", "Kabel", ("status",), "tenant", owned=("tenant", "description")),
    Kind(
        "virtual_machine",
        "virtualization/virtual-machines",
        "VM",
        ("cluster", "tenant", "status", "vcpus", "memory", "disk"),
        "tenant",
        owned=("comments",),
    ),
    Kind(
        "vm_interface",
        "virtualization/interfaces",
        "VM-Interface",
        ("enabled",),
        "vm",
        owned=("description",),
    ),
    Kind(
        "ip_address",
        "ipam/ip-addresses",
        "IP-Adresse",
        ("address", "tenant", "dns_name", "description", "status", "assigned_object_id"),
        "tenant",
    ),
    # Applikations-Tags an bestehenden bzw. im selben Sync angelegten Objekten (ADR 0031): kein
    # eigenes NetBox-Objekt, sondern die Tags des Ziels (PATCH) - zuletzt, wenn alles angelegt ist
    Kind("app_tag_vm", "virtualization/virtual-machines", "Applikations-Tag an VM", ("tags",)),
    Kind("app_tag_ip", "ipam/ip-addresses", "Applikations-Tag an IP-Adresse", ("tags",)),
)
KIND = {k.name: k for k in KINDS}
# nur gelesen/geloescht, wenn der Abschnitt gewaehlt ist (sonst waeren alle VMs "veraltet")
VIRTUAL_KINDS = frozenset(
    {"cluster_type", "cluster_group", "cluster", "virtual_machine", "vm_interface"}
)
ASSIGN = {"app_tag_vm": "virtual_machine", "app_tag_ip": "ip_address"}  # Zuordnung -> Ziel-Art
TERMINATIONS = ("a_terminations", "b_terminations")  # Enden eines Kabels (je ein Port)

Ref = tuple[str, str]  # (Art, Schluessel)


@dataclass
class Want:
    kind: str
    key: str
    label: str
    fields: dict[str, Any]
    refs: dict[str, Ref | list[Ref] | None] = field(default_factory=dict)
    # nur an eigenen Objekten gehalten; ein von Hand gepflegtes wird nur referenziert
    soft: tuple[str, ...] = ()


@dataclass
class Have:
    id: int
    key: str
    obj: dict[str, Any]
    tagged: bool


State = dict[str, dict[str, Have]]


# -- Hilfen ---------------------------------------------------------------------

_SLUG = re.compile(r"[^a-z0-9_-]+")
_DNS = re.compile(r"^([0-9A-Za-z_-]+|\*)(\.[0-9A-Za-z_-]+)*\.?$")


def slugify(value: str) -> str:
    return _SLUG.sub("-", value.lower()).strip("-")[:100] or "x"


def _net(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_network(str(value).strip(), strict=False))
    except ValueError:
        return None


def _ip(value: str | None) -> str | None:
    try:
        return ipaddress.ip_interface(str(value).strip()).ip.compressed
    except ValueError:
        return None


def _iface(value: str | None) -> ipaddress.IPv4Interface | ipaddress.IPv6Interface | None:
    try:
        return ipaddress.ip_interface(str(value).strip())
    except ValueError:
        return None


def _dns(value: str | None) -> str:
    name = (value or "").strip().rstrip(".")
    return name if name and len(name) <= 255 and _DNS.match(name) else ""


def _prefix_status(r) -> str:
    return "container" if r.status == "container" else "active"


def _cut(value: str | None, n: int) -> str:
    return (value or "")[:n]


def _join(*parts: str | None) -> str:
    """Teile mit " · " verbinden; auch bereits verbundene Teile ohne Doppelte."""
    seen: list[str] = []
    for part in parts:
        for p in (part or "").split(" · "):
            p = p.strip()
            if p and p not in seen:
                seen.append(p)
    return " · ".join(seen)


# -- Soll -------------------------------------------------------------------------


@dataclass
class PortVlans:
    """VLANs eines Switch-Ports (Verkabelung): aufgeloest erst nach dem Lesen von NetBox -
    Modus und VLANs nur, wenn die noetigen VLANs dort eindeutig vorhanden sind (ADR 0031)."""

    key: str  # Want-Schluessel des Ports (`<Geraet>/<Port>`)
    device: str
    site: str  # Slug des Standorts des Geraets
    group: str  # VLAN-Gruppe des Quellsystems (Slug) - zuerst gesucht
    mode: str  # access | tagged | tagged-all
    untagged: int | None  # Access-VLAN (Pflicht) bzw. Native VLAN (nur wenn vorhanden)
    required: list[int] = field(default_factory=list)  # Trunk: erlaubte VLANs des Switches
    optional: list[int] = field(default_factory=list)  # Trunk: VLAN 1 (nur wenn vorhanden)


@dataclass
class Wants:
    items: dict[tuple[str, str], Want] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    ports: list[PortVlans] = field(default_factory=list)  # Verkabelung: VLANs nach dem Lesen
    cable_ends: set[str] = field(default_factory=set)  # Geraete an Kabeln: Ports in NetBox lesen

    def add(self, want: Want) -> Want:
        return self.items.setdefault((want.kind, want.key), want)

    def get(self, kind: str, key: str) -> Want | None:
        return self.items.get((kind, key))

    def has(self, kind: str, key: str) -> bool:
        return (kind, key) in self.items

    def keys_of(self, kind: str) -> set[str]:
        return {k for (kd, k) in self.items if kd == kind}

    def ordered(self) -> list[Want]:
        order = {k.name: i for i, k in enumerate(KINDS)}
        return sorted(self.items.values(), key=lambda w: (order[w.kind], w.key))


_MANUFACTURER = {Platform.FORTIGATE: "Fortinet", Platform.CISCO_ACI: "Cisco",
                 Platform.CISCO_SWITCH: "Cisco"}  # fmt: skip
_IFACE_TYPE = {"physical": "other", "lag": "lag"}


def tenant_slug(tenant: Tenant) -> str:
    return slugify(tenant.name)


def group_slug(tenant: Tenant, source: SourceSystem) -> str:
    return slugify(f"{tenant_slug(tenant)}-{source.name}")


def build_wants(
    session: Session, tenant: Tenant, target: SourceSystem, sections: list[str]
) -> Wants:
    """Soll-Stand der Ziel-NetBox fuer einen Mandanten."""
    w = Wants()
    tslug = tenant_slug(tenant)
    tenant_ref: Ref = ("tenant", tslug)
    w.add(
        Want(
            "tag",
            TAG,
            "Tag automato",
            {
                "name": TAG,
                "slug": TAG,
                "color": "1b998b",
                "description": "Von Automato verwaltet (ADR 0010) - nicht von Hand ändern",
            },
        )
    )
    w.add(
        Want(
            "tenant",
            tslug,
            f"Tenant {tenant.name}",
            {
                "name": _cut(tenant.name, 100),
                "slug": tslug,
                "description": _cut(f"Automato-Mandant {tenant.code or ''}".strip(), 200),
            },
        )
    )
    index = site_store.SiteIndex(session, tenant.id)
    sites = index.by_id
    for s in sites.values():
        _site_want(w, tenant, s, index)
    if "inventory" in sections:
        _inventory_wants(session, w, tenant, sites, tenant_ref)
    if "mirror" in sections:
        _mirror_wants(session, w, tenant, target, tenant_ref)
    if "virtualization" in sections:
        _virtualization_wants(session, w, tenant, tenant_ref)
    if "cabling" in sections:
        _cabling_wants(session, w, tenant, sites, tenant_ref)
    if "applications" in sections:
        _application_wants(session, w, tenant)
    return w


# Virtuelle und physische Standorte (ADR 0015): beide als NetBox-Site, unterschieden durch die
# Standort-Gruppe; welcher virtuelle an welchen physischen laeuft, steht im Kommentar.
SITE_GROUPS = {
    "virtual": ("standorte-virtuell", "Virtuelle Standorte", "Automato: virtuelle Standorte"),
    "physical": ("standorte-physisch", "Physische Standorte", "Automato: physische Standorte"),
}


def _site_comments(tenant: Tenant, s: Site, index: site_store.SiteIndex) -> str:
    def names(codes: list[str]) -> str:
        return ", ".join(
            f"{c} ({index.by_code[c.lower()].name})" if c.lower() in index.by_code else c
            for c in codes
        )

    ref = index.ref(s) or {}
    if s.kind == "virtual":
        head = f"Virtueller Standort {s.code} von {tenant.name}."
        tail = (
            f"Läuft an den physischen Standorten: {names(ref.get('physical', []))}."
            if ref.get("physical")
            else "Keinem physischen Standort zugeordnet."
        )
    else:
        head = f"Physischer Standort {s.code} von {tenant.name}."
        tail = (
            f"Virtuelle Standorte hier: {names(ref.get('virtual', []))}."
            if ref.get("virtual")
            else "Kein virtueller Standort zugeordnet."
        )
    return f"{head} {tail} (Automato)"


def _site_want(w: Wants, tenant: Tenant, s: Site, index: site_store.SiteIndex) -> None:
    gslug, gname, gdesc = SITE_GROUPS.get(s.kind, SITE_GROUPS["virtual"])
    w.add(Want("site_group", gslug, gname, {"name": gname, "slug": gslug, "description": gdesc}))
    w.add(
        Want(
            "site",
            slugify(s.code),
            f"Standort {s.code} {s.name} ({site_store.KIND_LABEL.get(s.kind, s.kind)})",
            {
                "name": _cut(s.name or s.code, 100),
                "slug": slugify(s.code),
                "status": "active",
                "description": _cut(s.description, 200),
                "comments": _site_comments(tenant, s, index),
            },
            {"group": ("site_group", gslug)},
        )
    )


def _default_site(sites: dict[int, Site]) -> Site | None:
    """Standort ohne Angabe am Quellsystem: der einzige physische, sonst der einzige ueberhaupt."""
    physical = [s for s in sites.values() if s.kind == "physical"]
    if len(physical) == 1:
        return physical[0]
    return next(iter(sites.values())) if len(sites) == 1 else None


def _rows(session: Session, model, source: SourceSystem) -> list:
    return list(
        session.scalars(select(model).where(model.source_system_id == source.id).order_by(model.id))
    )


def _no_site_warning(w: Wants, src: SourceSystem, n: int) -> None:
    w.warnings.append(
        f"{src.name}: kein Standort gesetzt – {n} Geräte und ihre Ports werden"
        " nicht übernommen (Standort am Quellsystem wählen)."
    )


def _device_want(
    w: Wants, d: InvDevice, src: SourceSystem, site: Site, manufacturer: str, model: str,
    tenant_ref: Ref,
) -> None:  # fmt: skip
    """Geraet mit Hersteller, Rolle und Geraetetyp - gleich fuer Netzinventar und Verkabelung.
    Der Kommentar „Automato: gelesen aus <Quellsystem>“ zeigt den Abschnitt (`_device_section`)."""
    role = slugify(d.role or "device")
    w.add(
        Want(
            "manufacturer",
            slugify(manufacturer),
            f"Hersteller {manufacturer}",
            {"name": manufacturer, "slug": slugify(manufacturer)},
        )
    )
    w.add(
        Want(
            "device_role",
            role,
            f"Geräterolle {d.role}",
            {
                "name": _cut((d.role or "device").capitalize(), 100),
                "slug": role,
                "color": "607d8b",
            },
        )
    )
    w.add(
        Want(
            "device_type",
            model.lower(),
            f"Gerätetyp {model}",
            {"model": _cut(model, 100), "slug": slugify(model)},
            {"manufacturer": ("manufacturer", slugify(manufacturer))},
        )
    )
    comments = "\n".join(
        x
        for x in (
            f"Automato: gelesen aus {src.name}",
            f"Version {d.version}" if d.version else None,
            f"Node {d.node_id}" if d.node_id else None,
            f"Mgmt-IP {d.mgmt_ip}" if d.mgmt_ip else None,
        )
        if x
    )
    w.add(
        Want(
            "device",
            d.name,
            f"Gerät {d.name}",
            {
                "name": _cut(d.name, 64),
                "serial": _cut(d.serial, 50),
                "status": "active",
                "comments": comments,
            },
            {
                "device_type": ("device_type", model.lower()),
                "role": ("device_role", role),
                "site": ("site", slugify(site.code)),
                "tenant": tenant_ref,
            },
        )
    )


def _inventory_wants(
    session: Session, w: Wants, tenant: Tenant, sites: dict[int, Site], tenant_ref: Ref
) -> None:
    sources = list(
        session.scalars(
            select(SourceSystem)
            .where(
                SourceSystem.tenant_id == tenant.id,
                SourceSystem.platform.in_(INVENTORY_PLATFORMS),
            )
            .order_by(SourceSystem.id)
        )
    )
    only_site = _default_site(sites)

    def rows(model, source):
        return _rows(session, model, source)

    for src in sources:
        gslug = group_slug(tenant, src)
        vlans = rows(InvVlan, src)
        vids: set[int] = set()
        if vlans:
            w.add(
                Want(
                    "vlan_group",
                    gslug,
                    f"VLAN-Gruppe {src.name}",
                    {
                        "name": _cut(f"{src.name} ({tenant.name})", 100),
                        "slug": gslug,
                        "description": _cut(f"Automato: VLANs von {src.name}", 200),
                    },
                )
            )
        for v in vlans:
            vids.add(v.vid)
            w.add(
                Want(
                    "vlan",
                    f"{gslug}/{v.vid}",
                    f"VLAN {v.vid} {v.name} ({src.name})",
                    {
                        "vid": v.vid,
                        "name": _cut(v.name or f"VLAN{v.vid}", 64),
                        "status": "active",
                        "description": _cut(
                            _join(v.description, f"EPG {', '.join(v.epgs)}" if v.epgs else None),
                            200,
                        ),
                    },
                    {"group": ("vlan_group", gslug), "tenant": tenant_ref},
                )
            )

        def vlan_ref(vid: int | None, _g=gslug, _v=vids) -> Ref | None:
            return ("vlan", f"{_g}/{vid}") if vid is not None and vid in _v else None

        for vrf in rows(InvVrf, src):
            _vrf_want(w, vrf.name, tenant_ref)
        for sub in rows(InvSubnet, src):
            net = _net(sub.prefix)
            if net is None:
                continue
            if sub.vrf:
                _vrf_want(w, sub.vrf, tenant_ref)
            if sub.context == "interface":
                desc = f"Interface {sub.context_name} ({src.name})"
            else:
                path = "/".join(x for x in (sub.aci_tenant, sub.ap, sub.epg) if x)
                desc = _join(f"{sub.context.upper()} {sub.context_name}", path or None)
            existing = w.get("prefix", net)
            if existing is not None:
                existing.fields["description"] = _cut(
                    _join(existing.fields["description"], sub.description, desc), 200
                )
                continue
            w.add(
                Want(
                    "prefix",
                    net,
                    f"Netz {net}",
                    {
                        "prefix": net,
                        "status": "active",
                        "description": _cut(_join(sub.description, desc), 200),
                    },
                    {
                        "vrf": ("vrf", sub.vrf) if sub.vrf else None,
                        "vlan": vlan_ref(sub.vlan),
                        "tenant": tenant_ref,
                    },
                )
            )

        site = sites.get(src.site_id) if src.site_id else only_site
        devices = rows(InvDevice, src)
        if devices and site is None:
            _no_site_warning(w, src, len(devices))
            continue
        manufacturer = _MANUFACTURER.get(src.platform, "Unbekannt")
        for d in devices:
            if w.has("device", d.name):
                w.warnings.append(f"Gerät {d.name} kommt mehrfach vor – {src.name} übersprungen.")
                continue
            model = d.model or f"{manufacturer} {d.role or 'device'}"
            _device_want(w, d, src, site, manufacturer, model, tenant_ref)
        names = {d.name for d in devices}
        ifaces = [i for i in rows(InvInterface, src) if i.device in names]
        by_device: dict[str, set[str]] = defaultdict(set)
        for i in ifaces:
            by_device[i.device].add(i.name)
        for i in ifaces:
            key = f"{i.device}/{i.name}"
            mode = i.mode if i.mode in ("access", "tagged") else None
            untagged = vlan_ref(i.untagged_vlan) if mode else None
            tagged = (
                [r for r in (vlan_ref(v) for v in i.tagged_vlans or []) if r]
                if mode == "tagged"
                else []
            )
            w.add(
                Want(
                    "interface",
                    key,
                    f"Port {key}",
                    {
                        "name": _cut(i.name, 64),
                        "type": _IFACE_TYPE.get(i.kind, "virtual"),
                        "enabled": i.enabled is not False,
                        "mode": mode or "",
                        "description": _cut(
                            _join(
                                i.description,
                                f"Zone {i.zone}" if i.zone else None,
                                f"Policy {i.policy_group}" if i.policy_group else None,
                                f"Nachbar {i.neighbor}:{i.neighbor_port or ''}"
                                if i.neighbor
                                else None,
                                f"EPG {', '.join(i.epgs)}" if i.epgs else None,
                            ),
                            200,
                        ),
                    },
                    {
                        "device": ("device", i.device),
                        "untagged_vlan": untagged,
                        "tagged_vlans": tagged,
                        "lag": ("interface", f"{i.device}/{i.lag}")
                        if i.lag and i.lag in by_device[i.device] and i.lag != i.name
                        else None,
                        "parent": ("interface", f"{i.device}/{i.parent}")
                        if i.parent and i.parent in by_device[i.device] and i.parent != i.name
                        else None,
                    },
                )
            )
            for cidr in i.ips or []:
                iface = _iface(cidr)
                if iface is None:
                    continue
                ip = iface.ip.compressed
                w.add(
                    Want(
                        "ip_address",
                        ip,
                        f"IP {ip} ({key})",
                        {
                            "address": iface.with_prefixlen,
                            "status": "active",
                            "dns_name": "",
                            "description": _cut(f"Interface {i.name} ({i.device})", 200),
                            "assigned_object_type": "dcim.interface",
                        },
                        {"assigned_object_id": ("interface", key), "tenant": tenant_ref},
                    )
                )


def _vrf_want(w: Wants, name: str, tenant_ref: Ref) -> None:
    w.add(
        Want(
            "vrf",
            name,
            f"VRF {name}",
            {"name": _cut(name, 100), "description": "Automato"},
            {"tenant": tenant_ref},
        )
    )


def _mirror_wants(
    session: Session, w: Wants, tenant: Tenant, target: SourceSystem, tenant_ref: Ref
) -> None:
    ctx = references.context(session, tenant.id)
    primary = ctx.primary
    if primary is None or primary.source.id == target.id:
        w.warnings.append(
            "DDI-Spiegel: kein anderes führendes IPAM – diese NetBox ist selbst führend"
            " oder es ist kein IPAM eingelesen."
        )
        return
    src = primary.source.name
    for value, r in sorted(primary.prefix_by_value.items()):
        existing = w.get("prefix", value)
        desc = _cut(_join(r.description, f"Spiegel {src}"), 200)
        if existing is not None:
            existing.fields["description"] = _cut(
                _join(r.description, existing.fields["description"]), 200
            )
            continue
        w.add(
            Want(
                "prefix",
                value,
                f"Netz {value}",
                # Network Container der DDI bleiben Container (0.74.1)
                {"prefix": value, "status": _prefix_status(r), "description": desc},
                {"vrf": None, "vlan": None, "tenant": tenant_ref},
            )
        )
    for _v, _a, _b, r in sorted(primary.ranges, key=lambda x: (x[0], x[1], x[2])):
        start, _, end = r.value.partition("-")
        start, end = start.strip(), end.strip()
        plen = _plen(primary, start)
        w.add(
            Want(
                "ip_range",
                f"{_ip(start)}-{_ip(end)}",
                f"Range {r.value}",
                {
                    "start_address": f"{start}/{plen}",
                    "end_address": f"{end}/{plen}",
                    "status": "active",
                    "description": _cut(_join(r.description, f"Spiegel {src}"), 200),
                },
                {"tenant": tenant_ref},
            )
        )
    for ip, recs in sorted(primary.by_ip.items()):
        best = recs[0]
        dns = next((_dns(x.dns_name) for x in recs if _dns(x.dns_name)), "")
        existing = w.get("ip_address", ip)
        if existing is not None:  # Interface-IP: DNS-Name aus dem IPAM ergaenzen
            existing.fields["dns_name"] = dns
            continue
        w.add(
            Want(
                "ip_address",
                ip,
                f"IP {ip} {dns}".strip(),
                {
                    "address": f"{ip}/{_plen(primary, ip)}",
                    "status": "active",
                    "dns_name": dns,
                    "description": _cut(_join(best.description, f"Spiegel {src}"), 200),
                },
                {"tenant": tenant_ref},
            )
        )


CLUSTER_TYPE = ("vmware-vsphere", "VMware vSphere")
_VM_STATUS = {"poweredOn": "active"}  # sonst offline (ausgeschaltet, angehalten)
VM_INTERFACE = "virtualization.vminterface"


def _virtualization_wants(session: Session, w: Wants, tenant: Tenant, tenant_ref: Ref) -> None:
    """VMs des Mandanten (ohne Vorlagen) mit Cluster, Interfaces und IPs (ADR 0017)."""
    rows = session.execute(
        select(Vm, SourceSystem.name)
        .join(SourceSystem, SourceSystem.id == Vm.source_system_id)
        .where(Vm.tenant_id == tenant.id, Vm.template.is_(False))
        .options(selectinload(Vm.nics))
        .order_by(Vm.source_system_id, Vm.name, Vm.id)
    ).all()
    if not rows:
        return
    groups = virtual_store.PortgroupIndex.load(session, tenant.id)  # gleichnamige getrennt
    labels = {
        e.id: inventory_label(e)
        for e in session.scalars(
            select(Epg).where(Epg.id.in_({pg.epg_id for pg in groups.groups if pg.epg_id}))
        )
    }
    plens = _PrefixLens(w, references.context(session, tenant.id).primary)
    tslug = tenant_slug(tenant)
    w.add(
        Want(
            "cluster_type",
            CLUSTER_TYPE[0],
            f"Cluster-Typ {CLUSTER_TYPE[1]}",
            {"name": CLUSTER_TYPE[1], "slug": CLUSTER_TYPE[0], "description": "Automato: vCenter"},
        )
    )
    for vm, src in rows:
        cname = vm.cluster or vm.host
        if not cname:
            w.warnings.append(f"VM {vm.name} ({src}): ohne Cluster und Host – nicht übernommen.")
            continue
        if w.has("virtual_machine", vm.name):
            w.warnings.append(f"VM {vm.name} kommt mehrfach vor – {src} übersprungen.")
            continue
        group: Ref | None = None
        if vm.datacenter:
            gslug = slugify(f"{tslug}-{vm.datacenter}")
            group = ("cluster_group", gslug)
            w.add(
                Want(
                    "cluster_group",
                    gslug,
                    f"Cluster-Gruppe {vm.datacenter}",
                    {
                        "name": _cut(f"{vm.datacenter} ({tenant.name})", 100),
                        "slug": gslug,
                        "description": _cut(f"Automato: Datacenter {vm.datacenter} ({src})", 200),
                    },
                )
            )
        ckey = f"{group[1]}/{cname}" if group else cname
        w.add(
            Want(
                "cluster",
                ckey,
                f"Cluster {cname}",
                {
                    "name": _cut(cname, 100),
                    "status": "active",
                    "description": _cut(
                        f"Automato: {'Cluster' if vm.cluster else 'Host'} aus {src}", 200
                    ),
                },
                {"type": ("cluster_type", CLUSTER_TYPE[0]), "group": group, "tenant": tenant_ref},
            )
        )
        epgs = sorted(
            {
                labels[pg.epg_id]
                for n in vm.nics
                if (pg := groups.of(vm.source_system_id, n)) is not None and pg.epg_id in labels
            }
        )
        comments = "\n".join(
            x
            for x in (
                f"Automato: gelesen aus {src}",
                f"Gast-OS {vm.guest_os}" if vm.guest_os else None,
                f"Hostname {vm.hostname}" if vm.hostname else None,
                f"Host {vm.host}" if vm.host else None,
                f"EPG {', '.join(epgs)}" if epgs else None,
            )
            if x
        )
        fields = {
            "name": _cut(vm.name, 64),
            "status": _VM_STATUS.get(vm.power_state or "", "offline"),
            "vcpus": vm.vcpus,
            "memory": vm.memory_mb,  # NetBox: MB
            "disk": round(vm.disk_gb * 1024) if vm.disk_gb else None,  # NetBox 4.x: MB
            "comments": comments,
        }
        w.add(
            Want(
                "virtual_machine",
                vm.name,
                f"VM {vm.name}",
                {k: v for k, v in fields.items() if v is not None},  # unbekannt: nicht anfassen
                {"cluster": ("cluster", ckey), "tenant": tenant_ref},
            )
        )
        for nic in vm.nics:
            key = f"{vm.name}/{nic.name}"
            pg = groups.of(vm.source_system_id, nic)
            epg = labels.get(pg.epg_id) if pg is not None and pg.epg_id else None
            w.add(
                Want(
                    "vm_interface",
                    key,
                    f"VM-Interface {key}",
                    {
                        "name": _cut(nic.name, 64),
                        "enabled": nic.connected is not False,
                        "description": _cut(
                            _join(
                                f"MAC {nic.mac}" if nic.mac else None,
                                f"Portgruppe {nic.portgroup}" if nic.portgroup else None,
                                f"VLAN {nic.vlan}" if nic.vlan is not None else None,
                                f"EPG {epg}" if epg else None,
                            ),
                            200,
                        ),
                    },
                    {"virtual_machine": ("virtual_machine", vm.name)},
                )
            )
            for value in nic.ips or []:
                _vm_ip_want(w, value, key, f"VM {vm.name} {nic.name}", plens, tenant_ref)


def _vm_ip_want(w: Wants, value: str, key: str, label: str, plens, tenant_ref: Ref) -> None:
    ip = _ip(value)
    if ip is None:
        return
    existing = w.get("ip_address", ip)
    if existing is not None:
        # IP aus dem DDI-Spiegel: an das VM-Interface haengen; Geraete-Interface-IP bleibt
        if not existing.fields.get("assigned_object_type"):
            existing.fields["assigned_object_type"] = VM_INTERFACE
            existing.refs["assigned_object_id"] = ("vm_interface", key)
            existing.fields["description"] = _cut(_join(existing.fields["description"], label), 200)
        return
    w.add(
        Want(
            "ip_address",
            ip,
            f"IP {ip} ({key})",
            {
                "address": f"{ip}/{plens.of(ip)}",
                "status": "active",
                "description": _cut(label, 200),
                "assigned_object_type": VM_INTERFACE,
            },
            {"assigned_object_id": ("vm_interface", key), "tenant": tenant_ref},
            # von Hand gepflegte IP (NetBox als IPAM): nur die Zuordnung zur VM zaehlt
            soft=("address", "tenant", "status", "description"),
        )
    )


class _PrefixLens:
    """Praefixlaenge einer IP: laengstes Netz aus dem Soll bzw. dem fuehrenden IPAM."""

    def __init__(self, w: Wants, primary=None) -> None:
        self.nets: dict[int, set[tuple[int, int]]] = defaultdict(set)
        values = set(w.keys_of("prefix")) | set(primary.prefix_by_value if primary else ())
        for value in values:
            try:
                n = ipaddress.ip_network(value, strict=False)
            except ValueError:
                continue
            self.nets[n.version].add((n.prefixlen, int(n.network_address)))
        self.lens = {v: sorted({p for p, _ in nets}, reverse=True) for v, nets in self.nets.items()}

    def of(self, ip: str) -> int:
        addr = ipaddress.ip_address(ip)
        bits = addr.max_prefixlen
        for plen in self.lens.get(addr.version, ()):
            net = (int(addr) >> (bits - plen)) << (bits - plen)
            if (plen, net) in self.nets[addr.version]:
                return plen
        return bits


def _plen(index, ip: str) -> int:
    prefixes = index.containing_prefixes(ip)
    if prefixes:
        net = _net(prefixes[0].value)
        if net:
            return int(net.split("/")[1])
    return 128 if ":" in ip else 32


# -- Verkabelung (ADR 0031) ----------------------------------------------------------
# Gelesene Switches/Router (`cisco_switch`) mit ihren Ports und die Kabel der Verkabelung
# (`store/cabling.py`) zwischen zwei Ports, die beide Seiten per CDP/LLDP melden. Standort,
# Rolle und Hersteller wie im Netzinventar; ohne Standort bzw. Modell nichts raten (Hinweis).

_JUNIPER = re.compile(r"^(mx|ex|qfx|srx|acx|ptx)\d", re.I)  # Modelle der Junos-Geraete
# Kurzform des Portnamens (`cabling.port_key`) -> Mbit/s; NX-OS `eth` und Junos `et-` nach Speed
_NAME_SPEED = {"fa": 100, "gi": 1000, "tw": 2500, "fi": 5000, "te": 10000, "twe": 25000,
               "fo": 40000, "hu": 100000, "ge-": 1000, "xe-": 10000}  # fmt: skip
_COPPER_TYPE = {100: "100base-tx", 1000: "1000base-t", 2500: "2.5gbase-t", 5000: "5gbase-t",
                10000: "10gbase-t"}  # fmt: skip
_OPTIC_TYPE = {1000: "1000base-x-sfp", 10000: "10gbase-x-sfpp", 25000: "25gbase-x-sfp28",
               40000: "40gbase-x-qsfpp", 100000: "100gbase-x-qsfp28"}  # fmt: skip
_COPPER = re.compile(r"base-?t\b|basetx|rj-?45|10/100", re.I)
_OPTIC = re.compile(r"sfp|fiber|base-?[slz]x|base-?[slez]r|base-?x\b", re.I)
_ALL_VLANS = frozenset(range(1, 4095))
NOT_CONNECTABLE = ("virtual", "lag", "bridge")  # NetBox: an solchen Ports kein Kabel


def switch_manufacturer(model: str) -> str:
    """Hersteller aus dem gelesenen Modell: Junos-Geraete (MX, EX, QFX …) sonst Cisco."""
    return "Juniper" if _JUNIPER.match(model.strip()) else "Cisco"


def port_type(i: InvInterface) -> str:
    """NetBox-Typ eines Ports, soweit ableitbar: Port-Channel `lag`, SVI/Loopback/Unit
    `virtual`; physisch nach Speed (Name bzw. gelesener Speed) und Medium (Kupfer/optisch,
    `attrs.media`), sonst `other`."""
    if i.kind == "lag":
        return "lag"
    if i.kind != "physical":
        return "virtual"
    m = re.match(r"[a-z]+-?", cabling_store.port_key(i.name))
    speed = _NAME_SPEED.get(m.group(0)) if m else None
    speed = speed or cabling_store.speed_mbps(i.speed)
    if speed is None:
        return "other"
    media = str((i.attrs or {}).get("media") or "")
    if _COPPER.search(media) and speed in _COPPER_TYPE:
        return _COPPER_TYPE[speed]
    if _OPTIC.search(media) and speed in _OPTIC_TYPE:
        return _OPTIC_TYPE[speed]
    # ohne Angabe zum Medium: bis 5G Kupfer (RJ45), darueber optisch (SFP+/SFP28/QSFP)
    if speed <= 5000:
        return _COPPER_TYPE.get(speed, "other")
    return _OPTIC_TYPE.get(speed, "other")


def _port_vlans(
    i: InvInterface, key: str, site: str, group: str, switch_vlans: set[int]
) -> PortVlans | None:
    """Was ein Port an VLANs braucht: Access -> Access-VLAN; Trunk mit allen VLANs ->
    `tagged-all` (Native VLAN, wenn vorhanden); Trunk mit Liste -> die erlaubten VLANs, die es
    auf den Switches des Quellsystems gibt (ohne Native VLAN und VLAN 1 - diese nur, wenn
    vorhanden). None = Modus unbekannt."""
    if i.mode == "access":
        if i.untagged_vlan is None:
            return None
        return PortVlans(key, i.device, site, group, "access", i.untagged_vlan)
    if i.mode != "tagged":
        return None
    allowed = cabling_store.vlan_set(i.trunk_vlans) if i.trunk_vlans else set(i.tagged_vlans or [])
    if allowed is None:
        return None
    native = i.native_vlan if i.native_vlan is not None else i.untagged_vlan
    if allowed >= _ALL_VLANS:
        return PortVlans(key, i.device, site, group, "tagged-all", native)
    relevant = (allowed & switch_vlans if switch_vlans else allowed) - {native}
    return PortVlans(key, i.device, site, group, "tagged", native,
                     sorted(relevant - {1}), [1] if 1 in relevant else [])  # fmt: skip


def _examples(labels: list[str], n: int = 3) -> str:
    return ", ".join(labels[:n]) + (" …" if len(labels) > n else "")


def _cabling_wants(
    session: Session, w: Wants, tenant: Tenant, sites: dict[int, Site], tenant_ref: Ref
) -> None:
    sources = list(
        session.scalars(
            select(SourceSystem)
            .where(
                SourceSystem.tenant_id == tenant.id,
                SourceSystem.platform.in_(CABLING_PLATFORMS),
            )
            .order_by(SourceSystem.id)
        )
    )
    only_site = _default_site(sites)
    for src in sources:
        site = sites.get(src.site_id) if src.site_id else only_site
        devices = _rows(session, InvDevice, src)
        if devices and site is None:
            _no_site_warning(w, src, len(devices))
            continue
        names: set[str] = set()
        for d in devices:
            if w.has("device", d.name):
                w.warnings.append(f"Gerät {d.name} kommt mehrfach vor – {src.name} übersprungen.")
                continue
            if not (d.model or "").strip():
                w.warnings.append(
                    f"Gerät {d.name} ({src.name}): Modell nicht gelesen – ohne Gerätetyp mit Ports"
                    " und Kabeln nicht übernommen."
                )
                continue
            model = d.model.strip()
            _device_want(w, d, src, site, switch_manufacturer(model), model, tenant_ref)
            names.add(d.name)
        gslug = group_slug(tenant, src)
        switch_vlans = {v.vid for v in _rows(session, InvVlan, src)}
        ifaces = [i for i in _rows(session, InvInterface, src) if i.device in names]
        by_device: dict[str, set[str]] = defaultdict(set)
        for i in ifaces:
            by_device[i.device].add(i.name)
        for i in ifaces:
            key = f"{i.device}/{i.name}"
            if w.has("interface", key):
                continue
            w.add(
                Want(
                    "interface",
                    key,
                    f"Port {key}",
                    {
                        "name": _cut(i.name, 64),
                        "type": port_type(i),
                        "enabled": i.enabled is not False,
                        "description": _cut(i.description, 200),
                    },
                    {
                        "device": ("device", i.device),
                        "lag": ("interface", f"{i.device}/{i.lag}")
                        if i.lag and i.lag in by_device[i.device] and i.lag != i.name
                        else None,
                        "parent": ("interface", f"{i.device}/{i.parent}")
                        if i.parent and i.parent in by_device[i.device] and i.parent != i.name
                        else None,
                    },
                )
            )
            need = _port_vlans(i, key, slugify(site.code), gslug, switch_vlans)
            if need is not None:
                w.ports.append(need)
    _cable_wants(session, w, tenant, tenant_ref)


def _cable_wants(session: Session, w: Wants, tenant: Tenant, tenant_ref: Ref) -> None:
    """Kabel aus der Verkabelung: nur beidseitig gemeldete Verbindungen zwischen zwei
    physischen Ports gelesener Geraete (auch ACI-Leafs). Einseitige und nicht gelesene
    Gegenstellen nur als Hinweis; ob beide Ports in NetBox sind, prueft `resolve_after_read`."""
    cab = cabling_store.build(session, tenant.id)
    one_sided: list[str] = []
    unread = 0
    multi: list[str] = []
    used: set[str] = set()
    for link in sorted(cab.links.values(), key=lambda x: x.id):
        state = link.state
        a_end, b_end = link.a, link.b
        text = f"{a_end.device.name} {a_end.port or '?'} ↔ {b_end.device.name} {b_end.port or '?'}"
        if state == "one_sided":
            one_sided.append(text)
            continue
        if state != "both":
            unread += 1
            continue
        a, b = link.a.iface, link.b.iface
        if a is None or b is None or a.kind != "physical" or b.kind != "physical":
            continue  # Port nicht gelesen bzw. kein physischer Port (kein Kabel moeglich)
        ka, kb = sorted((f"{a.device}/{a.name}", f"{b.device}/{b.name}"))
        if ka in used or kb in used:
            multi.append(text)
            continue
        used |= {ka, kb}
        protocols = ", ".join(p.upper() for p in sorted(link.protocols))
        w.add(
            Want(
                "cable",
                f"{ka}~{kb}",
                f"Kabel {ka} ↔ {kb}",
                {
                    "status": "connected",
                    "description": _cut(f"Automato: {protocols} beidseitig", 200),
                },
                {
                    "a_terminations": [("interface", ka)],
                    "b_terminations": [("interface", kb)],
                    "tenant": tenant_ref,
                },
            )
        )
        w.cable_ends |= {a.device, b.device}
    if one_sided:
        w.warnings.append(
            f"Verkabelung: {len(one_sided)} einseitig gemeldete Verbindungen nicht als Kabel"
            f" übernommen (z. B. {_examples(one_sided)})."
        )
    if unread:
        w.warnings.append(
            f"Verkabelung: {unread} Verbindungen zu Geräten ohne CDP/LLDP-Daten (z. B. Firewall,"
            " ESXi-Host) nicht als Kabel übernommen."
        )
    if multi:
        w.warnings.append(
            f"Verkabelung: {len(multi)} Verbindungen an Ports mit mehreren Nachbarn nicht als"
            f" Kabel übernommen (z. B. {_examples(multi)})."
        )


# -- Applikationen (ADR 0031) ------------------------------------------------------------
# Je Applikation ein Tag `automato-app-<Mandant>-<Kuerzel bzw. Name>` (Name = Applikation,
# Beschreibung = Kuerzel/Zweck/Kritikalitaet) an den VMs und IP-Adressen ihrer Systeme - wie
# die Doku (`applications.systems_by_application`). Keine Custom Fields.

_APP_PURPOSE = {"dev": "Dev", "test": "Test", "prod": "Prod"}
_APP_CRITICALITY = {"low": "niedrig", "medium": "mittel", "high": "hoch", "critical": "kritisch"}
_APP_COLOR = {"low": "9e9e9e", "medium": "2196f3", "high": "ff9800", "critical": "f44336"}


def app_tag_prefix(tslug: str) -> str:
    return f"{APP_TAG_PREFIX}{tslug}-"


def app_tag_owner(slug: str, tslugs) -> str | None:
    """Mandant eines Applikations-Tags: der laengste Mandanten-Slug, dessen Praefix passt
    (`automato-app-kunde-a-b-…` gehoert zu `kunde-a-b`, nicht zu `kunde-a`)."""
    hits = [t for t in tslugs if slug.startswith(app_tag_prefix(t))]
    return max(hits, key=len) if hits else None


def app_tag_slugs(tslug: str, apps: list[Application]) -> dict[int, str]:
    """Slug je Applikation: Kuerzel, sonst Name; teilen sich mehrere denselben, behaelt die
    aelteste ihn und die uebrigen bekommen den Namen (bzw. die ID) dazu - stabil, solange
    Kuerzel und Name bleiben."""
    prefix = app_tag_prefix(tslug)
    room = max(100 - len(prefix), 8)
    out: dict[int, str] = {}
    taken: set[str] = set()
    for app in sorted(apps, key=lambda a: a.id):
        base = slugify(app.code or app.name)[:room]
        for cand in (base, f"{base}-{slugify(app.name)}"[:room], f"{base}-{app.id}"[-room:]):
            if cand not in taken:
                break
        taken.add(cand)
        out[app.id] = f"{prefix}{cand}"
    return out


def _app_description(app: Application) -> str:
    return _join(
        "Automato: Applikation",
        f"Kürzel {app.code}" if app.code else None,
        f"Zweck {_APP_PURPOSE.get(app.purpose, app.purpose)}" if app.purpose else None,
        f"Kritikalität {_APP_CRITICALITY.get(app.criticality, app.criticality)}"
        if app.criticality
        else None,
    )


def _application_wants(session: Session, w: Wants, tenant: Tenant) -> None:
    from app.store import applications as app_store  # erst hier: vermeidet Import-Kreise

    apps = list(
        session.scalars(
            select(Application).where(Application.tenant_id == tenant.id).order_by(Application.id)
        )
    )
    if not apps:
        return
    slugs = app_tag_slugs(tenant_slug(tenant), apps)
    systems = app_store.systems_by_application(session, tenant.id, apps)
    tags: dict[str, dict[str, set[str]]] = {"app_tag_vm": defaultdict(set),
                                            "app_tag_ip": defaultdict(set)}  # fmt: skip
    for app in apps:
        slug = slugs[app.id]
        w.add(
            Want(
                "tag",
                slug,
                f"Tag {app.name} ({slug})",
                {
                    "name": _cut(app.name, 100),
                    "slug": slug,
                    "color": _APP_COLOR.get(app.criticality or "", "607d8b"),
                    "description": _cut(_app_description(app), 200),
                },
            )
        )
        vms, ips = systems.get(app.id, (set(), set()))
        for vm in vms:
            tags["app_tag_vm"][vm].add(slug)
        for value in ips:
            ip = _ip(value)
            if ip:
                tags["app_tag_ip"][ip].add(slug)
    for kind, by_key in tags.items():
        for key, wanted in sorted(by_key.items()):
            w.add(Want(kind, key, f"{KIND[kind].label} {key}", {"tags": sorted(wanted)}))


# -- Ist --------------------------------------------------------------------------


def _key_of(kind: str, obj: dict) -> str | None:
    if kind in (
        "tag",
        "tenant",
        "site_group",
        "site",
        "manufacturer",
        "device_role",
        "vlan_group",
        "cluster_type",
        "cluster_group",
    ):
        return obj.get("slug")
    if kind == "device_type":
        return (obj.get("model") or "").lower() or None
    if kind in ("vrf", "device", "virtual_machine"):
        return obj.get("name")
    if kind == "cluster":
        group = obj.get("group") or {}
        return f"{group['slug']}/{obj.get('name')}" if group.get("slug") else obj.get("name")
    if kind == "vlan":
        group = obj.get("group") or {}
        return f"{group['slug']}/{obj.get('vid')}" if group.get("slug") else None
    if kind == "prefix":
        return _net(obj.get("prefix"))
    if kind == "ip_range":
        return f"{_ip(obj.get('start_address'))}-{_ip(obj.get('end_address'))}"
    if kind == "interface":
        device = obj.get("device") or {}
        return f"{device.get('name')}/{obj.get('name')}" if device.get("name") else None
    if kind == "vm_interface":
        vm = obj.get("virtual_machine") or {}
        return f"{vm.get('name')}/{obj.get('name')}" if vm.get("name") else None
    if kind == "ip_address":
        return _ip(obj.get("address"))
    if kind == "cable":
        return _cable_key(obj)
    return None


def _cable_key(obj: dict) -> str | None:
    """`<Geraet>/<Port>~<Geraet>/<Port>` (sortiert) fuer Kabel mit je einem Port an beiden
    Enden; andere Kabel (Patchpanel, Circuit, mehrere Enden) bleiben ausserhalb des Syncs."""
    ends = []
    for side in TERMINATIONS:
        terms = obj.get(side) or []
        if len(terms) != 1 or terms[0].get("object_type") != "dcim.interface":
            return None
        port = terms[0].get("object") or {}
        device = (port.get("device") or {}).get("name")
        if not device or not port.get("name"):
            return None
        ends.append(f"{device}/{port['name']}")
    return "~".join(sorted(ends))


def _tagged(obj: dict) -> bool:
    return any((t or {}).get("slug") == TAG for t in obj.get("tags") or [])


def _tag_slugs(obj: dict) -> set[str]:
    return {(t or {}).get("slug") for t in obj.get("tags") or []} - {None}


def read_state(
    writer,
    wants: Wants,
    tslug: str,
    gprefix: str,
    sections: list[str] | None = None,
    tenants: list[str] | None = None,
) -> State:
    """NetBox-Ist fuer die Arten des Syncs (nur GET). `tenants` = Slugs aller Mandanten
    (Besitz der Applikations-Tags, `app_tag_owner`)."""
    state: State = {k.name: {} for k in KINDS}
    state["vlan_ref"] = {}  # Verkabelung: alle VLANs in NetBox (nur zum Referenzieren)
    chosen = set(SECTIONS if sections is None else sections)
    virtual = "virtualization" in chosen
    apps = "applications" in chosen
    cabling = "cabling" in chosen

    def put(kind: str, objs: list[dict]) -> None:
        for obj in objs:
            key = _key_of(kind, obj)
            if key is not None and key not in state[kind]:
                state[kind][key] = Have(obj["id"], key, obj, _tagged(obj))

    for k in KINDS:
        if k.name in ("vlan", "interface", "vm_interface", "cable") or k.name in ASSIGN:
            continue
        # VMs auch fuer die Applikations-Tags - geloescht bzw. veraltet nur mit `virtualization`
        if k.name in VIRTUAL_KINDS and not virtual and not (apps and k.name == "virtual_machine"):
            continue
        # alle Tags nur mit den Applikationen (Namen muessen in NetBox eindeutig sein)
        params = {"slug": TAG} if k.name == "tag" and not apps else {}
        put(k.name, writer.read(k.endpoint, params))
    owners = tenants or [tslug]
    for slug, have in state["tag"].items():  # eigen = Applikations-Tag dieses Mandanten
        have.tagged = slug.startswith(APP_TAG_PREFIX) and app_tag_owner(slug, owners) == tslug

    our_tenant = state["tenant"].get(tslug)
    groups = [
        h.id
        for key, h in state["vlan_group"].items()
        if wants.has("vlan_group", key) or (h.tagged and key.startswith(gprefix))
    ]
    if cabling:  # alle VLANs: die Ports der Switches nehmen vorhandene VLANs (ADR 0031)
        everything = writer.read("ipam/vlans", {})
        ours = set(groups)
        put("vlan", [o for o in everything if (o.get("group") or {}).get("id") in ours])
        for o in everything:
            key = _vlan_ref_key(o)
            state["vlan_ref"].setdefault(key, Have(o["id"], key, o, _tagged(o)))
    else:
        for chunk in _chunks(groups, 50):
            put("vlan", writer.read("ipam/vlans", {"group_id": chunk}))
    devices = [
        h.id
        for key, h in state["device"].items()
        if wants.has("device", key)
        or (h.tagged and _tenant_id(h.obj) == _id(our_tenant))
        or key in wants.cable_ends  # Kabel auch an von Hand gepflegten Geraeten (nur Verweis)
    ]
    for chunk in _chunks(devices, 50):
        put("interface", writer.read("dcim/interfaces", {"device_id": chunk}))
    if cabling:
        for chunk in _chunks(devices, 50):
            put("cable", writer.read("dcim/cables", {"device_id": chunk}))
    if virtual:
        vms = [
            h.id
            for key, h in state["virtual_machine"].items()
            if wants.has("virtual_machine", key)
            or (h.tagged and _tenant_id(h.obj) == _id(our_tenant))
        ]
        for chunk in _chunks(vms, 50):
            put(
                "vm_interface",
                writer.read("virtualization/interfaces", {"virtual_machine_id": chunk}),
            )
    return state


def _vlan_ref_key(obj: dict) -> str:
    """Lesbarer Schluessel eines VLANs in NetBox: `<Gruppe bzw. Standort>/<VID> #<ID>`."""
    where = (obj.get("group") or {}).get("slug") or (obj.get("site") or {}).get("slug") or "global"
    return f"{where}/{obj.get('vid')} #{obj.get('id')}"


# -- Nach dem Lesen (ADR 0031) ----------------------------------------------------------


def resolve_after_read(wants: Wants, state: State) -> None:
    """Was erst mit dem Ist von NetBox feststeht: VLANs der Switch-Ports (nur vorhandene,
    eindeutige), Kabel nur zwischen Ports, die es in NetBox gibt bzw. die der Sync anlegt, und
    Applikations-Tags nur an solchen VMs/IPs - der Rest als Hinweis."""
    _resolve_port_vlans(wants, state)
    _drop_cables_without_ports(wants, state)
    _drop_tags_without_target(wants, state)


def _vlan_candidates(wants: Wants, state: State) -> dict[int, list[tuple]]:
    """VID -> (Gruppe, Standort, Geltungsbereich der Gruppe, Verweis) aller VLANs in NetBox
    und der im selben Sync angelegten (Netzinventar: VLAN-Gruppen ohne Geltungsbereich)."""
    out: dict[int, list[tuple]] = defaultdict(list)
    known: set[str] = set()
    for key, have in state["vlan_ref"].items():
        o = have.obj
        group = (o.get("group") or {}).get("slug")
        site = (o.get("site") or {}).get("slug")
        scope = None
        if group and group in state["vlan_group"]:
            g = state["vlan_group"][group].obj
            scope = (g.get("scope_type"), g.get("scope_id")) if g.get("scope_type") else None
        gkey = f"{group}/{o.get('vid')}" if group else None
        if gkey and (wants.has("vlan", gkey) or gkey in state["vlan"]):
            ref: Ref = ("vlan", gkey)
            known.add(gkey)
        else:
            ref = ("vlan_ref", key)
        out[o.get("vid")].append((group, site, scope, ref))
    for want in wants.items.values():
        if want.kind == "vlan" and want.key not in known:
            group = want.refs.get("group")
            out[want.fields["vid"]].append((group[1] if group else None, None, None,
                                           ("vlan", want.key)))  # fmt: skip
    return out


def _vlan_level(cand: tuple, port: PortVlans, site_id: int | None) -> int | None:
    """0 = VLAN-Gruppe des Quellsystems, 1 = am Standort des Geraets (VLAN bzw. Gruppe mit
    diesem Standort), 2 = global (ohne Standort, Gruppe ohne Geltungsbereich); None = an
    diesem Geraet nicht verwendbar."""
    group, site, scope, _ref = cand
    if group and group == port.group:  # Gruppe des Quellsystems - ohne bzw. mit diesem Standort
        return 0 if not scope or scope == ("dcim.site", site_id) else None
    if site:
        return 1 if site == port.site else None
    if scope:
        return 1 if scope == ("dcim.site", site_id) and site_id is not None else None
    return 2


def _pick_vlan(cands: dict, vid: int, port: PortVlans, site_id: int | None) -> Ref | str:
    """Verweis auf das VLAN bzw. `missing` / `ambiguous` (mehrere auf der besten Ebene)."""
    levels: dict[int, list[Ref]] = defaultdict(list)
    for cand in cands.get(vid, []):
        level = _vlan_level(cand, port, site_id)
        if level is not None and cand[3] not in levels[level]:
            levels[level].append(cand[3])
    if not levels:
        return "missing"
    refs = levels[min(levels)]
    return refs[0] if len(refs) == 1 else "ambiguous"


def _vid_text(vids: set[int]) -> str:
    ordered, parts, i = sorted(vids), [], 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        parts.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ", ".join(parts[:12]) + (" …" if len(parts) > 12 else "")


def _resolve_port_vlans(wants: Wants, state: State) -> None:
    """Modus und VLANs der Switch-Ports: nur wenn alle noetigen VLANs in NetBox eindeutig
    vorhanden sind (bzw. im selben Sync angelegt werden) - sonst bleiben Modus und VLANs am
    Port, wie sie sind, und der Hinweis nennt die VLANs je Geraet."""
    if not wants.ports:
        return
    cands = _vlan_candidates(wants, state)
    stats: dict[str, dict] = defaultdict(lambda: {"missing": set(), "ambiguous": set(),
                                                  "n_missing": 0, "n_ambiguous": 0})  # fmt: skip
    for port in wants.ports:
        want = wants.get("interface", port.key)
        if want is None:
            continue
        site = state["site"].get(port.site)
        site_id = site.id if site else None
        need = ([port.untagged] if port.mode == "access" else []) + port.required
        found: dict[int, Ref] = {}
        missing, ambiguous = set(), set()
        for vid in need:
            ref = _pick_vlan(cands, vid, port, site_id)
            if ref == "missing":
                missing.add(vid)
            elif ref == "ambiguous":
                ambiguous.add(vid)
            else:
                found[vid] = ref
        if missing or ambiguous:
            s = stats[port.device]
            s["missing"] |= missing
            s["ambiguous"] |= ambiguous
            s["n_missing"] += bool(missing)
            s["n_ambiguous"] += bool(ambiguous)
            continue

        def optional(vid: int | None, _p=port, _s=site_id) -> Ref | None:
            ref = _pick_vlan(cands, vid, _p, _s) if vid is not None else None
            return ref if isinstance(ref, tuple) else None

        want.fields["mode"] = port.mode
        untagged = found.get(port.untagged) if port.mode == "access" else optional(port.untagged)
        want.refs["untagged_vlan"] = untagged  # None: nicht anfassen (Native VLAN fehlt)
        tagged = [found[v] for v in port.required]
        tagged += [r for v in port.optional if (r := optional(v))]
        want.refs["tagged_vlans"] = tagged if port.mode == "tagged" else []
    for device, s in sorted(stats.items()):
        if s["missing"]:
            wants.warnings.append(
                f"{device}: Modus/VLANs an {s['n_missing']} Ports weggelassen – VLAN"
                f" {_vid_text(s['missing'])} nicht in NetBox."
            )
        if s["ambiguous"]:
            wants.warnings.append(
                f"{device}: Modus/VLANs an {s['n_ambiguous']} Ports weggelassen – VLAN"
                f" {_vid_text(s['ambiguous'])} in NetBox nicht eindeutig."
            )


def _port_available(key: str, wants: Wants, state: State) -> bool:
    if wants.has("interface", key):
        return True
    have = state["interface"].get(key)
    return have is not None and _norm(have.obj.get("type")) not in NOT_CONNECTABLE


def _drop_cables_without_ports(wants: Wants, state: State) -> None:
    dropped: list[str] = []
    for (kind, key), want in list(wants.items.items()):
        if kind != "cable":
            continue
        ends = [t[1] for side in TERMINATIONS for t in want.refs[side]]
        if not all(_port_available(e, wants, state) for e in ends):
            del wants.items[(kind, key)]
            dropped.append(want.label.removeprefix("Kabel "))
    if dropped:
        wants.warnings.append(
            f"Verkabelung: {len(dropped)} Kabel nicht übernommen – Port nicht in NetBox (Gerät"
            " ohne Standort bzw. Modell oder Abschnitt Netzinventar nicht gewählt), z. B."
            f" {_examples(dropped)}."
        )


def _drop_tags_without_target(wants: Wants, state: State) -> None:
    missing: dict[str, list[str]] = defaultdict(list)
    for kind, key in list(wants.items):
        target = ASSIGN.get(kind)
        if target is None or key in state[target] or wants.has(target, key):
            continue
        del wants.items[(kind, key)]
        missing[kind].append(key)
    if missing:
        vms, ips = missing.get("app_tag_vm", []), missing.get("app_tag_ip", [])
        wants.warnings.append(
            f"Applikationen: {len(vms)} VMs und {len(ips)} IP-Adressen nicht in NetBox – ohne"
            " Applikations-Tag (Abschnitte Virtualisierung bzw. DDI-Spiegel synchronisieren),"
            f" z. B. {_examples(vms + ips)}."
        )


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _id(have: Have | None) -> int | None:
    return have.id if have else None


def _tenant_id(obj: dict) -> int | None:
    t = obj.get("tenant")
    return t.get("id") if isinstance(t, dict) else t


# -- Eindeutige Namen ---------------------------------------------------------------


def unique_names(wants: Wants, state: State, tenant_name: str = "") -> None:
    """NetBox verlangt eindeutige Namen - VLANs je VLAN-Gruppe, Sites global (Dev-Befund:
    HTTP 500 "ipam_vlan_unique_group_name", weil mehrere VLANs einer ACI-EPG denselben Namen
    tragen). Doppelte Namen und Namen, die in NetBox ein anderes Objekt traegt, bekommen den
    Schluessel angehaengt: "EG_web (110)", "Zürich (zrh)". Konvergiert: faellt die Dublette
    weg, bekommt das Objekt beim naechsten Sync wieder den schlichten Namen. Applikations-Tags
    (global eindeutig, ADR 0031): "Webshop", sonst "Webshop (Kunde A)", sonst mit Slug."""
    _unique_tag_names(wants, state, tenant_name)
    by_group: dict[str, list[Want]] = defaultdict(list)
    for want in wants.items.values():
        group = want.refs.get("group") if want.kind == "vlan" else None
        if isinstance(group, tuple):
            by_group[group[1]].append(want)
    for gslug, vlans in by_group.items():
        taken: dict[str, set] = defaultdict(set)  # Name (klein) -> VIDs, die ihn in NetBox tragen
        for key, have in state["vlan"].items():
            if key.startswith(f"{gslug}/"):
                taken[str(have.obj.get("name") or "").lower()].add(have.obj.get("vid"))
        _suffix_duplicates(vlans, "vid", taken, 64)
    sites = [w for w in wants.items.values() if w.kind == "site"]
    taken_sites: dict[str, set] = defaultdict(set)
    for key, have in state["site"].items():
        taken_sites[str(have.obj.get("name") or "").lower()].add(key)
    _suffix_duplicates(sites, "slug", taken_sites, 100)


def _unique_tag_names(wants: Wants, state: State, tenant_name: str) -> None:
    tags = sorted(
        (w for w in wants.items.values() if w.kind == "tag" and w.key.startswith(APP_TAG_PREFIX)),
        key=lambda w: w.key,
    )
    if not tags:
        return
    used: dict[str, set[str]] = defaultdict(set)  # Name (klein) -> Slugs, die ihn tragen
    for slug, have in state["tag"].items():
        used[str(have.obj.get("name") or "").lower()].add(slug)
    wanted = {w.key for w in tags}
    for slug in wanted:  # die eigenen geben ihren Namen frei - er wird neu vergeben
        for names in used.values():
            names.discard(slug)
    for w in tags:
        name = str(w.fields["name"])
        options = (name, f"{name} ({tenant_name})" if tenant_name else name, f"{name} ({w.key})")
        for option in options:
            option = _cut(option, 100)
            if not used.get(option.lower()):
                break
        w.fields["name"] = option
        used[option.lower()].add(w.key)


def _suffix_duplicates(wants: list[Want], ident: str, taken: dict[str, set], limit: int) -> None:
    counts: dict[str, int] = defaultdict(int)
    for w in wants:
        counts[str(w.fields["name"]).lower()] += 1
    for w in sorted(wants, key=lambda x: str(x.fields[ident])):
        name, own = str(w.fields["name"]), w.fields[ident]
        if counts[name.lower()] > 1 or taken.get(name.lower(), set()) - {own}:
            w.fields["name"] = _cut(f"{name} ({own})", limit)


# -- Vorschau (Diff) ---------------------------------------------------------------

_PENDING = object()


def _norm(value: Any) -> Any:
    if isinstance(value, dict):
        for k in ("value", "id", "slug"):
            if k in value:
                return value[k]
        return None
    if isinstance(value, list):
        return sorted(_norm(v) for v in value)
    return value


def _display(value: Any) -> Any:
    if isinstance(value, dict):
        for k in ("label", "display", "name", "vid", "value", "id"):
            if value.get(k) not in (None, ""):
                return value[k]
        return None
    if isinstance(value, list):
        return [_display(v) for v in value]
    return value


def _same(old: Any, new: Any) -> bool:
    if old in (None, "") and new in (None, ""):
        return True
    if isinstance(old, str) and isinstance(new, str):
        return old.strip() == new.strip()
    if isinstance(old, str) and isinstance(new, int | float) and not isinstance(new, bool):
        try:  # Dezimalfeld als Text (z. B. vCPUs "2.00")
            return float(old) == float(new)
        except ValueError:
            return False
    return old == new


def _resolve(ref: Ref | None, state: State, wants: Wants) -> Any:
    if ref is None:
        return None
    have = state[ref[0]].get(ref[1])
    if have is not None:
        return have.id
    return _PENDING if wants.has(*ref) else None


def _changes(want: Want, have: Have, state: State, wants: Wants) -> list[dict]:
    out: list[dict] = []
    kind = KIND[want.kind]
    for f in kind.managed + (kind.owned if have.tagged else ()):
        if f in want.soft and not have.tagged:
            continue
        old_raw = have.obj.get(f)
        if f in want.refs:
            target = want.refs[f]
            if isinstance(target, list):
                ids = [_resolve(t, state, wants) for t in target]
                ids = [i for i in ids if i is not None]
                old = sorted(_norm(old_raw) or [])
                if _PENDING not in ids and sorted(ids) == old:
                    continue
                out.append({"field": f, "old": _display(old_raw), "new": [t[1] for t in target]})
            else:
                if target is None:
                    continue  # keine Vorgabe (z. B. Netz ohne bekanntes VLAN) - nicht anfassen
                new_id = _resolve(target, state, wants)
                if (
                    new_id is not _PENDING
                    and _same(_norm(old_raw), new_id)
                    and _same_type(f, want, have)
                ):
                    continue
                out.append({"field": f, "old": _display(old_raw), "new": target[1]})
        elif f in want.fields:
            new = want.fields[f]
            if _same(_norm(old_raw), new):
                continue
            out.append({"field": f, "old": _display(old_raw), "new": new})
    return out


def _same_type(f: str, want: Want, have: Have) -> bool:
    """Gleiche Objekt-ID reicht nicht: Port und VM-Interface koennen dieselbe ID tragen."""
    if f != "assigned_object_id" or not want.fields.get("assigned_object_type"):
        return True
    return have.obj.get("assigned_object_type") == want.fields["assigned_object_type"]


_PARENT = {"device": "device", "vm": "virtual_machine"}  # Scope -> Art des Elternobjekts


def _in_scope(
    kind: Kind, have: Have, state: State, tslug: str, gprefix: str,
    foreign: frozenset[str] = frozenset(),
) -> bool:  # fmt: skip
    """Darf der Sync das Objekt loeschen bzw. als veraltet fuehren? `foreign` = Geraete eines
    nicht gewaehlten Abschnitts (Netzinventar bzw. Verkabelung) - sie und ihre Ports bleiben."""
    if not have.tagged or not kind.scope:
        return False
    our = _id(state["tenant"].get(tslug))
    if kind.scope == "app":  # eigener Applikations-Tag (Besitz in `read_state`)
        return True
    if kind.scope == "tenant":
        if kind.name == "device" and have.key in foreign:
            return False
        return our is not None and _tenant_id(have.obj) == our
    if kind.scope == "group":
        return have.key.startswith(gprefix)
    if kind.scope in _PARENT:
        parent = _PARENT[kind.scope]
        name = (have.obj.get(parent) or {}).get("name")
        if parent == "device" and name in foreign:
            return False
        owner = state[parent].get(name) if name else None
        return owner is not None and owner.tagged and _tenant_id(owner.obj) == our
    return False


_FROM_SOURCE = re.compile(r"^Automato: gelesen aus (.+?)\s*$", re.M)


def _device_section(obj: dict, platforms: dict[str, Platform]) -> str:
    """Abschnitt eines von Automato angelegten Geraets - aus dem Kommentar „Automato: gelesen
    aus <Quellsystem>“: Switches & Router -> `cabling`, sonst (auch unbekannt) `inventory`."""
    m = _FROM_SOURCE.search(obj.get("comments") or "")
    platform = platforms.get(m.group(1)) if m else None
    return "cabling" if platform in CABLING_PLATFORMS else "inventory"


def diff(
    wants: Wants,
    state: State,
    tslug: str,
    gprefix: str,
    allow_delete: bool,
    sections: list[str] | None = None,
    platforms: dict[str, Platform] | None = None,
) -> list[dict]:
    """Vorschau. `sections` = gewaehlte Abschnitte (None = alle): Objekte der anderen werden
    weder geloescht noch als veraltet gefuehrt; `platforms` = Plattform je Quellsystem-Name."""
    chosen = set(SECTIONS if sections is None else sections)
    foreign = frozenset(
        key
        for key, have in state["device"].items()
        if have.tagged and _device_section(have.obj, platforms or {}) not in chosen
    )
    wanted_cables = wants.keys_of("cable")
    removed_cables = {  # werden vor dem Anlegen der neuen Kabel geloescht (`apply`)
        have.id
        for key, have in state["cable"].items()
        if allow_delete
        and key not in wanted_cables
        and _in_scope(KIND["cable"], have, state, tslug, gprefix, foreign)
    }
    items: list[dict] = []
    for want in wants.ordered():
        if want.kind in ASSIGN:
            continue  # Tags an VMs/IPs: `_assignments`
        have = state[want.kind].get(want.key)
        if have is None:
            busy = _occupied(want, state, removed_cables) if want.kind == "cable" else []
            if busy:  # Port traegt schon ein anderes Kabel: nichts anlegen
                items.append(_item(want.kind, want.key, want.label, "conflict", manual=True,
                                   changes=busy))  # fmt: skip
                continue
            items.append(_item(want.kind, want.key, want.label, "create", fields=_preview(want)))
            continue
        if want.kind == "tag" and want.key.startswith(APP_TAG_PREFIX) and not have.tagged:
            # gleicher Slug gehoert einem anderen Mandanten - nicht anfassen, nicht zuordnen
            change = {"field": "name", "old": have.obj.get("name"), "new": want.fields.get("name")}
            items.append(_item(want.kind, want.key, want.label, "conflict", id=have.id,
                               manual=True, changes=[change]))  # fmt: skip
            continue
        changes = _changes(want, have, state, wants)
        if not changes:
            action = "unchanged"
        else:
            action = "update" if have.tagged else "conflict"
        items.append(
            _item(
                want.kind,
                want.key,
                want.label,
                action,
                id=have.id,
                manual=not have.tagged,
                changes=changes,
            )
        )
    for kind in KINDS:
        if kind.name in ASSIGN or (kind.name in VIRTUAL_KINDS and "virtualization" not in chosen):
            continue  # VMs ohne den Abschnitt nur fuer die Applikations-Tags gelesen
        wanted = wants.keys_of(kind.name)
        for key, have in sorted(state[kind.name].items()):
            if key in wanted or not _in_scope(kind, have, state, tslug, gprefix, foreign):
                continue
            items.append(
                _item(
                    kind.name,
                    key,
                    f"{kind.label} {key}",
                    "delete" if allow_delete else "obsolete",
                    id=have.id,
                )
            )
    if "applications" in chosen:
        gone = {(i["kind"], i["key"]) for i in items if i["action"] == "delete"}
        items += _assignments(wants, state, gone)
    return items


def _occupied(want: Want, state: State, removed: set[int]) -> list[dict]:
    """Enden eines neuen Kabels, die in NetBox schon ein (anderes, bleibendes) Kabel tragen."""
    out = []
    for side in TERMINATIONS:
        for ref in want.refs.get(side) or []:
            have = state["interface"].get(ref[1])
            cable = _ref_id(have.obj.get("cable")) if have else None
            if cable is not None and cable not in removed:
                out.append({"field": ref[1], "old": f"#{cable}", "new": want.key})
    return out


def _ref_id(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _mine(wants: Wants, state: State) -> set[str]:
    """Applikations-Tags dieses Mandanten: in NetBox eigene und neu angelegte."""
    owned = {slug for slug, have in state["tag"].items() if have.tagged}
    new = {
        w.key
        for w in wants.items.values()
        if w.kind == "tag" and w.key.startswith(APP_TAG_PREFIX) and w.key not in state["tag"]
    }
    return owned | new


def _assignments(wants: Wants, state: State, gone: set[tuple[str, str]]) -> list[dict]:
    """Applikations-Tags an VMs und IP-Adressen: eigene Tags setzen bzw. entfernen, fremde
    bleiben. Nur an Objekten mit Tag `automato`; an von Hand gepflegten ist eine Abweichung ein
    Konflikt (bleibt, wie es ist). Objekte, die der Sync loescht, bekommen nichts."""
    mine = _mine(wants, state)
    items: list[dict] = []
    for kind, target in ASSIGN.items():
        label = KIND[kind].label
        wanted = sorted((w for w in wants.items.values() if w.kind == kind), key=lambda w: w.key)
        rows = [(w.key, sorted(set(w.fields["tags"]) & mine)) for w in wanted]
        seen = {key for key, _ in rows}
        rows += [(key, []) for key in sorted(state[target]) if key not in seen]
        for key, desired in rows:
            if (target, key) in gone:
                continue
            have = state[target].get(key)
            current = sorted(_tag_slugs(have.obj) & mine) if have else []
            if current == desired:
                if have is not None and desired:
                    items.append(_item(kind, key, f"{label} {key}", "unchanged", id=have.id))
                continue
            change = [{"field": "tags", "old": current, "new": desired}]
            if have is not None and not have.tagged:
                items.append(_item(kind, key, f"{label} {key}", "conflict", id=have.id,
                                   manual=True, changes=change))  # fmt: skip
                continue
            items.append(_item(kind, key, f"{label} {key}", "update",
                               id=have.id if have else None, changes=change))  # fmt: skip
    return items


def _item(kind: str, key: str, label: str, action: str, **extra) -> dict:
    return {"kind": kind, "key": key, "label": label, "action": action, **extra}


def _preview(want: Want) -> dict:
    out = {k: v for k, v in want.fields.items() if v not in (None, "", [])}
    for f, t in want.refs.items():
        if isinstance(t, list):
            if t:
                out[f] = [x[1] for x in t]
        elif t is not None:
            out[f] = t[1]
    return out


def summarize(items: list[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for i in items:
        entry = out.setdefault(i["kind"], {a: 0 for a in ACTIONS})
        entry[i["action"]] += 1
    return out


def fingerprint(items: list[dict]) -> str:
    """Fingerabdruck der Schreib-Aktionen (Vorschau == Uebernahme)."""
    relevant = [
        [i["kind"], i["key"], i["action"], i.get("fields"), i.get("changes")]
        for i in items
        if i["action"] in WRITE_ACTIONS
    ]
    raw = json.dumps(relevant, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


# -- Plan berechnen ----------------------------------------------------------------


@dataclass
class Plan:
    wants: Wants
    state: State
    items: list[dict]
    fingerprint: str


def compute(
    session: Session,
    writer,
    tenant: Tenant,
    target: SourceSystem,
    sections: list[str],
    allow_delete: bool,
) -> Plan:
    wants = build_wants(session, tenant, target, sections)
    tslug = tenant_slug(tenant)
    gprefix = f"{tslug}-"
    tenants = [tenant_slug(t) for t in session.scalars(select(Tenant))]
    state = read_state(writer, wants, tslug, gprefix, sections, tenants)
    resolve_after_read(wants, state)
    unique_names(wants, state, tenant.name)
    platforms = dict(
        session.execute(
            select(SourceSystem.name, SourceSystem.platform).where(
                SourceSystem.tenant_id == tenant.id
            )
        ).all()
    )
    items = diff(wants, state, tslug, gprefix, allow_delete, sections, platforms)
    return Plan(wants, state, items, fingerprint(items))


# -- Uebernehmen --------------------------------------------------------------------


def apply(writer, plan: Plan, progress=None) -> dict[str, dict[str, int]]:
    """Schreib-Aktionen des (frisch berechneten) Plans ausfuehren; Rueckgabe: Anzahlen.

    Bricht beim ersten Fehler ab (Teilstand bleibt - der naechste Plan setzt dort an).
    `progress(kind)` vor jeder Objektart: Lebenszeichen; darf abbrechen (Exception).
    """
    ids: dict[tuple[str, str], int] = {
        (kind, key): h.id for kind, haves in plan.state.items() for key, h in haves.items()
    }
    done: dict[str, dict[str, int]] = defaultdict(lambda: {"create": 0, "update": 0, "delete": 0})
    by_kind: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for i in plan.items:
        if i["action"] in WRITE_ACTIONS:
            by_kind[i["kind"]][i["action"]].append(i)

    def value(ref: Ref | list[Ref] | None) -> Any:
        if isinstance(ref, list):
            return [ids[r] for r in ref if r in ids]
        return ids.get(ref) if ref is not None else None

    for kind in KINDS:
        actions = by_kind.get(kind.name, {})
        if progress is not None and actions:
            progress(kind.name)
        if kind.name in ASSIGN:  # Applikations-Tags: Tags des Ziels setzen (fremde bleiben)
            mine = _mine(plan.wants, plan.state)
            patches = [_tag_patch(plan, kind.name, i, ids, mine) for i in actions.get("update", [])]
            patches = [p for p in patches if p is not None]
            if patches:
                writer.update(kind.endpoint, patches)
                done[kind.name]["update"] += len(patches)
            continue
        if kind.name == "cable":  # erst weg, dann neu: ein umgesteckter Port ist wieder frei
            dels = [i["id"] for i in actions.get("delete", [])]
            if dels:
                writer.delete(kind.endpoint, dels)
                done[kind.name]["delete"] += len(dels)
        creates = [plan.wants.get(kind.name, i["key"]) for i in actions.get("create", [])]
        creates = [c for c in creates if c is not None]
        if creates:
            bodies = []
            for want in creates:
                body = {k: v for k, v in want.fields.items() if v not in (None, "")}
                for f, ref in want.refs.items():
                    if _self_ref(kind, ref):
                        continue  # nach dem Anlegen nachziehen (LAG/Parent)
                    v = value(ref)
                    if v not in (None, []):
                        body[f] = _terminations(v) if f in TERMINATIONS else v
                if "assigned_object_type" in body and "assigned_object_id" not in body:
                    body.pop("assigned_object_type")
                if kind.name != "tag":
                    body["tags"] = [{"slug": TAG}]
                bodies.append(body)
            created = writer.create(kind.endpoint, bodies)
            for want, obj in zip(creates, created, strict=True):
                ids[(kind.name, want.key)] = obj["id"]
            done[kind.name]["create"] += len(created)
            later = []
            for want in creates:
                patch = {
                    f: value(ref)
                    for f, ref in want.refs.items()
                    if _self_ref(kind, ref) and value(ref) is not None
                }
                if patch:
                    later.append({"id": ids[(kind.name, want.key)], **patch})
            if later:
                writer.update(kind.endpoint, later)
        updates = []
        for i in actions.get("update", []):
            want = plan.wants.get(kind.name, i["key"])
            if want is None:
                continue
            body: dict[str, Any] = {"id": i["id"]}
            for ch in i.get("changes", []):
                f = ch["field"]
                body[f] = value(want.refs[f]) if f in want.refs else want.fields.get(f)
                if f == "assigned_object_id" and body[f] is not None:
                    body["assigned_object_type"] = want.fields.get("assigned_object_type")
            updates.append(body)
        if updates:
            writer.update(kind.endpoint, updates)
            done[kind.name]["update"] += len(updates)
    for kind in reversed(KINDS):
        if kind.name == "cable":
            continue  # schon vor dem Anlegen
        dels = [i["id"] for i in by_kind.get(kind.name, {}).get("delete", [])]
        if dels and progress is not None:
            progress(kind.name)
        if dels:
            writer.delete(kind.endpoint, dels)
            done[kind.name]["delete"] += len(dels)
    return {k: dict(v) for k, v in done.items()}


def _self_ref(kind: Kind, ref: Ref | list[Ref] | None) -> bool:
    return isinstance(ref, tuple) and ref[0] == kind.name


def _terminations(ids: list[int]) -> list[dict]:
    return [{"object_type": "dcim.interface", "object_id": i} for i in ids]


def _tag_patch(plan: Plan, kind: str, item: dict, ids: dict, mine: set[str]) -> dict | None:
    """PATCH der Tags eines Ziels: alle fremden Tags (auch `automato`) bleiben, die eigenen
    Applikations-Tags werden zu denen der Vorschau. Neu angelegte Ziele tragen `automato`."""
    target = ASSIGN[kind]
    tid = ids.get((target, item["key"]))
    if tid is None:
        return None
    have = plan.state[target].get(item["key"])
    keep = _tag_slugs(have.obj) - mine if have is not None else {TAG}
    desired = set(item["changes"][0]["new"]) if item.get("changes") else set()
    return {"id": tid, "tags": [{"slug": s} for s in sorted(keep | desired)]}


# -- Anzeige in der Sprache der Anfrage (ADR 0028) -----------------------------------------
# Gespeichert (am Plan) und nach NetBox geschrieben wird deutsch; uebersetzt wird nur die Anzeige.

KIND_EN = {
    "site_group": "Site group",
    "site": "Site",
    "manufacturer": "Manufacturer",
    "device_role": "Device role",
    "device_type": "Device type",
    "cluster_type": "Cluster type",
    "cluster_group": "Cluster group",
    "vlan_group": "VLAN group",
    "prefix": "Network",
    "device": "Device",
    "cable": "Cable",
    "vm_interface": "VM interface",
    "ip_address": "IP address",
    "app_tag_vm": "Application tag on VM",
    "app_tag_ip": "Application tag on IP address",
}
_SITE_KIND_EN = {"virtuell": "virtual", "physisch": "physical"}

# Hinweise der Vorschau (`Wants.warnings`): deutsche Form wie in `build_wants` -> englisch
WARNINGS_EN = (
    (
        "{src}: kein Standort gesetzt – {n} Geräte und ihre Ports werden nicht übernommen "
        "(Standort am Quellsystem wählen).",
        "{src}: no site set – {n} devices and their ports are not applied "
        "(choose a site on the source system).",
    ),
    (
        "Gerät {name} kommt mehrfach vor – {src} übersprungen.",
        "Device {name} occurs more than once – {src} skipped.",
    ),
    (
        "DDI-Spiegel: kein anderes führendes IPAM – diese NetBox ist selbst führend oder es ist "
        "kein IPAM eingelesen.",
        "DDI mirror: no other leading IPAM – this NetBox is leading itself or no IPAM has been "
        "collected.",
    ),
    (
        "VM {name} ({src}): ohne Cluster und Host – nicht übernommen.",
        "VM {name} ({src}): without cluster and host – not applied.",
    ),
    (
        "VM {name} kommt mehrfach vor – {src} übersprungen.",
        "VM {name} occurs more than once – {src} skipped.",
    ),
    # Verkabelung und Applikationen (ADR 0031)
    (
        "Gerät {name} ({src}): Modell nicht gelesen – ohne Gerätetyp mit Ports und Kabeln nicht "
        "übernommen.",
        "Device {name} ({src}): model not read – without device type not applied, nor its ports "
        "and cables.",
    ),
    (
        "{device}: Modus/VLANs an {n} Ports weggelassen – VLAN {vids} nicht in NetBox.",
        "{device}: mode/VLANs left out on {n} ports – VLAN {vids} not in NetBox.",
    ),
    (
        "{device}: Modus/VLANs an {n} Ports weggelassen – VLAN {vids} in NetBox nicht eindeutig.",
        "{device}: mode/VLANs left out on {n} ports – VLAN {vids} not unique in NetBox.",
    ),
    (
        "Verkabelung: {n} einseitig gemeldete Verbindungen nicht als Kabel übernommen "
        "(z. B. {examples}).",
        "Cabling: {n} links reported by one side only not applied as cables (e.g. {examples}).",
    ),
    (
        "Verkabelung: {n} Verbindungen zu Geräten ohne CDP/LLDP-Daten (z. B. Firewall, ESXi-Host) "
        "nicht als Kabel übernommen.",
        "Cabling: {n} links to devices without CDP/LLDP data (e.g. firewall, ESXi host) not "
        "applied as cables.",
    ),
    (
        "Verkabelung: {n} Verbindungen an Ports mit mehreren Nachbarn nicht als Kabel übernommen "
        "(z. B. {examples}).",
        "Cabling: {n} links on ports with several neighbors not applied as cables "
        "(e.g. {examples}).",
    ),
    (
        "Verkabelung: {n} Kabel nicht übernommen – Port nicht in NetBox (Gerät ohne Standort bzw. "
        "Modell oder Abschnitt Netzinventar nicht gewählt), z. B. {examples}.",
        "Cabling: {n} cables not applied – port not in NetBox (device without site or model, or "
        "section network inventory not chosen), e.g. {examples}.",
    ),
    (
        "Applikationen: {vms} VMs und {ips} IP-Adressen nicht in NetBox – ohne Applikations-Tag "
        "(Abschnitte Virtualisierung bzw. DDI-Spiegel synchronisieren), z. B. {examples}.",
        "Applications: {vms} VMs and {ips} IP addresses not in NetBox – without application tag "
        "(synchronize the sections virtualization or DDI mirror), e.g. {examples}.",
    ),
)


def kind_label(name: str) -> str:
    """Art fuer die Anzeige (`Kind.label` bleibt deutsch)."""
    kind = KIND.get(name)
    de = kind.label if kind else name
    return L(de, KIND_EN.get(name, de))


def _label_prefixes() -> list[tuple[str, str]]:
    pairs = [(f"{k.label} ", f"{KIND_EN[k.name]} ") for k in KINDS if k.name in KIND_EN]
    return sorted(pairs, key=lambda p: -len(p[0]))


def show_label(label: str) -> str:
    """Gespeicherte Bezeichnung einer Aktion (`Geraet fw-01`) fuer die Anzeige: die Art vorne
    und beim Standort die Art hinten; Namen bleiben, wie sie in NetBox stehen."""
    if lang() != "en" or not label:
        return label
    for de, en in _label_prefixes():
        if label.startswith(de):
            label = en + label[len(de) :]
            break
    if label.startswith("Site "):
        for de, en in _SITE_KIND_EN.items():
            if label.endswith(f" ({de})"):
                return label[: -len(de) - 1] + en + ")"
    return label


@lru_cache(maxsize=128)
def _template(de: str) -> re.Pattern:
    parts = re.split(r"\{(\w+)\}", de)
    return re.compile(
        "".join(re.escape(x) if i % 2 == 0 else f"(?P<{x}>.+?)" for i, x in enumerate(parts))
    )


def shown(text: str | None, table, values=None) -> str | None:
    """Gespeicherter deutscher Text -> Anzeige in der Sprache der Anfrage: die erste passende
    deutsche Form der Tabelle (`{name}` = beliebiger Teil) wird zur englischen; unbekannte Texte
    bleiben. `values(name, wert)` uebersetzt einzelne Teile."""
    if not text or lang() != "en":
        return text
    for de, en in table:
        m = _template(de).fullmatch(text)
        if m:
            parts = {k: values(k, v) if values else v for k, v in m.groupdict().items()}
            return en.format(**parts)
    return text


def show_warning(text: str) -> str:
    return shown(text, WARNINGS_EN) or text
