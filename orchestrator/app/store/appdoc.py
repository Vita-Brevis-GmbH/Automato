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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Epg, NatRule, NetworkObject, ObjectClass, RuleEndpoint, Site, Tenant
from app.store import rules as rules_store

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

_COMMON = ["Server Name", "TCP/UDP", "Target Systems", "Schutzmechanismen", "Description"]
TABLE_COLUMNS: dict[int, list[str]] = {
    1: [
        "Server Name",
        "TCP/UDP",
        "Target Systems",
        "Schutzmechanismen",
        "NAT Externe IPv4",
        "Description",
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

    # EPGs (ACI) als Systeme
    estmt = select(Epg)
    if tenant_id is not None:
        estmt = estmt.where(Epg.tenant_id == tenant_id)
    for epg in session.scalars(estmt):
        rids = {
            e.rule_id
            for e in session.scalars(select(RuleEndpoint).where(RuleEndpoint.epg_id == epg.id))
        }
        systems.append(SystemRef(name=epg.name, ip=None, rule_count=len(rids), kind="epg"))

    return sorted(systems, key=lambda s: (s.kind, s.name))


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
) -> dict:
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
        "site": {"id": site.id, "code": site.code, "name": site.name} if site else None,
        "tables": tables,
    }


def _epg_doc(session: Session, epg: Epg) -> dict:
    """ACI-EPG als System: Contracts nach scope/AP in die Tabellen 7/8/9 (+10) einordnen."""
    ap_by_name = {
        e.name: e.ap for e in session.scalars(select(Epg).where(Epg.tenant_id == epg.tenant_id))
    }
    rows_by_table: dict[int, list[dict]] = {i: [] for i, _, _ in TABLES}
    for r in rules_store.list_rules(session, epg.tenant_id):
        eps = [e for e in r.endpoints if e.kind.value == "epg"]
        on_src = any(
            (e.epg_id == epg.id or e.inline_value == epg.name) and e.role.value == "src"
            for e in eps
        )
        on_dst = any(
            (e.epg_id == epg.id or e.inline_value == epg.name) and e.role.value == "dst"
            for e in eps
        )
        if not (on_src or on_dst):
            continue
        cp_role = "src" if on_dst else "dst"
        cp = [e for e in eps if e.role.value == cp_role]
        cp_names = [e.inline_value for e in cp if e.inline_value]
        same_ap = any(ap_by_name.get(n) == epg.ap and epg.ap is not None for n in cp_names)
        idx = categorize_aci(r.aci_scope, same_ap, same_tenant=True)
        rows_by_table[idx].append(
            {
                "Server Name": epg.name,
                "TCP/UDP": ", ".join(s.canonical_name for s in r.services),
                "Target Systems": ", ".join(cp_names),
                "Schutzmechanismen": ", ".join(r.protections) if r.protections else "",
                "Description": r.name or f"#{r.external_id}",
            }
        )
    return _envelope(session, epg.name, epg.tenant_id, None, None, rows_by_table)


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
        return _host_doc(session, sys_obj)
    epg = session.scalars(
        select(Epg).where(
            Epg.name == system_name,
            *([Epg.tenant_id == tenant_id] if tenant_id is not None else []),
        )
    ).first()
    if epg is not None:
        return _epg_doc(session, epg)
    return None


def _host_doc(session: Session, sys_obj: NetworkObject) -> dict:
    """Firewall-zentrierte Doku eines Host-Systems (Tabellen 1–6, 10)."""
    system_name = sys_obj.name
    tenant_id = sys_obj.tenant_id
    sys_ip = sys_obj.value

    # NAT-Kontext: DNAT-Ziel (mapped_ip == System-IP) und SNAT-Pools des Mandanten.
    nat_stmt = select(NatRule)
    if tenant_id is not None:
        nat_stmt = nat_stmt.where(NatRule.tenant_id == tenant_id)
    nats = list(session.scalars(nat_stmt))
    dnat_for_sys = [
        n for n in nats if n.nat_type == "dnat_vip" and sys_ip and n.mapped_ip == sys_ip
    ]
    snat_ips = sorted({n.ext_ip for n in nats if n.nat_type == "snat_pool" and n.ext_ip})

    rules = rules_store.list_rules(session, tenant_id)
    obj_ids = {e.network_object_id for r in rules for e in r.endpoints if e.network_object_id}
    objs = rules_store.objects_by_ids(session, obj_ids)
    zone_ids = {z for r in rules for z in (r.src_zone_id, r.dst_zone_id) if z}
    zones = rules_store.zone_names(session, zone_ids)

    rows_by_table: dict[int, list[dict]] = {i: [] for i, _, _ in TABLES}
    for r in rules:
        src_eps = [e for e in r.endpoints if e.role.value == "src"]
        dst_eps = [e for e in r.endpoints if e.role.value == "dst"]
        on_src = any(e.network_object_id == sys_obj.id for e in src_eps)
        on_dst = any(e.network_object_id == sys_obj.id for e in dst_eps)
        if not (on_src or on_dst):
            continue
        direction = "in" if on_dst else "out"
        counterpart_eps = src_eps if direction == "in" else dst_eps
        cp_objs = [
            objs.get(e.network_object_id) if e.network_object_id else None for e in counterpart_eps
        ]
        cp_labels = [
            _label(o, e.inline_value) for o, e in zip(cp_objs, counterpart_eps, strict=True)
        ]
        cp_external = any(o is not None and o.object_class == ObjectClass.EXTERNAL for o in cp_objs)
        cp_zone = zones.get(r.src_zone_id) if direction == "in" else zones.get(r.dst_zone_id)
        is_dnat = direction == "in" and bool(dnat_for_sys)

        idx = categorize(
            direction=direction,
            counterpart_zone=cp_zone,
            counterpart_external=cp_external,
            is_dnat_target=is_dnat,
        )
        services = ", ".join(s.canonical_name for s in r.services)
        protections = ", ".join(r.protections) if r.protections else ""
        description = r.name or f"#{r.external_id}"
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
        rows_by_table[idx].append(row)

    return _envelope(session, system_name, tenant_id, sys_ip, sys_obj.site_id, rows_by_table)


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
