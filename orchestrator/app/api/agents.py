"""Collector-Agenten (ADR 0008): Admin-Registrierung + Agent-Pull-API.

- Admin (`/agents`): Agenten anlegen/auflisten. Das Klartext-Token wird **einmalig** bei
  der Anlage zurueckgegeben (danach nur noch der Hash gespeichert).
- Agent (`/internal/agent`, Token-Auth via `X-Agent-Token`): naechsten externen Lauf holen
  (`next-run`, inkl. Ziel und - fuer zugewiesene Geraete - Zugangsdaten, ADR 0009),
  zugewiesene Geraete (`targets`, fuer `--check`) und Heartbeat. Das Ergebnis liefert der
  Agent mit demselben Token an `/internal/collector-runs/{run_id}/result`.
"""

import io
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, credentials
from app.auth.deps import require_admin
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.db import get_session
from app.models import Agent, RunStatus, SourceSystem, Tenant
from app.store import agents as store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])
agent_router = APIRouter(prefix="/internal/agent", tags=["agent"])


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tenant_id: int | None = None


class AgentSystem(BaseModel):
    id: int
    name: str
    tenant_id: int | None
    has_credentials: bool


class AgentOut(BaseModel):
    id: int
    name: str
    tenant_id: int | None
    enabled: bool
    last_seen: datetime | None
    systems: list[AgentSystem] = []  # zugewiesene Geraete


class AgentCreated(AgentOut):
    token: str  # einmalig


class AgentPatch(BaseModel):
    enabled: bool


class Target(BaseModel):
    """Zu lesendes Geraet; Zugangsdaten nur an den zugewiesenen Agenten (ADR 0009)."""

    source_system_id: int
    name: str
    platform: str
    base_url: str
    verify_tls: bool
    read_scope: str = ""  # ACI: zu lesende Tenants (kommagetrennt)
    token: str | None = None  # FortiGate
    username: str | None = None  # Cisco ACI (APIC-Login)
    password: str | None = None


class NextRun(BaseModel):
    run_id: str
    platform: str
    tenant_id: int | None
    source_system_id: int | None
    target: Target | None = None


class NextRunOut(BaseModel):
    run: NextRun | None


def _out(a: Agent, systems: list | None = None) -> AgentOut:
    return AgentOut(
        id=a.id,
        name=a.name,
        tenant_id=a.tenant_id,
        enabled=a.enabled,
        last_seen=a.last_seen,
        systems=[
            AgentSystem(
                id=s.id, name=s.name, tenant_id=s.tenant_id, has_credentials=bool(s.credentials)
            )
            for s in systems or []
        ],
    )


# Dateien des Agent-Pakets (keine Secrets: agent.env erzeugt das UI lokal im Browser)
_PACKAGE_FILES = (
    "install.ps1",
    "run-agent.ps1",
    "agent.env.example",
    "agent_entry.py",
    "README.md",
)


@router.get("/package")
def download_package(
    _: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Agent-Paket als ZIP (collectors-Wheel + install.ps1/run-agent.ps1) - Admin."""
    base = Path(settings.agent_package_dir)
    wheels = sorted(base.glob("collectors-*.whl")) if base.is_dir() else []
    if not wheels:
        raise HTTPException(
            status_code=404,
            detail="agent package not available (orchestrator image without agent-package stage)",
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in [wheels[-1], *(base / n for n in _PACKAGE_FILES)]:
            if path.is_file():
                zf.write(path, f"automato-collector-agent/{path.name}")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="automato-collector-agent.zip"'},
    )


@router.get("", response_model=list[AgentOut])
def list_agents(_: Principal = Depends(require_admin), session: Session = Depends(get_session)):
    return [_out(a, store.assigned_systems(session, a)) for a in store.list_agents(session)]


@router.post("", response_model=AgentCreated, status_code=201)
def create_agent(
    body: AgentIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Agent anlegen - Admin. Name eindeutig (409), Mandant muss existieren (422)."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if session.scalar(select(Agent).where(Agent.name == name)) is not None:
        raise HTTPException(status_code=409, detail="agent name already exists")
    if body.tenant_id is not None and session.get(Tenant, body.tenant_id) is None:
        raise HTTPException(status_code=422, detail="tenant not found")
    agent, token = store.create_agent(session, name, body.tenant_id)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="agent.created",
        subject=str(agent.id),
        detail={"agent_id": agent.id, "name": agent.name, "tenant_id": agent.tenant_id},
    )
    return AgentCreated(
        id=agent.id,
        name=agent.name,
        tenant_id=agent.tenant_id,
        enabled=agent.enabled,
        last_seen=agent.last_seen,
        token=token,
    )


@router.patch("/{agent_id}", response_model=AgentOut)
def update_agent(
    agent_id: int,
    body: AgentPatch,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    agent = store.set_enabled(session, agent_id, body.enabled)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="agent.updated",
        subject=str(agent.id),
        detail={"agent_id": agent.id, "enabled": agent.enabled},
    )
    return _out(agent, store.assigned_systems(session, agent))


def require_agent(x_agent_token: str = Header(), session: Session = Depends(get_session)) -> Agent:
    agent = store.agent_by_token(session, x_agent_token)
    if agent is None:
        raise HTTPException(status_code=401, detail="invalid agent token")
    return agent


@agent_router.get("/next-run", response_model=NextRunOut)
def next_run(
    platform: str | None = None,
    agent: Agent = Depends(require_agent),
    session: Session = Depends(get_session),
):
    run = store.claim_next_run(session, agent, platform)
    if run is None:
        return NextRunOut(run=None)
    target = None
    src = session.get(SourceSystem, run.source_system_id) if run.source_system_id else None
    if src is not None:
        try:
            target = Target(**store.target_for(src, agent.id))
        except credentials.CredentialsUnavailable as exc:
            # Lauf nicht mit falschen/ohne Zugangsdaten starten - sichtbar abbrechen
            run.status = RunStatus.FAILED
            run.finished_at = datetime.now(UTC)
            run.message = f"Zugangsdaten nicht lesbar: {exc}"
            log.warning("Lauf %s abgebrochen: %s", run.id, exc)
            return NextRunOut(run=None)
    audit.record(
        session,
        actor=f"agent:{agent.name}",
        action="collector_run.claimed",
        subject=run.id,
        detail={"run_id": run.id, "platform": run.platform},
    )
    return NextRunOut(
        run=NextRun(
            run_id=run.id,
            platform=run.platform,
            tenant_id=run.tenant_id,
            source_system_id=run.source_system_id,
            target=target,
        )
    )


@agent_router.get("/targets", response_model=list[Target])
def targets(agent: Agent = Depends(require_agent), session: Session = Depends(get_session)):
    """Dem Agenten zugewiesene, aktive Geraete inkl. Zugangsdaten (fuer `--check`)."""
    out = []
    for src in store.assigned_systems(session, agent):
        if not src.enabled:
            continue
        try:
            out.append(Target(**store.target_for(src, agent.id)))
        except credentials.CredentialsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return out


@agent_router.post("/heartbeat", status_code=204)
def heartbeat(agent: Agent = Depends(require_agent)) -> None:
    # require_agent aktualisiert last_seen; nichts weiter zu tun.
    _ = agent
