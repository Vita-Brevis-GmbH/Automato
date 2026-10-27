"""NAT-/VIP-Store: Lesezugriff auf nat_rule, Anreicherung (DNS, Server, Regeln), Suche."""

import ipaddress

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DeviceObject, NatRule, ObjectCategory, SourceSystem

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


def _filtered(stmt, tenant_id, nat_type, only=None):
    if only is not None:  # NAT eines Kunden (ADR 0018)
        stmt = stmt.where(NatRule.id.in_(only))
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
    only=None,
) -> list[NatRule]:
    stmt = select(NatRule).order_by(NatRule.source_system_id, NatRule.name)
    stmt = _filtered(stmt, tenant_id, nat_type, only).offset(offset)
    return list(session.scalars(stmt if limit is None else stmt.limit(limit)))


def count_nat(
    session: Session, tenant_id: int | None = None, nat_type: str | None = None, only=None
) -> int:
    stmt = _filtered(select(func.count(NatRule.id)), tenant_id, nat_type, only)
    return session.scalar(stmt) or 0


def nat_by_name(
    session: Session, tenant_id: int, source_id: int | None = None, vdom: str | None = None
) -> dict[str, NatRule]:
    """VIP-/NAT-Name -> NatRule (fuer die Anreicherung der Regel-Detailansicht); mit Quellsystem
    nur dessen Eintraege im VDOM der Regel (gleiche Namen in zwei VDOMs, ADR 0019)."""
    stmt = select(NatRule).where(NatRule.tenant_id == tenant_id)
    if source_id is not None:
        stmt = stmt.where(
            NatRule.source_system_id == source_id,
            NatRule.vdom.is_(None) if vdom is None else NatRule.vdom == vdom,
        )
    return {n.name: n for n in session.scalars(stmt)}


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


# NSX ALB (ADR 0027): Angaben des Virtual Service aus `attrs`
_AVI_SETTINGS = {
    "tenant": "Avi-Tenant",
    "cloud": "Cloud",
    "vrf": "VRF",
    "vsvip": "VS VIP",
    "pool": "Pool",
    "pool_group": "Pool Group",
    "lb_algorithm": "Methode",
    "health_monitors": "Health-Monitor",
    "application_profile": "Application Profile",
    "snat": "Quelle zum Server",
    "oper_status": "Zustand",
    "parent": "Parent (SNI)",
    "domains": "Domains",
    "fqdn": "FQDN",
}
_AVI_VALUES = {"service_engine": "Service Engine (SNAT)", "client": "Client-IP bleibt"}


def settings(nat: NatRule) -> dict[str, str]:
    attrs = nat.attrs if isinstance(nat.attrs, dict) else {}
    if attrs.get("platform") == "avi":
        out = {
            label: _AVI_VALUES.get(str(attrs[key]), raw_text(attrs[key]))
            for key, label in _AVI_SETTINGS.items()
            if attrs.get(key) not in (None, "", [])
        }
        if attrs.get("content_switching"):
            out["Richtlinien"] = "weitere Pools über HTTP-Richtlinien möglich (nicht ausgewertet)"
        if attrs.get("enabled") is False:
            out["Aktiv"] = "nein"
        return out
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


def is_balancer(nat: NatRule) -> bool:
    """Virtual Service des NSX ALB (ADR 0027) - kein Eintrag einer Firewall."""
    return (nat.attrs or {}).get("platform") == "avi"


def _ports(text: str | None) -> tuple[int, int] | None:
    lo, _, hi = str(text or "").partition("-")
    try:
        return (int(lo), int(hi or lo)) if lo.strip() else None
    except ValueError:
        return None


def host_ip(text: str | None) -> str | None:
    """Einzelne Adresse (`10.0.0.1`, `10.0.0.1/32`) normiert, sonst None."""
    value = str(text or "").strip().removesuffix("/32").removesuffix("/128")
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return None


class Balancers:
    """Virtual Services des NSX ALB je VIP-Adresse (0.79.0): zeigt eine VIP der FortiGate (bzw.
    eine Regel) auf die VIP eines Load Balancers, geht es weiter zu dessen Servern -
    öffentliche IP → VIP der FortiGate → Virtual Service → Server des Pools."""

    def __init__(self, rows: list[NatRule]) -> None:
        self.by_ip: dict[str, list[NatRule]] = {}
        for n in rows:
            ip = host_ip(n.ext_ip) if is_balancer(n) else None
            if ip:
                self.by_ip.setdefault(ip, []).append(n)

    @classmethod
    def load(cls, session: Session, tenant_id: int) -> "Balancers":
        stmt = select(NatRule).where(NatRule.tenant_id == tenant_id, NatRule.nat_type == "dnat_vip")
        return cls(list(session.scalars(stmt.order_by(NatRule.name, NatRule.id))))

    def at(self, ip: str | None, port: str | None = None) -> list[NatRule]:
        """Virtual Services mit dieser VIP-Adresse; mit Port nur die, deren Port ihn umfasst."""
        found = self.by_ip.get(host_ip(ip) or "", [])
        want = _ports(port)
        if want is None:
            return list(found)
        out = []
        for n in found:
            have = _ports(n.ext_port)
            if have is None or have[0] <= want[1] and want[0] <= have[1]:
                out.append(n)
        return out

    def behind(self, nat: NatRule) -> list[NatRule]:
        """Virtual Services hinter einer VIP der FortiGate: ihr internes Ziel ist die VIP des
        Load Balancers - bei Port-Weiterleitung nur der weitergeleitete Port."""
        if is_balancer(nat):
            return []
        port = nat.mapped_port if nat.portforward else None
        out: list[NatRule] = []
        for ip in internal_ips(nat):
            out += [n for n in self.at(ip, port) if n not in out]
        return out

    def servers(self, nat: NatRule) -> list[str]:
        """Server-IPs der Virtual Services hinter einer VIP der FortiGate."""
        return list(dict.fromkeys(ip for lb in self.behind(nat) for ip in internal_ips(lb)))

    def vips_for(self, values: list[str]) -> list[str]:
        """VIP-Adressen der Virtual Services, deren Server in den Werten liegen (IP bzw.
        Netz) - fuer die Route zum Load Balancer statt zum Server."""
        nets = []
        for v in values:
            try:
                nets.append(ipaddress.ip_network(str(v).strip(), strict=False))
            except ValueError:
                continue
        out: list[str] = []
        for ip, lbs in self.by_ip.items():
            servers = {s for lb in lbs for s in internal_ips(lb)}
            for s in servers:
                try:
                    addr = ipaddress.ip_address(s)
                except ValueError:
                    continue
                if any(addr.version == n.version and addr in n for n in nets):
                    out.append(ip)
                    break
        return out


# Weg ueber einen Load Balancer im `via` eines Regel-Bereichs („V-EXT_x → vs-web“, 0.79.0)
CHAIN = " → "


def balancer_brief(lb: NatRule, source: str | None = None) -> dict:
    """Virtual Service kompakt fuer Regel-Detail und publizierte Dienste."""
    attrs = lb.attrs or {}
    servers = [
        f"{t['ip']}:{t['port']}" if t.get("port") else t["ip"]
        for t in lb.targets or []
        if isinstance(t, dict) and t.get("ip")
    ]
    out = {
        "id": lb.id,
        "name": lb.name,
        "source": source,
        "ext_ip": lb.ext_ip,
        "ext_port": lb.ext_port,
        "pool": attrs.get("pool") or attrs.get("pool_group"),
        "tenant": attrs.get("tenant"),
        "servers": servers,
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


# (Quellsystem, VDOM, Objektname) eines Firewall-Adressobjekts
Server = tuple[int, str | None, str]


def servers_by_ip(session: Session, tenant_id: int, ips: set[str]) -> dict[str, list[Server]]:
    """Interne IP -> (Quellsystem, VDOM, Name) aller Firewall-Adressobjekte mit genau dieser IP
    (z. B. H_web01) - mehrere, wenn die IP doppelt definiert ist (Befund "Doppelter Wert")."""
    ips = {i for i in ips if i}
    if not ips:
        return {}
    out: dict[str, list[Server]] = {}
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
            out.setdefault(values[0], []).append((o.source_system_id, o.vdom, o.name))
    return out


def server_names(
    candidates: list[Server], source_system_id: int, vdom: str | None = None
) -> list[str]:
    """Server einer NAT-Regel: bevorzugt die Objekte desselben VDOMs, dann derselben Firewall."""
    for wanted in (
        [name for sid, v, name in candidates if sid == source_system_id and v == vdom],
        [name for sid, _, name in candidates if sid == source_system_id],
        [name for _, _, name in candidates],
    ):
        if wanted:
            return list(dict.fromkeys(wanted))
    return []


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


def briefs(
    session: Session, tenant_id: int, source_id: int, names: list[str], vdom: str | None = None
) -> list[dict]:
    """VIPs/SNAT-Pools einer Regel (im VDOM der Regel) kompakt (IPs, Ports, DNS-Namen) fuer das
    Regel-Detail."""
    from app.store import references  # spaet: references importiert Modelle/Stores

    if not names:
        return []
    wanted = {n.lower() for n in names}
    rows = [
        n
        for n in session.scalars(select(NatRule).where(NatRule.source_system_id == source_id))
        if n.name.lower() in wanted and n.vdom == vdom
    ]
    ctx = references.context(session, tenant_id)
    lbs = Balancers.load(session, tenant_id)  # 0.79.0: VIP -> Virtual Service des NSX ALB
    stmt = select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
    names_of = dict(session.execute(stmt).tuples().all())
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
            "balancers": [
                balancer_brief(lb, names_of.get(lb.source_system_id)) for lb in lbs.behind(n)
            ],
        }
        for n in sorted(rows, key=lambda x: (x.nat_type, x.name))
    ]
