"""Collector-Agenten (ADR 0008): Admin-Registrierung + Agent-Pull-API.

- Admin (`/agents`): Agenten anlegen/auflisten. Das Klartext-Token wird **einmalig** bei
  der Anlage zurueckgegeben (danach nur noch der Hash gespeichert).
- Agent (`/internal/agent`, Token-Auth via `X-Agent-Token`): naechsten externen Lauf holen
  (`next-run`, inkl. Ziel und - fuer zugewiesene Geraete - Zugangsdaten, ADR 0009),
  zugewiesene Geraete (`targets`, fuer `--check`) und Heartbeat. Das Ergebnis liefert der
  Agent mit demselben Token an `/internal/collector-runs/{run_id}/result`.
- Selbst-Update (ADR 0011): `update` liefert Version + Dateien mit SHA-256, `update/files/
  {name}` die Datei; nur bei `auto_update` des Agenten. Der Agent meldet seine Version per
  Header `X-Agent-Version` bei jedem Aufruf.
- Zuerst aktualisieren (0.16.0): Ein installierter Agent mit alter Version bekommt neue Laeufe
  erst nach dem Update (`next-run` meldet `update: true`); laenger als `UPDATE_HOLD` wartende
  Laeufe holt er trotzdem ab. Dev-Befund 0.15.0: der erste Lauf nach einem Update lief noch
  mit dem alten Agenten.
"""

import io
import logging
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_serializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, credentials, tls
from app.auth import mtls
from app.auth.deps import require_admin
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.db import get_session
from app.models import Agent, RunStatus, SourceSystem, Tenant
from app.store import agent_access, agent_package, runs
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
    version: str | None = None  # vom Agenten gemeldete Paket-Version
    auto_update: bool = True  # Selbst-Update erlaubt (ADR 0011)
    update_available: bool | None = None  # None = Version unbekannt / kein Paket
    update_note: str | None = None  # warum der Agent nicht aktualisiert (z. B. Signatur fehlt)


class AgentCreated(AgentOut):
    token: str  # einmalig


class AccessIn(BaseModel):
    # Adresse des Orchestrators vom Agent-Rechner aus - nur ohne mTLS-Gateway (sonst dessen Adresse)
    orchestrator_url: str = Field(default="", max_length=300)


class AgentPatch(BaseModel):
    enabled: bool | None = None
    auto_update: bool | None = None


class UpdateFile(BaseModel):
    name: str
    sha256: str
    size: int


class UpdateOut(BaseModel):
    version: str | None  # <Release>+<Build>; None = kein Agent-Paket im Orchestrator
    release: str | None = None  # Plattform-Version, z. B. 0.2.0
    build: str | None = None  # Fingerabdruck der Dateien
    enabled: bool  # Selbst-Update fuer diesen Agenten erlaubt
    files: list[UpdateFile] = []
    # Ed25519-Signatur (Base64) ueber Version + Dateien, offline erstellt; der Agent prueft sie,
    # wenn bei ihm ein oeffentlicher Schluessel hinterlegt ist
    signature: str | None = None


class Target(BaseModel):
    """Zu lesendes Geraet; Zugangsdaten nur an den zugewiesenen Agenten (ADR 0009)."""

    source_system_id: int
    name: str
    platform: str
    base_url: str
    verify_tls: bool
    read_scope: str = ""  # ACI: zu lesende Tenants (kommagetrennt); Switches: Adressen
    # Switches: festgehaltene SSH-Host-Schluessel je Adresse (ohne sie lernte der Agent sie bei
    # jedem Lauf neu, ADR 0025 Nr. 3) - nur bei Switches & Router mitgeliefert
    known_hosts: dict[str, str] | None = None
    token: str | None = None  # FortiGate
    username: str | None = None  # Cisco ACI (APIC-Login); SNMPv3: Benutzer
    password: str | None = None
    # Switches & Router (ADR 0033): Zugang ssh | snmp und SNMPv3 authPriv - nur gesetzt, wo
    # zutreffend (sonst nicht in der Antwort)
    access: str | None = None
    auth_protocol: str | None = None
    auth_password: str | None = None
    priv_protocol: str | None = None
    priv_password: str | None = None
    context: str | None = None
    # 0.139.0: `rules` = nur Regeln, Objekte und NAT lesen (Lauf fuer eine Applikation)
    scope: str | None = None

    @model_serializer(mode="wrap")
    def _only_used(self, handler):
        """Felder der Switches nur, wo sie gelten - das Ziel anderer Plattformen bleibt gleich."""
        data = handler(self)
        for key in _SWITCH_ONLY:
            if data.get(key) is None:
                data.pop(key, None)
        return data


# dazu `scope` nur bei Teil-Laeufen (0.139.0)
_SWITCH_ONLY = ("known_hosts", "access", "auth_protocol", "auth_password", "priv_protocol",
                "priv_password", "context", "scope")  # fmt: skip


class NextRun(BaseModel):
    run_id: str
    platform: str
    tenant_id: int | None
    source_system_id: int | None
    target: Target | None = None


class NextRunOut(BaseModel):
    run: NextRun | None
    update: bool = False  # zuerst aktualisieren: neue Version liegt bereit (0.16.0)


# so lange warten neue Laeufe auf das Update eines veralteten Agenten (der alte prueft im
# Wach-Modus alle 5 min); danach holt er sie mit der bisherigen Version ab
UPDATE_HOLD = timedelta(minutes=10)


def _update_pending(agent: Agent, version: str | None, note: str | None, current: str | None):
    """Installierter Agent (meldet seine Update-Pruefung) mit alter Version, Selbst-Update an und
    ohne Fehler bei der letzten Pruefung."""
    if note is None or note.strip() not in ("", "ok") or not agent.auto_update:
        return False
    return bool(current and version and version.strip() != current)


def _out(a: Agent, systems: list | None = None, current: str | None = None) -> AgentOut:
    return AgentOut(
        id=a.id,
        name=a.name,
        tenant_id=a.tenant_id,
        enabled=a.enabled,
        last_seen=a.last_seen,
        version=a.version,
        auto_update=a.auto_update,
        update_available=(a.version != current) if a.version and current else None,
        update_note=runs.show_message(a.update_note),  # gespeichert deutsch (ADR 0028)
        systems=[
            AgentSystem(
                id=s.id,
                name=s.name,
                tenant_id=s.tenant_id,
                has_credentials=credentials.complete(s.platform.value, s.credentials, s.access),
            )
            for s in systems or []
        ],
    )


# Dateien des Agent-Pakets (keine Secrets: agent.env und Zertifikat kommen je Agent aus dem
# Zugang, POST /agents/{id}/access)
_PACKAGE_FILES = (
    "install.ps1",
    "run-agent.ps1",
    "agent.env.example",
    "agent_entry.py",
    "README.md",
)


CA_NAME = "automato-ca.crt"  # interne CA (TLS intern, 0.49.0) fuer Agenten direkt auf :8000


def _ca_pem(settings: Settings) -> bytes | None:
    """Oeffentliches Zertifikat der internen CA (kein Geheimnis) - nur mit TLS intern."""
    if not settings.internal_tls:
        return None
    path = Path(tls.ca_file(settings))
    return path.read_bytes() if path.is_file() else None


class PackageInfoOut(BaseModel):
    internal_tls: bool  # Orchestrator spricht auf :8000 HTTPS mit der internen CA
    # mTLS-Agent-Gateway (1.5.1): Agenten nur darueber - Adresse + Client-Zertifikat in agent.env
    gateway_url: str = ""
    # Agent-CA fuer den Zugang (1.6.0): None ohne Gateway, sonst ok | ca_missing | ca_denied |
    # ca_invalid
    agent_ca: str | None = None


@router.get("/package/info", response_model=PackageInfoOut)
def package_info(
    _: Principal = Depends(require_admin), settings: Settings = Depends(get_settings)
) -> PackageInfoOut:
    """Fuer die Einrichtung: TLS intern an (0.78.2, dann https://)? Mit dem mTLS-Gateway (1.5.1)
    dessen Adresse - Port 8000 ist dann fuer Agenten gesperrt - und ob die Agent-CA
    Zertifikate ausstellen kann (1.6.0). Die CA der internen TLS liegt im Zugang bzw. Paket."""
    return PackageInfoOut(
        internal_tls=settings.internal_tls,
        gateway_url=agent_access.gateway_url(settings),
        agent_ca=agent_access.ca_state(settings),
    )


@router.get("/package")
def download_package(
    _: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Agent-Paket als ZIP (collectors- + sot-gateway-Wheel, install.ps1/run-agent.ps1) - Admin."""
    base = Path(settings.agent_package_dir)
    wheels = sorted(base.glob("collectors-*.whl")) if base.is_dir() else []
    # Infoblox-/NetBox-Leser (ADR 0010) - Abhaengigkeit des collectors-Wheels
    sot_wheels = sorted(base.glob("sot_gateway-*.whl")) if base.is_dir() else []
    if not wheels:
        raise HTTPException(
            status_code=404,
            detail="agent package not available (orchestrator image without agent-package stage)",
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in [wheels[-1], *sot_wheels[-1:], *(base / n for n in _PACKAGE_FILES)]:
            if path.is_file():
                zf.write(path, f"automato-collector-agent/{path.name}")
        info = agent_package.manifest(settings.agent_package_dir)
        if info:  # install.ps1 legt sie ab -> kein unnoetiges Update direkt nach der Installation
            zf.writestr("automato-collector-agent/agent-version.txt", info["version"])
        if (pem := _ca_pem(settings)) is not None:  # TLS intern: install.ps1 legt sie ab (0.78.2)
            zf.writestr(f"automato-collector-agent/{CA_NAME}", pem)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="automato-collector-agent.zip"'},
    )


def _current_version(settings: Settings) -> str | None:
    info = agent_package.manifest(settings.agent_package_dir)
    return info["version"] if info else None


@router.get("", response_model=list[AgentOut])
def list_agents(
    _: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    current = _current_version(settings)
    return [
        _out(a, store.assigned_systems(session, a), current) for a in store.list_agents(session)
    ]


@router.post("", response_model=AgentCreated, status_code=201)
def create_agent(
    body: AgentIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
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
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    if body.enabled is not None:
        agent.enabled = body.enabled
    if body.auto_update is not None:
        agent.auto_update = body.auto_update
    session.flush()
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="agent.updated",
        subject=str(agent.id),
        detail={"agent_id": agent.id, "enabled": agent.enabled, "auto_update": agent.auto_update},
    )
    return _out(agent, store.assigned_systems(session, agent), _current_version(settings))


@router.post("/{agent_id}/access")
def download_access(
    agent_id: int,
    body: AccessIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Zugang des Agenten als ZIP (1.6.0, ADR 0052): agent.env mit neuem Token und - mit dem
    mTLS-Gateway - Client-Zertifikat, Schluessel und Agent-CA. Das bisherige Token gilt danach
    nicht mehr; Schluessel und Token werden nirgends gespeichert. 409 = nicht moeglich
    (`detail.reason`: ca_missing | ca_denied | ca_invalid | name | gateway_host), 422 = Adresse."""
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    gateway = settings.internal_mtls_mode != "off"
    try:
        if gateway and not agent_access.gateway_url(settings):
            raise agent_access.AccessError("gateway_host", "AUTOMATO_AGENT_GATEWAY_HOST not set")
        url = agent_access.orchestrator_url(settings, body.orchestrator_url)
        ca = agent_access.load_ca(settings) if gateway else None
        issued = agent_access.issue(ca, agent.name) if ca is not None else None
    except agent_access.AccessError as exc:
        status = 422 if exc.reason == "url" else 409
        detail = {"reason": exc.reason, "message": str(exc)}
        raise HTTPException(status_code=status, detail=detail) from exc
    token = store.renew_token(session, agent)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="agent.access_issued",
        subject=str(agent.id),
        detail={
            "agent_id": agent.id,
            "name": agent.name,
            "url": url,
            "certificate": issued is not None,
            "serial": format(issued.serial, "x") if issued else None,
            "not_after": issued.not_after.isoformat() if issued else None,
        },
    )
    data = agent_access.bundle(agent.name, url, token, settings, ca, issued, _ca_pem(settings))
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in agent.name)[:60] or "agent"
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="automato-zugang-{safe}.zip"',
            "Cache-Control": "no-store",
        },
    )


def require_agent(
    x_agent_token: str = Header(),
    x_agent_version: str | None = Header(default=None),
    x_agent_update: str | None = Header(default=None),
    channel: mtls.Channel = Depends(mtls.agent_channel),
    session: Session = Depends(get_session, scope="function"),
) -> Agent:
    agent = store.agent_by_token(session, x_agent_token)
    if agent is None:
        raise HTTPException(status_code=401, detail="invalid agent token")
    mtls.enforce(channel, agent.name)  # Produktion: nur ueber das mTLS-Agent-Gateway
    if not store.touch(session, agent):  # Zeile gesperrt: Stand beim naechsten Aufruf (0.78.1)
        return agent
    if x_agent_version:
        agent.version = x_agent_version.strip()[:64]
    if x_agent_update is not None:  # Ergebnis der Update-Pruefung (ab Agent 0.11.4)
        note = x_agent_update.strip()[:300]
        agent.update_note = None if note in ("", "ok") else note
    return agent


@agent_router.get("/next-run", response_model=NextRunOut)
def next_run(
    platform: str | None = None,
    x_agent_version: str | None = Header(default=None),
    x_agent_update: str | None = Header(default=None),
    agent: Agent = Depends(require_agent),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    update = _update_pending(agent, x_agent_version, x_agent_update, _current_version(settings))
    hold = datetime.now(UTC) - UPDATE_HOLD if update else None
    run = store.claim_next_run(session, agent, platform, queued_before=hold)
    if run is None:
        return NextRunOut(run=None, update=update)
    target = None
    src = session.get(SourceSystem, run.source_system_id) if run.source_system_id else None
    if src is not None:
        try:
            target = Target(**store.target_for(src, agent.id), scope=run.scope)
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
        ),
        update=update,
    )


@agent_router.get("/targets", response_model=list[Target])
def targets(
    agent: Agent = Depends(require_agent), session: Session = Depends(get_session, scope="function")
):
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


@agent_router.get("/update", response_model=UpdateOut)
def update_manifest(
    agent: Agent = Depends(require_agent), settings: Settings = Depends(get_settings)
):
    """Aktuelle Paket-Version und Dateien (SHA-256) fuer das Selbst-Update (ADR 0011)."""
    info = agent_package.manifest(settings.agent_package_dir)
    if info is None:
        return UpdateOut(version=None, enabled=False)
    meta = {k: info[k] for k in ("version", "release", "build")}
    if not agent.auto_update:
        return UpdateOut(**meta, enabled=False)
    signature = agent_package.load_signature(settings.agent_signature_file, info)
    return UpdateOut(**meta, enabled=True, files=info["files"], signature=signature)


@agent_router.get("/update/files/{name}")
def update_file(
    name: str,
    agent: Agent = Depends(require_agent),
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
):
    if not agent.auto_update:
        raise HTTPException(status_code=403, detail="auto update disabled for this agent")
    path = agent_package.file_path(settings.agent_package_dir, name)
    if path is None:
        raise HTTPException(status_code=404, detail="file not in agent package")
    info = agent_package.manifest(settings.agent_package_dir) or {}
    if name.startswith("collectors-"):
        audit.record(
            session,
            actor=f"agent:{agent.name}",
            action="agent.update_downloaded",
            subject=str(agent.id),
            detail={"agent_id": agent.id, "from_version": agent.version, "to": info.get("version")},
        )
    return FileResponse(path, media_type="application/octet-stream", filename=name)


@agent_router.post("/heartbeat", status_code=204)
def heartbeat(agent: Agent = Depends(require_agent)) -> None:
    # require_agent aktualisiert last_seen; nichts weiter zu tun.
    _ = agent
