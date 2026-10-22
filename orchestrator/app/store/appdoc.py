"""Applikations-Doku (UC1, systemzentriert) - die 10 Kategorien aus
`docs/usecases/uc1-applikations-doku-spec.md`.

Mittelpunkt ist ein **System** (Host-`network_object`). Für dieses System werden alle
Regeln, an denen es beteiligt ist, nach **Richtung × Scope × Enforcement** in 10
Tabellen eingeordnet. Nutzt `rule.protections` (Schutzmechanismen) und `nat_rule`
(`nat_type` dnat_vip/snat_pool) für die NAT-Spalten.

Model 2 (FortiGate): Tabellen 1–6, 9, 10; die ACI-internen Tabellen 7/8 bleiben leer bis
Model 1. Die Kategorisierung ist bewusst konservativ: nur eindeutig ableitbare Regeln
werden zugeordnet, der Rest landet in Tabelle 10 (unspezifisch). Rein lesend/pure Logik.
"""

from dataclasses import dataclass
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Epg,
    EpgEndpoint,
    EpgSubnet,
    NatRule,
    NetworkObject,
    ObjectClass,
    Rule,
    RuleDomain,
    RuleEndpoint,
    Site,
    Tenant,
)
from app.store import epgs as epg_store
from app.store import nat as nat_store
from app.store import relevance as relevance_store
from app.store import rule_index, sites, virtual
from app.store import rules as rules_store

SEP = " · "  # gleichnamige EPGs mehrerer ACI-Tenants: "EG_x · TE_a · AP_1"

# Tabellen-Metadaten (Index -> Titel, Enforcement). Spaltenlisten siehe TABLE_COLUMNS.
TABLES: list[tuple[int, str, str]] = [
    (1, "Internet Zugriff", "firewall"),
    (2, "Zugriff aus dem Internet", "firewall"),
    (3, "Zugriff in das Unternehmensnetzwerk", "firewall"),
    (4, "Zugriff aus dem Unternehmensnetzwerk", "firewall"),
    (5, "Zugriff L3O_vpn", "firewall"),
    (6, "Zugriff L3O_sra (Remote Access)", "firewall"),
    (7, "Zugriff innerhalb des Applikationsprofils", "aci"),
    (8, "Zugriff innerhalb des Security Tenant", "aci"),
    (9, "Zugriff zu anderen Security Tenants", "firewall"),
    (10, "Unspezifische Zugriffe auf die Applikation", "firewall"),
]

# "Bezug": wie die Regel das System erfasst - direkt, ueber ein Netz/einen Bereich bzw. eine
# Gruppe (Objektname), any, vzAny der VRF, ESG oder die EPG des Servers (Dev-Befund 0.10.0)
VIA = "Bezug"
# Zusatzfelder je Zeile (nicht als Spalte, 0.14.0): Regel-ID, Richtung aus Sicht des Systems
# (in = System ist Ziel, out = Quelle) und Art des Bezugs (specific = der Host bzw. die EPG
# selbst, subnet = groesseres Netz, any, vzany = vzAny der VRF, pref_group = Preferred Group)
RULE, DIRECTION, SCOPE = "_rule", "_dir", "_scope"
# Seite der Regel (src | dst) und das Objekt der Regel, ueber das das System erfasst wird -
# wie es in der Regel steht (H_web01, N_srv16 mit 10.20.0.0/16, any, EPG Tenant/AP/EPG), 0.14.1
SIDE, OBJECT, OBJECT_VALUE = "_side", "_obj", "_val"
# Relevanz (0.24.0, ADR 0019): kann die Regel Verkehr zum bzw. vom System ueberhaupt sehen
# (possible | impossible | unknown) und warum nicht - nach dem Routing ihrer Firewall
REL, REL_TEXT = "_rel", "_rel_text"
UNSPECIFIC = ("subnet", "any", "vzany", "pref_group")
_COMMON = ["Server Name", "TCP/UDP", "Target Systems", "Schutzmechanismen", "Description", VIA]
TABLE_COLUMNS: dict[int, list[str]] = {
    1: [
        "Server Name",
        "TCP/UDP",
        "Target Systems",
        "Schutzmechanismen",
        "NAT Externe IPv4",
        "Description",
        VIA,
    ],
    2: [
        "Source",
        "TCP/UDP",
        "Server Name",
        "VIP IP Adresse",
        "Externer DNS Name",
        "Externe IP Adressen",
        "Schutzmechanismen",
        "Description",
        VIA,
    ],
}
for _i in (3, 4, 5, 6, 7, 8, 9, 10):
    TABLE_COLUMNS[_i] = _COMMON

# Zonen-Marker (Substring im Zonennamen) -> Scope-Art. L3O_inet/bit/vpn/sra/res (ACI/Model 1);
# in Model 2 greifen meist nur NAT-basierte Kategorien (1/2) + Fallback (10).
_ZONE_MARKERS: list[tuple[str, str]] = [
    ("inet", "inet"),
    ("sra", "sra"),
    ("vpn", "vpn"),
    ("res", "res"),
    ("bit", "bit"),
]


def zone_kind(name: str | None) -> str | None:
    if not name:
        return None
    low = name.lower()
    for sub, kind in _ZONE_MARKERS:
        if sub in low:
            return kind
    return None


def categorize(
    *,
    direction: str,  # "in" (System = Ziel) | "out" (System = Quelle)
    counterpart_zone: str | None,
    counterpart_external: bool,
    is_dnat_target: bool,
) -> int:
    """Ordnet eine Regel einer der 10 Tabellen zu (pure; testbar)."""
    kind = zone_kind(counterpart_zone)
    if direction == "in":
        if is_dnat_target or kind == "inet":
            return 2
        if kind == "bit":
            return 3
        if kind == "vpn":
            return 5
        if kind == "sra":
            return 6
        if kind == "res":
            return 9
        return 10
    # direction == "out"
    if counterpart_external or kind == "inet":
        return 1
    if kind == "bit":
        return 4
    if kind == "vpn":
        return 5
    if kind == "sra":
        return 6
    if kind == "res":
        return 9
    return 10


@dataclass
class SystemRef:
    name: str
    ip: str | None
    rule_count: int
    kind: str = "host"  # host | epg


def list_systems(session: Session, tenant_id: int | None) -> list[SystemRef]:
    """Systeme mit Regelbezug: Host-Objekte (Firewall) und EPGs (ACI)."""
    stmt = (
        select(NetworkObject.name, NetworkObject.value, RuleEndpoint.rule_id)
        .join(RuleEndpoint, RuleEndpoint.network_object_id == NetworkObject.id)
        .where(NetworkObject.object_class == ObjectClass.HOST)
    )
    if tenant_id is not None:
        stmt = stmt.where(NetworkObject.tenant_id == tenant_id)
    agg: dict[str, tuple[str | None, set[int]]] = {}
    for name, value, rid in session.execute(stmt):
        ip, rids = agg.setdefault(name, (value, set()))
        rids.add(rid)
    systems = [
        SystemRef(name=n, ip=v, rule_count=len(rids), kind="host") for n, (v, rids) in agg.items()
    ]

    # EPGs (ACI) als Systeme - externe EPGs und vzAny sind keine Systeme
    estmt = select(Epg).where(Epg.kind == "epg")
    if tenant_id is not None:
        estmt = estmt.where(Epg.tenant_id == tenant_id)
    epgs = list(session.scalars(estmt))
    names = [(e.tenant_id, e.name) for e in epgs]
    rules_of: dict[int, set[int]] = {}
    if epgs:
        for epg_id, rule_id in session.execute(
            select(RuleEndpoint.epg_id, RuleEndpoint.rule_id).where(
                RuleEndpoint.epg_id.in_([e.id for e in epgs])
            )
        ):
            rules_of.setdefault(epg_id, set()).add(rule_id)
    for epg in epgs:
        name = epg_system_name(epg) if names.count((epg.tenant_id, epg.name)) > 1 else epg.name
        systems.append(
            SystemRef(name=name, ip=None, rule_count=len(rules_of.get(epg.id, ())), kind="epg")
        )

    return sorted(systems, key=lambda s: (s.kind, s.name))


def _target(other: Epg | None, inline: str | None, own: Epg) -> str:
    """Gegenstelle in der Doku: EPG mit dem ganzen ACI-Pfad `Tenant/AP/EPG` (Dev-Befund 0.14.0:
    `EG_dmz01` allein ist nicht eindeutig); externe EPG / vzAny mit L3Out bzw. VRF."""
    if other is None:
        return inline or ""
    if other.kind != "epg":
        return epg_store.label(other, own.aci_tenant)
    return epg_store.inventory_label(other)


def epg_system_name(epg: Epg) -> str:
    """Eindeutiger Systemname einer EPG, deren Name es in mehreren ACI-Tenants/APs gibt."""
    return SEP.join(x for x in (epg.name, epg.aci_tenant, epg.ap) if x)


def _label(obj: NetworkObject | None, inline: str | None) -> str:
    if obj is not None:
        return obj.value or obj.fqdn or obj.name
    return inline or "any"


def categorize_aci(scope: str | None, same_ap: bool, same_tenant: bool) -> int:
    """ACI-interne Kategorie: 7 (innerhalb AP), 8 (innerhalb Tenant), 9 (andere Tenants)."""
    if scope == "application-profile" or same_ap:
        return 7
    if scope == "global" or not same_tenant:
        return 9
    return 8  # context/tenant, anderes AP


def _envelope(
    session: Session,
    system_name: str,
    tenant_id: int,
    system_ip: str | None,
    site_id: int | None,
    rows_by_table: dict[int, list[dict]],
    vms: list[dict] | None = None,
    full: bool = False,
) -> dict:
    """Doku eines Systems. Regeln, die das System nach dem Routing nicht sehen koennen, stehen
    nicht in den Tabellen, sondern unter `impossible` mit Begruendung (`full` = alle Zeilen,
    fuer die Applikations-Doku, die selbst filtert)."""
    impossible: list[dict] = []
    if not full:
        for i in rows_by_table:
            keep = []
            for row in rows_by_table[i]:
                if row.get(REL) != relevance_store.IMPOSSIBLE:
                    keep.append(row)
                elif not any(
                    (x["rule_id"], x["side"]) == (row.get(RULE), row.get(SIDE)) for x in impossible
                ):
                    impossible.append(
                        {
                            "rule_id": row.get(RULE),
                            "description": row.get("Description") or "",
                            "side": row.get(SIDE),
                            "text": row.get(REL_TEXT) or "",
                        }  # fmt: skip
                    )
            rows_by_table[i] = keep
    tables = [
        {
            "index": i,
            "title": title,
            "enforcement": enf,
            "columns": TABLE_COLUMNS[i],
            "rows": rows_by_table[i],
        }
        for i, title, enf in TABLES
    ]
    tenant = session.get(Tenant, tenant_id)
    site = session.get(Site, site_id) if site_id else None
    return {
        "system": system_name,
        "system_ip": system_ip,
        "tenant_code": tenant.code if tenant else None,
        "site": sites.SiteIndex(session, site.tenant_id).ref(site) if site else None,
        "vms": vms or [],  # VMs dahinter (vCenter, ADR 0017)
        "tables": tables,
        "impossible": impossible,  # routingtechnisch nicht moeglich (ADR 0019)
    }


class TenantData:
    """Regeln, Objekte, Zonen, NAT und EPGs eines Mandanten - je Doku nur die Regeln der Systeme
    (per SQL ausgewaehlt), einmal geladen, auch wenn die Doku mehrerer Systeme (Applikation)
    gebaut wird (Dev-Befund: grosse Regelwerke)."""

    def __init__(self, session: Session, tenant_id: int):
        self.session = session
        self.tenant_id = tenant_id
        self._rules: dict[int, Rule] = {}
        self._objs: dict[int, NetworkObject] = {}
        self._zones: dict[int, str] = {}

    def rules(self, ids: set[int]) -> list[Rule]:
        """Regeln mit Endpunkten/Services in Firewall-Reihenfolge (fehlende nachgeladen)."""
        todo = [i for i in ids if i not in self._rules]
        if todo:
            for r in rules_store.list_rules(
                self.session, self.tenant_id, limit=None, rule_ids=todo
            ):
                self._rules[r.id] = r
            obj_ids = {e.network_object_id for i in todo for e in self._rules[i].endpoints}
            obj_ids -= set(self._objs)
            self._objs.update(rules_store.objects_by_ids(self.session, {i for i in obj_ids if i}))
            zone_ids = {
                z for i in todo for z in (self._rules[i].src_zone_id, self._rules[i].dst_zone_id)
            }
            self._zones.update(rules_store.zone_names(self.session, zone_ids - set(self._zones)))
        found = [self._rules[i] for i in ids if i in self._rules]
        return sorted(found, key=lambda r: (r.source_system_id, r.sequence or 0, r.id))

    def rule_ids(self, domain: RuleDomain, **endpoint) -> set[int]:
        """IDs der Regeln einer Domaene mit einem Endpunkt (network_object_id=… | epg_id=…)."""
        cond = [getattr(RuleEndpoint, k) == v for k, v in endpoint.items()]
        return set(
            self.session.scalars(
                select(RuleEndpoint.rule_id)
                .join(Rule, Rule.id == RuleEndpoint.rule_id)
                .where(Rule.tenant_id == self.tenant_id, Rule.domain == domain, *cond)
            )
        )

    @property
    def objs(self) -> dict:
        return self._objs

    @property
    def zones(self) -> dict:
        return self._zones

    @cached_property
    def nats(self) -> list[NatRule]:
        stmt = select(NatRule).where(NatRule.tenant_id == self.tenant_id)
        return list(self.session.scalars(stmt))

    @cached_property
    def epgs_by_id(self) -> dict[int, Epg]:
        stmt = select(Epg).where(Epg.tenant_id == self.tenant_id)
        return {e.id: e for e in self.session.scalars(stmt)}

    @cached_property
    def relevance(self) -> relevance_store.Relevance:
        return relevance_store.Relevance(self.session, self.tenant_id)


def _proxies(data: TenantData, epg: Epg) -> dict[int, str]:
    """EPG-IDs, deren Contracts fuer die EPG gelten, mit Bezug: die EPG selbst, vzAny ihrer
    VRF (alle EPGs der VRF) und ESGs, die sie per EPG-Selektor einschliessen."""
    out = {epg.id: "direkt"}
    for other in data.epgs_by_id.values():
        if (
            other.kind == "vzany"
            and epg.vrf
            and other.vrf == epg.vrf
            and other.aci_tenant in (epg.aci_tenant, "common")
        ):
            out[other.id] = f"vzAny {other.vrf}"
        elif other.kind == "esg" and epg.dn and f"EPG {epg.dn}" in (other.selectors or []):
            out[other.id] = f"ESG {other.name}"
    return out


def _aci_scope(rule: Rule, via: str) -> str:
    raw = rule.raw if isinstance(rule.raw, dict) else {}
    if raw.get("kind") == "preferred_group":
        return "pref_group"
    return "vzany" if via.startswith("vzAny") else "specific"


def _aci_rows(data: TenantData, epg: Epg, system_name: str, via: str | None = None) -> list:
    """ACI-Contracts einer EPG (direkt, ueber vzAny der VRF, ueber ESGs) als (Tabelle, Zeile).

    Gegenstelle gleiches AP (im selben ACI-Tenant) -> 7, anderer ACI-Tenant -> 9, hinter
    einem L3Out nach dessen Zone wie bei Firewall-Regeln. `via` (Server in der EPG) steht im
    Bezug vor dem Weg der EPG (vzAny, ESG)."""
    by_id = data.epgs_by_id
    proxies = _proxies(data, epg)
    ids: set[int] = set()
    for pid in proxies:
        ids |= data.rule_ids(RuleDomain.ACI_CONTRACT, epg_id=pid)
    ids |= data.rule_ids(RuleDomain.ACI_CONTRACT, epg_id=None, inline_value=epg.name)

    def self_via(e) -> str | None:  # aeltere Laeufe ohne EPG-Verknuepfung: ueber den Namen
        if e.epg_id in proxies:
            return proxies[e.epg_id]
        return "direkt" if e.epg_id is None and e.inline_value == epg.name else None

    out = []
    for r in data.rules(ids):
        eps = [e for e in r.endpoints if e.kind.value == "epg"]
        sides: dict[str, str] = {}
        for e in eps:
            v = self_via(e)
            if v and (e.role.value not in sides or v == "direkt"):
                sides[e.role.value] = v
        for side in sorted(sides, key=lambda x: x != "dst"):  # zuerst eingehend
            cp_role = "src" if side == "dst" else "dst"
            cp = [e for e in eps if e.role.value == cp_role]
            cp_epgs = [by_id[e.epg_id] for e in cp if e.epg_id in by_id]
            cp_names = [_target(by_id.get(e.epg_id), e.inline_value, epg) for e in cp]
            same_ap = any(
                c.ap == epg.ap and c.aci_tenant == epg.aci_tenant and epg.ap is not None
                for c in cp_epgs
            )
            same_tenant = all(c.aci_tenant == epg.aci_tenant for c in cp_epgs)
            external = [c for c in cp_epgs if c.kind == "external"]
            if external and len(external) == len(cp_epgs):
                idx = categorize(
                    direction="in" if side == "dst" else "out",
                    counterpart_zone=external[0].l3out,
                    counterpart_external=False,
                    is_dnat_target=False,
                )
            else:
                idx = categorize_aci(r.aci_scope, same_ap, same_tenant=same_tenant)
            how = sides[side]
            text = how if via is None else (via if how == "direkt" else f"{how} · {via}")
            obj = f"EPG {epg_store.inventory_label(epg)}" if how == "direkt" else how
            out.append(
                (
                    idx,
                    {
                        "Server Name": system_name,
                        "TCP/UDP": ", ".join(s.canonical_name for s in r.services),
                        "Target Systems": ", ".join(n for n in cp_names if n),
                        "Schutzmechanismen": ", ".join(r.protections) if r.protections else "",
                        "Description": r.name or f"#{r.external_id}",
                        VIA: text,
                        RULE: r.id,
                        DIRECTION: "in" if side == "dst" else "out",
                        SCOPE: _aci_scope(r, how),
                        SIDE: side,
                        OBJECT: obj,
                        OBJECT_VALUE: "",
                    },
                )
            )
    return out


def _fw_rows(
    data: TenantData,
    rule: Rule,
    sides: dict[str, tuple[str, str, str, str]],
    system_name: str,
    dnat_for_sys: list[NatRule],
    snat_ips: list[str],
    rels: dict[str, dict] | None = None,
) -> list:
    """Firewall-Regel als (Tabelle, Zeile) je Seite, auf der das System steht (Ziel =
    eingehend, Quelle = ausgehend); `sides` = Seite -> (Bezug, Art des Bezugs, Objekt der
    Regel, sein Bereich); `rels` = Seite -> Relevanz (Routing)."""
    objs, zones = data.objs, data.zones
    out = []
    for side in sorted(sides, key=lambda x: x != "dst"):
        direction = "in" if side == "dst" else "out"
        counterpart_eps = [e for e in rule.endpoints if e.role.value != side]
        cp_objs = [
            objs.get(e.network_object_id) if e.network_object_id else None for e in counterpart_eps
        ]
        cp_labels = [
            _label(o, e.inline_value) for o, e in zip(cp_objs, counterpart_eps, strict=True)
        ]
        cp_external = any(o is not None and o.object_class == ObjectClass.EXTERNAL for o in cp_objs)
        cp_zone = zones.get(rule.src_zone_id) if direction == "in" else zones.get(rule.dst_zone_id)
        idx = categorize(
            direction=direction,
            counterpart_zone=cp_zone,
            counterpart_external=cp_external,
            is_dnat_target=direction == "in" and bool(dnat_for_sys),
        )
        services = ", ".join(s.canonical_name for s in rule.services)
        protections = ", ".join(rule.protections) if rule.protections else ""
        description = rule.name or f"#{rule.external_id}"
        targets = ", ".join(cp_labels)
        if idx == 1:
            row = {
                "Server Name": system_name,
                "TCP/UDP": services,
                "Target Systems": targets,
                "Schutzmechanismen": protections,
                "NAT Externe IPv4": ", ".join(snat_ips),
                "Description": description,
            }
        elif idx == 2:
            vip = dnat_for_sys[0] if dnat_for_sys else None
            dns = ""
            if vip and vip.name:
                dns = vip.name.split("_", 1)[1] if "_" in vip.name else vip.name
            row = {
                "Source": targets,
                "TCP/UDP": services,
                "Server Name": system_name,
                "VIP IP Adresse": (vip.ext_ip if vip else "") or "",
                "Externer DNS Name": dns,
                "Externe IP Adressen": (vip.ext_ip if vip else "") or "",
                "Schutzmechanismen": protections,
                "Description": description,
            }
        else:
            row = {
                "Server Name": system_name,
                "TCP/UDP": services,
                "Target Systems": targets,
                "Schutzmechanismen": protections,
                "Description": description,
            }
        via, scope, obj, value = sides[side]
        meta = {RULE: rule.id, DIRECTION: direction, SCOPE: scope, SIDE: side}
        rel = (rels or {}).get(side)
        if rel is not None:
            meta |= {REL: rel["state"], REL_TEXT: rel["text"]}
        out.append((idx, {**row, VIA: via, **meta, OBJECT: obj, OBJECT_VALUE: value}))
    return out


def _nat_context(data: TenantData, ips: set[str]) -> tuple[list[NatRule], list[str]]:
    """VIPs mit einem der IPs als internem Ziel (auch Real Server) und alle SNAT-Pool-IPs."""
    dnat = [
        n for n in data.nats if n.nat_type == "dnat_vip" and ips & set(nat_store.internal_ips(n))
    ]
    snat = sorted({n.ext_ip for n in data.nats if n.nat_type == "snat_pool" and n.ext_ip})
    return dnat, snat


def _covered_rows(
    session: Session,
    data: TenantData,
    values: list[str],
    system_name: str,
    overlap: bool,
    direct_ids: set[int] | None = None,
    is_direct=None,
) -> list:
    """Firewall-Regeln eines Systems: direkt (Endpunkt) und alle, deren Quelle/Ziel den Wert
    enthaelt (Host-IP) bzw. sich mit ihm ueberschneidet (EPG-Subnetz) - ueber Netze, Bereiche,
    Gruppen und any, gleichwertig (Dev-Befund 0.10.0)."""
    cover = rule_index.covers(
        session, data.tenant_id, values, overlap=overlap, domain=RuleDomain.FIREWALL
    )
    dnat, snat = _nat_context(data, {v.split("/")[0] for v in values})
    out = []
    for r in data.rules((direct_ids or set()) | set(cover)):
        sides: dict[str, tuple[str, str, str, str]] = {}
        c = cover.get(r.id)
        if is_direct is not None:
            for e in r.endpoints:
                role = e.role.value
                if is_direct(e) and role not in sides:
                    o = data.objs.get(e.network_object_id) if e.network_object_id else None
                    if c is not None and role in c.sides:  # Objektname wie auf der Firewall
                        sides[role] = ("direkt", "specific", c.via(role), c.value(role))
                    else:
                        sides[role] = ("direkt", "specific", o.name if o else "", _label(o, None))
        for role in c.sides if c else ():
            sides.setdefault(role, (c.via(role), c.scope(role), c.via(role), c.value(role)))
        if any(side[1] != "any" for side in sides.values()):
            # Server -> any: das System steht gezielt auf einer Seite; dass any auf der Gegenseite
            # es auch umfasst, ist kein eigener Zugriff (Dev-Befund 0.14.0)
            sides = {k: v for k, v in sides.items() if v[1] != "any"}
        zones = {"src": data.zones.get(r.src_zone_id), "dst": data.zones.get(r.dst_zone_id)}
        rels = {s: data.relevance.check(r, s, values, zones[s]) for s in sides}
        out += _fw_rows(data, r, sides, system_name, dnat, snat, rels)
    return out


def epg_doc(session: Session, epg: Epg, data: TenantData | None = None, full: bool = False) -> dict:
    """ACI-EPG als System: Contracts (direkt, vzAny der VRF, ESGs) nach scope/AP in die
    Tabellen 7/8/9 (+ L3Out-Tabellen) und die Firewall-Regeln, die ihre Subnetze bzw.
    Endpunkte erfassen (auch ueber groessere Netze und any). Als Name der ganze ACI-Pfad."""
    data = data or TenantData(session, epg.tenant_id)
    name = epg_store.inventory_label(epg)
    rows_by_table: dict[int, list[dict]] = {i: [] for i, _, _ in TABLES}
    for idx, row in _aci_rows(data, epg, name):
        rows_by_table[idx].append(row)
    values = [s.ip for s in session.scalars(select(EpgSubnet).where(EpgSubnet.epg_id == epg.id))]
    values += [
        ep.ip for ep in session.scalars(select(EpgEndpoint).where(EpgEndpoint.epg_id == epg.id))
    ]
    for idx, row in _covered_rows(session, data, values, name, overlap=True):
        rows_by_table[idx].append(row)
    host = session.get(NetworkObject, epg.network_object_id) if epg.network_object_id else None
    return _envelope(
        session,
        name,
        epg.tenant_id,
        host.value if host else None,
        host.site_id if host else None,
        rows_by_table,
        epg_store.vms_of(session, [epg.id]).get(epg.id, []),
        full,
    )


def build_doc(session: Session, system_name: str, tenant_id: int | None) -> dict | None:
    """Baut die Tabellen für ein System: Host → Firewall-Tabellen (1–6,10), EPG → ACI (7–9)."""
    sys_obj = session.scalars(
        select(NetworkObject).where(
            NetworkObject.name == system_name,
            NetworkObject.object_class == ObjectClass.HOST,
            *([NetworkObject.tenant_id == tenant_id] if tenant_id is not None else []),
        )
    ).first()
    if sys_obj is not None:
        return host_doc(session, sys_obj)
    name, *where = system_name.split(SEP)  # "EG_x · TE_a · AP_1" bei gleichen Namen
    epg = session.scalars(
        select(Epg).where(
            Epg.name == name,
            Epg.kind == "epg",
            *([Epg.aci_tenant == where[0]] if where else []),
            *([Epg.ap == where[1]] if len(where) > 1 else []),
            *([Epg.tenant_id == tenant_id] if tenant_id is not None else []),
        )
    ).first()
    if epg is not None:
        return epg_doc(session, epg)
    return None


def host_doc(
    session: Session, sys_obj: NetworkObject, data: TenantData | None = None, full: bool = False
) -> dict:
    """Doku eines Host-Systems: alle Firewall-Regeln, die den Server erfassen - direkt und ueber
    Netze, Bereiche, Gruppen und any -, dazu die ACI-Contracts der EPG(s) des Servers."""
    system_name = sys_obj.name
    tenant_id = sys_obj.tenant_id
    sys_ip = sys_obj.value
    data = data or TenantData(session, tenant_id)

    rows_by_table: dict[int, list[dict]] = {i: [] for i, _, _ in TABLES}
    direct = data.rule_ids(RuleDomain.FIREWALL, network_object_id=sys_obj.id)
    for idx, row in _covered_rows(
        session,
        data,
        [sys_ip] if sys_ip else [],
        system_name,
        overlap=False,
        direct_ids=direct,
        is_direct=lambda e: e.network_object_id == sys_obj.id,
    ):
        rows_by_table[idx].append(row)
    ip = sys_ip.split("/")[0] if sys_ip else None
    vms = virtual.Lookup(session, tenant_id, {ip} if ip else set())
    # ACI: die EPG(s), in denen der Server steht (verknuepft, gelernter Endpunkt, Portgruppe
    # der VM)
    for epg in host_epgs(session, data, sys_obj, vms.epg_ids(ip)):
        for idx, row in _aci_rows(data, epg, system_name, via=f"EPG {epg_store.label(epg)}"):
            rows_by_table[idx].append(row)
    info = vms.info(ip)
    return _envelope(
        session,
        system_name,
        tenant_id,
        sys_ip,
        sys_obj.site_id,
        rows_by_table,
        [info] if info else [],
        full,
    )


def vm_rows(
    session: Session,
    data: TenantData,
    name: str,
    ips: list[str],
    hosts: list[NetworkObject],
    epg_ids: set[int],
) -> list:
    """Zeilen einer VM (Applikations-Doku, 0.14.0): Firewall-Regeln ueber ihre IPs - direkt
    ueber das Host-Objekt einer IP, sonst ueber Netze, Bereiche, Gruppen und any - und die
    Contracts ihrer EPGs (Portgruppe, gelernter Endpunkt, verknuepfter Server)."""
    host_ids = {h.id for h in hosts}
    direct: set[int] = set()
    for hid in host_ids:
        direct |= data.rule_ids(RuleDomain.FIREWALL, network_object_id=hid)
    out = _covered_rows(
        session,
        data,
        ips,
        name,
        overlap=False,
        direct_ids=direct,
        is_direct=lambda e: e.network_object_id in host_ids,
    )
    ids = set(epg_ids)
    for h in hosts:
        ids |= {e.id for e in host_epgs(session, data, h)}
    if ips:
        ids |= set(session.scalars(select(EpgEndpoint.epg_id).where(EpgEndpoint.ip.in_(ips))))
    for i in sorted(ids):
        epg = data.epgs_by_id.get(i)
        if epg is not None and epg.kind == "epg":
            out += _aci_rows(data, epg, name, via=f"EPG {epg_store.inventory_label(epg)}")
    return out


def host_epgs(
    session: Session, data: TenantData, sys_obj: NetworkObject, vm_epgs: set[int] = frozenset()
) -> list[Epg]:
    ids = {e.id for e in data.epgs_by_id.values() if e.network_object_id == sys_obj.id}
    ids |= vm_epgs
    if sys_obj.value:
        ip = sys_obj.value.split("/")[0]
        ids |= set(session.scalars(select(EpgEndpoint.epg_id).where(EpgEndpoint.ip == ip)))
    return [
        data.epgs_by_id[i]
        for i in sorted(ids)
        if i in data.epgs_by_id and data.epgs_by_id[i].kind == "epg"
    ]


def assign_site(
    session: Session, system_name: str, tenant_id: int | None, site_id: int | None
) -> NetworkObject | None:
    """Verortet ein System (Host) an einem Standort. None, wenn System/Site ungueltig."""
    sys_obj = session.scalars(
        select(NetworkObject).where(
            NetworkObject.name == system_name,
            NetworkObject.object_class == ObjectClass.HOST,
            *([NetworkObject.tenant_id == tenant_id] if tenant_id is not None else []),
        )
    ).first()
    if sys_obj is None:
        return None
    if site_id is not None:
        site = session.get(Site, site_id)
        if site is None or site.tenant_id != sys_obj.tenant_id:
            raise ValueError("site not found for tenant")
    sys_obj.site_id = site_id
    session.flush()
    return sys_obj
