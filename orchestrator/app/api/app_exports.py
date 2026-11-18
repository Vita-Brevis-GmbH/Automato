"""Export der Applikations-Doku im Hintergrund (1.21.0, ADR 0069).

Anwender 2026-11-18: „Weiter wäre es noch super, wenn ich eine Auswahl treffen könnte, welche
Applikationen ich herunterladen möchte. Zudem geht es sehr lange, bis der Export steht, könnte man
dies als Hintergrunddienst laufen lassen?“

- `POST /applications/export` legt einen Auftrag an (gewählte Applikationen, Export-Passwort nur
  in der Anfrage) und kehrt sofort zurück (202); jede gewählte Id muss sichtbar sein, sonst 404
  mit den Ids - nie still ausgelassen. Ein Auftrag je Benutzer zugleich.
- `GET /applications/exports` (meine Exporte), `GET …/{id}` (Fortschritt), `POST …/{id}/cancel`,
  `DELETE …/{id}` und `GET …/{id}/download` - nur fuer den Auftraggeber, sonst 404.
- Inhalt wie bisher (1.0.1): je Applikation ein ZIP mit Word und Netzwerkplan (draw.io), mit dem
  Export-Passwort AES-256-verschluesselt (`store/secure_zip`); eine fehlgeschlagene Applikation
  steht im Auftrag und in `FEHLER.txt`, die uebrigen kommen trotzdem.

Lesen fuer Betreuer (viewer) und Mandanten-Benutzer im eigenen Mandanten (`mandant.read`, ADR
0068); nicht fuer Kunden-Benutzer in gemeinsamen Mandanten (sie exportieren ihre Applikationen
einzeln, ohne Verbindungen und Plan - ADR 0018). Die Arbeit selbst: `store/export_jobs.py`.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session, sessionmaker

from app import audit, modules
from app.api import applications as apps_api
from app.api.customer_filter import customer_view
from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session, get_session_factory
from app.i18n import L, lang, use
from app.models import Application, ExportJob, Tenant
from app.store import applications as store
from app.store import export_jobs, secure_zip, word_export

log = logging.getLogger(__name__)
router = APIRouter(prefix="/applications", tags=["applications"])
KIND = "applications"
MAX_IDS = 5000


class ExportIn(BaseModel):
    """Export im Hintergrund: Passwort nur in der Anfrage (nie gespeichert, nicht im Audit)."""

    tenant_id: int | None = None
    customer_id: int | None = None
    password: str = Field(min_length=secure_zip.MIN_PASSWORD, max_length=200)
    sections: str | None = Field(default=None, max_length=200)
    # Auswahl (1.21.0); ohne = alle sichtbaren Applikationen des Mandanten bzw. Kunden
    ids: list[int] | None = Field(default=None, min_length=1, max_length=MAX_IDS)

    @field_validator("password")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("password missing")
        return value


class ExportError(BaseModel):
    application_id: int
    name: str
    error: str


class ExportOut(BaseModel):
    id: str
    kind: str
    tenant_id: int
    status: str  # waiting | running | done | failed | cancelled | aborted
    total: int
    done: int
    current: str | None
    errors: list[ExportError]
    message: str | None  # Grund bei Fehler bzw. Abbruch (Sprache der Anfrage)
    filename: str | None
    size: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    expires_at: datetime
    cancelling: bool  # Abbruch angestossen, die aktuelle Applikation laeuft noch


def _out(job: ExportJob) -> ExportOut:
    return ExportOut(
        id=job.id, kind=job.kind, tenant_id=job.tenant_id, status=job.status, total=job.total,
        done=job.done, current=job.current, errors=[ExportError(**e) for e in job.errors or []],
        message=export_jobs.reason_text(job) if job.message else None, filename=job.filename,
        size=job.size if job.status == export_jobs.DONE else None,
        created_at=export_jobs.aware(job.created_at),
        started_at=export_jobs.aware(job.started_at),
        finished_at=export_jobs.aware(job.finished_at),
        expires_at=export_jobs.aware(job.expires_at),
        cancelling=export_jobs.cancelling(job.id),
    )  # fmt: skip


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=L("Export nicht gefunden", "export not found"))


def _own(session: Session, principal: Principal, export_id: str) -> ExportJob:
    """Nur der Auftraggeber - und nur, solange er den Mandanten noch sieht."""
    job = export_jobs.own(session, export_id, principal.subject)
    if job is None or principal.is_customer:
        raise _not_found()
    tenant_scope(principal, job.tenant_id)
    return job


def _chosen(session: Session, principal: Principal, body: ExportIn, tenant_id: int
            ) -> list[Application]:  # fmt: skip
    """Gewaehlte Applikationen in der Reihenfolge der Liste; jede gewaehlte Id muss sichtbar
    sein (Mandant, Kunde), sonst 404 mit den Ids - nie still auslassen."""
    view = customer_view(session, principal, tenant_id, body.customer_id)
    mine = view.applications(session) if view else None
    visible = [a for a in store.list_applications(session, tenant_id) if mine is None or mine(a)]
    if body.ids is None:
        chosen = visible
    else:
        wanted = list(dict.fromkeys(body.ids))
        by_id = {a.id: a for a in visible}
        missing = [i for i in wanted if i not in by_id]
        if missing:
            ids = ", ".join(str(i) for i in missing[:20]) + (" …" if len(missing) > 20 else "")
            raise HTTPException(status_code=404, detail=L(
                f"Applikationen nicht gefunden bzw. nicht sichtbar: {ids}",
                f"applications not found or not visible: {ids}"))  # fmt: skip
        chosen = [by_id[i] for i in wanted]
    if not chosen:
        raise HTTPException(status_code=404, detail=L("keine Applikationen", "no applications"))
    return sorted(chosen, key=lambda a: (a.name.casefold(), a.id))


@router.post("/export", response_model=ExportOut, status_code=202)
def start_export(
    body: ExportIn,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    """Doku der gewaehlten Applikationen als Auftrag im Hintergrund (siehe Modul) - sofort
    zurueck mit dem Auftrag; Fortschritt ueber `GET /applications/exports/{id}`."""
    want = apps_api._sections(body.sections)
    tenant_id = apps_api._require_tenant(principal, body.tenant_id)
    if not modules.enabled(session, tenant_id, "doku"):  # der Mandant steht im Rumpf
        raise HTTPException(status_code=403, detail={
            "code": "module_disabled", "module": "doku",
            "message": modules.disabled_text("doku")})  # fmt: skip
    chosen = _chosen(session, principal, body, tenant_id)
    export_jobs.purge(session)
    if export_jobs.active_of(session, principal.subject) is not None:
        raise HTTPException(status_code=409, detail=L(
            "Es läuft schon ein Export – warten, bis er fertig ist, oder ihn abbrechen",
            "An export is already running – wait until it is finished or cancel it"))  # fmt: skip
    reads.maybe_audit_read(session, principal, tenant_id, "applications")
    tenant = session.get(Tenant, tenant_id)
    when = datetime.now(UTC)
    name = (f"{L('applikationen', 'applications')}-"
            f"{word_export.slug(tenant.name if tenant else '', 'mandant')}"
            f"-{when:%Y-%m-%d-%H%M}.zip")  # fmt: skip
    job = export_jobs.create(session, KIND, principal.subject, tenant_id, len(chosen), name)
    session.commit()  # vor dem Thread: er liest den Auftrag mit eigener Session
    work = _work(factory, principal, tenant_id, [a.id for a in chosen], want, body.password,
                 lang())  # fmt: skip
    export_jobs.start(factory, job.id, work)
    session.refresh(job)
    return _out(job)


def _work(factory: sessionmaker, principal: Principal, tenant_id: int, ids: list[int],
          want: set[str] | None, password: str, code: str) -> export_jobs.Work:  # fmt: skip
    """Arbeit des Auftrags - das Passwort nur in dieser Closure (Speicher des Threads)."""

    def work(session: Session, progress: export_jobs.Progress,
             bundle: export_jobs.Bundle) -> None:  # fmt: skip
        with use(code):
            ctx = apps_api.bulk(session, tenant_id)
            background = BackgroundTasks()
            exported = 0
            for n, app_id in enumerate(ids):
                app = session.get(Application, app_id)
                if app is None:  # inzwischen geloescht
                    progress.error(app_id, f"ID {app_id}", L("Applikation gelöscht",
                                                             "application deleted"))  # fmt: skip
                    continue
                progress.step(n, app.name)
                try:
                    with session.begin_nested():
                        _base, files = apps_api.export_files(
                            session, factory, background, principal, app, want, "zip", ctx
                        )
                    session.commit()  # gerechnete Staende der Doku behalten
                except Exception as exc:  # noqa: BLE001 - eine Applikation haelt nichts auf
                    session.rollback()
                    log.warning("Export: Applikation %s fehlgeschlagen", app_id, exc_info=True)
                    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                    progress.error(app.id, app.name, f"{type(exc).__name__}: {detail}"[:300])
                    continue
                slug = word_export.slug(app.name, "applikation")
                bundle.add(bundle.unique(f"{slug}.zip"), secure_zip.encrypted(files, password))
                exported += 1
            progress.step(len(ids), None)
            if progress.errors:
                lines = "\n".join(f"{e['name']} (ID {e['application_id']}): {e['error']}"
                                  for e in progress.errors)  # fmt: skip
                bundle.add(L("FEHLER.txt", "ERRORS.txt"), L(
                    "Diese Applikationen konnten nicht exportiert werden (Protokoll des "
                    "Orchestrators):\n",
                    "These applications could not be exported (see the orchestrator log):\n",
                ) + lines + "\n")  # fmt: skip
            for task in background.tasks:  # was die Staende hinter die Antwort legten
                task.func(*task.args, **task.kwargs)
            audit.record(
                session,
                actor=f"user:{principal.subject}",
                action="application.exported",
                subject="applications",
                detail={
                    "tenant_ids": [tenant_id],
                    "count": exported,
                    "failed": len(progress.errors),
                    "encrypted": True,
                    "export_id": progress.job_id,
                },  # fmt: skip
            )
            session.commit()

    return work


@router.get("/exports", response_model=list[ExportOut])
def my_exports(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Meine Exporte (neueste zuerst), optional eines Mandanten; Abgelaufenes faellt weg."""
    export_jobs.purge(session)
    allowed = principal.tenant_ids
    out = []
    for job in export_jobs.mine(session, principal.subject, tenant_id, KIND):
        if principal.is_admin or allowed is None or job.tenant_id in allowed:
            out.append(_out(job))
    return out


@router.get("/exports/{export_id}", response_model=ExportOut)
def get_export(
    export_id: str,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    return _out(_own(session, principal, export_id))


@router.post("/exports/{export_id}/cancel", response_model=ExportOut)
def cancel_export(
    export_id: str,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Laufenden bzw. wartenden Export abbrechen - endet nach der aktuellen Applikation."""
    job = _own(session, principal, export_id)
    if job.status not in export_jobs.ACTIVE:
        raise HTTPException(status_code=409, detail=L("Export läuft nicht mehr",
                                                      "export is no longer running"))  # fmt: skip
    if not export_jobs.cancel(job.id):  # kein Thread in diesem Prozess: gilt als abgebrochen
        export_jobs.abort(job, export_jobs.CANCELLED, export_jobs.BY_USER)
    return _out(job)


@router.delete("/exports/{export_id}", status_code=204)
def delete_export(
    export_id: str,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Fertigen bzw. beendeten Export samt Datei entfernen (ein laufender: zuerst abbrechen)."""
    job = _own(session, principal, export_id)
    if job.status in export_jobs.ACTIVE and export_jobs.running_here(job.id):
        raise HTTPException(
            status_code=409,
            detail=L(
                "Export läuft noch – zuerst abbrechen", "export still running – cancel it first"
            ),
        )
    export_jobs.remove(session, job)
    return Response(status_code=204)


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: str,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Fertige Datei (nur der Auftraggeber, bis zum Ablauf)."""
    job = _own(session, principal, export_id)
    path = export_jobs.file_of(job.id)
    expired = export_jobs.aware(job.expires_at) < export_jobs.now()
    if job.status != export_jobs.DONE or expired or not path.is_file():
        raise HTTPException(status_code=404, detail=L(
            "Datei nicht (mehr) vorhanden", "file no longer available"))  # fmt: skip
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="application.export_downloaded",
        subject="applications",
        detail={"tenant_id": job.tenant_id, "export_id": job.id},
    )
    return FileResponse(path, media_type="application/zip", filename=job.filename or "export.zip")
