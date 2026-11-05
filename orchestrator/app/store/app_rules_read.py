"""Regeln einer Applikation neu einlesen (0.139.0, ADR 0047).

Anwender 2026-11-05: „Wenn ich im Realen System was ändere für eine App auf diversen Firewalls
und ACI, würde ich gerne nicht alle Firewalls und ACI neu integrieren, sondern nur die Regeln
neu berechnen, welche geändert haben. Dies würde viel Zeit sparen.“

- **Welche Geraete** (`sources`): alle FortiGates und Cisco ACI des Mandanten; die der
  Applikation zuerst und vorgewaehlt - die ihrer Regeln und Contracts in der Doku und die
  Firewalls bzw. Fabrics auf den Wegen ihrer Verbindungen (aus dem gespeicherten Stand, ADR 0037).
- **Lesen** (`start`): je Geraet ein Lauf nur der Regeln (`runs.RULES`): Regeln, Objekte und NAT
  bzw. der Tenant-Baum - ohne Interfaces, Routing, HA, Fabric und Border-Leafs, die vom letzten
  vollen Lauf bleiben. Wie „Jetzt einlesen“ rein lesend gegenueber den Geraeten.
- **Danach** rechnet nur diese Applikation neu (Befunde Regel <-> Weg, Doku, Verbindungen), sobald
  ihr letzter Teil-Lauf fertig ist (`api/internal._after_run`); die anderen Applikationen zeigen
  „veraltet“ und rechnen beim naechsten Durchgang bzw. beim Oeffnen.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import Application, CollectorRun, DocSnapshot, Rule, SourceSystem
from app.store import doc_snapshots, runs

RULES_REASON = "rules"  # Regel bzw. Contract der Doku
PATH_REASON = "path"  # auf dem Weg einer Verbindung


def _from_snapshots(session: Session, app: Application) -> dict[int, set[str]]:
    """Geraete der Applikation aus ihren gespeicherten Staenden -> {Quellsystem: Gruende}."""
    out: dict[int, set[str]] = defaultdict(set)
    rule_ids: set[int] = set()
    for row in session.scalars(select(DocSnapshot).where(DocSnapshot.application_id == app.id)):
        try:
            data = doc_snapshots._unpack(row.data).get("result") or {}
        except (ValueError, TypeError):  # unlesbarer Stand: ueberspringen
            continue
        if row.kind == doc_snapshots.DOC:
            for r in data.get("rules") or []:
                if isinstance(r, dict) and r.get("source_system_id"):
                    out[int(r["source_system_id"])].add(RULES_REASON)
            continue
        for c in data.get("connections") or []:
            for h in c.get("hops") or [] if isinstance(c, dict) else []:
                if h.get("source_system_id"):
                    out[int(h["source_system_id"])].add(PATH_REASON)
                for chk in h.get("checks") or []:
                    rule_ids |= {e["rule_id"] for e in chk.get("rules") or [] if e.get("rule_id")}
    if rule_ids:  # Verbund: die Regeln der anderen Firewalls am selben Hop
        stmt = select(Rule.source_system_id).where(Rule.id.in_(list(rule_ids))).distinct()
        for sid in session.scalars(stmt):
            if sid:
                out[sid].add(PATH_REASON)
    return out


def sources(session: Session, app: Application) -> list[dict]:
    """FortiGates und Cisco ACI des Mandanten fuer „Regeln neu einlesen“: die der Applikation
    zuerst (`own`, mit Grund), je Geraet ob schon ein Lauf offen ist und der letzte volle Lauf."""
    own = _from_snapshots(session, app)
    rows = session.scalars(
        select(SourceSystem).where(SourceSystem.tenant_id == app.tenant_id,
                                   SourceSystem.enabled.is_(True))
    )  # fmt: skip
    out = []
    for s in rows:
        platform = s.platform.value
        if platform not in runs.RULES_PLATFORMS:
            continue
        blocking = runs.blocking(session, s.id)
        last = session.scalars(
            select(CollectorRun.finished_at)
            .where(CollectorRun.source_system_id == s.id, CollectorRun.scope.is_(None),
                   CollectorRun.finished_at.is_not(None))
            .order_by(CollectorRun.finished_at.desc()).limit(1)
        ).first()  # fmt: skip
        reasons = sorted(own.get(s.id, set()))
        out.append({
            "source_system_id": s.id, "name": s.name, "platform": platform,
            "own": bool(reasons), "reasons": reasons,
            "open_run": runs.describe(session, blocking) if blocking is not None else None,
            "last_full_run": last.isoformat() if last else None,
        })  # fmt: skip
    out.sort(key=lambda x: (not x["own"], x["name"].lower()))
    return out


def start(session: Session, app: Application, source_ids: list[int] | None, actor: str
          ) -> list[dict]:  # fmt: skip
    """Je Geraet einen Lauf nur der Regeln anlegen (ohne Angabe: die der Applikation) ->
    [{source_system_id, name, run_id, execution, skipped}]. Wer schon liest bzw. wartet, wird mit
    Grund uebersprungen (wie „Alle einlesen“)."""
    listed = {x["source_system_id"]: x for x in sources(session, app)}
    wanted = source_ids if source_ids is not None else [k for k, v in listed.items() if v["own"]]
    runs.expire_stale(session)
    out = []
    for sid in dict.fromkeys(wanted):
        item = {"source_system_id": sid, "name": None, "run_id": None, "execution": None,
                "skipped": None}  # fmt: skip
        out.append(item)
        src = session.get(SourceSystem, sid)
        if src is None or src.tenant_id != app.tenant_id or sid not in listed:
            item["skipped"] = L("kein FortiGate bzw. Cisco ACI dieses Mandanten",
                                "not a FortiGate or Cisco ACI of this tenant")  # fmt: skip
            continue
        item["name"] = src.name
        open_run = runs.blocking(session, sid)
        if open_run is not None:
            item["skipped"] = runs.describe(session, open_run)
            continue
        try:
            run = runs.queue_collect(session, platform=src.platform.value,
                                     tenant_id=app.tenant_id, source_system_id=sid,
                                     execution="inprocess", actor=actor, scope=runs.RULES,
                                     application_id=app.id)  # fmt: skip
        except runs.RunRejected as exc:
            item["skipped"] = str(exc)
            continue
        item["run_id"], item["execution"] = run.id, run.execution
    return out
