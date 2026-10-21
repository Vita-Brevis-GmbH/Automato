"""Abgeleiteter Regelindex (R18, Dev-Befund „grosse Regelwerke"): bauen und abfragen.

Beim Einlesen je Quellsystem (bzw. nach einem Referenzlauf fuer den Mandanten) berechnet:
- Suchtext je Regel: Name, ID, Abschnitt, Aktion, Kommentar, Zonen, Schutzprofile, Services,
  Objekte wie auf der Firewall inkl. aller (verschachtelten) Gruppenmitglieder mit Werten, FQDN,
  Kommentar, Interface, aufgeloeste Endpunkte, ACI-Lage (EPG, AP, Tenant, BD, VRF, L3Out, DN),
  NAT (VIPs mit externer/interner IP und Port, SNAT-Pools) und die DNS-Namen der IPs aus dem
  IPAM (dort steht der Server ohne Praefix, z. B. web01.example.ch fuer H_web01),
- die Listenzeile (`rule_view.row`) und das IPv4/IPv6-Gegenstueck (`rule_pairs`),
- welche Firewall-Objekte die Regel verwendet - direkt oder ueber Gruppen (Regel-Zahl und
  Regeln im Objekt-Detail, NAT-Nutzung),
- welche IP-Bereiche sie als Quelle/Ziel abdeckt (IP-Suche, Applikations-Doku).

Abfragen: eine Suche aus mehreren Woertern verlangt alle; ein Wort, das eine IP ist, trifft
auch, wenn die IP in einem Netz bzw. Bereich der Regel liegt (any ausgenommen - sonst traefe
jede offene Regel). Exakte Filter `object` (Name) bzw. `object_id` und `nat`.
"""

import ipaddress
from collections import defaultdict
from functools import lru_cache

from sqlalchemy import delete, exists, func, insert, or_, select
from sqlalchemy.orm import Session

from app.models import (
    EndpointKind,
    EpgEndpoint,
    EpgSubnet,
    MatchStatus,
    NatRule,
    NetworkObject,
    ObjectCategory,
    RefRole,
    Rule,
    RuleDomain,
    RuleEndpoint,
    RuleIndex,
    RuleIpRange,
    RuleObjectUsage,
    RulePairDecision,
    SourceSystem,
)
from app.schemas.rules import AciContractOut, RuleNatOut, RuleOut, RulePairOut
from app.store import epgs as epg_store
from app.store import nat as nat_store
from app.store import objects as objects_store
from app.store import references, rule_pairs, rule_view, virtual
from app.store import rules as rules_store

# -- IP-Bereiche --------------------------------------------------------------


def ip_hex(ip) -> str:
    """IP als Hex fester Breite - Textvergleich = numerischer Vergleich (beide Familien)."""
    return f"{int(ip):032x}"


_ANY = {
    4: (ip_hex(ipaddress.ip_address("0.0.0.0")), ip_hex(ipaddress.ip_address("255.255.255.255"))),  # noqa: S104
    6: (ip_hex(ipaddress.ip_address("::")), ip_hex(ipaddress.ip_address("ffff:" * 7 + "ffff"))),
}


def _ip(text: str):
    try:
        return ipaddress.ip_address(text.strip())
    except ValueError:
        return None


def _parse_range(text: str) -> tuple[int, str, str, bool] | None:
    v = text.strip()
    if not v:
        return None
    if "-" in v and "/" not in v:
        a, _, b = v.partition("-")
        lo, hi = _ip(a), _ip(b)
        if lo and hi and lo.version == hi.version:
            return lo.version, ip_hex(min(lo, hi)), ip_hex(max(lo, hi)), False
        return None
    try:
        net = ipaddress.ip_network(v, strict=False)
    except ValueError:
        return None
    return (
        net.version,
        ip_hex(net.network_address),
        ip_hex(net.broadcast_address),
        net.prefixlen == 0,
    )


@lru_cache(maxsize=200_000)
def value_range(value: str | None) -> tuple[int, str, str, bool] | None:
    """(Familie, von, bis, any) eines Werts: IP, CIDR, Bereich a-b, `range:`/`subnet:`/`vip:`.
    FQDN, Geo, ISDB usw. decken keinen IP-Bereich ab -> None."""
    if not value:
        return None
    parsed = _parse_range(value)
    if parsed is None:
        tag, sep, rest = value.partition(":")
        if sep and tag in ("range", "subnet", "ip"):
            parsed = _parse_range(rest)
        elif sep and tag == "vip":
            parsed = _parse_range(rest.partition("=")[2])
    return parsed


def _families(rule: Rule) -> tuple[int, ...]:
    return {"v4": (4,), "v6": (6,)}.get(rule.ip_family or "", (4, 6))


# -- Aufbau -------------------------------------------------------------------


class _Builder:
    """Laedt Objekte, NAT, EPGs und IPAM-Kontext der Quellsysteme einmal fuer alle Regeln."""

    def __init__(self, session: Session, rules: list[Rule], tenant_id: int):
        sources = {r.source_system_id for r in rules}
        self.directory = objects_store.Directory(session, sources)
        # Ziele je VIP (0.12.0): abgeleitete VIP-Ziele und Adressobjekte hinter der VIP zaehlen
        # die Regeln der VIP mit
        self.vip_targets: dict[tuple[int, str], list] = defaultdict(list)
        for o in self.directory.by_id.values():
            attrs = o.attrs or {}
            if o.category is ObjectCategory.VIP_TARGET and attrs.get("vip"):
                self.vip_targets[(o.source_system_id, str(attrs["vip"]).lower())].append(o)
            for vip in attrs.get("vip_of") or []:
                self.vip_targets[(o.source_system_id, str(vip).lower())].append(o)
        self.objs = rules_store.objects_by_ids(
            session,
            {e.network_object_id for r in rules for e in r.endpoints if e.network_object_id},
        )
        self.zones = rules_store.zone_names(
            session, {z for r in rules for z in (r.src_zone_id, r.dst_zone_id) if z}
        )
        self.nat: dict[tuple[int, str], NatRule] = {}
        if sources:
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
        self.ctx = references.context(session, tenant_id)
        self.vms = virtual.Lookup(session, tenant_id)  # VM-Namen der IPs (vCenter, ADR 0017)
        self._dns: dict[str, list[str]] = {}

    def dns(self, value: str) -> list[str]:
        if value not in self._dns:
            self._dns[value] = references.dns_names(self.ctx, value)
        return self._dns[value]

    def build(self, rule: Rule, position: int) -> tuple[dict, list[dict], list[dict], object]:
        raw = rule.raw if isinstance(rule.raw, dict) else {}
        source = rule.source_system_id
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
        nat_names: set[str] = set()
        usage: dict[tuple[int, str], bool] = {}
        ranges: dict[tuple, str | None] = {}
        vips: list[str] = []

        def add_range(role: str, value: str | None, via: str | None) -> None:
            parsed = value_range(value)
            if parsed is not None:
                ranges.setdefault((role, *parsed), via)

        for ref in rule.refs:
            kind = "svc" if ref.role is RefRole.SERVICE else "addr"
            obj = self.directory.by_id.get(ref.device_object_id) if ref.device_object_id else None
            obj = obj or self.directory.find(source, ref.name, kind, ref.family)
            if obj is None:
                continue
            for i, o in enumerate(self.directory.expand(obj)):
                key = (o.id, ref.role.value)
                usage[key] = usage.get(key, False) or i == 0
                names.add(o.name.lower())
                parts += [o.name, o.fqdn, o.comment, o.interface, *(o.values or [])]
                if kind == "addr":
                    values += [str(v) for v in o.values or []]
                    for v in o.values or []:  # Werte wie auf der Firewall (auch Gruppen)
                        add_range(ref.role.value, str(v), o.name)
                if o.category is ObjectCategory.VIP:
                    vips.append(o.name)
                    nat_names.add(o.name.lower())
                    for t in self.vip_targets.get((source, o.name.lower()), []):
                        usage.setdefault((t.id, ref.role.value), False)  # ueber die VIP
                        names.add(t.name.lower())
                    nat = self.nat.get((source, o.name.lower()))
                    if nat is not None:
                        parts += [nat.ext_ip, nat.ext_port, nat.mapped_ip, nat.mapped_port]
                        internal = nat_store.internal_ips(nat)  # Virtual Server: alle
                        parts += [
                            *internal,
                            *(str(t.get("address") or "") for t in nat.targets or []),
                        ]
                        values += [v for v in (nat.ext_ip, *internal) if v]
                        for ip in internal:  # DNAT-Ziel: der Server bzw. die Real Server
                            add_range("dst", ip, o.name)
        for pool in rule_view.snat_pools(rule) or []:
            names.add(pool.lower())
            nat_names.add(pool.lower())
            parts.append(pool)
            pobj = self.directory.find(source, pool)
            if pobj is not None and pobj.category is ObjectCategory.IPPOOL:
                usage[(pobj.id, "snat")] = True
            nat = self.nat.get((source, pool.lower()))
            if nat is not None:
                parts += [nat.ext_ip, nat.mapped_ip]
                values += [v for v in (nat.ext_ip, nat.mapped_ip) if v]
                add_range("snat", nat.ext_ip, pool)
        for e in rule.endpoints:
            role = e.role.value
            obj = self.objs.get(e.network_object_id) if e.network_object_id else None
            if obj is not None:
                names.add(obj.name.lower())
                parts += [obj.name, obj.value, obj.fqdn]
                if obj.value:
                    values.append(obj.value)
                    add_range(role, obj.value, obj.name)
            elif e.kind is EndpointKind.ANY:
                for fam in _families(rule):
                    ranges.setdefault((role, fam, *_ANY[fam], True), e.inline_value or "any")
            parts.append(e.inline_value)
            epg = self.epgs.get(e.epg_id) if e.epg_id else None
            if epg is not None:
                label = epg_store.label(epg)
                names |= {epg.name.lower(), label.lower(), (epg.dn or "").lower()}
                parts += [epg.name, label, epg.aci_tenant, epg.ap, epg.bd, epg.vrf, epg.l3out]
                parts.append(epg.dn)
                for ip in self.epg_values.get(epg.id, []):
                    values.append(ip)
                    add_range(role, ip, label)
        for v in dict.fromkeys(values):
            parts += self.dns(v)
            parts += self.vms.names(v)

        vips = sorted(set(vips))
        index = {
            "rule_id": rule.id,
            "tenant_id": rule.tenant_id,
            "source_system_id": source,
            "text": "\n".join(str(p).lower() for p in parts if p),
            "names": _joined(names - {""}),
            "nat": _joined(nat_names),
            "row": rule_view.row(rule, self.objs, self.epgs, self.zones, vips),
        }
        usage_rows = [
            {
                "rule_id": rule.id,
                "device_object_id": oid,
                "source_system_id": source,
                "role": role,
                "direct": direct,
            }
            for (oid, role), direct in usage.items()
        ]
        range_rows = [
            {
                "rule_id": rule.id,
                "tenant_id": rule.tenant_id,
                "source_system_id": source,
                "role": role,
                "family": fam,
                "lo": lo,
                "hi": hi,
                "is_any": is_any,
                "via": (via or "")[:300] or None,
            }
            for (role, fam, lo, hi, is_any), via in ranges.items()
        ]
        return index, usage_rows, range_rows, _pair_info(rule, position)


def _joined(names: set[str]) -> str:
    return "\n" + "\n".join(sorted(names)) + "\n" if names else ""


def _pair_info(rule: Rule, position: int) -> rule_pairs.PairInfo | None:
    if rule.domain is not RuleDomain.FIREWALL or rule.ip_family not in ("v4", "v6"):
        return None

    def refs(role: RefRole) -> frozenset[str]:
        return frozenset(r.name.lower() for r in rule.refs if r.role is role)

    return rule_pairs.PairInfo(
        rule_id=rule.id,
        source_id=rule.source_system_id,
        family=rule.ip_family,
        action=rule.action.value,
        name=rule.name or "",
        position=position,
        src_zone=rule.src_zone_id,
        dst_zone=rule.dst_zone_id,
        src=refs(RefRole.SRC),
        dst=refs(RefRole.DST),
        service=refs(RefRole.SERVICE) or frozenset(s.canonical_name for s in rule.services),
        external_id=rule.external_id,
    )


def rebuild(
    session: Session,
    tenant_id: int | None,
    source_system_id: int | None = None,
    rules: list[Rule] | None = None,
) -> int:
    """Index fuer die Regeln eines Quellsystems (bzw. des ganzen Mandanten) neu aufbauen;
    `rules` = gerade eingelesene Regeln des Quellsystems (spart das erneute Laden)."""
    if tenant_id is None:
        return 0
    session.flush()
    if source_system_id is not None:
        sources = select(SourceSystem.id).where(SourceSystem.id == source_system_id)
    else:
        sources = select(SourceSystem.id).where(SourceSystem.tenant_id == tenant_id)
    for model in (RuleIndex, RuleObjectUsage, RuleIpRange):
        session.execute(delete(model).where(model.source_system_id.in_(sources)))
    if source_system_id is None:  # verwaiste Zeilen (Regeln ohne Quellsystem)
        session.execute(delete(RuleIndex).where(RuleIndex.tenant_id == tenant_id))
    if rules is None:
        rules = rules_store.list_rules(
            session, tenant_id, limit=None, source_system_id=source_system_id
        )
    if not rules:
        return 0
    builder = _Builder(session, rules, tenant_id)
    index_rows: list[dict] = []
    usage_rows: list[dict] = []
    range_rows: list[dict] = []
    infos = []
    for position, rule in enumerate(rules):
        index, usage, ranges, info = builder.build(rule, position)
        index_rows.append(index)
        usage_rows += usage
        range_rows += ranges
        if info is not None:
            infos.append(info)
    pairs = rule_pairs.pair(infos, pair_decisions(session, tenant_id, source_system_id))
    for row in index_rows:
        p = pairs.get(row["rule_id"])
        row["pair_rule_id"] = p.partner_id if p else None
        row["pair_match"] = p.match if p else None
        row["pair_diff"] = list(p.diff) if p else None
    session.execute(insert(RuleIndex), index_rows)
    if usage_rows:
        session.execute(insert(RuleObjectUsage), usage_rows)
    if range_rows:
        session.execute(insert(RuleIpRange), range_rows)
    session.flush()
    return len(rules)


def pair_decisions(
    session: Session, tenant_id: int, source_system_id: int | None = None
) -> rule_pairs.Decisions:
    """Von Hand bestaetigte/abgelehnte IPv4/IPv6-Paare des Mandanten (bzw. Quellsystems)."""
    stmt = select(RulePairDecision).where(RulePairDecision.tenant_id == tenant_id)
    if source_system_id is not None:
        stmt = stmt.where(RulePairDecision.source_system_id == source_system_id)
    return {
        (d.source_system_id, d.v4_external_id, d.v6_external_id): d.decision
        for d in session.scalars(stmt)
    }


def ensure(session: Session, tenant_id: int | None) -> None:
    """Fehlt der Index fuer Regeln (z. B. direkt nach dem Update auf 0.9.0), wird er fuer deren
    Quellsysteme einmalig aufgebaut - Listen und Suche lesen nur noch aus dem Index."""
    stmt = (
        select(Rule.tenant_id, Rule.source_system_id)
        .outerjoin(RuleIndex, RuleIndex.rule_id == Rule.id)
        .where(RuleIndex.rule_id.is_(None))
        .distinct()
    )
    if tenant_id is not None:
        stmt = stmt.where(Rule.tenant_id == tenant_id)
    for tid, sid in session.execute(stmt).all():
        rebuild(session, tid, sid)


# -- Abfragen -----------------------------------------------------------------


def _overlaps(parsed: tuple[int, str, str, bool], include_any: bool, roles=("src", "dst")):
    """Regel hat einen IP-Bereich (Quelle/Ziel), der sich mit `parsed` ueberschneidet."""
    fam, lo, hi, _ = parsed
    cond = [
        RuleIpRange.rule_id == Rule.id,
        RuleIpRange.family == fam,
        RuleIpRange.lo <= hi,
        RuleIpRange.hi >= lo,
        RuleIpRange.role.in_(roles),
    ]
    if not include_any:
        cond.append(RuleIpRange.is_any.is_(False))
    return exists(select(RuleIpRange.id).where(*cond))


def conditions(
    q: str | None = None,
    object_name: str | None = None,
    object_id: int | None = None,
    nat: str | None = None,
    net: str | None = None,
    net_any: bool = True,
) -> list:
    """WHERE-Bedingungen ueber `Rule.id` fuer Suche und exakte Filter. Ein Suchwort, das eine
    IP ist, trifft Regeln mit dieser IP im Bereich; ein Netz (CIDR) trifft Regeln, deren
    Bereiche sich mit ihm ueberschneiden (beides ohne any). `net` = Regeln eines Subnetzes
    (Quelle/Ziel ueberschneidet sich, auf Wunsch inkl. any)."""
    conds = []
    has_index = select(RuleIndex.rule_id).where(RuleIndex.rule_id == Rule.id)
    for token in (q or "").lower().split():
        text = exists(has_index.where(RuleIndex.text.contains(token, autoescape=True)))
        ip = _ip(token)
        if ip is not None:
            hx = ip_hex(ip)
            inside = exists(
                select(RuleIpRange.id).where(
                    RuleIpRange.rule_id == Rule.id,
                    RuleIpRange.family == ip.version,
                    RuleIpRange.lo <= hx,
                    RuleIpRange.hi >= hx,
                    RuleIpRange.is_any.is_(False),
                )
            )
            text = or_(text, inside)
        elif "/" in token and (parsed := _parse_range(token)) is not None and not parsed[3]:
            text = or_(text, _overlaps(parsed, include_any=False))
        conds.append(text)
    if net:
        parsed = value_range(net.strip())
        conds.append(_overlaps(parsed, net_any) if parsed else Rule.id.is_(None))
    if object_name:
        key = f"\n{object_name.strip().lower()}\n"
        conds.append(exists(has_index.where(RuleIndex.names.contains(key, autoescape=True))))
    if object_id is not None:
        conds.append(
            exists(
                select(RuleObjectUsage.id).where(
                    RuleObjectUsage.rule_id == Rule.id,
                    RuleObjectUsage.device_object_id == object_id,
                )
            )
        )
    if nat:
        key = f"\n{nat.strip().lower()}\n"
        conds.append(exists(has_index.where(RuleIndex.nat.contains(key, autoescape=True))))
    return conds


class Cover:
    """Wie eine Regel einen Wert abdeckt: je Seite der engste Bereich (Objekt/Wert, any) und ob
    er innerhalb des Werts liegt (Host im EPG-Subnetz)."""

    def __init__(self) -> None:
        self.sides: dict[str, tuple[int, str | None, bool, bool, str]] = {}

    def add(
        self,
        role: str,
        width: int,
        via: str | None,
        is_any: bool,
        inside: bool = False,
        value: str = "",
    ):
        if role not in self.sides or width < self.sides[role][0]:
            self.sides[role] = (width, via, is_any, inside, value)

    def via(self, role: str) -> str:
        """`any` bzw. das Objekt/der Wert, ueber den die Regel greift."""
        _, via, is_any, _, _ = self.sides[role]
        return "any" if is_any else (via or "Netz")

    def value(self, role: str) -> str:
        """Der Bereich des Objekts (Host, Netz, a-b; leer bei any)."""
        return self.sides[role][4]

    def scope(self, role: str) -> str:
        """`specific` (genau der Host bzw. innerhalb des Netzes), `subnet` (ein groesseres Netz
        bzw. ein Bereich, der den Wert enthaelt) oder `any` (Applikations-Doku, 0.14.0)."""
        width, _, is_any, inside, _ = self.sides[role]
        if is_any:
            return "any"
        return "specific" if width == 0 or inside else "subnet"


def covers(
    session: Session,
    tenant_id: int,
    values: list[str],
    overlap: bool = False,
    domain: RuleDomain | None = None,
) -> dict[int, Cover]:
    """Regel-ID -> Abdeckung (Quelle/Ziel) fuer IPs bzw. Netze: Bereiche, die den Wert
    enthalten (bzw. mit `overlap` sich mit ihm ueberschneiden - EPG-Subnetze), inkl. any."""
    out: dict[int, Cover] = {}
    for value in dict.fromkeys(values):
        parsed = value_range(value)
        if parsed is None:
            continue
        fam, lo, hi, _ = parsed
        stmt = select(
            RuleIpRange.rule_id,
            RuleIpRange.role,
            RuleIpRange.lo,
            RuleIpRange.hi,
            RuleIpRange.via,
            RuleIpRange.is_any,
        ).where(
            RuleIpRange.tenant_id == tenant_id,
            RuleIpRange.family == fam,
            RuleIpRange.role.in_(("src", "dst")),
        )
        if overlap:
            stmt = stmt.where(RuleIpRange.lo <= hi, RuleIpRange.hi >= lo)
        else:
            stmt = stmt.where(RuleIpRange.lo <= lo, RuleIpRange.hi >= hi)
        if domain is not None:
            stmt = stmt.join(Rule, Rule.id == RuleIpRange.rule_id).where(Rule.domain == domain)
        vlo, vhi = int(lo, 16), int(hi, 16)
        for rule_id, role, rlo, rhi, via, is_any in session.execute(stmt):
            a, b = int(rlo, 16), int(rhi, 16)
            inside = not is_any and vlo <= a and b <= vhi
            text = "" if is_any else range_text(fam, a, b)
            out.setdefault(rule_id, Cover()).add(role, b - a, via, is_any, inside, text)
    return out


def range_text(family: int, lo: int, hi: int) -> str:
    """Bereich als Text: Host, Netz (CIDR) bzw. `a-b`."""
    cls = ipaddress.IPv4Address if family == 4 else ipaddress.IPv6Address
    first, last = cls(lo), cls(hi)
    nets = list(ipaddress.summarize_address_range(first, last))
    if len(nets) == 1:
        net = nets[0]
        return str(net.network_address) if net.num_addresses == 1 else str(net)
    return f"{first}-{last}"


def usage_counts(session: Session, object_ids: list[int]) -> dict[int, int]:
    """Objekt-ID -> Anzahl Regeln, die es verwenden (direkt oder ueber Gruppen)."""
    if not object_ids:
        return {}
    rows = session.execute(
        select(RuleObjectUsage.device_object_id, func.count(func.distinct(RuleObjectUsage.rule_id)))
        .where(RuleObjectUsage.device_object_id.in_(object_ids))
        .group_by(RuleObjectUsage.device_object_id)
    )
    return dict(rows.all())


def rule_ids_using(session: Session, object_id: int) -> list[int]:
    """Regeln (in Firewall-Reihenfolge), die das Objekt verwenden - direkt oder ueber Gruppen."""
    return list(
        session.scalars(
            select(Rule.id)
            .where(
                exists(
                    select(RuleObjectUsage.id).where(
                        RuleObjectUsage.rule_id == Rule.id,
                        RuleObjectUsage.device_object_id == object_id,
                    )
                )
            )
            .order_by(Rule.source_system_id, Rule.sequence, Rule.id)
        )
    )


def outs(session: Session, rules: list[Rule]) -> list[RuleOut]:
    """Listenzeilen aus dem Index; nicht aufgeloeste Objekte live (Abgleich aendert sie)."""
    if not rules:
        return []
    ids = [r.id for r in rules]
    index = {
        i.rule_id: i for i in session.scalars(select(RuleIndex).where(RuleIndex.rule_id.in_(ids)))
    }
    missing = [r for r in rules if r.id not in index or index[r.id].row is None]
    if missing:  # Index (noch) nicht aufgebaut - jetzt nachholen
        for tid, sid in {(r.tenant_id, r.source_system_id) for r in missing}:
            rebuild(session, tid, sid)
        index = {
            i.rule_id: i
            for i in session.scalars(select(RuleIndex).where(RuleIndex.rule_id.in_(ids)))
        }
    unmatched = dict(
        session.execute(
            select(RuleEndpoint.rule_id, func.count(RuleEndpoint.id))
            .join(NetworkObject, NetworkObject.id == RuleEndpoint.network_object_id)
            .where(
                RuleEndpoint.rule_id.in_(ids),
                NetworkObject.match_status == MatchStatus.UNMATCHED,
            )
            .group_by(RuleEndpoint.rule_id)
        ).all()
    )
    partners = {i.pair_rule_id for i in index.values() if i.pair_rule_id}
    partner_rows = (
        {
            i.rule_id: i.row or {}
            for i in session.scalars(select(RuleIndex).where(RuleIndex.rule_id.in_(partners)))
        }
        if partners
        else {}
    )
    out: list[RuleOut] = []
    for r in rules:
        idx = index.get(r.id)
        row = dict(idx.row or {}) if idx is not None else {}
        aci, nat = row.pop("aci", None), row.pop("nat", None)
        pair = None
        if idx is not None and idx.pair_rule_id and idx.pair_rule_id in partner_rows:
            partner = partner_rows[idx.pair_rule_id]
            pair = RulePairOut(
                rule_id=idx.pair_rule_id,
                name=partner.get("name") or f"#{idx.pair_rule_id}",
                family=partner.get("family"),
                match=idx.pair_match or "similar",
                diff=idx.pair_diff or [],
            )
        out.append(
            RuleOut(
                **row,
                id=r.id,
                tenant_id=r.tenant_id,
                source_system_id=r.source_system_id,
                domain=r.domain.value,
                num_unmatched=unmatched.get(r.id, 0),
                aci=AciContractOut(**aci) if aci else None,
                nat=RuleNatOut(**nat) if nat else None,
                pair=pair,
            )
        )
    return out


def load_rules(session: Session, ids: list[int], children: bool = False) -> list[Rule]:
    """Regeln in der Reihenfolge der IDs; fuer Listenzeilen (`outs`) ohne Endpunkte/Verweise."""
    if not ids:
        return []
    if children:
        found = rules_store.list_rules(session, limit=None, rule_ids=ids)
    else:
        found = session.scalars(select(Rule).where(Rule.id.in_(ids)))
    by_id = {r.id: r for r in found}
    return [by_id[i] for i in ids if i in by_id]
