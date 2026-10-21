"""System-Seite (Admin): Versionen, Datenbank-Migration, Agent-Paket und Signatur, Zeitplan,
Sicherheits-Schalter und letzte Sicherung - der Betriebsstand auf einen Blick.

Nur lesend; zeigt nie Schluessel oder Secrets, nur ob sie gesetzt sind.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import __version__, scheduler
from app.auth.deps import require_admin
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.db import get_session
from app.models import Agent, SourceSystem
from app.store import agent_package

router = APIRouter(prefix="/system", tags=["system"])

_ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"


class DatabaseOut(BaseModel):
    revision: str | None  # Stand der Datenbank
    head: str | None  # neueste Migration im Code
    up_to_date: bool


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
        database=_database(session),
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
