"""Prüfung: statische Route überdeckt - sie wird nie aktiv (1.19.0, `store/static_routes`).

Eine gleiche Route mit besserer Distanz bzw. spezifischere Routen, die ihr ganzes Netz abdecken,
machen sie nie aktiv. Bewusste Backup-Routen (schwebende Routen: Distanz über dem Standard des
Geräts, anderer Weg bzw. hinter einer BGP-/OSPF-Route) sind kein Befund - die Ansicht zeigt sie
als „Backup“. Begründung im Modul-Text von `store/static_routes.py`. Doppelte Routen meldet
`statisch_doppelt`, nicht diese Prüfung."""

from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.models import FindingType, Severity, SubjectKind
from app.store.reconcile import Issue


def _run(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    from app.store import static_routes

    out = []
    for s in static_routes.of_check(session, tenant_id, cx).items:
        if not s.enabled or s.state != static_routes.SHADOWED or s.duplicate > 1:
            continue
        detail = s.detail()
        if s.winner:
            detail["winner"] = s.winner
        if s.covered_by:
            detail["covered_by"] = s.covered_by
            detail["covered_total"] = s.covered_total
        out.append(Issue(FindingType.STATIC_SHADOWED, SubjectKind.NODE, s.subject(),
                         s.source_system_id, Severity.WARNING, detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "statisch_ueberdeckt",
    "analyse",
    ("Statische Route überdeckt", "Static route shadowed"),
    (
        "Statische Route, die nie aktiv wird: eine gleiche Route mit besserer Distanz bzw. "
        "spezifischere Routen über ihr ganzes Netz - ohne bewusste Backup-Routen (Distanz über "
        "dem Standard, anderer Weg)",
        "Static route that never becomes active: an equal route with a better distance or more "
        "specific routes over its whole network - without deliberate backup routes (distance "
        "above the default, other path)",
    ),
    frozenset({FindingType.STATIC_SHADOWED}),
    _run,
    order=63,
)
