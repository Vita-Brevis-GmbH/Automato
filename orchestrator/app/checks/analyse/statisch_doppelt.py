"""Prüfung: statische Route doppelt (1.19.0, `store/static_routes`).

Dieselbe Route (Präfix, Next-Hop bzw. Interface, Ziel und VRF) steht mehrfach eingeschaltet in
der Konfiguration eines Geräts (FortiGate je Seq, ACI je L3Out). Router mit nur gelesener
Routing-Tabelle kennen keine doppelten Routen (dieselbe Route ersetzt die alte)."""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.models import FindingType, Severity, SubjectKind
from app.store.reconcile import Issue


def _run(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    from app.store import static_routes

    groups = defaultdict(list)
    for s in static_routes.of_check(session, tenant_id, cx).items:
        if s.enabled and s.duplicate > 1:
            groups[(s.source_system_id, s.subject())].append(s)
    out = []
    for (sid, subject), same in sorted(groups.items()):
        first = same[0]
        entries = [{k: v for k, v in (("seq", s.seq), ("l3out", s.l3out),
                                      ("distance", s.distance), ("priority", s.priority),
                                      ("comment", s.comment), ("state", s.state))
                    if v not in (None, "")} for s in same]  # fmt: skip
        detail = {**first.detail(), "count": len(same), "entries": entries}
        for k in ("seq", "distance", "priority", "comment", "state", "reason", "l3out"):
            detail.pop(k, None)
        out.append(Issue(FindingType.STATIC_DUPLICATE, SubjectKind.NODE, subject, sid,
                         Severity.WARNING, detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "statisch_doppelt",
    "analyse",
    ("Statische Route doppelt", "Static route duplicated"),
    (
        "Dieselbe statische Route (Präfix, Next-Hop bzw. Interface, VRF) mehrfach eingeschaltet "
        "in der Konfiguration eines Geräts",
        "The same static route (prefix, next-hop or interface, VRF) enabled several times in "
        "the configuration of a device",
    ),
    frozenset({FindingType.STATIC_DUPLICATE}),
    _run,
    order=64,
)
