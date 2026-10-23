"""Name bzw. FQDN -> IP fuer die Weg-Suche (0.35.0, Dev-Befund 0.34.0).

Fuer die Weg-Suche muss niemand die IPs kennen: Quelle und Ziel duerfen ein Name sein. Gesucht
wird in dem, was gelesen ist - nur lesend, je Mandant:

1. **IP** direkt (mit dem Namen aus dem IPAM),
2. **IPAM** (DDI bzw. NetBox, aktueller Referenzstand): DNS-Name bzw. Name einer IP, eines
   Host- oder A-Records,
3. **Objekte der Firewalls** (Adressobjekte mit einer IP, auch ohne Konventions-Praefix:
   `web01` findet `H_web01`), FQDN-Objekte ueber ihren FQDN im IPAM,
4. **VMs** (vCenter) ueber Namen bzw. Hostname mit ihren IPs.

Treffer zuerst exakt, dann am Anfang, dann irgendwo im Namen; je IP einmal.

Dienste (0.35.0): `FT_https` bzw. `Tenant/FT_https` -> die Dienste des Service-Objekts bzw.
ACI-Filters (`tcp/443`), Gruppen aufgeloest.
"""

import ipaddress

from sot_gateway.models import IP_KINDS
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import DeviceObject, ReferenceRecordRow, SourceSystem, Vm, VmIp
from app.models.device_object import ObjectCategory
from app.store import references

LIMIT = 20


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


def _hit(ip: str, name: str, kind: str, source: str | None, detail: str | None) -> dict:
    return {"ip": ip, "name": name, "kind": kind, "source": source, "detail": detail}


def _rank(name: str, q: str) -> int:
    low = name.lower()
    short = low.split(".", 1)[0]
    if q in (low, short):
        return 0
    if low.startswith(q) or short.endswith(q) or f"_{q}" in low:
        return 1
    return 2


def resolve(session: Session, tenant_id: int, query: str, limit: int = LIMIT) -> list[dict]:
    """Treffer fuer `query`: je IP {ip, name, kind (ip|ipam|object|vm), source, detail}."""
    q = (query or "").strip().lower().rstrip(".")
    if not q:
        return []
    literal = _ip(q)
    if literal is not None:
        ctx = references.context(session, tenant_id)
        names = references.dns_names(ctx, literal)
        first = names[0] if names else None
        return [_hit(literal, first, "ip", None, ", ".join(names[1:]) or None)]
    if len(q) < 2:
        return []
    like = f"%{q}%"
    names = dict(
        session.execute(
            select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
        ).all()
    )
    hits: list[tuple[int, int, dict]] = []  # (Rang, Quelle, Treffer)

    runs = [s.reference_run_id for s in references.tenant_reference_sources(session, tenant_id)]
    if runs:
        for r in session.scalars(
            select(ReferenceRecordRow)
            .where(
                ReferenceRecordRow.collector_run_id.in_(runs),
                ReferenceRecordRow.kind.in_(sorted(IP_KINDS)),
                or_(
                    func.lower(ReferenceRecordRow.dns_name).like(like),
                    func.lower(ReferenceRecordRow.name).like(like),
                ),
            )
            .limit(limit * 5)
        ):
            ip = _ip(r.value)
            label = r.dns_name or r.name
            if ip and label:
                source = names.get(r.source_system_id)
                hits.append((_rank(label, q), 0, _hit(ip, label, "ipam", source, r.kind)))
    fqdn_objects: list[DeviceObject] = []
    for o in session.scalars(
        select(DeviceObject)
        .where(
            DeviceObject.tenant_id == tenant_id,
            DeviceObject.category.in_((ObjectCategory.ADDRESS, ObjectCategory.VIP_TARGET)),
            or_(func.lower(DeviceObject.name).like(like), func.lower(DeviceObject.fqdn).like(like)),
        )
        .limit(limit * 5)
    ):
        ips = [ip for ip in (_ip(v) for v in o.values or []) if ip]
        if not ips and o.fqdn:
            fqdn_objects.append(o)
        for ip in ips:
            detail = " · ".join(x for x in (o.obj_type, f"VDOM {o.vdom}" if o.vdom else None) if x)
            source = names.get(o.source_system_id)
            hits.append((_rank(o.name, q), 1, _hit(ip, o.name, "object", source, detail or None)))
    if fqdn_objects:  # FQDN-Objekt: die IP(s) des FQDN aus dem IPAM
        by_name = _ipam_by_dns(session, runs, {o.fqdn.lower().rstrip(".") for o in fqdn_objects})
        for o in fqdn_objects:
            for ip in by_name.get(o.fqdn.lower().rstrip("."), []):
                source = names.get(o.source_system_id)
                hit = _hit(ip, o.name, "object", source, f"FQDN {o.fqdn}")
                hits.append((_rank(o.name, q), 1, hit))
    for vm, ip in session.execute(
        select(Vm, VmIp.ip)
        .join(VmIp, VmIp.vm_id == Vm.id)
        .where(
            Vm.tenant_id == tenant_id,
            or_(func.lower(Vm.name).like(like), func.lower(Vm.hostname).like(like)),
        )
        .limit(limit * 5)
    ):
        if (addr := _ip(ip)) is not None:
            source = names.get(vm.source_system_id)
            hits.append((_rank(vm.name, q), 2, _hit(addr, vm.name, "vm", source, vm.hostname)))

    out: dict[str, dict] = {}
    for *_order, hit in sorted(hits, key=lambda h: (h[0], h[1], h[2]["name"].lower())):
        if hit["ip"] in out:
            also = out[hit["ip"]].setdefault("also", [])
            if hit["name"] != out[hit["ip"]]["name"] and hit["name"] not in also:
                also.append(hit["name"])
            continue
        out[hit["ip"]] = hit
        if len(out) >= limit:
            break
    return list(out.values())


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
