"""NetBox-Sync (ADR 0010): Soll-Stand aus Automato -> Vorschau -> Uebernahme per Klick.

Einziger Schreibweg der Plattform. Ablauf:

1. **Soll** (`build_wants`): aus dem Automato-Bestand eines Mandanten
   - Netzinventar (Abschnitt `inventory`): Geraete mit Ports (Modus, VLANs, LAG, Parent,
     Interface-IPs), VLANs je Quellsystem (VLAN-Gruppe), reale Subnetze (inkl. ACI
     BD/EPG/VRF), VRFs - Automato ist hier fuehrend;
   - DDI-Spiegel (Abschnitt `mirror`): Netze, Ranges und IPs mit DNS-Name aus dem
     fuehrenden IPAM (z. B. Infoblox), wenn die Ziel-NetBox nicht selbst fuehrend ist.
2. **Ist** (`read_state`): NetBox lesen (nur GET).
3. **Vorschau** (`diff`): je Objekt anlegen | aendern | unveraendert | Konflikt | loeschen.
   Geaendert/geloescht wird nur, was das Tag `automato` traegt; von Hand gepflegte Objekte
   werden hoechstens referenziert - weichen sie ab, ist das ein *Konflikt* (bleibt, wie es
   ist). Loeschen nur mit `allow_delete`, sonst als *veraltet* angezeigt.
4. **Uebernahme** (`apply`): Plan neu berechnen, Fingerabdruck muss zur Vorschau passen,
   dann Bulk-Schreiben in Abhaengigkeitsreihenfolge; Loeschen in umgekehrter Reihenfolge.
"""

import hashlib
import ipaddress
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    InvDevice,
    InvInterface,
    InvSubnet,
    InvVlan,
    InvVrf,
    Platform,
    Site,
    SourceSystem,
    Tenant,
)
from app.store import references

TAG = "automato"
SECTIONS = ("inventory", "mirror")
INVENTORY_PLATFORMS = (Platform.FORTIGATE, Platform.CISCO_ACI)
WRITE_ACTIONS = ("create", "update", "delete")
ACTIONS = ("create", "update", "unchanged", "conflict", "delete", "obsolete")


@dataclass(frozen=True)
class Kind:
    name: str
    endpoint: str
    label: str
    managed: tuple[str, ...]  # Felder, die der Sync aktuell haelt (Aenderung/Konflikt)
    scope: str = ""  # "" = nie loeschen | tenant | group | device


# Reihenfolge = Abhaengigkeiten (Anlegen vorwaerts, Loeschen rueckwaerts)
KINDS = (
    Kind("tag", "extras/tags", "Tag", ()),
    Kind("tenant", "tenancy/tenants", "Tenant", ("name",)),
    Kind("site", "dcim/sites", "Standort", ("name",)),
    Kind("manufacturer", "dcim/manufacturers", "Hersteller", ()),
    Kind("device_role", "dcim/device-roles", "Geräterolle", ()),
    Kind("device_type", "dcim/device-types", "Gerätetyp", ()),
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
    Kind(
        "ip_address",
        "ipam/ip-addresses",
        "IP-Adresse",
        ("address", "tenant", "dns_name", "description", "status", "assigned_object_id"),
        "tenant",
    ),
)
KIND = {k.name: k for k in KINDS}

Ref = tuple[str, str]  # (Art, Schluessel)


@dataclass
class Want:
    kind: str
    key: str
    label: str
    fields: dict[str, Any]
    refs: dict[str, Ref | list[Ref] | None] = field(default_factory=dict)


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
class Wants:
    items: dict[tuple[str, str], Want] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

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


_MANUFACTURER = {Platform.FORTIGATE: "Fortinet", Platform.CISCO_ACI: "Cisco"}
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
    sites = {s.id: s for s in session.scalars(select(Site).where(Site.tenant_id == tenant.id))}
    for s in sites.values():
        w.add(
            Want(
                "site",
                slugify(s.code),
                f"Standort {s.code} {s.name}",
                {
                    "name": _cut(s.name or s.code, 100),
                    "slug": slugify(s.code),
                    "status": "active",
                    "description": _cut(s.description, 200),
                },
            )
        )
    if "inventory" in sections:
        _inventory_wants(session, w, tenant, sites, tenant_ref)
    if "mirror" in sections:
        _mirror_wants(session, w, tenant, target, tenant_ref)
    return w


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
    only_site = next(iter(sites.values())) if len(sites) == 1 else None

    def rows(model, source):
        return list(
            session.scalars(
                select(model).where(model.source_system_id == source.id).order_by(model.id)
            )
        )

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
            w.warnings.append(
                f"{src.name}: kein Standort gesetzt – {len(devices)} Geräte und ihre Ports werden"
                " nicht übernommen (Standort am Quellsystem wählen)."
            )
            continue
        manufacturer = _MANUFACTURER.get(src.platform, "Unbekannt")
        for d in devices:
            if w.has("device", d.name):
                w.warnings.append(f"Gerät {d.name} kommt mehrfach vor – {src.name} übersprungen.")
                continue
            role = slugify(d.role or "device")
            model = d.model or f"{manufacturer} {d.role or 'device'}"
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
                {"prefix": value, "status": "active", "description": desc},
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


def _plen(index, ip: str) -> int:
    prefixes = index.containing_prefixes(ip)
    if prefixes:
        net = _net(prefixes[0].value)
        if net:
            return int(net.split("/")[1])
    return 128 if ":" in ip else 32


# -- Ist --------------------------------------------------------------------------


def _key_of(kind: str, obj: dict) -> str | None:
    if kind in ("tag", "tenant", "site", "manufacturer", "device_role", "vlan_group"):
        return obj.get("slug")
    if kind == "device_type":
        return (obj.get("model") or "").lower() or None
    if kind in ("vrf", "device"):
        return obj.get("name")
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
    if kind == "ip_address":
        return _ip(obj.get("address"))
    return None


def _tagged(obj: dict) -> bool:
    return any((t or {}).get("slug") == TAG for t in obj.get("tags") or [])


def read_state(writer, wants: Wants, tslug: str, gprefix: str) -> State:
    """NetBox-Ist fuer die Arten des Syncs (nur GET)."""
    state: State = {k.name: {} for k in KINDS}

    def put(kind: str, objs: list[dict]) -> None:
        for obj in objs:
            key = _key_of(kind, obj)
            if key is not None and key not in state[kind]:
                state[kind][key] = Have(obj["id"], key, obj, _tagged(obj))

    for k in KINDS:
        if k.name in ("vlan", "interface"):
            continue
        put(k.name, writer.read(k.endpoint, {"slug": TAG} if k.name == "tag" else {}))

    our_tenant = state["tenant"].get(tslug)
    groups = [
        h.id
        for key, h in state["vlan_group"].items()
        if wants.has("vlan_group", key) or (h.tagged and key.startswith(gprefix))
    ]
    for chunk in _chunks(groups, 50):
        put("vlan", writer.read("ipam/vlans", {"group_id": chunk}))
    devices = [
        h.id
        for key, h in state["device"].items()
        if wants.has("device", key) or (h.tagged and _tenant_id(h.obj) == _id(our_tenant))
    ]
    for chunk in _chunks(devices, 50):
        put("interface", writer.read("dcim/interfaces", {"device_id": chunk}))
    return state


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _id(have: Have | None) -> int | None:
    return have.id if have else None


def _tenant_id(obj: dict) -> int | None:
    t = obj.get("tenant")
    return t.get("id") if isinstance(t, dict) else t


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
    for f in KIND[want.kind].managed:
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
                if new_id is not _PENDING and _same(_norm(old_raw), new_id):
                    continue
                out.append({"field": f, "old": _display(old_raw), "new": target[1]})
        elif f in want.fields:
            new = want.fields[f]
            if _same(_norm(old_raw), new):
                continue
            out.append({"field": f, "old": _display(old_raw), "new": new})
    return out


def _in_scope(kind: Kind, have: Have, state: State, tslug: str, gprefix: str) -> bool:
    if not have.tagged or not kind.scope:
        return False
    our = _id(state["tenant"].get(tslug))
    if kind.scope == "tenant":
        return our is not None and _tenant_id(have.obj) == our
    if kind.scope == "group":
        return have.key.startswith(gprefix)
    if kind.scope == "device":
        dev = (have.obj.get("device") or {}).get("name")
        dev_have = state["device"].get(dev) if dev else None
        return dev_have is not None and dev_have.tagged and _tenant_id(dev_have.obj) == our
    return False


def diff(wants: Wants, state: State, tslug: str, gprefix: str, allow_delete: bool) -> list[dict]:
    items: list[dict] = []
    for want in wants.ordered():
        have = state[want.kind].get(want.key)
        if have is None:
            items.append(_item(want.kind, want.key, want.label, "create", fields=_preview(want)))
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
        wanted = wants.keys_of(kind.name)
        for key, have in sorted(state[kind.name].items()):
            if key in wanted or not _in_scope(kind, have, state, tslug, gprefix):
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
    state = read_state(writer, wants, tslug, gprefix)
    items = diff(wants, state, tslug, gprefix, allow_delete)
    return Plan(wants, state, items, fingerprint(items))


# -- Uebernehmen --------------------------------------------------------------------


def apply(writer, plan: Plan) -> dict[str, dict[str, int]]:
    """Schreib-Aktionen des (frisch berechneten) Plans ausfuehren; Rueckgabe: Anzahlen.

    Bricht beim ersten Fehler ab (Teilstand bleibt - der naechste Plan setzt dort an).
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
                        body[f] = v
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
        dels = [i["id"] for i in by_kind.get(kind.name, {}).get("delete", [])]
        if dels:
            writer.delete(kind.endpoint, dels)
            done[kind.name]["delete"] += len(dels)
    return {k: dict(v) for k, v in done.items()}


def _self_ref(kind: Kind, ref: Ref | list[Ref] | None) -> bool:
    return isinstance(ref, tuple) and ref[0] == kind.name
