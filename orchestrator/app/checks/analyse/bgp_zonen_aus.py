"""Prüfung: BGP-Ausgangsfilter gegen die Zonen diesseits (0.116.0, ADR 0041 Stufe 3,
`store/bgp_zones`).

Je BGP-Nachbar einer FortiGate mit gelesener Konfiguration: was die Firewall dem Nachbarn
ankündigen soll (Netze der Zonen, die er über sie erreicht; an einen Nachbarn in einer Zone der
Art `external` nur öffentliche) gegen den Ausgangsfilter und gegen das, was sie heute ankündigt.
Teil-Durchgang „Zonen“ und im Abgleich nach jedem Lauf."""

from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.checks.analyse.bgp_filter import SHOWN, _label
from app.models import FindingType, Severity, SubjectKind
from app.store.reconcile import Issue


def _run(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    from app.store import bgp_filters, bgp_zones

    f = bgp_filters.Filters(session, tenant_id)
    peers = f.peers()
    if not peers:
        return []
    zs = bgp_zones.ZoneSoll(session, tenant_id, f)
    if zs.st is None:
        return []
    out: list[Issue] = []
    for p in peers:
        r = zs.for_peer(p, "out")
        if r.get("state") != "differs":
            continue
        label = _label(cx, session, tenant_id, p.source_system_id, p.node, p.context)
        lists = r["lists"]
        detail = {"label": label, "peer": p.peer_ip, "source_system_id": p.source_system_id,
                  "context": p.context, "zones": [z["name"] for z in r["zones"]],
                  "neighbor_zone": r["neighbor_zone"]["name"], "public_only": r["public_only"],
                  "open": any(x["open"] for x in lists),
                  "missing": [n for x in lists for n in x["missing"]][:SHOWN],
                  "too_open": [t["prefix"] for x in lists for t in x["too_open"]][:SHOWN],
                  "excluded_allowed": [n for x in lists
                                       for n in x["excluded_allowed"]][:SHOWN],
                  "announced_outside": [n for x in lists
                                        for n in x.get("announced_outside") or []][:SHOWN],
                  "announced_outside_total": sum(x.get("announced_outside_total") or 0
                                                 for x in lists)}  # fmt: skip
        out.append(Issue(FindingType.BGP_ZONE_OUT_MISMATCH, SubjectKind.NODE,
                         f"{label} → {p.peer_ip}", p.source_system_id, Severity.INFO,
                         detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "bgp_zonen_aus",
    "analyse",
    ("BGP-Ausgangsfilter gegen die Zonen", "BGP outbound filters against the zones"),
    (
        "Je BGP-Nachbar: Ausgangsfilter und heutige Ankündigung gegen die Netze der Zonen, die "
        "der Nachbar über die Firewall erreicht (gesperrt, ausserhalb angekündigt, zu offen)",
        "Per BGP neighbor: outbound filter and today's announcements against the networks of the "
        "zones the neighbor reaches via the firewall (denied, announced outside, too open)",
    ),
    frozenset({FindingType.BGP_ZONE_OUT_MISMATCH}),
    _run,
    step="zonen",
    order=64,
)
