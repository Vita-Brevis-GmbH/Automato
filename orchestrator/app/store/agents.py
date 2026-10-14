"""Agent-Store (ADR 0008): Registrierung + Pull der naechsten externen Laeufe.

Agent-Token werden nur als SHA-256-Hash gehalten; das Klartext-Token wird einmalig bei
der Registrierung zurueckgegeben. `claim_next_run` holt den aeltesten offenen externen
Lauf im Scope des Agenten und schaltet ihn auf RUNNING (einmalige Zuteilung).
"""

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, CollectorRun, RunStatus


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_agent(session: Session, name: str, tenant_id: int | None) -> tuple[Agent, str]:
    """Legt einen Agenten an und gibt (Agent, Klartext-Token) zurueck (Token einmalig)."""
    token = secrets.token_urlsafe(32)
    agent = Agent(name=name, token_hash=hash_token(token), tenant_id=tenant_id)
    session.add(agent)
    session.flush()
    return agent, token


def list_agents(session: Session) -> list[Agent]:
    return list(session.scalars(select(Agent).order_by(Agent.name)))


def set_enabled(session: Session, agent_id: int, enabled: bool) -> Agent | None:
    agent = session.get(Agent, agent_id)
    if agent is None:
        return None
    agent.enabled = enabled
    session.flush()
    return agent


def agent_by_token(session: Session, token: str) -> Agent | None:
    agent = session.scalars(
        select(Agent).where(Agent.token_hash == hash_token(token), Agent.enabled)
    ).first()
    if agent is not None:
        agent.last_seen = datetime.now(UTC)
    return agent


def claim_next_run(
    session: Session, agent: Agent, platform: str | None = None
) -> CollectorRun | None:
    """Aeltesten offenen externen Lauf im Scope des Agenten holen und auf RUNNING setzen."""
    stmt = (
        select(CollectorRun)
        .where(CollectorRun.status == RunStatus.QUEUED, CollectorRun.execution == "external")
        .order_by(CollectorRun.queued_at, CollectorRun.id)
    )
    if agent.tenant_id is not None:
        stmt = stmt.where(CollectorRun.tenant_id == agent.tenant_id)
    if platform is not None:
        stmt = stmt.where(CollectorRun.platform == platform)
    run = session.scalars(stmt).first()
    if run is None:
        return None
    run.status = RunStatus.RUNNING
    session.flush()
    return run
