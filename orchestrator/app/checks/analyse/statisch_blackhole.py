"""Prüfung: Blackhole-Route (1.19.0, `store/static_routes`) - nur ein Hinweis.

Eine eingeschaltete statische Route ins Leere (FortiGate `blackhole`, Null0 bzw. Discard, ACI
ohne Next-Hop) verwirft den Verkehr in ihr Netz. Oft gewollt (gegen Schleifen bei VPN, für
zusammengefasste Netze) - daher nur ein Hinweis."""

from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.models import FindingType, Severity, SubjectKind
from app.store.reconcile import Issue


def _run(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    from app.store import static_routes

    return [
        Issue(FindingType.STATIC_BLACKHOLE, SubjectKind.NODE, s.subject(), s.source_system_id,
              Severity.INFO, detail=s.detail())
        for s in static_routes.of_check(session, tenant_id, cx).items
        if s.enabled and s.blackhole
    ]  # fmt: skip


CHECK = Check(
    "statisch_blackhole",
    "analyse",
    ("Blackhole-Route", "Blackhole route"),
    (
        "Hinweis: statische Route ins Leere (Blackhole, Null0, Discard) - der Verkehr in ihr "
        "Netz wird verworfen",
        "Note: static route into nothing (blackhole, Null0, discard) - traffic to its network "
        "is dropped",
    ),
    frozenset({FindingType.STATIC_BLACKHOLE}),
    _run,
    order=65,
)
