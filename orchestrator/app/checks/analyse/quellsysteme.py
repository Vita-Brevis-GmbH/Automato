"""Prüfung: Datenqualität der Quellsysteme (ADR 0029).

Steckbare Prüfung (0.110.0, ADR 0039) - aus `store/reconcile.py` hierher verschoben."""

from sqlalchemy.orm import Session

from app.checks import Check
from app.models import (
    FindingType,
    Severity,
    SubjectKind,
)
from app.store.reconcile import Issue


def _source_issues(session: Session, tenant_id: int, settings=None) -> list[Issue]:
    """Datenqualitaet (ADR 0029, `store/data_quality.source_checks`): Stand eines Quellsystems
    veraltet (letzter erfolgreicher Lauf aelter als die Grenze, Zeitplan beachtet) bzw. letzter
    Lauf nur teilweise gelesen (Geraete nicht erreicht, nicht lesbar, Routing-Kontexte)."""
    from app.store import data_quality

    return [
        Issue(FindingType(item["type"]), SubjectKind.SOURCE, item["subject"],
              item["source_system_id"], Severity(item["severity"]), detail=item["detail"])
        for item in data_quality.source_checks(session, tenant_id, settings)
    ]  # fmt: skip


CHECK = Check(
    "quellsysteme",
    "analyse",
    ("Stand der Quellsysteme", "State of the source systems"),
    (
        "Letzter erfolgreicher Lauf zu alt bzw. letzter Lauf nur teilweise gelesen",
        "Last successful run too old or last run only partially collected",
    ),
    frozenset({FindingType.SOURCE_STALE, FindingType.SOURCE_PARTIAL}),
    lambda session, tenant_id, cx: _source_issues(session, tenant_id, cx.settings),
    step="quellen",
    order=90,
)
