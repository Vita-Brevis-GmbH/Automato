"""Prüfung: statische Routen nicht (ganz) geprüft (1.19.0, ADR 0048 „keine stillen Grenzen“).

Je Gerät bzw. Kontext ein Hinweis mit den statischen Routen, die eine der Prüfungen nicht
beurteilen konnte, und dem Grund: Routing-Tabelle nicht (ganz) lesbar, Ziel ist ein Adressobjekt
(Präfix unbekannt), Next-Hop aus einer SD-WAN-Zone bzw. dynamisch (DHCP/PPPoE), nicht installierte
Route nur über ein Interface (sein Zustand ist nicht gelesen)."""

from collections import Counter, defaultdict

from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.models import FindingType, Severity, SubjectKind
from app.store.reconcile import Issue

ROUTES = 10  # Routen im Detail


def _run(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    from app.store import static_routes

    groups = defaultdict(list)
    for s in static_routes.of_check(session, tenant_id, cx).items:
        if s.enabled and s.unchecked:
            groups[(s.source_system_id, s.node, s.context)].append(s)
    out = []
    for (sid, _node, _ctx), rows in sorted(groups.items()):
        first = rows[0]
        counts = Counter(u for s in rows for u in s.unchecked)
        detail = {
            "node": first.node_key, "label": first.label, "source": first.source,
            "device": first.node, "total": len(rows), "counts": dict(sorted(counts.items())),
            "routes": [s.subject() for s in rows[:ROUTES]],
        }  # fmt: skip
        out.append(Issue(FindingType.STATIC_UNCHECKED, SubjectKind.NODE, first.label, sid,
                         Severity.INFO, detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "statisch_ungeprueft",
    "analyse",
    ("Statische Routen nicht geprüft", "Static routes not checked"),
    (
        "Hinweis je Gerät: statische Routen, die die Prüfungen nicht beurteilen konnten "
        "(Routing-Tabelle nicht lesbar, Adressobjekt, SD-WAN-Zone, dynamisches Gateway, nur "
        "Interface) - gezählt statt still übergangen",
        "Note per device: static routes the checks could not judge (routing table not readable, "
        "address object, SD-WAN zone, dynamic gateway, interface only) - counted instead of "
        "silently skipped",
    ),
    frozenset({FindingType.STATIC_UNCHECKED}),
    _run,
    order=66,
)
