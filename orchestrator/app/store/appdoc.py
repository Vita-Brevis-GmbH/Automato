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

from app.models import NatRule, NetworkObject, ObjectClass, RuleEndpoint
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


def list_systems(session: Session, tenant_id: int | None) -> list[SystemRef]:
    """Host-Systeme, die in mindestens einer Regel als Endpunkt vorkommen."""
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
    return [SystemRef(name=n, ip=v, rule_count=len(rids)) for n, (v, rids) in sorted(agg.items())]


def _label(obj: NetworkObject | None, inline: str | None) -> str:
    if obj is not None:
        return obj.value or obj.fqdn or obj.name
    return inline or "any"


def build_doc(session: Session, system_name: str, tenant_id: int | None) -> dict | None:
    """Baut die 10 Tabellen für ein System. None, wenn das System unbekannt ist."""
    sys_obj = session.scalars(
        select(NetworkObject).where(
            NetworkObject.name == system_name,
            NetworkObject.object_class == ObjectClass.HOST,
            *([NetworkObject.tenant_id == tenant_id] if tenant_id is not None else []),
        )
    ).first()
    if sys_obj is None:
        return None
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
    return {"system": system_name, "system_ip": sys_ip, "tables": tables}
