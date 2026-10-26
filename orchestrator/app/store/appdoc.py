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
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    RuleIndex,
    Site,
    SourceSystem,
    Tenant,
)
from app.models.rule import EndpointKind
from app.store import (
    aci_direction,
    aci_relevance,
    aci_shared,
    references,
    rule_index,
    rule_view,
    sites,
    virtual,
)
from app.store import epgs as epg_store
from app.store import nat as nat_store
from app.store import relevance as relevance_store
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
# Gruppe (Objektname), any, vzAny der VRF, ESG oder die EPG des Servers (Dev-Befund 0.10.0).
# Steht in jeder Zeile (Treffer, Erklaerung), seit 0.34.0 aber nicht mehr als Spalte der
# Kategorien - dort sagte er den Anwendern nichts.
VIA = "Bezug"
# Spalten seit 0.34.0 (Dev-Befund 0.33.0): Aktion der Regel und das Geraet, auf dem sie steht
ACTION, DEVICE = "Aktion", "Gerät"
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
# EPG (`Tenant/AP/EPG`), ueber die ein ACI-Contract fuer das System gilt - fuer den Filter nach
# EPG in der Applikations-Doku (0.42.3); leer bei Firewall-Regeln und externen EPGs
EPG = "_epg"
# ACI: DNs der Beteiligten eines Treffers - eigener Endpunkt bzw. Gegenstellen im Scope (0.43.2)
PARTIES = "_parties"
# ACI ist zustandslos (0.69.1): Zeile der Rueckrichtung (Provider -> Consumer) mit den Diensten,
# die der Provider aufbauen kann (`aci_direction`); die Seite (`SIDE`) bleibt die des Contracts
REVERSE = "_reverse"
UNSPECIFIC = ("subnet", "any", "vzany", "pref_group")
_COMMON = [
    "Server Name",
    "TCP/UDP",
    "Target Systems",
    ACTION,
    "Schutzmechanismen",
    DEVICE,
    "Description",
]
TABLE_COLUMNS: dict[int, list[str]] = {
    1: [
        "Server Name",
        "TCP/UDP",
        "Target Systems",
        ACTION,
        "Schutzmechanismen",
        "NAT Externe IPv4",
        DEVICE,
        "Description",
    ],
    2: [
        "Source",
        "TCP/UDP",
        "Server Name",
        "VIP IP Adresse",
        "Externer DNS Name",
        "Externe IP Adressen",
        ACTION,
        "Schutzmechanismen",
        DEVICE,
        "Description",
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
        if is_dnat_target or kind == "inet" or counterpart_external:
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


def list_systems(
    session: Session, tenant_id: int | None, view=None, rule_ids: set[int] | None = None
) -> list[SystemRef]:
    """Systeme mit Regelbezug: Host-Objekte (Firewall) und EPGs (ACI); mit `view` nur die
    eines Kunden (ADR 0018), mit `rule_ids` zaehlen nur diese Regeln (Kunden-Benutzer)."""
    stmt = (
        select(NetworkObject.name, NetworkObject.value, RuleEndpoint.rule_id)
        .join(RuleEndpoint, RuleEndpoint.network_object_id == NetworkObject.id)
        .where(NetworkObject.object_class == ObjectClass.HOST)
    )
    if tenant_id is not None:
        stmt = stmt.where(NetworkObject.tenant_id == tenant_id)
    if view is not None:
        stmt = stmt.where(NetworkObject.id.in_(view.ids("object")))
    agg: dict[str, tuple[str | None, set[int]]] = {}
    for name, value, rid in session.execute(stmt):
        ip, rids = agg.setdefault(name, (value, set()))
        if rule_ids is None or rid in rule_ids:
            rids.add(rid)
    systems = [
        SystemRef(name=n, ip=v, rule_count=len(rids), kind="host") for n, (v, rids) in agg.items()
    ]

    # EPGs (ACI) als Systeme - externe EPGs und vzAny sind keine Systeme
    estmt = select(Epg).where(Epg.kind == "epg")
    if tenant_id is not None:
        estmt = estmt.where(Epg.tenant_id == tenant_id)
    if view is not None:
        estmt = estmt.where(Epg.id.in_(view.ids("epg")))
    epgs = list(session.scalars(estmt))
    names = [(e.tenant_id, e.name) for e in epgs]
    rules_of: dict[int, set[int]] = {}
    if epgs:
        for epg_id, rule_id in session.execute(
            select(RuleEndpoint.epg_id, RuleEndpoint.rule_id).where(
                RuleEndpoint.epg_id.in_([e.id for e in epgs])
            )
        ):
            if rule_ids is None or rule_id in rule_ids:
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
        for rows in rows_by_table.values():
            for row in rows:
                row.pop(PARTIES, None)  # nur fuer die Treffer der Applikations-Doku
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
        self._nat: dict[int, dict] = {}

    def rules(self, ids: set[int]) -> list[Rule]:
        """Regeln mit Endpunkten/Services in Firewall-Reihenfolge (fehlende nachgeladen)."""
        todo = [i for i in ids if i not in self._rules]
        if todo:
            # NAT je Regel aus dem Regelindex: SNAT (Pool bzw. Interface-IP) und VIPs im Ziel
            for rid, row in self.session.execute(
                select(RuleIndex.rule_id, RuleIndex.row).where(RuleIndex.rule_id.in_(todo))
            ):
                self._nat[rid] = (row or {}).get("nat") or {}
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
    def nat_by_name(self) -> dict[tuple[int, str | None, str], NatRule]:
        """VIPs und SNAT-Pools je (Firewall, VDOM, Name) - wie im Regelindex."""
        return {(n.source_system_id, n.vdom, n.name.lower()): n for n in self.nats}

    def rule_nat(self, rule: Rule) -> tuple[list[NatRule], str]:
        """NAT einer erlaubenden Firewall-Policy (Dev-Befund 0.33.0): ihre VIPs (DNAT) und ihr
        SNAT - die Pool-Adressen bzw. „Interface-IP“. Sperrende Regeln haben kein NAT."""
        if rule.domain is not RuleDomain.FIREWALL or rule.action is not RuleAction.ALLOW:
            return [], ""
        info = self._nat.get(rule.id) or {}
        key = (rule.source_system_id, rule.vdom)
        vips = [self.nat_by_name.get((*key, str(v).lower())) for v in info.get("vips") or []]
        snat = ""
        if info.get("snat"):
            pools = info.get("pools") or []
            found = [self.nat_by_name.get((*key, str(p).lower())) for p in pools]
            snat = (
                ", ".join(
                    (n.ext_ip if n is not None and n.ext_ip else p)
                    for n, p in zip(found, pools, strict=True)
                )
                if pools
                else "Interface-IP"
            )
        return [v for v in vips if v is not None], snat

    @cached_property
    def device_names(self) -> dict[int, str]:
        stmt = select(SourceSystem.id, SourceSystem.name).where(
            SourceSystem.tenant_id == self.tenant_id
        )
        return dict(self.session.execute(stmt).all())

    @cached_property
    def vdom_counts(self) -> dict[int, int]:
        """Anzahl VDOMs je Firewall (nur root = die Firewall selbst, ohne VDOM-Angabe)."""
        out: dict[int, set[str]] = {}
        for sid, vdom in self.session.execute(
            select(Rule.source_system_id, Rule.vdom)
            .where(Rule.tenant_id == self.tenant_id, Rule.vdom.is_not(None))
            .distinct()
        ):
            out.setdefault(sid, set()).add(vdom)
        return {sid: len(v) for sid, v in out.items()}

    def device(self, rule: Rule) -> str:
        """Geraet der Regel: Firewall (mit VDOM, wenn es mehrere gibt) bzw. APIC."""
        sid = rule.source_system_id
        if sid not in self.device_names:  # Altbestand ohne Mandant am Quellsystem
            system = self.session.get(SourceSystem, sid)
            self.device_names[sid] = system.name if system else f"#{sid}"
        name = self.device_names[sid]
        if rule.vdom and self.vdom_counts.get(rule.source_system_id, 0) > 1:
            return f"{name} · VDOM {rule.vdom}"
        return name

    @cached_property
    def dns(self):
        return references.context(self.session, self.tenant_id)

    @cached_property
    def epgs_by_id(self) -> dict[int, Epg]:
        stmt = select(Epg).where(Epg.tenant_id == self.tenant_id)
        return {e.id: e for e in self.session.scalars(stmt)}

    @cached_property
    def relevance(self) -> relevance_store.Relevance:
        return relevance_store.Relevance(self.session, self.tenant_id)

    @cached_property
    def aci(self) -> aci_relevance.Classifier:
        """Zuordnung zu externen EPGs (0.28.0); teilt den Graph der Wege."""
        return aci_relevance.Classifier(self.session, self.tenant_id, lambda: self.relevance.graph)

    @cached_property
    def leaks(self) -> aci_shared.Leaks:
        """Wege zwischen VRFs im Fabric (Shared Services, Route-Leaking; 0.44.0)."""
        return aci_shared.Leaks(self.session, self.tenant_id, self.aci)


def _proxies(data: TenantData, epg: Epg) -> dict[int, str]:
    """EPG-IDs, deren Contracts fuer die EPG gelten, mit Bezug: die EPG selbst, vzAny ihrer
    VRF (alle EPGs der VRF) und ESGs, die sie per EPG-Selektor einschliessen."""
    out = {epg.id: "direkt"}
    own = vrf_key(data, epg)
    for other in data.epgs_by_id.values():
        # vzAny genau der VRF der EPG (0.44.0: aufgeloeste VRF statt Name und Tenant/common)
        if other.kind == "vzany" and own[1] and vrf_key(data, other) == own:
            out[other.id] = f"vzAny {other.vrf}"
        elif other.kind == "esg" and epg.dn and f"EPG {epg.dn}" in (other.selectors or []):
            out[other.id] = f"ESG {other.name}"
    return out


def action_label(rule: Rule) -> str:
    return "Allow" if rule.action is RuleAction.ALLOW else "Deny"


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
        own: dict[str, list[str]] = {}  # Seite -> DNs der eigenen Endpunkte (EPG, vzAny, ESG)
        for e in eps:
            v = self_via(e)
            if v and (e.role.value not in sides or v == "direkt"):
                sides[e.role.value] = v
            if v and e.epg_id in by_id and by_id[e.epg_id].dn:
                own.setdefault(e.role.value, []).append(by_id[e.epg_id].dn)
        for side in sorted(sides, key=lambda x: x != "dst"):  # zuerst eingehend
            cp_role = "src" if side == "dst" else "dst"
            cp = [e for e in eps if e.role.value == cp_role]
            # nur Gegenstellen im Scope des Contracts (Dev-Befund 0.43.0: Contract aus common
            # mit Scope VRF - die Consumer anderer Tenants bzw. VRFs erreicht er nicht)
            kept = [e for e in cp if e.epg_id not in by_id
                    or scope_allows(data, r.aci_scope, epg, by_id[e.epg_id])]  # fmt: skip
            if cp and not kept:
                continue
            # zwischen VRFs nur mit Leak (Shared Service, Route-Leaking; 0.44.0) - sonst
            # oeffnet der Contract im Fabric keinen Weg (wie die Weg-Suche)
            rel, rel_text, blocked = None, "", []
            for e in kept:
                other = by_id.get(e.epg_id)
                if (
                    other is None
                    or other.kind not in ("epg", "esg")
                    or epg.kind not in ("epg", "esg")
                ):
                    continue
                state, why = data.leaks.between(epg, other)
                if state == aci_shared.IMPOSSIBLE:
                    blocked.append(e)
                    rel_text = why
                elif state == aci_shared.UNKNOWN:
                    rel, rel_text = aci_shared.UNKNOWN, why
            if blocked and len(blocked) < len(kept):
                kept = [e for e in kept if e not in blocked]  # die erreichbaren bleiben
            elif blocked:
                rel = aci_shared.IMPOSSIBLE
            cp = kept
            cp_epgs = [by_id[e.epg_id] for e in cp if e.epg_id in by_id]
            cp_names = [_target(by_id.get(e.epg_id), e.inline_value, epg) for e in cp]
            same_ap = any(
                c.ap == epg.ap and c.aci_tenant == epg.aci_tenant and epg.ap is not None
                for c in cp_epgs
            )
            same_tenant = all(c.aci_tenant == epg.aci_tenant for c in cp_epgs)
            external = [c for c in cp_epgs if c.kind == "external"]
            only_external = bool(external) and len(external) == len(cp_epgs)
            how = sides[side]
            text = how if via is None else (via if how == "direkt" else f"{how} · {via}")
            obj = f"EPG {epg_store.inventory_label(epg)}" if how == "direkt" else how
            targets = ", ".join(n for n in cp_names if n)
            forward = "in" if side == "dst" else "out"
            # ACI ist zustandslos (0.69.1): Filter ohne Ports oeffnen auch den Weg vom Provider
            # zum Consumer - eine eigene Zeile in der Gegenrichtung (z. B. aus dem Internet)
            back = aci_direction.reverse(r)
            for direction in (forward, "out" if forward == "in" else "in")[: 2 if back else 1]:
                reverse = direction != forward
                if only_external:
                    idx = categorize(
                        direction=direction,
                        counterpart_zone=external[0].l3out,
                        counterpart_external=False,
                        is_dnat_target=False,
                    )
                else:
                    idx = categorize_aci(r.aci_scope, same_ap, same_tenant=same_tenant)
                services = ", ".join(back if reverse else rule_view.service_labels(r))
                out.append(
                    (
                        idx,
                        {
                            "Server Name": system_name,
                            "TCP/UDP": services + (" (Rückrichtung)" if reverse else ""),
                            "Target Systems": targets,
                            **({"Source": targets} if idx == 2 else {}),
                            ACTION: action_label(r),
                            "Schutzmechanismen": ", ".join(r.protections) if r.protections else "",
                            DEVICE: data.device(r),
                            "Description": r.name or f"#{r.external_id}",
                            VIA: f"{text} · Rückrichtung" if reverse else text,
                            RULE: r.id,
                            DIRECTION: direction,
                            SCOPE: _aci_scope(r, how),
                            SIDE: side,
                            OBJECT: obj,
                            OBJECT_VALUE: "",
                            EPG: epg_store.inventory_label(epg) if epg.kind == "epg" else None,
                            # Beteiligte (0.43.2): eigene Endpunkte und Gegenstellen im Scope (DN)
                            # - die Applikations-Doku zeigt nur sie, nicht alle des Contracts
                            PARTIES: {
                                "own": own.get(side, []),
                                "counter": [c.dn for c in cp_epgs if c.dn],
                            },
                            **({REVERSE: back} if reverse else {}),
                            **({REL: rel, REL_TEXT: rel_text} if rel else {}),
                        },
                    )
                )
    return out


def vrf_key(data: TenantData, epg: Epg) -> tuple[int | None, str | None]:
    """(APIC, `Tenant:VRF`) einer EPG."""
    return epg.source_system_id, data.aci.vrf_of(epg)


def scope_allows(data: TenantData, scope: str | None, own: Epg, other: Epg) -> bool:
    """Gegenstelle im Scope des Contracts (`aci_relevance.Classifier.scope_allows`)."""
    return data.aci.scope_allows(scope, own, other)


def fabric_epgs(
    data: TenantData, known: set[int], by_ip: set[int], prefer: frozenset | set = frozenset()
) -> set[int]:
    """EPGs eines Systems im ACI (Dev-Befund 0.42.2): die bekannten (Portgruppe der VM) und
    die nur ueber die IP gefundenen (gelernter Endpunkt, damit verknuepfter Server) - diese nur
    in den VRFs der bekannten. Gleiche Adressen gibt es in mehreren ACI-Tenants bzw. VRFs; ein
    Endpunkt mit der IP einer VM in einer fremden VRF ist ein anderes System. Ohne bekannte EPG
    gelten die VRFs in `prefer` (die der Applikation), sofern eine passt, sonst alle."""
    epgs = data.epgs_by_id
    known = {i for i in known if i in epgs and epgs[i].kind == "epg"}
    found = {i for i in by_ip if i in epgs and epgs[i].kind == "epg"} - known
    allowed = {vrf_key(data, epgs[i]) for i in known}
    if not allowed and prefer:
        allowed = {vrf_key(data, epgs[i]) for i in found} & set(prefer)
    if not allowed:
        return known | found
    return known | {i for i in found if vrf_key(data, epgs[i]) in allowed}


def _ext_rows(data: TenantData, ips: list[str], system_name: str, vrfs: set | None = None) -> list:
    """ACI-Contracts eines Systems ausserhalb des Fabrics (0.28.0): ueber die externen EPGs,
    deren Subnetze seine IP erfassen - mit der Relevanz nach der Zuordnung im ACI (zuordnender
    Scope, laengstes Subnetz der VRF, nicht im Fabric, bei Gleichstand das Routing).

    Steht das System in EPGs (`vrfs` = deren VRFs), zaehlen nur externe EPGs dieser VRFs
    (Dev-Befund 0.42.2): 0.0.0.0/0 einer fremden VRF erfasst jede Adresse, ihre Contracts
    gehoeren zu den Systemen dort, nicht zur Applikation."""
    best: dict[int, tuple[Epg, object, tuple[str, str]]] = {}
    for value in dict.fromkeys(ips):
        ip = relevance_store._ip(value)
        if ip is None:
            continue
        for epg in data.aci.externals(ip):
            if vrfs and vrf_key(data, epg) not in vrfs:
                continue
            if data.aci.inside(epg, ip):
                continue  # im Fabric: die Contracts seiner EPG gelten, nicht die externen
            rel = data.aci.check(epg, ip)
            prev = best.get(epg.id)
            if rel is not None and (
                prev is None or aci_relevance.RANK[rel[0]] < aci_relevance.RANK[prev[2][0]]
            ):
                best[epg.id] = (epg, data.aci.subnet_for(epg, ip), rel)
    out = []
    for epg, net, (state, text) in best.values():
        length = net.prefixlen
        scope = "any" if length == 0 else "specific" if length == net.max_prefixlen else "subnet"
        label = aci_relevance.ext_label(epg)
        for idx, row in _aci_rows(data, epg, system_name, via=f"externe EPG {label} ({net})"):
            if row[SCOPE] == "specific":
                row[SCOPE] = scope
            if row[OBJECT].startswith("EPG "):
                row[OBJECT] = f"externe EPG {label}"
            row |= {OBJECT_VALUE: str(net), REL: state, REL_TEXT: text}
            out.append((idx, row))
    return out


def _fw_rows(
    data: TenantData,
    rule: Rule,
    sides: dict[str, tuple[str, str, str, str]],
    system_name: str,
    dnat_for_sys: list[NatRule],
    rels: dict[str, dict] | None = None,
) -> list:
    """Firewall-Regel als (Tabelle, Zeile) je Seite, auf der das System steht (Ziel =
    eingehend, Quelle = ausgehend); `sides` = Seite -> (Bezug, Art des Bezugs, Objekt der
    Regel, sein Bereich); `rels` = Seite -> Relevanz (Routing).

    NAT nur aus der Regel selbst (Dev-Befund 0.33.0): ihre VIPs, sofern sie auf das System
    zeigen, und ihr SNAT - nicht alle VIPs bzw. Pools des Mandanten, und nie bei sperrenden
    Regeln."""
    objs, zones = data.objs, data.zones
    vips, snat = data.rule_nat(rule)
    mine = {n.id for n in dnat_for_sys}
    rule_dnat = [n for n in vips if n.id in mine]
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
        # Gegenstelle any (0.25.0): das, was hinter Interface bzw. Zone der Regel liegt
        cp_any = [e.kind is EndpointKind.ANY for e in counterpart_eps]
        behind = (
            data.relevance.behind(rule, "src" if direction == "in" else "dst", cp_zone)
            if any(cp_any)
            else None
        )
        if behind is not None:
            cp_labels = [
                f"{label} → {behind['text']}" if is_any else label
                for label, is_any in zip(cp_labels, cp_any, strict=True)
            ]
            cp_external = cp_external or behind["kind"] == "internet"
        idx = categorize(
            direction=direction,
            counterpart_zone=cp_zone,
            counterpart_external=cp_external,
            is_dnat_target=direction == "in" and bool(rule_dnat),
        )
        services = ", ".join(rule_view.service_labels(rule))
        protections = ", ".join(rule.protections) if rule.protections else ""
        description = rule.name or f"#{rule.external_id}"
        targets = ", ".join(cp_labels)
        common = {ACTION: action_label(rule), DEVICE: data.device(rule), "Description": description}
        if idx == 1:
            row = {
                "Server Name": system_name,
                "TCP/UDP": services,
                "Target Systems": targets,
                "Schutzmechanismen": protections,
                "NAT Externe IPv4": snat if direction == "out" else "",
                **common,
            }
        elif idx == 2:
            vip = rule_dnat[0] if direction == "in" and rule_dnat else None
            ext = (vip.ext_ip if vip else "") or ""
            names = references.dns_names(data.dns, ext) if ext else []
            dns = names[0] if names else ""
            if not dns and vip and vip.name:  # V-EXT_www.example.ch -> www.example.ch
                dns = vip.name.split("_", 1)[1] if "_" in vip.name else ""
            row = {
                "Source": targets,
                "TCP/UDP": services,
                "Server Name": system_name,
                "VIP IP Adresse": ext,
                "Externer DNS Name": dns,
                "Externe IP Adressen": ", ".join(n.ext_ip for n in rule_dnat if n.ext_ip),
                "Schutzmechanismen": protections,
                **common,
            }
        else:
            row = {
                "Server Name": system_name,
                "TCP/UDP": services,
                "Target Systems": targets,
                "Schutzmechanismen": protections,
                **common,
            }
        via, scope, obj, value = sides[side]
        meta = {RULE: rule.id, DIRECTION: direction, SCOPE: scope, SIDE: side}
        rel = (rels or {}).get(side)
        if rel is not None:
            meta |= {REL: rel["state"], REL_TEXT: rel["text"]}
        out.append((idx, {**row, VIA: via, **meta, OBJECT: obj, OBJECT_VALUE: value}))
    return out


def _nat_context(data: TenantData, ips: set[str]) -> list[NatRule]:
    """VIPs mit einem der IPs als internem Ziel (auch Real Server)."""
    return [
        n for n in data.nats if n.nat_type == "dnat_vip" and ips & set(nat_store.internal_ips(n))
    ]


def _covered_rows(
    session: Session,
    data: TenantData,
    values: list[str],
    system_name: str,
    overlap: bool,
    direct_ids: set[int] | None = None,
    is_direct=None,
    contained: list[str] | None = None,
) -> list:
    """Firewall-Regeln eines Systems: direkt (Endpunkt) und alle, deren Quelle/Ziel den Wert
    enthaelt (Host-IP) bzw. sich mit ihm ueberschneidet (EPG-Subnetz) - ueber Netze, Bereiche,
    Gruppen und any, gleichwertig (Dev-Befund 0.10.0). `contained`: Werte, die eine Regel ganz
    enthalten muss (Subnetz eines BD, den die EPG mit anderen teilt, 0.43.1)."""
    cover = rule_index.covers(
        session, data.tenant_id, values, overlap=overlap, domain=RuleDomain.FIREWALL
    )
    if contained:
        more = rule_index.covers(session, data.tenant_id, contained, domain=RuleDomain.FIREWALL)
        for rid, c in more.items():
            for role, side in c.sides.items():
                cover.setdefault(rid, rule_index.Cover()).add(role, *side)
        values = [*values, *contained]
    dnat = _nat_context(data, {v.split("/")[0] for v in values})
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
        out += _fw_rows(data, r, sides, system_name, dnat, rels)
    return out


def _epg_values(session: Session, data: TenantData, epg: Epg) -> tuple[list[str], list[str]]:
    """Adressen einer EPG fuer ihre Firewall-Regeln (Dev-Befund 0.43.0): `own` - eigene
    Subnetze, Subnetze eines BD nur mit dieser EPG, IPs ihrer VMs (Portgruppe) und Endpunkte;
    eine Regel, die sich damit ueberschneidet, gilt. `shared` - Subnetze eines BD, den sie mit
    anderen EPGs teilt: eine Regel gilt nur, wenn sie das ganze Subnetz enthaelt; ein Host bzw.
    Teilnetz darin gehoert sonst womoeglich zu einer anderen EPG des BD."""
    bd = (epg.source_system_id, epg.aci_tenant, epg.bd)
    others = epg.bd is not None and any(
        e.id != epg.id and e.kind == "epg" and (e.source_system_id, e.aci_tenant, e.bd) == bd
        for e in data.epgs_by_id.values()
    )
    own: list[str] = []
    shared: list[str] = []
    for s in session.scalars(select(EpgSubnet).where(EpgSubnet.epg_id == epg.id)):
        (shared if s.origin == "bd" and others else own).append(s.ip)
    own += session.scalars(select(EpgEndpoint.ip).where(EpgEndpoint.epg_id == epg.id))
    for vm in epg_store.vms_of(session, [epg.id]).get(epg.id, []):
        own += vm["ips"]
    return list(dict.fromkeys(own)), shared


def epg_doc(session: Session, epg: Epg, data: TenantData | None = None, full: bool = False) -> dict:
    """ACI-EPG als System: Contracts (direkt, vzAny der VRF, ESGs) nach scope/AP in die
    Tabellen 7/8/9 (+ L3Out-Tabellen) und die Firewall-Regeln, die ihre Subnetze bzw.
    Endpunkte erfassen (auch ueber groessere Netze und any). Als Name der ganze ACI-Pfad."""
    data = data or TenantData(session, epg.tenant_id)
    name = epg_store.inventory_label(epg)
    rows_by_table: dict[int, list[dict]] = {i: [] for i, _, _ in TABLES}
    for idx, row in _aci_rows(data, epg, name):
        rows_by_table[idx].append(row)
    own, shared = _epg_values(session, data, epg)
    for idx, row in _covered_rows(session, data, own, name, overlap=True, contained=shared):
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
    session: Session,
    sys_obj: NetworkObject,
    data: TenantData | None = None,
    full: bool = False,
    prefer: frozenset | set = frozenset(),
) -> dict:
    """Doku eines Host-Systems: alle Firewall-Regeln, die den Server erfassen - direkt und ueber
    Netze, Bereiche, Gruppen und any -, dazu die ACI-Contracts der EPG(s) des Servers
    (`prefer` = VRFs der Applikation, siehe `fabric_epgs`)."""
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
    # ACI: die EPG(s), in denen der Server steht (Portgruppe der VM, gelernter Endpunkt,
    # verknuepft)
    epgs = host_epgs(session, data, sys_obj, vms.epg_ids(ip), prefer)
    for epg in epgs:
        for idx, row in _aci_rows(data, epg, system_name, via=f"EPG {epg_store.label(epg)}"):
            rows_by_table[idx].append(row)
    # ausserhalb des Fabrics: ueber die externe EPG, der das ACI die IP zuordnet (0.28.0)
    vrfs = {vrf_key(data, e) for e in epgs}
    for idx, row in _ext_rows(data, [ip] if ip else [], system_name, vrfs):
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
    prefer: frozenset | set = frozenset(),
    mapped: set[str] | frozenset = frozenset(),
) -> list:
    """Zeilen einer VM (Applikations-Doku, 0.14.0): Firewall-Regeln ueber ihre IPs - direkt
    ueber das Host-Objekt einer IP, sonst ueber Netze, Bereiche, Gruppen und any - und die
    Contracts ihrer EPGs (Portgruppe; gelernter Endpunkt bzw. verknuepfter Server nur in den
    VRFs der Portgruppen, siehe `fabric_epgs`).

    Vorgabe vor Gelerntem (0.43.1, R12): Fuer IPs an einer NIC mit EPG (`mapped`) gilt die EPG
    der Portgruppe; ein gelernter Endpunkt mit dieser IP in einer anderen EPG (alt, verschoben,
    gleiche Adresse) bringt keine Contracts."""
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
    free = [h for h in hosts if not h.value or h.value.split("/")[0] not in mapped]
    free_ids = {h.id for h in free}
    by_ip = {e.id for e in data.epgs_by_id.values() if e.network_object_id in free_ids}
    values = list(dict.fromkeys([*ips, *(h.value.split("/")[0] for h in free if h.value)]))
    values = [v for v in values if v not in mapped]
    if values:
        by_ip |= set(session.scalars(select(EpgEndpoint.epg_id).where(EpgEndpoint.ip.in_(values))))
    ids = fabric_epgs(data, set(epg_ids), by_ip, prefer)
    for i in sorted(ids):
        epg = data.epgs_by_id[i]
        out += _aci_rows(data, epg, name, via=f"EPG {epg_store.inventory_label(epg)}")
    vrfs = {vrf_key(data, data.epgs_by_id[i]) for i in ids}
    return out + _ext_rows(data, ips, name, vrfs)


def host_epgs(
    session: Session,
    data: TenantData,
    sys_obj: NetworkObject,
    vm_epgs: set[int] = frozenset(),
    prefer: frozenset | set = frozenset(),
) -> list[Epg]:
    """EPGs eines Servers: die Portgruppen seiner VM (`vm_epgs`, Vorgabe vor Gelerntem, 0.43.1),
    sonst ueber die IP (gelernter Endpunkt, verknuepft) - in den VRFs von `prefer`
    (`fabric_epgs`)."""
    if vm_epgs:
        return [data.epgs_by_id[i] for i in sorted(fabric_epgs(data, set(vm_epgs), set()))]
    by_ip = {e.id for e in data.epgs_by_id.values() if e.network_object_id == sys_obj.id}
    if sys_obj.value:
        ip = sys_obj.value.split("/")[0]
        by_ip |= set(session.scalars(select(EpgEndpoint.epg_id).where(EpgEndpoint.ip == ip)))
    ids = fabric_epgs(data, set(vm_epgs), by_ip, prefer)
    return [data.epgs_by_id[i] for i in sorted(ids)]


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
