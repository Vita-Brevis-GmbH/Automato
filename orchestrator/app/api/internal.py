"""Interne API fuer Collectors (Service-zu-Service, Token-geschuetzt).

Collectors haben keinen DB-Zugriff (Least Privilege, ADR 0003) - sie liefern
ihre normalisierten Ergebnisse ausschliesslich ueber diesen Endpunkt ab. Bei
erfolgreichem Lauf mit bekanntem Ziel (tenant_id + source_system_id) werden die
Regeln in den Rule & Service Store persistiert und gegen den SoT gematcht (M7).
"""

import hmac
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import audit, credentials
from app.config import get_settings
from app.db import get_session
from app.models import CollectorRun, RunStatus, SourceSystem, Tenant
from app.schemas.collector import CollectorResult
from app.sot import build_adapter
from app.store import agents as agent_store
from app.store import ingest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


def _internal_ok(token: str | None) -> bool:
    return token is not None and hmac.compare_digest(token, get_settings().internal_api_token)


def _reason(exc: Exception) -> str:
    """Kurzer Grund fuer die Lauf-Meldung (bei DB-Fehlern ohne SQL/Parameter)."""
    if isinstance(exc, SQLAlchemyError) and getattr(exc, "orig", None) is not None:
        exc = exc.orig
    return f"{type(exc).__name__}: {exc}"[:500]


def require_internal_token(x_internal_token: str = Header()) -> None:
    if not _internal_ok(x_internal_token):
        raise HTTPException(status_code=401, detail="invalid internal token")


@router.get(
    "/collector-runs/{run_id}/target",
    dependencies=[Depends(require_internal_token)],
)
def run_target(run_id: str, session: Session = Depends(get_session)) -> dict:
    """Ziel eines zentralen Laufs inkl. Zugangsdaten (nur fuer den zentralen Worker).

    Laeufe ueber einen Agenten bekommen ihr Ziel ausschliesslich beim Abholen (next-run).
    """
    run = session.get(CollectorRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.execution != "inprocess":
        raise HTTPException(status_code=403, detail="run is executed by an agent")
    src = session.get(SourceSystem, run.source_system_id) if run.source_system_id else None
    if src is None:
        return {"target": None}
    try:
        return {"target": agent_store.target_for(src, None)}
    except credentials.CredentialsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class ResultAck(BaseModel):
    """Quittung: endgueltiger Lauf-Status (z. B. failed, wenn das Einspielen scheitert)."""

    run_id: str
    status: str
    message: str | None = None


@router.post("/collector-runs/{run_id}/result", response_model=ResultAck)
def submit_result(
    run_id: str,
    result: CollectorResult,
    x_internal_token: str | None = Header(default=None),
    x_agent_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> ResultAck:
    """Ergebnis abliefern: zentraler Worker mit internem Token, Agent mit seinem eigenen
    Token - dann nur fuer einen Lauf, den genau dieser Agent abgeholt hat (ADR 0009)."""
    run = session.get(CollectorRun, run_id)
    if _internal_ok(x_internal_token):
        actor = None
    elif x_agent_token:
        agent = agent_store.agent_by_token(session, x_agent_token)
        if agent is None:
            raise HTTPException(status_code=401, detail="invalid agent token")
        if run is not None and run.agent_id != agent.id:
            raise HTTPException(status_code=403, detail="run was not claimed by this agent")
        actor = f"agent:{agent.name}"
    else:
        raise HTTPException(status_code=401, detail="invalid internal token")
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    run.status = RunStatus.COMPLETED if result.status == "completed" else RunStatus.FAILED
    run.finished_at = datetime.now(UTC)
    run.rule_count = len(result.rules)
    run.message = result.message

    persisted = False
    if run.status is RunStatus.COMPLETED and run.tenant_id and run.source_system_id:
        tenant = session.get(Tenant, run.tenant_id)
        try:
            sot = build_adapter(tenant, get_settings())
            # Savepoint: ein Fehler beim Einspielen verwirft nur den Ingest - Lauf-Status,
            # Meldung und Audit lassen sich danach trotzdem speichern (kein 500)
            with session.begin_nested():
                ingest.persist_result(session, run, result, sot)
            persisted = True
        except Exception as exc:  # noqa: BLE001 - Ingest-Fehler sichtbar machen, Lieferung annehmen
            log.exception("Einspielen von Lauf %s fehlgeschlagen", run.id)
            run.status = RunStatus.FAILED
            run.message = f"Einspielen fehlgeschlagen: {_reason(exc)}"

    audit.record(
        session,
        actor=actor or f"collector:{run.platform}",
        action=f"collector_run.{run.status}",
        subject=run.id,
        detail={"rule_count": run.rule_count, "persisted": persisted},
    )
    return ResultAck(run_id=run.id, status=run.status.value, message=run.message)
