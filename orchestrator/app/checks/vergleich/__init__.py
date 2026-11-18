"""Prüfungen des Moduls „Wege vergleichen“ (1.19.0, ADR 0067): L3-Abschnitte gegen L1/L2."""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.checks import Context
from app.models import FindingType, Severity, SubjectKind

ITEMS = 20  # Abweichungen je Befund im Detail


def deviations(session: Session, tenant_id: int, cx: Context, kind: str,
               ftype: FindingType) -> list:  # fmt: skip
    """Je L3-Abschnitt mit einer Abweichung der Prüfung `kind` (vlan, cable, stp) ein Befund."""
    from app.store import path_compare
    from app.store.reconcile import Issue

    out = []
    for s in path_compare.of_check(session, tenant_id, cx).items:
        check = s.checks[kind]
        if s.reasons or check.state != path_compare.DEVIATION:
            continue
        items = [i for i in check.items if i.get("kind") != "unread"]
        detail = {"section": s.label, "a": s.a, "b": s.b, "vlan": s.vlan,
                  "vlan_from": s.vlan_from, "items": items[:ITEMS],
                  "more": max(0, len(items) - ITEMS),
                  "segment": (s.segment or {}).get("text")}  # fmt: skip
        out.append(Issue(ftype, SubjectKind.PATH, s.label, s.source_system_id, Severity.WARNING,
                         detail=detail))  # fmt: skip
    return out


def unchecked(session: Session, tenant_id: int, cx: Context) -> list:
    """Je Grund ein Hinweis mit der Zahl der Abschnitte und Beispielen (ADR 0048: nie still)."""
    from app.store import path_compare
    from app.store.reconcile import Issue

    groups: dict[str, list[str]] = defaultdict(list)
    for s in path_compare.of_check(session, tenant_id, cx).items:
        for reason in s.unchecked():
            groups[reason].append(s.label)
    out = []
    for reason in path_compare.REASONS:
        labels = groups.get(reason)
        if not labels:
            continue
        detail = {"reason": reason, "count": len(labels),
                  "sections": labels[:path_compare.EXAMPLES]}  # fmt: skip
        out.append(Issue(FindingType.PATH_L2_UNCHECKED, SubjectKind.PATH,
                         f"Wege vergleichen: {reason}", None, Severity.INFO,
                         detail=detail))  # fmt: skip
    return out
