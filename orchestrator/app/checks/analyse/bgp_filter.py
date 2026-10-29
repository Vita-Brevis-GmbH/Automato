"""Prüfung: BGP-Filter Soll/Ist (0.113.0, ADR 0041, `store/bgp_filters`).

Je BGP-Nachbar einer FortiGate mit gelesener Konfiguration: Netze, die der Eingangsfilter
ausdrücklich erlaubt, von denen der (verbundene) Nachbar nichts liefert; erhaltene Netze, die der
Filter sperrt; eBGP-Nachbarn ohne Eingangsfilter. Je ACI-L3Out: import-rtctrl ohne enforce import
(wirkt nicht) bzw. mit enforce import erwartet, aber nichts erhalten."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.models import FindingType, Severity, SubjectKind, TopoL3Out
from app.store.reconcile import Issue

SHOWN = 10  # Netze im Detail


def _label(cx: Context, session: Session, tenant_id: int, sid: int, node: str, ctx: str) -> str:
    from app.store import paths

    graph = cx.extra.get("graph") or paths.Graph(session, tenant_id)
    cx.extra["graph"] = graph
    return graph.label(sid, node, ctx)[1]


def _run(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    from app.store import bgp_filters

    out: list[Issue] = []
    f = bgp_filters.Filters(session, tenant_id)
    for p in f.peers():
        r = f.report(p, limit=0)
        label = _label(cx, session, tenant_id, p.source_system_id, p.node, p.context)
        base = {"label": label, "peer": p.peer_ip, "remote_as": r["remote_as"],
                "source_system_id": p.source_system_id, "context": p.context}  # fmt: skip
        subject = f"{label} → {p.peer_ip}"
        inbound = r["in"]
        missing = [e for e in inbound["entries"] if e.get("missing")]
        if missing and r["established"]:
            out.append(Issue(
                FindingType.BGP_EXPECTED_MISSING, SubjectKind.NODE, subject,
                p.source_system_id, Severity.WARNING,
                detail={**base, "entries": [e["prefix"] for e in missing][:SHOWN],
                        "total": len(missing),
                        "filters": sorted({e["filter"] for e in missing})},
            ))  # fmt: skip
        denied = inbound["counts"].get("deny", 0)
        if denied:
            full = f.report(p, limit=bgp_filters.SHOW)
            nets = [x["prefix"] for x in full["in"]["received"] if x["verdict"] == "deny"]
            out.append(Issue(
                FindingType.BGP_OUTSIDE_FILTER, SubjectKind.NODE, subject,
                p.source_system_id, Severity.WARNING,
                detail={**base, "prefixes": nets[:SHOWN], "total": denied,
                        "filters": sorted(set(inbound["filters"].values()))},
            ))  # fmt: skip
        if r["ebgp"] and inbound["open"]:
            out.append(Issue(
                FindingType.BGP_NO_INBOUND_FILTER, SubjectKind.NODE, subject,
                p.source_system_id, Severity.INFO,
                detail={**base, "received": inbound["received_total"]},
            ))  # fmt: skip
    stmt = select(TopoL3Out).where(TopoL3Out.tenant_id == tenant_id).order_by(TopoL3Out.id)
    for o in session.scalars(stmt):
        rc = o.route_control or {}
        if not rc.get("import"):
            continue
        subject = f"{o.name} · {o.vrf or o.aci_tenant}"
        base = {"l3out": o.name, "aci_tenant": o.aci_tenant, "vrf": o.vrf,
                "source_system_id": o.source_system_id}  # fmt: skip
        if "import" not in (rc.get("enforce") or []):
            out.append(Issue(
                FindingType.ACI_IMPORT_NOT_ENFORCED, SubjectKind.NODE, subject,
                o.source_system_id, Severity.INFO,
                detail={**base, "entries": [e["prefix"] for e in rc["import"]][:SHOWN],
                        "total": len(rc["import"])},
            ))  # fmt: skip
            continue
        r = bgp_filters.l3out_report(session, o, limit=0)
        missing = [e for e in r["import"]["entries"] if e.get("missing")]
        if missing and r["peers"]:
            out.append(Issue(
                FindingType.BGP_EXPECTED_MISSING, SubjectKind.NODE, subject,
                o.source_system_id, Severity.WARNING,
                detail={**base, "entries": [e["prefix"] for e in missing][:SHOWN],
                        "total": len(missing), "filters": ["import-rtctrl"]},
            ))  # fmt: skip
    return out


CHECK = Check(
    "bgp_filter",
    "analyse",
    ("BGP-Filter Soll/Ist", "BGP filters expected/actual"),
    (
        "Je BGP-Nachbar: Netze, die der Eingangsfilter erlaubt, aber nicht ankommen; erhaltene "
        "Netze, die der Filter sperrt; eBGP ohne Eingangsfilter; ACI import-rtctrl ohne enforce",
        "Per BGP neighbor: networks the inbound filter permits that do not arrive; received "
        "networks the filter denies; eBGP without inbound filter; ACI import-rtctrl not enforced",
    ),
    frozenset(
        {
            FindingType.BGP_EXPECTED_MISSING,
            FindingType.BGP_OUTSIDE_FILTER,
            FindingType.BGP_NO_INBOUND_FILTER,
            FindingType.ACI_IMPORT_NOT_ENFORCED,
        }
    ),
    _run,
    order=62,
)
