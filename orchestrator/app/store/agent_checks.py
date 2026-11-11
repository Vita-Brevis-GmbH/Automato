"""Prüfaufträge für Geräte hinter einem Agenten (1.7.0).

Anwender 2026-11-11: „Wenn ich den internen Worker verwende, kann ich die Verbindung prüfen, beim
Collector-Agent nicht.“ Das Gerät erreicht nur der Agent: „Prüfen“ legt hier einen Auftrag an,
der Agent bekommt ihn beim nächsten Abruf (`/internal/agent/next-run`, im Wach-Modus alle 20 s)
samt Zugangsdaten, prüft read-only (Name, Verbindung, Anmeldung) und meldet das Ergebnis.
Antwortet der Agent nicht, verfällt der Auftrag sichtbar (`expired`) - nie still offen.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Agent, AgentCheck, SourceSystem

WAIT = timedelta(minutes=3)  # so lange wartet ein Auftrag darauf, dass der Agent ihn abholt
RUNNING = timedelta(minutes=5)  # abgeholt, aber ohne Antwort (Gerät hängt, Agent beendet)
KEEP = timedelta(days=1)  # erledigte Aufträge danach löschen
MAX_CLAIM = 10  # je Abruf höchstens so viele Prüfungen
MAX_STEPS = 10
MAX_TEXT = 500
STEPS = frozenset({"dns", "tcp", "login"})


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def expire(check: AgentCheck, now: datetime | None = None) -> AgentCheck:
    """Offenen Auftrag ohne Antwort als verfallen kennzeichnen (beim Lesen)."""
    now = now or _now()
    requested, claimed = _aware(check.requested_at), _aware(check.claimed_at)
    late = (check.status == "queued" and requested is not None and requested < now - WAIT) or (
        check.status == "running" and claimed is not None and claimed < now - RUNNING
    )
    if late:
        check.status = "expired"
        check.finished_at = now
    return check


def request(session: Session, src: SourceSystem, by: str | None) -> AgentCheck:
    """Auftrag für das Gerät anlegen - ein noch offener Auftrag wird weiterverwendet (doppelt
    klicken prüft nicht doppelt)."""
    now = _now()
    session.execute(delete(AgentCheck).where(AgentCheck.requested_at < now - KEEP))
    open_check = session.scalars(
        select(AgentCheck)
        .where(
            AgentCheck.source_system_id == src.id,
            AgentCheck.agent_id == src.agent_id,
            AgentCheck.status.in_(("queued", "running")),
        )
        .order_by(AgentCheck.id.desc())
    ).first()
    if open_check is not None and expire(open_check, now).status != "expired":
        return open_check
    check = AgentCheck(
        agent_id=src.agent_id,
        source_system_id=src.id,
        status="queued",
        requested_by=(by or "")[:200] or None,
        requested_at=now,
    )
    session.add(check)
    session.flush()
    return check


def claim(session: Session, agent: Agent) -> list[AgentCheck]:
    """Offene Aufträge des Agenten abholen (nur Geräte, die ihm noch zugewiesen sind)."""
    now = _now()
    rows = list(
        session.scalars(
            select(AgentCheck)
            .join(SourceSystem, SourceSystem.id == AgentCheck.source_system_id)
            .where(
                AgentCheck.agent_id == agent.id,
                AgentCheck.status == "queued",
                AgentCheck.requested_at >= now - WAIT,
                SourceSystem.agent_id == agent.id,
            )
            .order_by(AgentCheck.id)
            .limit(MAX_CLAIM)
            .with_for_update(skip_locked=True, of=AgentCheck)
        )
    )
    for check in rows:
        check.status = "running"
        check.claimed_at = now
    return rows


def _steps(raw: object) -> list[dict]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("step") not in STEPS:
            continue
        out.append(
            {
                "step": item["step"],
                "ok": bool(item.get("ok")),
                "detail": str(item.get("detail") or "")[:MAX_TEXT],
            }
        )
    return out[:MAX_STEPS]


def finish(
    session: Session, agent: Agent, check_id: int, ok: bool, steps: object, hint: str | None
) -> AgentCheck | None:
    """Ergebnis des Agenten übernehmen - nur für seinen eigenen, abgeholten Auftrag."""
    check = session.get(AgentCheck, check_id)
    if check is None or check.agent_id != agent.id or check.status != "running":
        return None
    check.status = "done"
    check.ok = bool(ok)
    check.finished_at = _now()
    check.result = {"steps": _steps(steps), "hint": (hint or "")[:MAX_TEXT] or None}
    return check


def latest(session: Session, src: SourceSystem, check_id: int) -> AgentCheck | None:
    check = session.get(AgentCheck, check_id)
    if check is None or check.source_system_id != src.id:
        return None
    return expire(check)
