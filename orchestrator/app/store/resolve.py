"""Name bzw. FQDN -> IP fuer die Weg-Suche (0.35.0, Dev-Befund 0.34.0; Netze seit 0.38.0).

Fuer die Weg-Suche muss niemand die IPs kennen: Quelle und Ziel duerfen ein Name sein. Gesucht
wird in dem, was gelesen ist - nur lesend, je Mandant:

**Hosts**
1. **IP** direkt (mit dem Namen aus dem IPAM),
2. **IPAM** (DDI bzw. NetBox, aktueller Referenzstand): DNS-Name bzw. Name einer IP, eines
   Host- oder A-Records,
3. **Objekte der Firewalls** (Adressobjekte mit einer IP, auch ohne Konventions-Praefix:
   `web01` findet `H_web01`), FQDN-Objekte ueber ihren FQDN im IPAM,
4. **VMs** (vCenter) ueber Namen bzw. Hostname mit ihren IPs.

**Netze** (0.38.0, Dev-Befund 0.37.0)
5. **Netz** direkt (`10.20.0.0/24`),
6. **IPAM-Netze** (Prefix ueber Name, Beschreibung bzw. Anfang des Netzes),
7. **Netz-Objekte der Firewalls** (Adressobjekte mit einem Netz, ohne 0.0.0.0/0),
8. **Subnetze des Inventars** (FortiGate-Interfaces, ACI-BD/-EPG ueber Tenant, AP, EPG, BD).

Ein Netz wird mit einer Adresse daraus geprueft (`ip`): der ersten, die keinem gelesenen
Geraet gehoert (Interface bzw. Gateway) - die Oberflaeche zeigt sie an.

Treffer zuerst exakt, dann am Anfang, dann irgendwo im Namen; je IP bzw. Netz einmal. Die
Antwort ist seitenweise mit Gesamtzahl und Anzahl je Art (Hosts, Netze); je Quelle hoechstens
`CAP` Eintraege (`capped`: genauer suchen).

Dienste (0.35.0): `FT_https` bzw. `Tenant/FT_https` -> die Dienste des Service-Objekts bzw.
ACI-Filters (`tcp/443`), Gruppen aufgeloest.
"""

import ipaddress
import re

from sot_gateway.models import IP_KINDS, ReferenceKind
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    DeviceObject,
    InvInterface,
    InvSubnet,
    ReferenceRecordRow,
    SourceSystem,
    Vm,
    VmIp,
)
from app.models.device_object import ObjectCategory
from app.store import references

LIMIT = 50
CAP = 2000  # je Quelle
TYPES = ("host", "net")
_ADDRESSISH = re.compile(r"^[0-9a-f.:]+(/\d{1,3})?$")


def _ip(value: str | None) -> str | None:
    """Einzelne IP aus `10.0.0.5`, `10.0.0.5/32`; Netze und Bereiche -> None."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        if "/" in text:
            net = ipaddress.ip_network(text, strict=False)
            return str(net.network_address) if net.num_addresses == 1 else None
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def _net(value: str | None):
    """Netz aus `10.0.0.0/24` (auch `10.0.0.5/24`); einzelne IPs, 0/0 und Bereiche -> None."""
    text = (value or "").strip()
    if "/" not in text:
        return None
    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None
    return net if net.num_addresses > 1 and net.prefixlen > 0 else None


def _hit(ip: str, name: str | None, kind: str, source: str | None, detail: str | None) -> dict:
    return {"ip": ip, "name": name, "kind": kind, "type": "host", "net": None,
            "source": source, "detail": detail}  # fmt: skip


def _net_hit(net, name, kind: str, source: str | None, detail: str | None) -> dict:
    return {"ip": None, "name": name, "kind": kind, "type": "net", "net": str(net),
            "source": source, "detail": detail}  # fmt: skip


def _rank(name: str, q: str) -> int:
    low = name.lower()
    short = low.split(".", 1)[0]
    if q in (low, short):
        return 0
    if low.startswith(q) or short.endswith(q) or f"_{q}" in low or f"|{q}" in low:
        return 1
    return 2


def _join(*parts) -> str | None:
    return " · ".join(str(p) for p in parts if p) or None


def resolve(
    session: Session,
    tenant_id: int,
    query: str,
    limit: int = LIMIT,
    offset: int = 0,
    only: str | None = None,
) -> dict:
    """Treffer fuer `query`: {hits, total, counts {host, net}, capped}; je Treffer {ip, name,
    kind (ip|ipam|object|vm|net|subnet), type (host|net), net, source, detail, also}."""
    q = (query or "").strip().lower()
    if not _ADDRESSISH.match(q):  # FQDN mit Punkt am Ende; `10.20.1.` bleibt (nicht 10.20.100)
        q = q.rstrip(".")
    hits = _collect(session, tenant_id, q) if q else ([], False)
    found, capped = hits
    counts = {t: sum(1 for h in found if h["type"] == t) for t in TYPES}
    chosen = [h for h in found if only in (None, h["type"])]
    page = chosen[offset : offset + limit]
    if any(h["type"] == "net" for h in page):
        owned = _owned(session, tenant_id)
        for h in page:
            if h["type"] == "net":
                h["ip"] = _sample(ipaddress.ip_network(h["net"]), owned)
    return {"hits": page, "total": len(chosen), "counts": counts, "capped": capped}


def _collect(session: Session, tenant_id: int, q: str) -> tuple[list[dict], bool]:
    literal = _ip(q)
    if literal is not None:
        ctx = references.context(session, tenant_id)
        names = references.dns_names(ctx, literal)
        first = names[0] if names else None
        return [_hit(literal, first, "ip", None, ", ".join(names[1:]) or None)], False
    runs = [s.reference_run_id for s in references.tenant_reference_sources(session, tenant_id)]
    names = dict(
        session.execute(
            select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
        ).all()
    )
    if (net := _net(q)) is not None:
        return _literal_net(session, tenant_id, runs, names, net), False
    if len(q) < 2:
        return [], False
    found = _Found()
    _hosts(session, tenant_id, runs, names, q, found)
    _nets(session, tenant_id, runs, names, q, found)
    return found.merged(), found.capped


class _Found:
    """Treffer mit (Rang, Quelle) - je IP bzw. Netz einmal, weitere Namen unter `also`."""

    def __init__(self):
        self.rows: list[tuple[int, int, dict]] = []
        self.capped = False

    def add(self, rank: int, source: int, hit: dict) -> None:
        self.rows.append((rank, source, hit))

    def scan(self, rows):
        """Zeilen einer Quelle (hoechstens CAP); `capped`, wenn es mehr gaebe."""
        out = list(rows)
        self.capped = self.capped or len(out) >= CAP
        return out

    def merged(self) -> list[dict]:
        out: dict[str, dict] = {}
        for *_order, hit in sorted(self.rows, key=lambda h: (h[0], h[1], _key_name(h[2]))):
            key = f"net:{hit['net']}" if hit["type"] == "net" else hit["ip"]
            if key in out:
                first = out[key]
                also = first.setdefault("also", [])
                if hit["name"] and hit["name"] != first["name"] and hit["name"] not in also:
                    also.append(hit["name"])
                continue
            out[key] = hit
        return list(out.values())


def _key_name(hit: dict) -> str:
    return (hit["name"] or hit["net"] or hit["ip"] or "").lower()


def _hosts(session, tenant_id, runs, names, q, found: _Found) -> None:
    like = f"%{q}%"
    by_value = _ADDRESSISH.match(q) is not None  # Anfang einer IP (10.20.0.)
    if runs:
        conds = [
            func.lower(ReferenceRecordRow.dns_name).like(like),
            func.lower(ReferenceRecordRow.name).like(like),
        ]
        if by_value:
            conds.append(ReferenceRecordRow.value.like(f"{q}%"))
        for r in found.scan(
            session.scalars(
                select(ReferenceRecordRow)
                .where(
                    ReferenceRecordRow.collector_run_id.in_(runs),
                    ReferenceRecordRow.kind.in_(sorted(IP_KINDS)),
                    or_(*conds),
                )
                .limit(CAP)
            )
        ):
            ip = _ip(r.value)
            label = r.dns_name or r.name
            if ip and (label or by_value):
                source = names.get(r.source_system_id)
                rank = _rank(label, q) if label and q in label.lower() else 1
                found.add(rank, 0, _hit(ip, label, "ipam", source, r.kind))
    fqdn_objects: list[DeviceObject] = []
    for o in found.scan(
        session.scalars(
            select(DeviceObject)
            .where(
                DeviceObject.tenant_id == tenant_id,
                DeviceObject.category.in_((ObjectCategory.ADDRESS, ObjectCategory.VIP_TARGET)),
                or_(
                    func.lower(DeviceObject.name).like(like),
                    func.lower(DeviceObject.fqdn).like(like),
                    *([cast(DeviceObject.values, String).like(f'%"{q}%')] if by_value else []),
                ),
            )
            .limit(CAP)
        )
    ):
        ips = [ip for ip in (_ip(v) for v in o.values or []) if ip]
        nets = [n for n in (_net(v) for v in o.values or []) if n]
        if not ips and not nets and o.fqdn:
            fqdn_objects.append(o)
        source = names.get(o.source_system_id)
        detail = _join(o.obj_type, f"VDOM {o.vdom}" if o.vdom else None)
        rank = _rank(o.name, q) if q in o.name.lower() else 1
        for ip in ips:
            if not by_value or q in o.name.lower() or ip.startswith(q):
                found.add(rank, 1, _hit(ip, o.name, "object", source, detail))
        for net in nets:  # Netz-Objekt (0.38.0)
            if not by_value or q in o.name.lower() or str(net).startswith(q):
                found.add(rank, 1, _net_hit(net, o.name, "object", source, detail))
    if fqdn_objects:  # FQDN-Objekt: die IP(s) des FQDN aus dem IPAM
        by_name = _ipam_by_dns(session, runs, {o.fqdn.lower().rstrip(".") for o in fqdn_objects})
        for o in fqdn_objects:
            for ip in by_name.get(o.fqdn.lower().rstrip("."), []):
                source = names.get(o.source_system_id)
                hit = _hit(ip, o.name, "object", source, f"FQDN {o.fqdn}")
                found.add(_rank(o.name, q), 1, hit)
    conds = [func.lower(Vm.name).like(like), func.lower(Vm.hostname).like(like)]
    if by_value:
        conds.append(VmIp.ip.like(f"{q}%"))
    for vm, ip in found.scan(
        session.execute(
            select(Vm, VmIp.ip)
            .join(VmIp, VmIp.vm_id == Vm.id)
            .where(Vm.tenant_id == tenant_id, or_(*conds))
            .limit(CAP)
        )
    ):
        if (addr := _ip(ip)) is not None:
            source = names.get(vm.source_system_id)
            rank = _rank(vm.name, q) if q in vm.name.lower() else 1
            found.add(rank, 2, _hit(addr, vm.name, "vm", source, vm.hostname))


def _prefix_hit(r: ReferenceRecordRow, net, source: str | None) -> dict:
    label = r.name if r.name and r.name != r.value else r.description
    detail = _join(r.description if label != r.description else None,
                   f"VRF {r.vrf}" if r.vrf else None, r.site,
                   f"VLAN {r.vlan_vid}" if r.vlan_vid else None)  # fmt: skip
    return _net_hit(net, label, "ipam", source, detail)


def _subnet_label(s: InvSubnet) -> tuple[str, str | None]:
    """Name und Detail eines Inventar-Subnetzes: EPG `Tenant|AP|EPG`, BD bzw. Interface."""
    if s.context == "epg" and s.epg:
        return "|".join(x for x in (s.aci_tenant, s.ap, s.epg) if x), _join(
            "EPG-Subnetz", f"BD {s.bd}" if s.bd else None, f"VRF {s.vrf}" if s.vrf else None
        )
    if s.context == "bd":
        name = "|".join(x for x in (s.aci_tenant, s.bd or s.context_name) if x)
        epg = "|".join(x for x in (s.ap, s.epg) if x)
        return name, _join("BD-Subnetz", f"EPG {epg}" if epg else None,
                           f"VRF {s.vrf}" if s.vrf else None)  # fmt: skip
    return s.context_name, _join(
        "Interface", f"VDOM {s.vdom}" if s.vdom else None, s.description,
        f"VLAN {s.vlan}" if s.vlan else None,
    )  # fmt: skip


def _nets(session, tenant_id, runs, names, q, found: _Found) -> None:
    like = f"%{q}%"
    by_value = _ADDRESSISH.match(q) is not None
    if runs:
        conds = [
            func.lower(ReferenceRecordRow.name).like(like),
            func.lower(ReferenceRecordRow.description).like(like),
        ]
        if by_value:
            conds.append(ReferenceRecordRow.value.like(f"{q}%"))
        for r in found.scan(
            session.scalars(
                select(ReferenceRecordRow)
                .where(
                    ReferenceRecordRow.collector_run_id.in_(runs),
                    ReferenceRecordRow.kind == ReferenceKind.PREFIX,
                    or_(*conds),
                )
                .limit(CAP)
            )
        ):
            if (net := _net(r.value)) is not None:
                hit = _prefix_hit(r, net, names.get(r.source_system_id))
                text = hit["name"] or ""
                found.add(_rank(text, q) if q in text.lower() else 1, 0, hit)
    cols = (InvSubnet.context_name, InvSubnet.epg, InvSubnet.bd, InvSubnet.ap,
            InvSubnet.aci_tenant, InvSubnet.description)  # fmt: skip
    conds = [func.lower(c).like(like) for c in cols]
    if by_value:
        conds.append(InvSubnet.prefix.like(f"{q}%"))
    for s in found.scan(
        session.scalars(
            select(InvSubnet).where(InvSubnet.tenant_id == tenant_id, or_(*conds)).limit(CAP)
        )
    ):
        if (net := _net(s.prefix)) is not None:
            name, detail = _subnet_label(s)
            hit = _net_hit(net, name, "subnet", names.get(s.source_system_id), detail)
            rank = min(_rank(x, q) for x in (name, s.epg or "", s.bd or "", s.context_name))
            found.add(rank, 3, hit)


def _literal_net(session, tenant_id, runs, names, net) -> list[dict]:
    """Netz direkt: mit den Namen aus IPAM und Inventar (gleiches Netz)."""
    found = _Found()
    found.add(0, 9, _net_hit(net, None, "net", None, None))
    if runs:
        for r in session.scalars(
            select(ReferenceRecordRow).where(
                ReferenceRecordRow.collector_run_id.in_(runs),
                ReferenceRecordRow.kind == ReferenceKind.PREFIX,
                ReferenceRecordRow.value == str(net),
            )
        ):
            found.add(0, 0, _prefix_hit(r, net, names.get(r.source_system_id)))
    for s in session.scalars(
        select(InvSubnet).where(InvSubnet.tenant_id == tenant_id, InvSubnet.prefix == str(net))
    ):
        name, detail = _subnet_label(s)
        found.add(0, 3, _net_hit(net, name, "subnet", names.get(s.source_system_id), detail))
    (hit,) = found.merged()
    return [hit]


def _owned(session: Session, tenant_id: int) -> set[str]:
    """IPs gelesener Geraete: Interfaces und Gateways (fuer die Adresse eines Netzes)."""
    out: set[str] = set()
    values = [g for (g,) in session.execute(
        select(InvSubnet.gateway).where(InvSubnet.tenant_id == tenant_id)
    )]  # fmt: skip
    for (ips,) in session.execute(
        select(InvInterface.ips).where(InvInterface.tenant_id == tenant_id)
    ):
        values += [v for v in ips or [] if isinstance(v, str)]
    for v in values:
        try:
            out.add(str(ipaddress.ip_interface((v or "").strip()).ip))
        except ValueError:
            continue
    return out


def _sample(net, owned: set[str]) -> str:
    """Adresse fuer die Pruefung eines Netzes: die erste, die keinem Geraet gehoert."""
    first = None
    for i, ip in enumerate(net.hosts()):
        first = first or str(ip)
        if str(ip) not in owned:
            return str(ip)
        if i >= 255:
            break
    return first or str(net.network_address)


def _ipam_by_dns(session: Session, runs: list[str], fqdns: set[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if not runs or not fqdns:
        return out
    for dns, value in session.execute(
        select(ReferenceRecordRow.dns_name, ReferenceRecordRow.value).where(
            ReferenceRecordRow.collector_run_id.in_(runs),
            ReferenceRecordRow.kind.in_(sorted(IP_KINDS)),
            func.lower(ReferenceRecordRow.dns_name).in_(sorted(fqdns)),
        )
    ):
        ip = _ip(value)
        if ip and dns:
            out.setdefault(dns.lower().rstrip("."), []).append(ip)
    return out


def service_values(session: Session, tenant_id: int, name: str) -> list[str] | None:
    """Dienste eines Service-Objekts bzw. ACI-Filters nach Namen (`FT_https`,
    `Tenant/FT_https`), Gruppen aufgeloest; None, wenn es kein solches Objekt gibt."""
    want = name.strip().lower()
    if not want:
        return None
    rows = list(
        session.scalars(
            select(DeviceObject).where(
                DeviceObject.tenant_id == tenant_id,
                DeviceObject.category.in_((ObjectCategory.SERVICE, ObjectCategory.SERVICE_GROUP)),
            )
        )
    )
    by_name: dict[tuple[int, str], DeviceObject] = {}
    for o in rows:
        by_name.setdefault((o.source_system_id, o.name.lower()), o)
    found = [o for o in rows if o.name.lower() == want or o.name.lower().rsplit("/", 1)[-1] == want]
    if not found:
        return None
    out: list[str] = []
    seen: set[int] = set()

    def walk(o: DeviceObject) -> None:
        if o.id in seen:
            return
        seen.add(o.id)
        for v in o.values or []:
            if isinstance(v, str) and v.lower() not in out:
                out.append(v.lower())
        for m in o.members or []:
            child = by_name.get((o.source_system_id, str(m).lower()))
            if child is not None:
                walk(child)

    for o in found:
        walk(o)
    return out
