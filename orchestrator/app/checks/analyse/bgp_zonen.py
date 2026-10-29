"""Prüfung: BGP-Eingangsfilter gegen die Zonen dahinter (0.114.0, ADR 0041 Stufe 2,
`store/bgp_zones`).

Je BGP-Nachbar einer FortiGate mit gelesener Konfiguration, deren Firewall und Nachbar im
Zonenplan liegen: Netze der Zonen hinter dem Nachbarn, die der Eingangsfilter sperrt, erlaubte
Ausschlüsse, Regeln ausserhalb der Zonen bzw. kein Eingangsfilter. Teil-Durchgang „Zonen“: neu
nach jedem Speichern einer Zone, sonst im Abgleich nach jedem Lauf."""

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
        r = zs.for_peer(p)
        if r.get("state") != "differs":
            continue
        label = _label(cx, session, tenant_id, p.source_system_id, p.node, p.context)
        lists = r["lists"]
        detail = {"label": label, "peer": p.peer_ip, "source_system_id": p.source_system_id,
                  "context": p.context, "zones": [z["name"] for z in r["zones"]],
                  "neighbor_zone": r["neighbor_zone"]["name"],
                  "open": any(x["open"] for x in lists),
                  "missing": [n for x in lists for n in x["missing"]][:SHOWN],
                  "too_open": [t["prefix"] for x in lists for t in x["too_open"]][:SHOWN],
                  "excluded_allowed": [n for x in lists
                                       for n in x["excluded_allowed"]][:SHOWN]}  # fmt: skip
        out.append(Issue(FindingType.BGP_ZONE_MISMATCH, SubjectKind.NODE, f"{label} → {p.peer_ip}",
                         p.source_system_id, Severity.INFO, detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "bgp_zonen",
    "analyse",
    ("BGP-Filter gegen die Zonen", "BGP filters against the zones"),
    (
        "Je BGP-Nachbar: Eingangsfilter gegen die Netze und Ausschlüsse der Zonen dahinter "
        "(Netz gesperrt, Ausschluss erlaubt, Regel ausserhalb der Zonen, kein Filter)",
        "Per BGP neighbor: inbound filter against the networks and exclusions of the zones behind "
        "(network denied, exclusion permitted, rule outside the zones, no filter)",
    ),
    frozenset({FindingType.BGP_ZONE_MISMATCH}),
    _run,
    step="zonen",
    order=63,
)
