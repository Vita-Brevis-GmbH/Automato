"""Prüfung: Reservierungen überschneiden sich (1.19.0, ADR 0066).

Zwei Reservierungen derselben Art teilen sich Nummern - VLANs am selben Standort bzw. eine für
alle Standorte (`store/reservations.overlap`). Läuft im Abgleich und nach jedem Speichern einer
Reservierung."""

from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.models import FindingType


def _run(session: Session, tenant_id: int, cx: Context) -> list:
    from app.store import reservations
    from app.store.reconcile import Issue

    usage = cx.extra.setdefault("reservation_usage", reservations.Usage(session, tenant_id))
    found = reservations.problems(session, tenant_id, usage, in_use=False)
    return [Issue(p.type, reservations.SUBJECT_KIND, p.subject, None, p.severity, detail=p.detail)
            for p in found]  # fmt: skip


CHECK = Check(
    "reservierung_doppelt",
    "analyse",
    ("Doppelt reserviert", "Reserved twice"),
    (
        "Reservierungen derselben Art überschneiden sich (VLANs am selben Standort bzw. für "
        "alle Standorte)",
        "Reservations of the same kind overlap (VLANs at the same site or for all sites)",
    ),
    frozenset({FindingType.RESERVATION_OVERLAP}),
    _run,
    step="reservierungen",
    order=67,
)
