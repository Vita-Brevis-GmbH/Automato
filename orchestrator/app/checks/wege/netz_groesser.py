"""Prüfung: Netz einer Regel grösser als die Routen (0.111.0, ADR 0040, `store/net_assign`).

Ein Netz-Objekt einer Firewall-Regel (z. B. 10.0.0.0/8), das die Firewall nicht als Ganzes
routet - genauere Routen darin laufen anders bzw. nur die Default-Route bleibt -, und das keine
Zuordnung von Hand, keine Zone und keine Anbindung erklärt: wohin es gehört, ist unklar."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.models import (
    FindingType,
    NetworkObject,
    Rule,
    RuleDomain,
    RuleEndpoint,
    Severity,
    SubjectKind,
)
from app.store.reconcile import Issue

PARTS = 10  # Teile im Detail
RULES = 5  # Regeln im Detail


def _run(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    from app.store import net_assign, paths

    rows = session.execute(
        select(Rule.id, Rule.name, Rule.external_id, Rule.source_system_id, Rule.vdom,
               NetworkObject.value)
        .join(RuleEndpoint, RuleEndpoint.rule_id == Rule.id)
        .join(NetworkObject, NetworkObject.id == RuleEndpoint.network_object_id)
        .where(Rule.tenant_id == tenant_id, Rule.domain == RuleDomain.FIREWALL)
    ).all()  # fmt: skip
    groups: dict[tuple, dict[int, str]] = defaultdict(dict)
    for rid, name, ext, sid, vdom, value in rows:
        net = net_assign.as_net(value)
        if net is not None and sid is not None:
            groups[(sid, vdom or "root", str(net))][rid] = name or f"#{ext}"
    if not groups:
        return []
    graph = cx.extra.get("graph") or paths.Graph(session, tenant_id)
    cx.extra["graph"] = graph
    assigner = net_assign.Assigner(session, tenant_id)
    out: list[Issue] = []
    for (sid, vdom, prefix), rules in sorted(groups.items()):
        key = (sid, vdom)
        if key not in graph.nodes:
            continue
        net = net_assign.as_net(prefix)
        route = graph.route(key, net)
        if not net_assign.wider(net, route):
            continue
        decided = assigner.decide(net, route)
        if not decided.open:
            continue
        node = graph.nodes[key]
        via = ", ".join(h.get("ip") or h.get("interface") or "?" for h in route["next_hops"])
        names = sorted(rules.values())
        out.append(Issue(
            FindingType.NET_WIDER_THAN_ROUTES, SubjectKind.NETWORK_OBJECT,
            f"{prefix} · {node.label}", sid, Severity.WARNING,
            detail={
                "value": prefix, "source_system_id": sid, "source": node.label,
                **({"vdom": vdom} if vdom != "root" else {}),
                "route": route["prefix"], "protocol": route.get("protocol"), "via": via,
                "parts": [p["prefix"] for p in route.get("inner") or []][:PARTS],
                "parts_total": route.get("inner_total") or len(route.get("inner") or []),
                "state": decided.state, "rules": names[:RULES], "rule_ids": sorted(rules),
            },
        ))  # fmt: skip
    return out


CHECK = Check(
    "netz_groesser",
    "wege",
    ("Netz der Regel grösser als die Routen", "Network of the rule wider than the routes"),
    (
        "Netz-Objekt einer Regel, das die Firewall nicht als Ganzes routet (Teile genauer "
        "geroutet bzw. nur die Default-Route) und das keine Zone, Anbindung bzw. Zuordnung von "
        "Hand erklärt",
        "Network object of a rule the firewall does not route as a whole (parts routed more "
        "specifically or only the default route) and that no zone, exit or manual assignment "
        "explains",
    ),
    frozenset({FindingType.NET_WIDER_THAN_ROUTES}),
    _run,
    order=85,
)
