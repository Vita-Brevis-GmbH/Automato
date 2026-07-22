"""Interne API fuer Collectors (Service-zu-Service, Token-geschuetzt).

Collectors haben keinen DB-Zugriff (Least Privilege, ADR 0003) - sie liefern
ihre normalisierten Ergebnisse ausschliesslich ueber diesen Endpunkt ab. Bei
erfolgreichem Lauf mit bekanntem Ziel (tenant_id + source_system_id) werden die
Regeln in den Rule & Service Store persistiert und gegen den SoT gematcht (M7).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.db import get_session
from app.models import CollectorRun, RunStatus, Tenant
from app.schemas.collector import CollectorResult
from app.sot import build_adapter
from app.store import ingest

router = APIRouter(prefix="/internal", tags=["internal"])


def require_internal_token(x_internal_token: str = Header()) -> None:
    if x_internal_token != get_settings().internal_api_token:
        raise HTTPException(status_code=401, detail="invalid internal token")


@router.post(
    "/collector-runs/{run_id}/result",
    dependencies=[Depends(require_internal_token)],
    status_code=204,
)
def submit_result(
    run_id: str,
    result: CollectorResult,
    session: Session = Depends(get_session),
) -> None:
    run = session.get(CollectorRun, run_id)
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
            ingest.persist_result(session, run, result, sot)
            persisted = True
        except Exception as exc:  # noqa: BLE001 - Ingest-Fehler sichtbar machen, Lieferung annehmen
            run.message = f"{run.message or ''} | ingest-fehler: {exc}".strip(" |")

    audit.record(
        session,
        actor=f"collector:{run.platform}",
        action=f"collector_run.{run.status}",
        subject=run.id,
        detail={"rule_count": run.rule_count, "persisted": persisted},
    )
