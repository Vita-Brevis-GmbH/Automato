"""Collector-Agenten (ADR 0008): Admin-Registrierung + Agent-Pull-API.

- Admin (`/agents`): Agenten anlegen/auflisten. Das Klartext-Token wird **einmalig** bei
  der Anlage zurueckgegeben (danach nur noch der Hash gespeichert).
- Agent (`/internal/agent`, Token-Auth via `X-Agent-Token`): naechsten externen Lauf holen
  (`next-run`) und Heartbeat. Der Agent liefert das Ergebnis wie gehabt an
  `/internal/collector-runs/{run_id}/result`.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit
from app.auth.deps import require_admin
from app.auth.principal import Principal
from app.db import get_session
from app.models import Agent
from app.store import agents as store

router = APIRouter(prefix="/agents", tags=["agents"])
agent_router = APIRouter(prefix="/internal/agent", tags=["agent"])


class AgentIn(BaseModel):
    name: str
    tenant_id: int | None = None


class AgentOut(BaseModel):
    id: int
    name: str
    tenant_id: int | None
    enabled: bool


class AgentCreated(AgentOut):
    token: str  # einmalig


class NextRun(BaseModel):
    run_id: str
    platform: str
    tenant_id: int | None
    source_system_id: int | None


class NextRunOut(BaseModel):
    run: NextRun | None


def _out(a: Agent) -> AgentOut:
    return AgentOut(id=a.id, name=a.name, tenant_id=a.tenant_id, enabled=a.enabled)


@router.get("", response_model=list[AgentOut])
def list_agents(_: Principal = Depends(require_admin), session: Session = Depends(get_session)):
    return [_out(a) for a in store.list_agents(session)]


@router.post("", response_model=AgentCreated, status_code=201)
def create_agent(
    body: AgentIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    agent, token = store.create_agent(session, body.name, body.tenant_id)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="agent.created",
        subject=str(agent.id),
        detail={"agent_id": agent.id, "name": agent.name, "tenant_id": agent.tenant_id},
    )
    return AgentCreated(
        id=agent.id, name=agent.name, tenant_id=agent.tenant_id, enabled=agent.enabled, token=token
    )


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
        )
    )


@agent_router.post("/heartbeat", status_code=204)
def heartbeat(agent: Agent = Depends(require_agent)) -> None:
    # require_agent aktualisiert last_seen; nichts weiter zu tun.
    _ = agent
