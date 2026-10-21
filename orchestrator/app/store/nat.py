"""NAT-/VIP-Store: Lesezugriff auf nat_rule, Anreicherung (DNS, Server, Regeln), Suche."""

import ipaddress

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DeviceObject, NatRule, ObjectCategory

# Praefixe, hinter denen die Konvention den externen Namen fuehrt (V-EXT_<fqdn>, IPP_<fqdn>)
_NAME_PREFIXES = ("V-EXT_", "VIP_", "IPP_")
# Einstellungen aus dem Rohdatensatz, die das Detail zusaetzlich zeigt (FortiGate)
_SETTINGS = {  # Interface (extintf) steht in eigener Zeile
    "type": "Typ",
    "arp-reply": "ARP-Antwort",
    "src-filter": "Quell-Filter",
    "service": "Services",
    "nat-source-vip": "SNAT mit VIP-IP",
    "startip": "Start-IP",
    "endip": "End-IP",
    "source-startip": "Quelle von",
    "source-endip": "Quelle bis",
    "permit-any-host": "Jeder Host",
}


def _filtered(stmt, tenant_id, nat_type):
    if tenant_id is not None:
        stmt = stmt.where(NatRule.tenant_id == tenant_id)
    if nat_type:
        stmt = stmt.where(NatRule.nat_type == nat_type)
    return stmt


def list_nat(
    session: Session,
    tenant_id: int | None = None,
    limit: int | None = 500,
    offset: int = 0,
    nat_type: str | None = None,
) -> list[NatRule]:
    stmt = select(NatRule).order_by(NatRule.source_system_id, NatRule.name)
    stmt = _filtered(stmt, tenant_id, nat_type).offset(offset)
    return list(session.scalars(stmt if limit is None else stmt.limit(limit)))


def count_nat(session: Session, tenant_id: int | None = None, nat_type: str | None = None) -> int:
    return session.scalar(_filtered(select(func.count(NatRule.id)), tenant_id, nat_type)) or 0


def nat_by_name(session: Session, tenant_id: int) -> dict[str, NatRule]:
    """VIP-/NAT-Name -> NatRule (fuer die Anreicherung der Regel-Detailansicht)."""
    rows = session.scalars(select(NatRule).where(NatRule.tenant_id == tenant_id))
    return {n.name: n for n in rows}


def nat_target(nat: NatRule) -> str:
    """Kompakte Ziel-Darstellung: mapped_ip[:port][/proto]."""
    target = nat.mapped_ip or "?"
    if nat.mapped_port:
        target += f":{nat.mapped_port}"
    if nat.protocol:
        target += f"/{nat.protocol}"
    return target


def raw_text(value) -> str:
    """FortiGate-Werte lesbar: Listen von {name|range} -> kommagetrennt."""
    if isinstance(value, list):
        return ", ".join(
            str(v.get("name") or v.get("range") or v) if isinstance(v, dict) else str(v)
            for v in value
        )
    return str(value)


def comment(nat: NatRule) -> str | None:
    raw = nat.raw if isinstance(nat.raw, dict) else {}
    value = raw.get("comment") or raw.get("comments")
    return str(value) if value else None


def interface(nat: NatRule) -> str | None:
    raw = nat.raw if isinstance(nat.raw, dict) else {}
    value = raw.get("extintf")
    return raw_text(value) if value else None


def settings(nat: NatRule) -> dict[str, str]:
    raw = nat.raw if isinstance(nat.raw, dict) else {}
    return {
        label: raw_text(raw[key])
        for key, label in _SETTINGS.items()
        if raw.get(key) not in (None, "", [], "0.0.0.0")  # noqa: S104 - Anzeigewert
    }


def external_name(nat: NatRule) -> str | None:
    """Publizierter Name eines VIPs aus der Namenskonvention (V-EXT_www.example.ch); SNAT-Pools
    haben keinen."""
    if nat.nat_type == "snat_pool":
        return None
    for prefix in _NAME_PREFIXES:
        if nat.name.upper().startswith(prefix):
            return nat.name[len(prefix) :] or None
    return None


SLB = "server-load-balance"  # FortiGate Virtual Server (Lastverteilung)


def internal_ips(nat: NatRule) -> list[str]:
    """Interne Ziele: zugeordnete IP bzw. alle Real Server eines Virtual Servers."""
    ips = [nat.mapped_ip] if nat.mapped_ip else []
    ips += [t["ip"] for t in nat.targets or [] if isinstance(t, dict) and t.get("ip")]
    return list(dict.fromkeys(ips))


def servers_by_ip(
    session: Session, tenant_id: int, ips: set[str]
) -> dict[str, list[tuple[int, str]]]:
    """Interne IP -> (Quellsystem, Name) aller Firewall-Adressobjekte mit genau dieser IP
    (z. B. H_web01) - mehrere, wenn die IP doppelt definiert ist (Befund "Doppelter Wert")."""
    ips = {i for i in ips if i}
    if not ips:
        return {}
    out: dict[str, list[tuple[int, str]]] = {}
    rows = session.scalars(
        select(DeviceObject)
        .where(
            DeviceObject.tenant_id == tenant_id,
            DeviceObject.category == ObjectCategory.ADDRESS,
        )
        .order_by(DeviceObject.name)
    )
    for o in rows:
        values = [str(v).removesuffix("/32") for v in o.values or []]
        if len(values) == 1 and values[0] in ips:
            out.setdefault(values[0], []).append((o.source_system_id, o.name))
    return out


def server_names(candidates: list[tuple[int, str]], source_system_id: int) -> list[str]:
    """Server einer NAT-Regel: bevorzugt die Objekte derselben Firewall."""
    same = [name for sid, name in candidates if sid == source_system_id]
    return same or list(dict.fromkeys(name for _, name in candidates))


def device_object(session: Session, nat: NatRule) -> DeviceObject | None:
    category = ObjectCategory.IPPOOL if nat.nat_type == "snat_pool" else ObjectCategory.VIP
    return session.scalars(
        select(DeviceObject).where(
            DeviceObject.source_system_id == nat.source_system_id,
            DeviceObject.category == category,
            DeviceObject.name == nat.name,
        )
    ).first()


def _in_range(value: str | None, ip) -> bool:
    if not value:
        return False
    start, _, end = value.partition("-")
    try:
        lo = ipaddress.ip_address(start.strip())
        hi = ipaddress.ip_address(end.strip()) if end else lo
    except ValueError:
        return False
    return lo.version == ip.version and lo <= ip <= hi


def matches(fields: list[str | None], ranges: list[str | None], query: str) -> bool:
    """Alle Woerter der Suche muessen treffen; eine IP auch innerhalb eines Bereichs."""
    text = "\n".join(f.lower() for f in fields if f)
    for token in query.lower().split():
        if token in text:
            continue
        try:
            ip = ipaddress.ip_address(token)
        except ValueError:
            return False
        if not any(_in_range(r, ip) for r in ranges):
            return False
    return True


def briefs(session: Session, tenant_id: int, source_id: int, names: list[str]) -> list[dict]:
    """VIPs/SNAT-Pools einer Regel kompakt (IPs, Ports, DNS-Namen) fuer das Regel-Detail."""
    from app.store import references  # spaet: references importiert Modelle/Stores

    if not names:
        return []
    wanted = {n.lower() for n in names}
    rows = [
        n
        for n in session.scalars(select(NatRule).where(NatRule.source_system_id == source_id))
        if n.name.lower() in wanted
    ]
    ctx = references.context(session, tenant_id)
    return [
        {
            "id": n.id,
            "name": n.name,
            "nat_type": n.nat_type,
            "ext_ip": n.ext_ip,
            "ext_port": n.ext_port,
            "mapped_ip": n.mapped_ip,
            "mapped_port": n.mapped_port,
            "protocol": n.protocol,
            "external_name": external_name(n),
            "ext_dns": references.dns_names(ctx, n.ext_ip),
            "mapped_dns": references.dns_names(ctx, n.mapped_ip),
        }
        for n in sorted(rows, key=lambda x: (x.nat_type, x.name))
    ]
