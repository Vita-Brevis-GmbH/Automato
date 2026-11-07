"""Prüfung: Nachkontrolle des NetBox-Syncs (0.115.0, ADR 0042).

Roadmap Version 1 Nr. 5: nach „Übernehmen“ liest Automato die Ziel-NetBox über einen rein
lesenden Client neu und vergleicht je geschriebenem Objekt (`store/netbox_sync.verify`, am Plan
`verification`). Je Abweichung der letzten Übernahme je NetBox ein Befund: angelegt, aber nicht
da; geschrieben, aber andere Werte; gelöscht, aber noch da. Mit der nächsten Übernahme (bzw. ihrer
Nachkontrolle) fallen behobene Abweichungen weg."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.i18n import use
from app.models import (
    FindingType,
    Platform,
    Severity,
    SourceSystem,
    SubjectKind,
    SyncPlan,
    SyncStatus,
)
from app.store import netbox_sync
from app.store.reconcile import Issue


def _last_applied(session: Session, sid: int) -> SyncPlan | None:
    return session.scalars(
        select(SyncPlan)
        .where(SyncPlan.source_system_id == sid, SyncPlan.status == SyncStatus.APPLIED.value)
        .order_by(SyncPlan.applied_at.desc(), SyncPlan.id.desc())
        .limit(1)
    ).first()


def _run(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    out: list[Issue] = []
    targets = session.scalars(select(SourceSystem).where(
        SourceSystem.tenant_id == tenant_id, SourceSystem.platform == Platform.NETBOX))  # fmt: skip
    for target in targets:
        plan = _last_applied(session, target.id)
        check = (plan.verification or {}) if plan is not None else {}
        for d in check.get("deviations") or []:
            with use("en"):  # gespeichert deutsch, die englische Bezeichnung fuer den Text
                label_en = netbox_sync.show_label(d["label"])
            detail = {"netbox": target.name, "plan_id": plan.id, "kind": d["kind"],
                      "key": d["key"], "label": d["label"], "label_en": label_en,
                      "action": d["action"], "problem": d["problem"],
                      "changes": d.get("changes") or [], "at": check.get("at")}  # fmt: skip
            out.append(Issue(FindingType.NETBOX_SYNC_MISMATCH, SubjectKind.NETBOX,
                             f"{target.name}|{d['kind']}|{d['key']}", target.id,
                             Severity.WARNING, detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "netbox_nachkontrolle",
    "schreibpfad",
    ("Nachkontrolle des NetBox-Syncs", "Verification of the NetBox sync"),
    (
        "Nach dem Übernehmen NetBox neu gelesen: geschriebenes Objekt fehlt, hat andere Werte "
        "bzw. ist nicht gelöscht",
        "NetBox read again after applying: written object missing, with other values or not "
        "deleted",
    ),
    frozenset({FindingType.NETBOX_SYNC_MISMATCH}),
    _run,
    step="quellen",
    order=95,
)
