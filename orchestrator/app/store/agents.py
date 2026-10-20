"""Agent-Store (ADR 0008): Registrierung + Pull der naechsten externen Laeufe.

Agent-Token werden nur als SHA-256-Hash gehalten; das Klartext-Token wird einmalig bei
der Registrierung zurueckgegeben. `claim_next_run` holt den aeltesten offenen externen
Lauf im Scope des Agenten und schaltet ihn auf RUNNING (einmalige Zuteilung).

Geraete-Zuweisung (ADR 0009): Laeufe eines Geraets mit zugewiesenem Agenten kann nur dieser
abholen; `target_for` liefert das Ziel - die Zugangsdaten nur an den zugewiesenen Agenten.
"""

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import credentials
from app.models import Agent, CollectorRun, RunStatus, SourceSystem


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
        .outerjoin(SourceSystem, SourceSystem.id == CollectorRun.source_system_id)
        .where(CollectorRun.status == RunStatus.QUEUED, CollectorRun.execution == "external")
        # Geraet mit zugewiesenem Agenten: nur dieser; ohne Zuweisung: jeder Agent im Scope
        .where(or_(SourceSystem.agent_id.is_(None), SourceSystem.agent_id == agent.id))
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
    run.agent_id = agent.id
    session.flush()
    return run


def target_for(src: SourceSystem, agent_id: int | None) -> dict:
    """Ziel eines Laufs; die Zugangsdaten (FortiGate: token, ACI: username/password) nur fuer
    den zugewiesenen Agenten (bzw. den zentralen Worker, `agent_id=None` bei einem Geraet
    ohne Agent). Wirft CredentialsUnavailable."""
    secret: dict = {}
    if src.credentials and src.agent_id == agent_id:
        secret = credentials.decrypt(src.credentials)
    return {
        "source_system_id": src.id,
        "name": src.name,
        "platform": src.platform.value,
        "base_url": src.base_url,
        "verify_tls": src.verify_tls,
        "read_scope": src.read_scope,
        "token": secret.get("token"),
        "username": secret.get("username"),
        "password": secret.get("password"),
    }


def assigned_systems(session: Session, agent: Agent) -> list[SourceSystem]:
    stmt = select(SourceSystem).where(SourceSystem.agent_id == agent.id).order_by(SourceSystem.name)
    return list(session.scalars(stmt))
