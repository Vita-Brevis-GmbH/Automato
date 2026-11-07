"""Prüfung: BGP-Eingangsfilter gegen die Zonen dahinter (0.114.0, ADR 0041 Stufe 2,
`store/bgp_zones`).

Je BGP-Nachbar einer FortiGate mit gelesener Konfiguration, deren Firewall und Nachbar im
Zonenplan liegen: Netze der Zonen hinter dem Nachbarn, die der Eingangsfilter sperrt, erlaubte
Ausschlüsse, Regeln ausserhalb der Zonen bzw. kein Eingangsfilter. Teil-Durchgang „Zonen“: neu
nach jedem Speichern einer Zone, sonst im Abgleich nach jedem Lauf.

Seit 0.132.0 (Anwender: „Eingangsfilter gibt es nur bei Verbindungen nach nicht durch mich
verwaltete Geräte“) auch „ohne Eingangsfilter“ (`bgp_no_inbound_filter`) - nur zu einem Nachbarn
in einer fremd verwalteten Zone; selbst verwaltete Nachbarn brauchen keinen."""

from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.checks.analyse.bgp_filter import SHOWN, _label
from app.models import FindingType, Severity, SubjectKind
from app.store.reconcile import Issue

WHY = 30  # Begruendungen im Befund (0.127.0)


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
        neighbor = zs.neighbor_of(p)
        if zs.external(neighbor):  # 0.132.0: nur zu fremd verwalteten Zonen
            report = f.report(p, limit=0)
            if report["in"]["open"]:
                label = _label(cx, session, tenant_id, p.source_system_id, p.node, p.context)
                detail = {"label": label, "peer": p.peer_ip, "remote_as": report["remote_as"],
                          "source_system_id": p.source_system_id, "context": p.context,
                          "neighbor_zone": zs.zones[neighbor].name,
                          "received": report["in"]["received_total"]}  # fmt: skip
                out.append(Issue(FindingType.BGP_NO_INBOUND_FILTER, SubjectKind.NODE,
                                 f"{label} → {p.peer_ip}", p.source_system_id, Severity.INFO,
                                 detail=detail))  # fmt: skip
        if r.get("state") != "differs":
            continue
        label = _label(cx, session, tenant_id, p.source_system_id, p.node, p.context)
        lists = r["lists"]
        detail = {"label": label, "peer": p.peer_ip, "source_system_id": p.source_system_id,
                  "context": p.context, "zones": [z["name"] for z in r["zones"]],
                  "neighbor_zone": r["neighbor_zone"]["name"],
                  "open": any(x["open"] for x in lists),
                  "missing": [n for x in lists for n in x["missing"]][:SHOWN],
                  "missing_total": sum(x.get("missing_total", len(x["missing"])) for x in lists),
                  "too_open": [t["prefix"] for x in lists for t in x["too_open"]][:SHOWN],
                  "too_open_total": sum(len(x["too_open"]) for x in lists),
                  "excluded_allowed": [n for x in lists
                                       for n in x["excluded_allowed"]][:SHOWN],
                  # 0.127.0 (Anwender: „ich sehe keine Details, weshalb“): je Abweichung Netz,
                  # Zone und entscheidende Regel; dazu woher die Zonen kommen
                  "why": [w for x in lists for w in x.get("reasons") or []][:WHY],
                  "chain": r.get("chain")}  # fmt: skip
        out.append(Issue(FindingType.BGP_ZONE_MISMATCH, SubjectKind.NODE, f"{label} → {p.peer_ip}",
                         p.source_system_id, Severity.INFO, detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "bgp_zonen",
    "analyse",
    ("BGP-Filter gegen die Zonen", "BGP filters against the zones"),
    (
        "Je BGP-Nachbar: Eingangsfilter gegen die Netze und Ausschlüsse der Zonen dahinter "
        "(Netz gesperrt, Ausschluss erlaubt, Regel ausserhalb der Zonen, kein Filter zu einer "
        "fremd verwalteten Zone)",
        "Per BGP neighbor: inbound filter against the networks and exclusions of the zones behind "
        "(network denied, exclusion permitted, rule outside the zones, no filter towards an "
        "externally managed zone)",
    ),
    frozenset({FindingType.BGP_ZONE_MISMATCH, FindingType.BGP_NO_INBOUND_FILTER}),
    _run,
    step="zonen",
    order=63,
)
