"""System-Seite (Admin): Versionen, Datenbank-Migration, Agent-Paket und Signatur, Zeitplan,
Sicherheits-Schalter und letzte Sicherung - der Betriebsstand auf einen Blick. Seit 0.40.0
`GET /system/connections`: Verbindungen vom Orchestrator-Container aus (Datenbank, Redis,
Keycloak, zentral gelesene Quellsysteme) mit Hinweis bzw. ufw-Regel. Seit 0.48.0 die
DB-Rolle des Orchestrators (getrennt vom Eigentuemer?) und die Aufbewahrung
(`GET /system/retention`, `POST /system/retention/purge` raeumt sofort auf).

Zeigt nie Schluessel oder Secrets, nur ob sie gesetzt sind.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import __version__, scheduler
from app.auth.deps import require_admin, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.db import get_session
from app.models import Agent, SourceSystem
from app.store import agent_package, connections, db_roles, health, retention

router = APIRouter(prefix="/system", tags=["system"])

_ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"


class DbRoleOut(BaseModel):
    role: str
    superuser: bool
    ddl: bool  # darf Tabellen anlegen/aendern (Superuser, CREATE im Schema, Eigentuemer)
    owns_tables: int
    separated: bool  # App-Rolle ohne DDL (0.48.0)


class DatabaseOut(BaseModel):
    revision: str | None  # Stand der Datenbank
    head: str | None  # neueste Migration im Code
    up_to_date: bool
    role: DbRoleOut | None = None  # None ausserhalb von PostgreSQL


class AgentPackageOut(BaseModel):
    version: str | None
    signature: str  # no_package | unsigned | unchecked | signed | invalid
    pubkey_set: bool
    agents: int
    agents_current: int  # melden die aktuelle Paket-Version


class SchedulerOut(BaseModel):
    enabled: bool
    interval_seconds: int
    timezone: str
    scheduled_sources: int
    next_run_at: datetime | None


class SecurityOut(BaseModel):
    environment: str
    auth_mode: str
    internal_mtls_mode: str
    audit_encryption: bool
    credentials_key: str  # setting | file | missing


class BackupOut(BaseModel):
    at: datetime | None = None
    file: str | None = None
    encrypted: bool | None = None
    size: int | None = None


class SystemStatusOut(BaseModel):
    version: str
    database: DatabaseOut
    agent_package: AgentPackageOut
    scheduler: SchedulerOut
    security: SecurityOut
    backup: BackupOut | None


def _database(session: Session) -> DatabaseOut:
    try:
        revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 - Tests/Dev ohne Alembic-Tabelle
        session.rollback()
        revision = None
    head = None
    if _ALEMBIC_DIR.is_dir():
        cfg = Config()
        cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
        head = ScriptDirectory.from_config(cfg).get_current_head()
    return DatabaseOut(
        revision=revision, head=head, up_to_date=revision is not None and revision == head
    )


def _database_role(session: Session) -> DbRoleOut | None:
    try:
        role = db_roles.describe(session)
    except Exception:  # noqa: BLE001 - Anzeige, kein Abbruch der System-Seite
        session.rollback()
        return None
    return DbRoleOut(**role) if role else None


def _backup(settings: Settings) -> BackupOut | None:
    try:
        data = json.loads(Path(settings.backup_status_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return BackupOut(**{k: data.get(k) for k in ("at", "file", "encrypted", "size")})


def _scheduler(session: Session, settings: Settings) -> SchedulerOut:
    sources = list(
        session.scalars(
            select(SourceSystem).where(
                SourceSystem.schedule.is_not(None), SourceSystem.enabled.is_(True)
            )
        )
    )
    now = datetime.now(UTC)
    upcoming = [
        t
        for s in sources
        if (t := scheduler.next_run(s.schedule, settings.scheduler_timezone, now))
    ]
    return SchedulerOut(
        enabled=settings.scheduler_enabled,
        interval_seconds=settings.scheduler_interval_seconds,
        timezone=settings.scheduler_timezone,
        scheduled_sources=len(sources),
        next_run_at=min(upcoming) if upcoming else None,
    )


@router.get("/status", response_model=SystemStatusOut)
def status(
    _: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    sig = agent_package.signature_status(
        settings.agent_package_dir, settings.agent_signature_file, settings.agent_signing_pubkey
    )
    agents = session.scalar(select(func.count()).select_from(Agent)) or 0
    current = 0
    if sig["version"]:
        current = (
            session.scalar(
                select(func.count()).select_from(Agent).where(Agent.version == sig["version"])
            )
            or 0
        )
    key_file = Path(settings.credentials_key_file)
    return SystemStatusOut(
        version=__version__,
        database=_database(session).model_copy(update={"role": _database_role(session)}),
        agent_package=AgentPackageOut(
            version=sig["version"],
            signature=sig["state"],
            pubkey_set=bool(settings.agent_signing_pubkey),
            agents=agents,
            agents_current=current,
        ),
        scheduler=_scheduler(session, settings),
        security=SecurityOut(
            environment=settings.environment,
            auth_mode=settings.auth_mode,
            internal_mtls_mode=settings.internal_mtls_mode,
            audit_encryption=bool(settings.audit_encryption_key),
            credentials_key="setting"
            if settings.credentials_key
            else ("file" if key_file.is_file() else "missing"),
        ),
        backup=_backup(settings),
    )


@router.get("/health")
def health_status(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Betriebszustand als Ampel (0.12.0): je Pruefung ok | warn | error mit Satz und Ziel-
    Ansicht; Quellsysteme, Agenten und NetBox-Syncs des Mandanten. Zeigt nie Secrets."""
    tid = tenant_scope(principal, tenant_id)
    db = _database(session)
    # ohne alembic_version (Tests/Dev mit create_all) gilt die Datenbank als aktuell
    items = health.checks(session, settings, tid, database_ok=db.up_to_date or db.revision is None)
    return health.summary(items)


@router.get("/connections")
def connection_checks(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Verbindungen jetzt pruefen (0.40.0), vom Orchestrator-Container aus: Datenbank, Redis,
    Keycloak (OIDC), jedes zentral gelesene Quellsystem (Name, TCP, bei NetBox `/api/status/`
    - nur lesend); Agent-Systeme als Hinweis. Dauert wenige Sekunden; zeigt nie Secrets."""
    tid = tenant_scope(principal, tenant_id)
    return connections.run(session, settings, None if tid is None else {tid})


@router.get("/retention")
def retention_status(
    _: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Aufbewahrung (0.48.0): Fristen in Tagen, was jetzt faellig ist, zuletzt aufgeraeumt."""
    return retention.status(session, settings)


@router.post("/retention/purge")
def retention_purge(
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Jetzt aufraeumen: loescht, was ueber der Frist ist (aktueller Stand bleibt); auditiert."""
    policy = retention.Policy.of(settings)
    if not policy.active:
        raise HTTPException(409, "keine Fristen gesetzt (AUTOMATO_RETENTION_*_DAYS = 0)")
    counts = retention.purge(session, policy, actor=f"user:{principal.subject}")
    session.commit()
    return {"deleted": counts, **retention.status(session, settings)}
