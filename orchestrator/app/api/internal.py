"""Interne API fuer Collectors (Service-zu-Service, Token-geschuetzt).

Collectors haben keinen DB-Zugriff (Least Privilege, ADR 0003) - sie liefern
ihre normalisierten Ergebnisse ausschliesslich ueber diesen Endpunkt ab. Bei
erfolgreichem Lauf mit bekanntem Ziel (tenant_id + source_system_id) werden die
Regeln in den Rule & Service Store persistiert und gegen den SoT gematcht (M7).
"""

import hmac
import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import audit, credentials
from app.auth import mtls
from app.config import get_settings
from app.db import get_session
from app.i18n import german
from app.models import REFERENCE_PLATFORMS, CollectorRun, RunStatus, SourceSystem, Tenant
from app.schemas.collector import CollectorResult, ReferenceBatch
from app.sot import build_adapter
from app.store import agents as agent_store
from app.store import customers, ingest, reconcile, references, rule_index, runs, tenant_lock

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
def run_target(run_id: str, session: Session = Depends(get_session, scope="function")) -> dict:
    """Ziel eines zentralen Laufs inkl. Zugangsdaten (nur fuer den zentralen Worker).

    Laeufe ueber einen Agenten bekommen ihr Ziel ausschliesslich beim Abholen (next-run).
    """
    run = session.get(CollectorRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.execution != "inprocess":
        raise HTTPException(status_code=403, detail="run is executed by an agent")
    if run.status is RunStatus.QUEUED:  # zentraler Worker beginnt: wie das Abholen beim Agenten
        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(UTC)
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
    message: str | None = None  # gespeicherte Meldung (deutsch)
    # dieselbe in der Sprache der Anfrage (`Accept-Language` des Agenten, 0.88.0) - fuer seine
    # Konsole; nur wenn uebersetzt (sonst fehlt das Feld), gespeichert wird nur die deutsche
    shown: str | None = None


def _settled(run, result: CollectorResult) -> bool:
    """Lauf schon abgeschlossen - ein weiteres Ergebnis aendert nichts, die Antwort quittiert
    nur dessen Stand (Dev-Befund 0.17.0): der Agent sendet nach einem Timeout erneut bzw.
    meldet „Upload fehlgeschlagen“, waehrend der erste Upload noch eingespielt wird. Ein
    vollstaendiges Ergebnis nach einem Abbruch (failed) wird noch angenommen."""
    if run.status is RunStatus.COMPLETED:
        return True
    return run.status is RunStatus.FAILED and result.status != "completed"


def _authorized_run(
    session: Session,
    run_id: str,
    internal: str | None,
    agent_token: str | None,
    channel: mtls.Channel,
    lock: bool = False,
):
    """Lauf + Akteur: internes Token (zentraler Worker) oder der abholende Agent. `lock` sperrt
    den Lauf bis zum Ende der Transaktion (Ergebnis: ein zweiter Upload wartet auf den ersten)."""
    run = session.get(CollectorRun, run_id, with_for_update=lock)
    if _internal_ok(internal):
        actor = None
    elif agent_token:
        # ohne Lebenszeichen (0.78.1): das Einspielen haelt die Transaktion lange offen - eine
        # geschriebene Agent-Zeile sperrte bis dahin jede andere Anfrage des Agenten
        agent = agent_store.agent_by_token(session, agent_token)
        if agent is None:
            raise HTTPException(status_code=401, detail="invalid agent token")
        mtls.enforce(channel, agent.name)
        if run is not None and run.agent_id != agent.id:
            raise HTTPException(status_code=403, detail="run was not claimed by this agent")
        actor = f"agent:{agent.name}"
    else:
        raise HTTPException(status_code=401, detail="invalid internal token")
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run, actor


@router.post("/collector-runs/{run_id}/references", status_code=204)
def submit_references(
    run_id: str,
    batch: ReferenceBatch,
    x_internal_token: str | None = Header(default=None),
    x_agent_token: str | None = Header(default=None),
    channel: mtls.Channel = Depends(mtls.agent_channel),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    """Teil-Upload einer Referenzquelle (Infoblox/NetBox komplett gelesen, ADR 0010)."""
    run, _ = _authorized_run(session, run_id, x_internal_token, x_agent_token, channel)
    if run.platform not in REFERENCE_PLATFORMS:
        raise HTTPException(status_code=422, detail="run is not a reference source run")
    if run.status in (RunStatus.COMPLETED, RunStatus.FAILED):
        raise HTTPException(status_code=409, detail="run already finished")
    if not run.tenant_id or not run.source_system_id:
        raise HTTPException(status_code=422, detail="run has no tenant/source system")
    with german():  # gespeichert deutsch, gleich welche Sprache der Agent schickt
        references.add_batch(session, run, batch.records)


def _finish_reference_run(session: Session, run, result: CollectorResult, actor) -> "ResultAck":
    """Referenzlauf abschliessen: Stand uebernehmen (oder verwerfen), Mandant neu abgleichen."""
    run.finished_at = datetime.now(UTC)
    run.message = result.message
    source = session.get(SourceSystem, run.source_system_id) if run.source_system_id else None
    received = references.run_count(session, run.id)
    if result.status == "completed" and result.reference_count not in (None, received):
        result = result.model_copy(
            update={
                "status": "failed",
                "message": f"Teil-Upload unvollstaendig ({received} von {result.reference_count})",
            }
        )
        run.message = result.message
    if result.status == "completed" and source is not None and run.tenant_id:

        def finalize() -> None:
            tenant_lock.hold(session, run.tenant_id)  # vor allen Zeilen (0.80.1)
            run.rule_count = references.finalize_run(session, run, source)
            reconcile.reconcile_tenant(session, run.tenant_id)
            # Suchtext der Regeln enthaelt DNS-Namen aus dem IPAM -> Index neu
            rule_index.rebuild(session, run.tenant_id)
            reconcile.refresh_app_findings(session, run.tenant_id)  # Regel <-> Weg
            customers.derive(session, run.tenant_id)  # Kunden (ADR 0018)

        error = _attempt(run, finalize, session)
        if error is None:
            run.status = RunStatus.COMPLETED
        else:  # wie beim Ingest: Lauf failed statt 500
            run.status = RunStatus.FAILED
            run.message = f"Einspielen fehlgeschlagen: {error}"
    else:
        references.discard_run(session, run)  # Teil-Uploads eines gescheiterten Laufs verwerfen
        run.status = RunStatus.FAILED
        run.rule_count = 0
    audit.record(
        session,
        actor=actor or f"collector:{run.platform}",
        action=f"collector_run.{run.status}",
        subject=run.id,
        detail={"rule_count": run.rule_count, "persisted": run.status is RunStatus.COMPLETED},
    )
    return ResultAck(run_id=run.id, status=run.status.value, message=run.message)


def _attempt(run, work, session: Session) -> str | None:
    """`work` im Savepoint ausfuehren; bei Deadlock bzw. Serialisierungsfehler bis zu
    `tenant_lock.ATTEMPTS`-mal (0.80.1). Rueckgabe: Grund des Fehlschlags bzw. None. Ein Fehler
    verwirft nur das Einspielen - Status, Meldung und Audit des Laufs bleiben speicherbar."""
    for attempt in range(1, tenant_lock.ATTEMPTS + 1):
        try:
            with session.begin_nested():
                work()
            return None
        except Exception as exc:  # noqa: BLE001 - Fehler sichtbar machen, Lieferung annehmen
            if tenant_lock.transient(exc) and attempt < tenant_lock.ATTEMPTS:
                log.warning("Lauf %s: %s - neuer Versuch", run.id, _reason(exc))
                time.sleep(attempt)
                continue
            log.exception("Einspielen von Lauf %s fehlgeschlagen", run.id)
            return _reason(exc)
    return None  # pragma: no cover


def _entry_count(result: CollectorResult) -> int:
    """Eintraege des Laufs (Spalte „Einträge“): Regeln bzw. - ohne Regeln - die
    publizierten Dienste (NSX ALB, 0.78.5), VMs (vCenter) oder Geraete (Switches)."""
    if result.rules:
        return len(result.rules)
    if result.nat_rules:
        return len(result.nat_rules)
    if result.virtual is not None and result.virtual.vms:
        return len(result.virtual.vms)
    return len(result.inventory.devices) if result.inventory is not None else 0


@router.post(
    "/collector-runs/{run_id}/result", response_model=ResultAck, response_model_exclude_unset=True
)
def submit_result(
    run_id: str,
    result: CollectorResult,
    x_internal_token: str | None = Header(default=None),
    x_agent_token: str | None = Header(default=None),
    channel: mtls.Channel = Depends(mtls.agent_channel),
    session: Session = Depends(get_session, scope="function"),
) -> ResultAck:
    """Ergebnis abliefern: zentraler Worker mit internem Token, Agent mit seinem eigenen
    Token - dann nur fuer einen Lauf, den genau dieser Agent abgeholt hat (ADR 0009).

    Seit 0.80.1 nacheinander (`tenant_lock.slot`): wer wartet, gibt seine Verbindung vorher
    zurueck; ein schon abgeschlossener Lauf wird ohne Warten quittiert.

    Gespeichert wird deutsch, auch wenn der Agent eine andere Sprache schickt (`german`); die
    Quittung bringt die Meldung zusaetzlich in seiner Sprache (`shown`, ADR 0028 Schritt 4)."""
    with german():
        ack = _receive(session, run_id, result, x_internal_token, x_agent_token, channel)
    shown = runs.show_message(ack.message)
    return ack if shown == ack.message else ack.model_copy(update={"shown": shown})


def _receive(
    session: Session,
    run_id: str,
    result: CollectorResult,
    x_internal_token: str | None,
    x_agent_token: str | None,
    channel: mtls.Channel,
) -> ResultAck:
    run, actor = _authorized_run(session, run_id, x_internal_token, x_agent_token, channel)
    if _settled(run, result):
        return ResultAck(run_id=run.id, status=run.status.value, message=run.message)
    session.rollback()  # Verbindung zurueck in den Pool, solange das Ergebnis wartet
    with tenant_lock.slot(run_id):
        ack = _submit(session, run_id, result, x_internal_token, x_agent_token, channel)
        session.commit()  # vor dem naechsten Einspielen
    return ack


def _submit(
    session: Session,
    run_id: str,
    result: CollectorResult,
    x_internal_token: str | None,
    x_agent_token: str | None,
    channel: mtls.Channel,
) -> ResultAck:
    run, actor = _authorized_run(
        session, run_id, x_internal_token, x_agent_token, channel, lock=True
    )
    if _settled(run, result):
        return ResultAck(run_id=run.id, status=run.status.value, message=run.message)
    if run.platform in REFERENCE_PLATFORMS:
        return _finish_reference_run(session, run, result, actor)

    run.status = RunStatus.COMPLETED if result.status == "completed" else RunStatus.FAILED
    run.finished_at = datetime.now(UTC)
    run.rule_count = _entry_count(result)
    run.message = result.message

    persisted = False
    if run.status is RunStatus.COMPLETED and run.tenant_id and run.source_system_id:
        tenant = session.get(Tenant, run.tenant_id)
        try:
            # gespeicherter Stand der Referenzquellen, sonst Live-Adapter (Rueckfall)
            sot = references.snapshot_sot(session, tenant.id) or build_adapter(
                tenant, get_settings()
            )
        except Exception as exc:  # noqa: BLE001 - Ingest-Fehler sichtbar machen, Lieferung annehmen
            log.exception("Einspielen von Lauf %s fehlgeschlagen", run.id)
            sot, error = None, _reason(exc)
        else:
            # Savepoint: ein Fehler beim Einspielen verwirft nur den Ingest - Lauf-Status,
            # Meldung und Audit lassen sich danach trotzdem speichern (kein 500)
            error = _attempt(run, lambda: ingest.persist_result(session, run, result, sot),
                             session)  # fmt: skip
        if error is None:
            persisted = True
        else:
            run.status = RunStatus.FAILED
            run.message = f"Einspielen fehlgeschlagen: {error}"

    audit.record(
        session,
        actor=actor or f"collector:{run.platform}",
        action=f"collector_run.{run.status}",
        subject=run.id,
        detail={"rule_count": run.rule_count, "persisted": persisted},
    )
    return ResultAck(run_id=run.id, status=run.status.value, message=run.message)
