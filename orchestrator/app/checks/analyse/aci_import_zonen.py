"""Prüfung: ACI Import Route Control gegen die Zonen dahinter (0.116.0, ADR 0041 Stufe 3,
`store/bgp_zones.for_l3out`).

Je L3Out, dessen VRF und Peer im Zonenplan liegen: Netze der Zonen hinter dem L3Out gegen die
Subnetze mit Import Route Control (Import nicht erzwungen, Netz fehlt, Subnetz ausserhalb der
Zonen bzw. 0.0.0.0/0 aggregate, Ausschluss erlaubt). Teil-Durchgang „Zonen“ und im Abgleich nach
jedem Lauf."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.checks.analyse.bgp_filter import SHOWN
from app.models import FindingType, Severity, SubjectKind, TopoL3Out
from app.store.reconcile import Issue


def _run(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    from app.store import bgp_zones

    l3outs = list(session.scalars(select(TopoL3Out).where(TopoL3Out.tenant_id == tenant_id)
                                  .order_by(TopoL3Out.id)))  # fmt: skip
    if not l3outs:
        return []
    zs = bgp_zones.ZoneSoll(session, tenant_id)
    if zs.st is None:
        return []
    out: list[Issue] = []
    for o in l3outs:
        r = zs.for_l3out(o)
        if r.get("state") != "differs":
            continue
        detail = {"l3out": o.name, "vrf": o.vrf, "aci_tenant": o.aci_tenant,
                  "l3out_id": o.id, "zones": [z["name"] for z in r["zones"]],
                  "neighbor_zone": r["neighbor_zone"]["name"],
                  "not_enforced": not r["import_enforced"],
                  "missing": r["missing"][:SHOWN], "missing_total": r["missing_total"],
                  "too_open": [t["prefix"] for t in r["too_open"]][:SHOWN],
                  "excluded_allowed": r["excluded_allowed"][:SHOWN]}  # fmt: skip
        out.append(Issue(FindingType.ACI_IMPORT_ZONE_MISMATCH, SubjectKind.NODE,
                         f"{o.name} · {o.vrf or o.aci_tenant}", o.source_system_id,
                         Severity.INFO, detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "aci_import_zonen",
    "analyse",
    ("ACI Import Route Control gegen die Zonen", "ACI import route control against the zones"),
    (
        "Je L3Out: Subnetze mit Import Route Control gegen die Netze der Zonen dahinter "
        "(nicht erzwungen, fehlt, zu offen, Ausschluss erlaubt)",
        "Per L3Out: subnets with import route control against the networks of the zones behind "
        "(not enforced, missing, too open, exclusion permitted)",
    ),
    frozenset({FindingType.ACI_IMPORT_ZONE_MISMATCH}),
    _run,
    step="zonen",
    order=65,
)
