"""NAT-/VIP-Store: Lesezugriff auf nat_rule, Anreicherung (DNS, Server, Regeln), Suche."""

import ipaddress
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.i18n import L
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
# Beschriftungen auf Englisch (0.87.0, ADR 0028) - die Schluessel der Ausgabe sind Anzeige
_LABEL_EN = {
    "Typ": "Type", "ARP-Antwort": "ARP reply", "Quell-Filter": "Source filter",
    "SNAT mit VIP-IP": "SNAT with VIP IP", "Start-IP": "Start IP", "End-IP": "End IP",
    "Quelle von": "Source from", "Quelle bis": "Source to", "Jeder Host": "Any host",
    "Methode": "Method", "Health-Monitor": "Health monitor",
    "Quelle zum Server": "Source to server",
    "Zustand": "State", "Richtlinien": "Policies", "Aktiv": "Enabled",
    "Client-IP bleibt": "client IP preserved",
}  # fmt: skip


def _en(de: str) -> str:
    return L(de, _LABEL_EN.get(de, de))


def settings(nat: NatRule) -> dict[str, str]:
    attrs = nat.attrs if isinstance(nat.attrs, dict) else {}
    if attrs.get("platform") == "avi":
        out = {
            _en(label): (_en(_AVI_VALUES[str(attrs[key])]) if str(attrs[key]) in _AVI_VALUES
                         else raw_text(attrs[key]))
            for key, label in _AVI_SETTINGS.items()
            if attrs.get(key) not in (None, "", [])
        }  # fmt: skip
        if attrs.get("content_switching"):
            out[_en("Richtlinien")] = L(
                "weitere Pools über HTTP-Richtlinien möglich (nicht ausgewertet)",
                "more pools possible via HTTP policies (not evaluated)")  # fmt: skip
        if attrs.get("enabled") is False:
            out[_en("Aktiv")] = L("nein", "no")
        return out
    raw = nat.raw if isinstance(nat.raw, dict) else {}
    return {
        _en(label): raw_text(raw[key])
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
VIP_GROUP = "vipgrp"  # FortiGate VIP-Gruppe: Adressgruppe mit diesem Typ (0.96.0)


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


Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Span = tuple[Address, Address]


def span(text: str | None) -> Span | None:
    """Adresse, Bereich `a-b` bzw. Netz -> (erste, letzte) Adresse; unlesbar -> None."""
    value = str(text or "").strip()
    if not value:
        return None
    start, sep, end = value.partition("-")
    try:
        if sep:
            lo, hi = ipaddress.ip_address(start.strip()), ipaddress.ip_address(end.strip())
            return (lo, hi) if lo.version == hi.version and lo <= hi else None
        net = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None
    return net[0], net[-1]


_UNSET = {"0.0.0.0", "::", "0.0.0.0-0.0.0.0"}  # noqa: S104 - FortiOS-Standardwert = leer


def mapped_values(nat: NatRule) -> list[str]:
    """Interne Adressen einer VIP der Firewall wie auf dem Geraet (0.96.0): alle Eintraege von
    `mappedip` (Bereiche, mehrere Werte) aus den Rohdaten - gespeichert ist nur der erste -,
    sonst die zugeordnete IP."""
    raw = nat.raw if isinstance(nat.raw, dict) else {}
    value = raw.get("mappedip")
    items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    out: list[str] = []
    for v in items:
        text = str((v.get("range") if isinstance(v, dict) else v) or "").strip()
        if text and text not in _UNSET and text not in out:
            out.append(text)
    return out or ([nat.mapped_ip] if nat.mapped_ip else [])


def mapped_addr(nat: NatRule) -> str | None:
    """FQDN-VIP der FortiGate: Name des Adressobjekts hinter `mapped-addr`."""
    raw = nat.raw if isinstance(nat.raw, dict) else {}
    value = raw.get("mapped-addr")
    if isinstance(value, list):  # manche FortiOS-Versionen: [{"name": ...}]
        value = next((m.get("name") for m in value if isinstance(m, dict)), None)
    return str(value) if value else None


def _fqdn_key(text: str | None) -> str:
    return str(text or "").strip().rstrip(".").lower()


def _overlap(a: tuple[int, int] | None, b: tuple[int, int] | None) -> bool:
    """Port-Bereiche ueberlappen; ohne Angabe = jeder Port."""
    return a is None or b is None or (a[0] <= b[1] and b[0] <= a[1])


def _port_text(lo: int, hi: int) -> str:
    return str(lo) if lo == hi else f"{lo}-{hi}"


def address_fqdns(
    session: Session, tenant_id: int, rows: list[NatRule]
) -> dict[tuple[int, str | None, str], str]:
    """FQDN der Adressobjekte hinter FQDN-VIPs (`mapped-addr`) - (Quellsystem, VDOM, Name klein)
    -> FQDN; nur wenn es solche VIPs gibt (eine Abfrage)."""
    wanted = {(n.source_system_id, n.vdom, name) for n in rows if (name := mapped_addr(n))}
    if not wanted:
        return {}
    stmt = select(DeviceObject).where(
        DeviceObject.tenant_id == tenant_id,
        DeviceObject.category == ObjectCategory.ADDRESS,
        DeviceObject.name.in_({name for _, _, name in wanted}),
    )
    out: dict[tuple[int, str | None, str], str] = {}
    for o in session.scalars(stmt):
        fqdn = o.fqdn or next(
            (str(v)[5:] for v in o.values or [] if str(v).startswith("fqdn:")), None
        )
        if fqdn and (o.source_system_id, o.vdom, o.name) in wanted:
            out[(o.source_system_id, o.vdom, o.name.lower())] = fqdn
    return out


@dataclass(frozen=True)
class Link:
    """Glied der Kette (0.96.0): VIP einer Firewall -> Virtual Service des Load Balancers, mit
    der externen Adresse und dem Port, die zu ihm fuehren (Bereich 1:1, Port-Weiterleitung)."""

    vip: NatRule
    lb: NatRule
    ext_ip: str | None
    ext_port: str | None
    via: str = "ip"  # ip | fqdn (FQDN-VIP auf den FQDN des Virtual Service)


class Balancers:
    """Virtual Services des NSX ALB je VIP-Adresse (0.79.0): zeigt eine VIP der FortiGate (bzw.
    eine Regel) auf die VIP eines Load Balancers, geht es weiter zu dessen Servern -
    öffentliche IP → VIP der FortiGate → Virtual Service → Server des Pools.

    Seit 0.96.0 auch mit Bereichen und mehreren Werten in `mappedip` (Bereich auf Bereich mit
    demselben Abstand), Port-Weiterleitung mit Port-Bereichen, Virtual Server der FortiGate
    (Real Server auf die VIP des Load Balancers), FQDN-VIPs (`mapped-addr` = FQDN des Virtual
    Service) und umgekehrt den VIPs davor (`fronts`)."""

    def __init__(
        self, rows: list[NatRule], fqdns: dict[tuple[int, str | None, str], str] | None = None
    ) -> None:
        self.rows = rows
        self.by_ip: dict[str, list[NatRule]] = {}
        self.by_fqdn: dict[str, list[NatRule]] = {}
        for n in rows:
            ip = host_ip(n.ext_ip) if is_balancer(n) else None
            if ip:
                self.by_ip.setdefault(ip, []).append(n)
            if is_balancer(n):
                for f in (n.attrs or {}).get("fqdn") or []:
                    if _fqdn_key(f):
                        self.by_fqdn.setdefault(_fqdn_key(f), [])
                        if n not in self.by_fqdn[_fqdn_key(f)]:
                            self.by_fqdn[_fqdn_key(f)].append(n)
        self._addrs = [(ipaddress.ip_address(ip), lbs) for ip, lbs in self.by_ip.items()]
        self.fqdns = fqdns or {}  # (Quellsystem, VDOM, Objekt klein) -> FQDN des Objekts
        self._links: dict[int, list[Link]] = {}
        self._front: dict[int, list[Link]] | None = None

    @classmethod
    def load(cls, session: Session, tenant_id: int) -> "Balancers":
        stmt = select(NatRule).where(NatRule.tenant_id == tenant_id, NatRule.nat_type == "dnat_vip")
        rows = list(session.scalars(stmt.order_by(NatRule.name, NatRule.id)))
        return cls(rows, address_fqdns(session, tenant_id, rows))

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
        out: list[NatRule] = []
        for link in self.links(nat):
            if link.lb not in out:
                out.append(link.lb)
        return out

    def _legs(self, nat: NatRule) -> list[tuple[Span, str | None]]:
        """Interne Ziele einer VIP mit dem Port dorthin: Virtual Server = Real Server mit ihrem
        Port, sonst `mappedip` (bei Port-Weiterleitung der weitergeleitete Port)."""
        if nat.vip_type == SLB and nat.targets:
            out = []
            for t in nat.targets:
                s = span(t.get("ip")) if isinstance(t, dict) else None
                if s is not None:
                    out.append((s, str(t["port"]) if t.get("port") else None))
            return out
        port = nat.mapped_port if nat.portforward else None
        return [(s, port) for v in mapped_values(nat) if (s := span(v)) is not None]

    def _within(self, s: Span) -> list[NatRule]:
        if s[0] == s[1]:
            return list(self.by_ip.get(s[0].compressed, []))
        return [
            lb
            for addr, lbs in self._addrs
            if addr.version == s[0].version and s[0] <= addr <= s[1]
            for lb in lbs
        ]

    def links(self, nat: NatRule) -> list[Link]:
        """Kette einer VIP der Firewall: je Virtual Service dahinter die externe Adresse und
        der Port, die zu ihm fuehren."""
        if is_balancer(nat) or nat.nat_type != "dnat_vip":
            return []
        if nat.id in self._links and nat.id is not None:
            return self._links[nat.id]
        out: list[Link] = []
        ext = span(nat.ext_ip)

        def fits(lb: NatRule, want) -> bool:  # nur im selben Mandanten, Port passt
            if lb.tenant_id != nat.tenant_id or any(x.lb is lb for x in out):
                return False
            return _overlap(want, _ports(lb.ext_port))

        for s, port in self._legs(nat):
            want = _ports(port)
            for lb in self._within(s):
                if fits(lb, want):
                    ext_ip = self._ext_ip(nat, ext, s, lb)
                    out.append(Link(nat, lb, ext_ip, self._ext_port(nat, lb)))
        name = mapped_addr(nat)
        fqdn = self.fqdns.get((nat.source_system_id, nat.vdom, name.lower())) if name else None
        for lb in self.by_fqdn.get(_fqdn_key(fqdn), []) if fqdn else []:
            if fits(lb, _ports(nat.mapped_port if nat.portforward else None)):
                out.append(Link(nat, lb, nat.ext_ip, self._ext_port(nat, lb), "fqdn"))
        if nat.id is not None:
            self._links[nat.id] = out
        return out

    @staticmethod
    def _ext_ip(nat: NatRule, ext: Span | None, s: Span, lb: NatRule) -> str | None:
        """Externe Adresse zum Virtual Service: Bereich auf Bereich gleicher Groesse = dieselbe
        Stelle im externen Bereich, sonst die externe Adresse der VIP."""
        addr = span(lb.ext_ip)
        if ext is None or addr is None or ext[0] == ext[1] or s[0] == s[1]:
            return nat.ext_ip
        if ext[0].version != s[0].version or int(ext[1]) - int(ext[0]) != int(s[1]) - int(s[0]):
            return nat.ext_ip
        return ipaddress.ip_address(int(ext[0]) + int(addr[0]) - int(s[0])).compressed

    @staticmethod
    def _ext_port(nat: NatRule, lb: NatRule) -> str | None:
        """Externer Port zum Virtual Service: Virtual Server der FortiGate bzw. VIP ohne
        Port-Weiterleitung - der Port der VIP bzw. des Virtual Service; mit Port-Weiterleitung
        der Teil des externen Bereichs, der auf den Port des Virtual Service fuehrt."""
        if nat.vip_type == SLB:
            return nat.ext_port
        if not nat.portforward:
            return lb.ext_port or nat.ext_port
        ext, mapped, want = _ports(nat.ext_port), _ports(nat.mapped_port), _ports(lb.ext_port)
        if ext is None or mapped is None or mapped[0] == mapped[1] or want is None:
            return nat.ext_port
        if ext[1] - ext[0] != mapped[1] - mapped[0]:
            return nat.ext_port
        lo, hi = max(want[0], mapped[0]), min(want[1], mapped[1])
        if lo > hi:
            return nat.ext_port
        shift = ext[0] - mapped[0]
        return _port_text(lo + shift, hi + shift)

    def fronts(self, lb: NatRule) -> list[Link]:
        """VIPs der Firewalls vor einem Virtual Service (umgekehrte Kette)."""
        if self._front is None:
            self._front = {}
            for n in self.rows:
                for link in self.links(n):
                    self._front.setdefault(link.lb.id, []).append(link)
        return list(self._front.get(lb.id, []))

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
