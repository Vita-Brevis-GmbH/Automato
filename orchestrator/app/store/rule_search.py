"""Suche ueber Regeln: findet alles, was zu einer Regel gehoert (Dev-Befund: die Suche fand
Objekte nicht, die nur ueber eine Gruppe in der Regel stehen).

Je Regel wird ein Suchtext gebildet aus:
- Regel: Name, ID, Abschnitt, Aktion, Kommentar, Zonen, Schutzprofile (UTM),
- Objekten wie auf der Firewall, Gruppen rekursiv mit allen Mitgliedern, je Objekt Name, Werte,
  FQDN, Kommentar, Interface,
- Services (Objektnamen, Gruppenmitglieder, kanonische Ports),
- aufgeloesten Endpunkten (Name, Wert, FQDN) und ACI-Lage (EPG, AP, Tenant, BD, VRF, L3Out, DN),
- NAT: VIPs mit externer/interner IP und Port, SNAT-Pools der Policy,
- DNS-Namen der Host-IPs aus dem IPAM (dort steht der Server ohne Praefix, z. B.
  web01.example.ch fuer das Firewall-Objekt H_web01).

Eine Suche aus mehreren Woertern verlangt alle. Ein Wort, das eine IP ist, trifft auch, wenn
die IP in einem Netz bzw. Bereich der Regel liegt ("any"/0.0.0.0/0 ausgenommen - sonst traefe
jede offene Regel). Daneben die exakten Filter `object` (Regeln, die ein Objekt verwenden -
auch ueber Gruppen) und `nat` (Regeln mit diesem VIP bzw. SNAT-Pool). Rein lesend.
"""

import ipaddress
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DeviceObject, EpgEndpoint, EpgSubnet, NatRule, ObjectCategory, Rule
from app.store import epgs as epg_store
from app.store import references
from app.store import rules as rules_store

_ANY_NETS = {ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0")}  # noqa: S104


def snat_pools(rule: Rule) -> list[str] | None:
    """SNAT einer FortiGate-Policy: Pool-Namen; [] = NAT auf die Interface-IP; None = kein NAT."""
    raw = rule.raw if isinstance(rule.raw, dict) else {}
    if str(raw.get("nat", "")).lower() != "enable":
        return None
    if str(raw.get("ippool", "")).lower() != "enable":
        return []
    pools = raw.get("poolname") or []
    return [p["name"] for p in pools if isinstance(p, dict) and p.get("name")]


def _ip(token: str):
    try:
        return ipaddress.ip_address(token)
    except ValueError:
        return None


def _contains(value: str, ip) -> bool:
    """Liegt die IP im Wert (IP, CIDR, Bereich a-b, typisiert `range:`/`subnet:`)?"""
    v = value.split(":", 1)[1] if value.split(":", 1)[0] in ("range", "subnet", "ip") else value
    v = v.strip()
    if "-" in v and "/" not in v:
        a, _, b = v.partition("-")
        lo, hi = _ip(a.strip()), _ip(b.strip())
        return bool(lo and hi and lo.version == ip.version and lo <= ip <= hi)
    try:
        net = ipaddress.ip_network(v, strict=False)
    except ValueError:
        return False
    return net not in _ANY_NETS and net.version == ip.version and ip in net


class RuleSearch:
    """Suchtexte und Werte je Regel - Firewall-Objekte, NAT und EPGs einmal geladen."""

    def __init__(self, session: Session, rules: list[Rule]):
        self.session = session
        tenants = {r.tenant_id for r in rules}
        sources = {r.source_system_id for r in rules}
        obj_ids = {e.network_object_id for r in rules for e in r.endpoints if e.network_object_id}
        self.objs = rules_store.objects_by_ids(session, obj_ids)
        zone_ids = {z for r in rules for z in (r.src_zone_id, r.dst_zone_id) if z}
        self.zones = rules_store.zone_names(session, zone_ids)
        self.device: dict[tuple[int, str], list[DeviceObject]] = defaultdict(list)
        self.nat: dict[tuple[int, str], NatRule] = {}
        if sources:
            for o in session.scalars(
                select(DeviceObject).where(DeviceObject.source_system_id.in_(sources))
            ):
                self.device[(o.source_system_id, o.name.lower())].append(o)
            for n in session.scalars(select(NatRule).where(NatRule.source_system_id.in_(sources))):
                self.nat[(n.source_system_id, n.name.lower())] = n
        epg_ids = {e.epg_id for r in rules for e in r.endpoints if e.epg_id}
        self.epgs = epg_store.by_ids(session, epg_ids)
        self.epg_values: dict[int, list[str]] = defaultdict(list)
        if epg_ids:
            for s in session.scalars(select(EpgSubnet).where(EpgSubnet.epg_id.in_(epg_ids))):
                self.epg_values[s.epg_id].append(s.ip)
            for ep in session.scalars(select(EpgEndpoint).where(EpgEndpoint.epg_id.in_(epg_ids))):
                self.epg_values[ep.epg_id].append(ep.ip)
        self._tenants = tenants
        self.ctx = {t: references.context(session, t) for t in tenants if t is not None}
        self._text: dict[int, str] = {}
        self._values: dict[int, list[str]] = {}
        self._names: dict[int, set[str]] = {}

    # -- Firewall-Objekte rekursiv -------------------------------------------------------

    def expand(self, source_id: int, name: str, seen: set | None = None) -> list[DeviceObject]:
        """Objekt mit allen (verschachtelten) Gruppenmitgliedern."""
        seen = seen if seen is not None else set()
        key = (source_id, name.lower())
        if key in seen:
            return []
        seen.add(key)
        out: list[DeviceObject] = []
        for o in self.device.get(key, []):
            out.append(o)
            for member in o.members or []:
                out += self.expand(source_id, member, seen)
        return out

    def _ref_objects(self, rule: Rule) -> list[DeviceObject]:
        seen: set = set()
        return [o for ref in rule.refs for o in self.expand(rule.source_system_id, ref.name, seen)]

    # -- Suchtext, Werte, Namen ---------------------------------------------------------

    def _build(self, rule: Rule) -> None:
        raw = rule.raw if isinstance(rule.raw, dict) else {}
        parts: list[str | None] = [
            rule.name,
            str(rule.id),
            rule.external_id,
            rule.section_label,
            rule.action.value,
            str(raw.get("comments") or raw.get("comment") or ""),
            self.zones.get(rule.src_zone_id),
            self.zones.get(rule.dst_zone_id),
            *(rule.protections or []),
            *(s.canonical_name for s in rule.services),
        ]
        if rule.aci_scope:
            parts += [rule.aci_scope, raw.get("name"), raw.get("dn")]
        values: list[str] = []
        names: set[str] = {r.name.lower() for r in rule.refs}
        for o in self._ref_objects(rule):
            names.add(o.name.lower())
            parts += [o.name, o.fqdn, o.comment, o.interface, *(o.values or [])]
            values += [str(v) for v in o.values or []]
            if o.category is ObjectCategory.VIP:
                nat = self.nat.get((rule.source_system_id, o.name.lower()))
                if nat is not None:
                    parts += [nat.ext_ip, nat.ext_port, nat.mapped_ip, nat.mapped_port]
                    values += [v for v in (nat.ext_ip, nat.mapped_ip) if v]
        for pool in snat_pools(rule) or []:
            names.add(pool.lower())
            parts.append(pool)
            nat = self.nat.get((rule.source_system_id, pool.lower()))
            if nat is not None:
                parts += [nat.ext_ip, nat.mapped_ip]
                values += [v for v in (nat.ext_ip, nat.mapped_ip) if v]
        for e in rule.endpoints:
            obj = self.objs.get(e.network_object_id) if e.network_object_id else None
            if obj is not None:
                names.add(obj.name.lower())
                parts += [obj.name, obj.value, obj.fqdn]
                if obj.value:
                    values.append(obj.value)
            parts.append(e.inline_value)
            epg = self.epgs.get(e.epg_id) if e.epg_id else None
            if epg is not None:
                label = epg_store.label(epg)
                names |= {epg.name.lower(), label.lower(), (epg.dn or "").lower()}
                parts += [epg.name, label, epg.aci_tenant, epg.ap, epg.bd, epg.vrf, epg.l3out]
                parts.append(epg.dn)
                values += self.epg_values.get(epg.id, [])
        ctx = self.ctx.get(rule.tenant_id)
        if ctx is not None:
            for v in dict.fromkeys(values):
                parts += references.dns_names(ctx, v)
        self._text[rule.id] = "\n".join(p.lower() for p in parts if p)
        self._values[rule.id] = values
        self._names[rule.id] = names - {""}

    def text(self, rule: Rule) -> str:
        if rule.id not in self._text:
            self._build(rule)
        return self._text[rule.id]

    def matches(self, rule: Rule, query: str) -> bool:
        text = self.text(rule)
        for token in query.lower().split():
            if token in text:
                continue
            ip = _ip(token)
            if ip is None or not any(_contains(v, ip) for v in self._values[rule.id]):
                return False
        return True

    def uses_object(self, rule: Rule, name: str) -> bool:
        """Verwendet die Regel das Objekt (direkt, ueber eine Gruppe, als Endpunkt/EPG)?"""
        self.text(rule)
        return name.lower() in self._names[rule.id]

    def uses_nat(self, rule: Rule, name: str) -> bool:
        """Regel mit diesem VIP (Ziel, auch ueber Gruppen) bzw. SNAT-Pool."""
        key = name.lower()
        if key in {p.lower() for p in snat_pools(rule) or []}:
            return True
        return any(
            o.category is ObjectCategory.VIP and o.name.lower() == key
            for o in self._ref_objects(rule)
        )

    def nat_usage(self, rules: list[Rule]) -> dict[tuple[int, str], set[int]]:
        """(Quellsystem, NAT-Name klein) -> IDs der Regeln mit diesem VIP bzw. SNAT-Pool."""
        out: dict[tuple[int, str], set[int]] = defaultdict(set)
        for r in rules:
            for name in [*(snat_pools(r) or []), *self.vips(r)]:
                out[(r.source_system_id, name.lower())].add(r.id)
        return out

    def vips(self, rule: Rule) -> list[str]:
        """VIP-Objekte der Regel (DNAT), auch ueber Gruppen."""
        return sorted({o.name for o in self._ref_objects(rule) if o.category is ObjectCategory.VIP})
