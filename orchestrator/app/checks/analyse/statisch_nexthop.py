"""Prüfung: Next-Hop einer statischen Route nicht erreichbar (1.19.0, `store/static_routes`).

Eine eingeschaltete statische Route, die nicht aktiv ist - auch eine Backup-Route, die im
Ernstfall greifen soll -, erreicht ihren Next-Hop nicht: kein angeschlossenes Netz und keine
Route dorthin im selben VRF, nur über die Default-Route bzw. das Netz des Next-Hops liegt an
einem anderen Interface als angegeben. Aktive Routen sind aufgelöst (sie stehen in der
Routing-Tabelle). Was nicht prüfbar ist (SD-WAN-Zone, dynamisches Gateway, Routing-Tabelle
nicht lesbar), zählt die Prüfung `statisch_ungeprueft`."""

from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.models import FindingType, Severity, SubjectKind
from app.store.reconcile import Issue


def _run(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    from app.store import static_routes

    out = []
    for s in static_routes.of_check(session, tenant_id, cx).items:
        hop = s.nexthop or {}
        if not s.enabled or hop.get("state") != "unreachable":
            continue
        detail = {**s.detail(), "nexthop": hop.get("text")}
        if hop.get("via"):
            detail["nexthop_route"] = hop["via"]
        out.append(Issue(FindingType.STATIC_NEXTHOP_UNREACHABLE, SubjectKind.NODE, s.subject(),
                         s.source_system_id, Severity.WARNING, detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "statisch_nexthop",
    "analyse",
    ("Statische Route: Next-Hop nicht erreichbar", "Static route: next-hop unreachable"),
    (
        "Nicht aktive statische Route (auch Backup), deren Next-Hop in keinem angeschlossenen "
        "Netz liegt und zu dem keine Route im selben VRF führt (nur Default-Route bzw. anderes "
        "Interface)",
        "Inactive static route (also backup) whose next-hop is in no connected network and that "
        "no route in the same VRF leads to (only the default route or another interface)",
    ),
    frozenset({FindingType.STATIC_NEXTHOP_UNREACHABLE}),
    _run,
    order=62,
)
