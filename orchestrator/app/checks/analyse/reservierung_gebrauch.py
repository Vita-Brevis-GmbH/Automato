"""Prüfung: reservierte VLAN- bzw. AS-Nummer gelesen in Gebrauch (1.19.0, ADR 0066).

Eine reservierte Nummer ist in den gelesenen Daten in Gebrauch (VLAN-Tabelle der Geräte am
Standort der Reservierung bzw. BGP lokal oder als Nachbar, `store/reservations`). War sie beim
Reservieren frei, ist das ein Hinweis „jetzt belegt“ (umgesetzt bzw. vergeben - freigeben),
sonst ein Konflikt. Läuft im Abgleich und nach jedem Speichern einer Reservierung."""

from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.models import FindingType


def _run(session: Session, tenant_id: int, cx: Context) -> list:
    from app.store import reservations
    from app.store.reconcile import Issue

    usage = cx.extra.setdefault("reservation_usage", reservations.Usage(session, tenant_id))
    found = reservations.problems(session, tenant_id, usage, overlaps=False)
    return [Issue(p.type, reservations.SUBJECT_KIND, p.subject, None, p.severity, detail=p.detail)
            for p in found]  # fmt: skip


CHECK = Check(
    "reservierung_gebrauch",
    "analyse",
    ("Reservierung in Gebrauch", "Reservation in use"),
    (
        "Reservierte VLAN- bzw. AS-Nummer gelesen in Gebrauch: Hinweis „jetzt belegt“, wenn "
        "sie beim Reservieren frei war, sonst Konflikt",
        "Reserved VLAN or AS number read in use: note “now in use” if it was free when it was "
        "reserved, otherwise conflict",
    ),
    frozenset({FindingType.RESERVATION_IN_USE, FindingType.RESERVATION_CONFLICT}),
    _run,
    step="reservierungen",
    order=66,
)
