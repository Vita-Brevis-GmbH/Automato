"""Ausgaenge der Zonen gegen das Routing pruefen (0.105.0, ADR 0035).

Anwender 2026-10-28: „Ich weise in den Zonen die möglichen Ausgänge an, wohin die L3Outs oder
die Transfer-Netzwerke hinführen dürfen“ und „Findings, wenn ich die Netzwerke nicht von dort
lerne, wo ich sie erwarte gemäss Netzwerk-Zone“.

Eine Zone mit Netzen (Selektoren „Netz“) und Ausgaengen ueber Firewall-Interfaces bzw.
Firewall-Zonen (`via_fw_interface`, `via_fw_zone`) erwartet: die Firewall erreicht ihre Netze
ueber diese Interfaces. Je Firewall und Kontext (VDOM), in dem ein Ausgang vorkommt, zaehlt die
wirksame Route mit dem laengsten Praefix, das das Netz ganz enthaelt (RIB, Standard-VRF):

- Next-Hop ueber einen Ausgang der Zone: in Ordnung;
- sonst Befund `zone_net_exit` - mit dem Interface, ueber das die Firewall das Netz lernt, und
  den Zonen, deren Ausgang es ist; nur die Default-Route bzw. gar keine Route: ebenso.

ACI-L3Outs (`via_l3out`) ordnen in der Doku zu, geprueft werden hier nur Firewalls. Rein lesend.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InvInterface, LogicalZone, SourceSystem, TopoRoute
from app.store import topology
from app.store import zones as zone_store

FW_EXITS = ("via_fw_interface", "via_fw_zone")


def _net(value: str):
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return None


def checks(session: Session, tenant_id: int) -> list[dict]:
    """Befunde je Zone, Netz und Firewall (siehe oben)."""
    zones = [(z, [s for s in z.selectors if s.kind in FW_EXITS and not s.exclude])
             for z in zone_store.load(session, tenant_id)]  # fmt: skip
    zones = [(z, ex) for z, ex in zones if ex]
    if not zones:
        return []
    names = dict(session.execute(
        select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
    ).all())  # fmt: skip
    members: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for sid, name, zone in session.execute(
        select(InvInterface.source_system_id, InvInterface.name, InvInterface.zone).where(
            InvInterface.tenant_id == tenant_id
        )
    ):
        members[sid][name.casefold()].add(name)
        if zone:
            members[sid][zone.casefold()].add(name)
    wanted = {s.source_system_id for _z, ex in zones for s in ex}
    sids = set(members) if None in wanted else {s for s in wanted if s is not None}
    routes: dict[tuple[int, str], list[tuple]] = defaultdict(list)
    for r in session.scalars(
        select(TopoRoute).where(
            TopoRoute.source_system_id.in_(sids), TopoRoute.table == "rib",
            TopoRoute.best.is_(True),
        )
    ):  # fmt: skip
        net = _net(f"{r.prefix}/{r.prefix_len}") if "/" not in r.prefix else _net(r.prefix)
        if net is None or topology.table_of(r) != 0:
            continue
        hops = {h.get("interface") for h in r.next_hops or [] if h.get("interface")}
        routes[(r.source_system_id, r.context)].append((net, hops))

    def expected(sid: int, exits) -> set[str]:
        out: set[str] = set()
        for s in exits:
            if s.source_system_id in (None, sid):
                out |= members.get(sid, {}).get(s.value.strip().casefold(), {s.value.strip()})
        return out

    issues = []
    for (sid, context), table in sorted(routes.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        used = set().union(*(hops for _n, hops in table)) if table else set()
        for z, exits in zones:
            mine = expected(sid, exits)
            if not mine or not (mine & used):
                continue  # kein Ausgang der Zone auf dieser Firewall bzw. in diesem Kontext
            for s in z.selectors:
                if s.kind != "prefix" or s.exclude or (net := _net(s.value)) is None:
                    continue
                best = max((r for r in table if r[0].version == net.version
                            and net.subnet_of(r[0])), key=lambda r: r[0].prefixlen,
                           default=None)  # fmt: skip
                if best is not None and best[1] & mine:
                    continue
                via = sorted(best[1]) if best else []
                others = sorted({o.name for o, ex in zones if o.id != z.id
                                 and expected(sid, ex) & set(via)})  # fmt: skip
                issues.append({
                    "type": "zone_net_exit", "subject": f"{z.name} · {net}",
                    "source_system_id": sid, "severity": "warning",
                    "zone_id": z.id, "zone": z.name, "net": str(net),
                    "source": names.get(sid), "context": context, "via": via,
                    "expected": sorted(mine), "others": others,
                    "default": best is not None and best[0].prefixlen == 0, "missing": best is None,
                })  # fmt: skip
    return issues


def selectors_of(z: LogicalZone) -> list[str]:
    """Ausgaenge einer Zone zur Anzeige („fw-01: port3“, „L3Out L3O_inet“)."""
    return [s.value for s in z.selectors if s.kind in (*FW_EXITS, "via_l3out") and not s.exclude]
