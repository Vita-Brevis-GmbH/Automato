"""Quellsystem-Endpunkte: Liste, Anlegen, Ändern, Löschen.

Ein Quellsystem ist ein Zielsystem, aus dem gelesen wird (z. B. eine FortiGate), und gehört
zu genau einem Mandanten (Name je Mandant eindeutig). Lesen = `viewer` (mandantsgescoped),
Schreiben = `admin`.

Geräte-Zugangsdaten (ADR 0009): `base_url` + Zugangsdaten werden hier erfasst und dem
zugewiesenen Collector-Agenten (`agent_id`) beim Abholen eines Laufs übergeben - FortiGate:
`token`, Cisco ACI: `username` + `password` (APIC-Login). Sie sind **nur schreibbar**:
gespeichert verschlüsselt, ausgegeben wird nur `has_credentials`. Bei PATCH bleiben nicht
angegebene Felder erhalten (z. B. nur das Passwort ändern), `""` entfernt ein Feld.

Switches & Router (ADR 0033): `access` = `ssh` (Standard) bzw. `snmp` - mit SNMPv3 sind die
Zugangsdaten `username`, `auth_password`, `priv_password` (Pflicht, authPriv), `auth_protocol`
(SHA/SHA-2, Vorgabe sha256), `priv_protocol` (AES, Vorgabe aes128) und optional `context`.
Ausgegeben werden davon nur die Protokolle (`snmp`). Ein Wechsel des Zugangs verwirft die
bisherigen Zugangsdaten (sie gehören zum anderen Zugang).
"""

import re
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app import audit, credentials, schedule, scheduler
from app.api.customer_filter import customer_view, own_view
from app.auth.deps import require_admin, require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.config import get_settings
from app.db import get_session, get_session_factory
from app.i18n import L
from app.models import (
    REFERENCE_PLATFORMS,
    Agent,
    CollectorRun,
    DeviceObject,
    InvInterface,
    NatRule,
    ReferenceRecordRow,
    ReferenceRole,
    Rule,
    Site,
    SourceSystem,
    Tenant,
    TopoContext,
)
from app.models.source_system import Platform
from app.store import app_refresh, deletion, reachability, sites
from app.store import naming_profiles as profile_store

router = APIRouter(prefix="/systems", tags=["systems"])


class SystemOut(BaseModel):
    id: int
    tenant_id: int | None  # None = Altbestand, noch keinem Mandanten zugeordnet
    name: str
    platform: str
    enabled: bool
    base_url: str = ""
    agent_id: int | None = None  # verwaltender Collector-Agent (None = zentraler Worker)
    verify_tls: bool = True
    read_scope: str = ""  # ACI: zu lesende Tenants (kommagetrennt, leer = alle)
    has_credentials: bool = False  # Zugangsdaten selbst werden nie ausgegeben
    ref_role: str | None = None  # Referenzquellen: primary | enrichment (ADR 0010)
    has_write_token: bool = False  # NetBox: Schreib-Token fuer den NetBox-Sync hinterlegt
    reference_count: int = 0  # Referenzquellen: Eintraege im aktuellen Stand
    reference_at: datetime | None = None  # Zeitpunkt des aktuellen Stands
    site_id: int | None = None  # Standort des Geraets (NetBox-Sync)
    site_hint: dict | None = None  # Standort aus dem Namen (Vorschlag, ADR 0015)
    schedule: str | None = None  # Zeitplan (Cron, 5 Felder, Zeitzone scheduler_timezone)
    next_run_at: datetime | None = None  # naechster Termin laut Zeitplan
    rule_count: int = 0
    nat_count: int = 0
    # FortiGate: gelesene VDOMs - nur `root` = die Firewall selbst (ohne VDOM anzeigen)
    vdoms: list[str] = []
    parallel_group: str | None = None  # Verbund paralleler Firewalls (0.18.0)
    # Switches (0.62.0): festgehaltene SSH-Host-Schluessel je Adresse (nur Fingerabdruck)
    host_keys: list[dict] = []
    # Switches & Router (ADR 0033): Zugang ssh | snmp; bei SNMPv3 nur die Protokolle und ob ein
    # Context hinterlegt ist - nie Benutzer oder Passwoerter
    access: str | None = None
    snmp: dict | None = None


SnmpAuth = Literal["sha", "sha224", "sha256", "sha384", "sha512"]
SnmpPriv = Literal["aes128", "aes192", "aes256"]


class SystemIn(BaseModel):
    tenant_id: int
    name: str = Field(min_length=1, max_length=200)
    platform: Platform
    base_url: str = Field(default="", max_length=500)
    agent_id: int | None = None
    verify_tls: bool = True
    read_scope: str = Field(default="", max_length=20000)
    ref_role: Literal["primary", "enrichment"] | None = None
    site_id: int | None = None
    schedule: str | None = Field(default=None, max_length=100)  # leer/None = kein Zeitplan
    # Zugangsdaten, nur schreibbar: FortiGate/NetBox token, ACI/Infoblox username + password,
    # NetBox optional write_token (nur NetBox-Sync, nie an Agenten)
    token: str | None = Field(default=None, max_length=4096)
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=4096)
    write_token: str | None = Field(default=None, max_length=4096)
    # Switches & Router: Zugang (ADR 0033); SNMPv3-Zugangsdaten nur schreibbar wie die übrigen
    access: Literal["ssh", "snmp"] | None = None
    auth_protocol: SnmpAuth | Literal[""] | None = None
    auth_password: str | None = Field(default=None, max_length=4096)
    priv_protocol: SnmpPriv | Literal[""] | None = None
    priv_password: str | None = Field(default=None, max_length=4096)
    context: str | None = Field(default=None, max_length=200)


class SystemPatch(BaseModel):
    tenant_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    agent_id: int | None = None  # null = Zuweisung aufheben
    verify_tls: bool | None = None
    read_scope: str | None = Field(default=None, max_length=20000)
    ref_role: Literal["primary", "enrichment"] | None = None
    site_id: int | None = None  # null = Standort entfernen
    # Switches: Host-Schluessel dieser Adressen vergessen (beim naechsten Lauf neu lernen)
    forget_host_keys: list[str] | None = Field(default=None, max_length=2000)
    schedule: str | None = Field(default=None, max_length=100)  # "" oder null = kein Zeitplan
    # Verbund paralleler Firewalls (0.18.0); "" oder null = keiner
    parallel_group: str | None = Field(default=None, max_length=100)
    # weglassen = unverändert, "" = Feld entfernen (alle leer = keine Zugangsdaten)
    token: str | None = Field(default=None, max_length=4096)
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=4096)
    write_token: str | None = Field(default=None, max_length=4096)
    # Switches & Router (ADR 0033): Wechsel des Zugangs verwirft die bisherigen Zugangsdaten
    access: Literal["ssh", "snmp"] | None = None
    auth_protocol: SnmpAuth | Literal[""] | None = None
    auth_password: str | None = Field(default=None, max_length=4096)
    priv_protocol: SnmpPriv | Literal[""] | None = None
    priv_password: str | None = Field(default=None, max_length=4096)
    context: str | None = Field(default=None, max_length=200)


class SystemDeleted(BaseModel):
    id: int
    rules: int
    nat_rules: int
    findings: int
    references: int = 0


def _counts(session: Session, model) -> dict[int, int]:
    rows = session.execute(
        select(model.source_system_id, func.count()).group_by(model.source_system_id)
    )
    return {sid: n for sid, n in rows}


def _vdoms(session: Session, source_ids: list[int]) -> dict[int, list[str]]:
    """VDOMs je Quellsystem aus Regeln, Objekten, NAT, Interfaces und Routing."""
    out: dict[int, set[str]] = {}
    if not source_ids:
        return {}
    columns = [(m, m.vdom) for m in (Rule, DeviceObject, NatRule, InvInterface)]
    for model, column in [*columns, (TopoContext, TopoContext.context)]:
        stmt = select(model.source_system_id, column).where(model.source_system_id.in_(source_ids))
        if model is TopoContext:
            stmt = stmt.where(TopoContext.kind == "vdom")
        for sid, vdom in session.execute(stmt.distinct()):
            if vdom:
                out.setdefault(sid, set()).add(vdom)
    return {sid: sorted(v) for sid, v in out.items()}


def _reference_stats(session: Session, sources) -> dict[int, tuple[int, datetime | None]]:
    """Anzahl Eintraege + Zeitpunkt des aktuellen Stands je Referenzquelle."""
    runs = {s.reference_run_id: s.id for s in sources if s.reference_run_id}
    if not runs:
        return {}
    counts = dict(
        session.execute(
            select(ReferenceRecordRow.collector_run_id, func.count())
            .where(ReferenceRecordRow.collector_run_id.in_(runs))
            .group_by(ReferenceRecordRow.collector_run_id)
        ).all()
    )
    finished = dict(
        session.execute(
            select(CollectorRun.id, CollectorRun.finished_at).where(CollectorRun.id.in_(runs))
        ).all()
    )
    return {sid: (counts.get(rid, 0), finished.get(rid)) for rid, sid in runs.items()}


def _access_out(s: SourceSystem) -> str | None:
    return (s.access or "ssh") if s.platform is Platform.CISCO_SWITCH else None


def _snmp_out(s: SourceSystem) -> dict | None:
    """SNMPv3 (ADR 0033): nur die Protokolle und ob ein Context hinterlegt ist."""
    if not credentials.is_snmp(s.platform.value, s.access) or not s.credentials:
        return None
    try:
        data = credentials.decrypt(s.credentials)
    except credentials.CredentialsUnavailable:
        return None
    return {"auth_protocol": data.get("auth_protocol"), "priv_protocol": data.get("priv_protocol"),
            "has_context": bool(data.get("context"))}  # fmt: skip


def _has_write_token(s: SourceSystem) -> bool:
    if s.platform is not Platform.NETBOX or not s.credentials:
        return False
    try:
        return bool(credentials.decrypt(s.credentials).get("write_token"))
    except credentials.CredentialsUnavailable:
        return False


def _site_hints(session: Session, sources: list[SourceSystem]) -> dict[int, dict]:
    """Standort aus dem Geraete-/Cluster-Namen (fwc11-000-<tenant>, ADR 0015) - Vorschlag."""
    out: dict[int, dict] = {}
    indexes: dict[int, tuple] = {}
    for src in sources:
        if src.tenant_id is None or src.platform in REFERENCE_PLATFORMS:
            continue
        if src.tenant_id not in indexes:
            indexes[src.tenant_id] = (
                sites.SiteIndex(session, src.tenant_id),
                profile_store.prefixes_for_tenant(session, src.tenant_id),
            )
        index, prefixes = indexes[src.tenant_id]
        hint = index.from_name(src.name, prefixes)
        if hint is not None:
            out[src.id] = hint
    return out


def _out(
    s: SourceSystem,
    rules: dict[int, int],
    nat: dict[int, int],
    refs: dict | None = None,
    site_hint: dict | None = None,
    vdoms: list[str] | None = None,
) -> SystemOut:
    ref_count, ref_at = (refs or {}).get(s.id, (0, None))
    return SystemOut(
        id=s.id,
        tenant_id=s.tenant_id,
        name=s.name,
        platform=s.platform.value,
        enabled=s.enabled,
        base_url=s.base_url,
        agent_id=s.agent_id,
        verify_tls=s.verify_tls,
        read_scope=s.read_scope,
        has_credentials=credentials.complete(s.platform.value, s.credentials, s.access),
        ref_role=s.ref_role,
        has_write_token=_has_write_token(s),
        reference_count=ref_count,
        reference_at=ref_at,
        site_id=s.site_id,
        site_hint=site_hint,
        schedule=s.schedule,
        next_run_at=scheduler.next_run(s.schedule, get_settings().scheduler_timezone)
        if s.enabled
        else None,
        rule_count=rules.get(s.id, 0),
        nat_count=nat.get(s.id, 0),
        vdoms=vdoms or [],
        parallel_group=s.parallel_group,
        host_keys=[
            {"host": h, "fingerprint": fingerprint(k)}
            for h, k in sorted((s.ssh_host_keys or {}).items())
        ],  # fmt: skip
        access=_access_out(s),
        snmp=_snmp_out(s),
    )


def _single_out(session: Session, s: SourceSystem) -> SystemOut:
    usage = deletion.source_usage(session, s.id)
    rules, nat = {s.id: usage["rules"]}, {s.id: usage["nat_rules"]}
    vdoms = _vdoms(session, [s.id]).get(s.id)
    return _out(s, rules, nat, _reference_stats(session, [s]), vdoms=vdoms)


def _get_scoped(session: Session, principal: Principal, system_id: int) -> SourceSystem:
    src = session.get(SourceSystem, system_id)
    if src is None:
        raise HTTPException(status_code=404, detail="source system not found")
    if src.tenant_id is not None:
        tenant_scope(principal, src.tenant_id)  # fremder Mandant -> 403
    elif not principal.is_admin:
        raise HTTPException(status_code=404, detail="source system not found")
    return src


def _check_agent(session: Session, agent_id: int | None, tenant_id: int | None) -> None:
    """Agent muss existieren und zum Mandanten des Geräts passen (oder mandantsübergreifend)."""
    if agent_id is None:
        return
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=422, detail="agent not found")
    if agent.tenant_id is not None and agent.tenant_id != tenant_id:
        raise HTTPException(status_code=422, detail="agent belongs to another tenant")


_CREDENTIAL_KEYS = ("token", "username", "password", "write_token", "auth_protocol",
                    "auth_password", "priv_protocol", "priv_password", "context")  # fmt: skip
_PASSWORDS = ("password", "auth_password", "priv_password")  # ohne strip (Leerzeichen zaehlen)


def _credential_changes(body: SystemIn | SystemPatch) -> dict[str, str]:
    return {key: value for key in _CREDENTIAL_KEYS if (value := getattr(body, key)) is not None}


def _access(platform: Platform, value: str | None) -> str | None:
    """Zugang eines Quellsystems (ADR 0033): nur bei Switches & Router; `ssh` = Standard."""
    if value is None:
        return None
    if platform is not Platform.CISCO_SWITCH:
        raise HTTPException(status_code=422, detail="access only supported for cisco_switch")
    return value


def _check_snmp(current: dict) -> None:
    """SNMPv3 nur authPriv (ADR 0033): Protokolle mit Vorgabe, Passwoerter mit 8 Zeichen."""
    for key, default in credentials.SNMP_DEFAULTS.items():
        current[key] = current.get(key) or default
    for key in ("auth_password", "priv_password"):
        if len(current[key]) < credentials.SNMP_MIN_PASSWORD:
            raise HTTPException(
                status_code=422,
                detail=f"{key}: at least {credentials.SNMP_MIN_PASSWORD} characters (SNMPv3)",
            )


def _apply_credentials(src: SourceSystem, changes: dict[str, str], reset: bool = False) -> bool:
    """Zugangsdaten-Felder setzen und verschlüsselt ablegen; True, wenn etwas geändert wurde.

    Nicht angegebene Felder bleiben erhalten, `""` entfernt ein Feld. Ein Gerät hat entweder
    alle Felder seiner Plattform oder keine (422). `reset`: Zugang gewechselt - die bisherigen
    Zugangsdaten gehören zum anderen Zugang und fallen weg."""
    if not changes and not reset:
        return False
    platform = src.platform.value
    required = credentials.fields(platform, src.access)
    allowed = required + credentials.optional_fields(platform, src.access)
    unknown = sorted(set(changes) - set(allowed))
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"{', '.join(unknown)} not supported for {platform}"
        )
    try:
        current = credentials.decrypt(src.credentials) if src.credentials and not reset else {}
    except credentials.CredentialsUnavailable:
        current = {}  # alter Schlüssel: Zugangsdaten nur noch vollständig neu setzbar
    for key, value in changes.items():
        value = value if key in _PASSWORDS else value.strip()
        if value:
            current[key] = value
        else:
            current.pop(key, None)
    current = {k: v for k, v in current.items() if k in allowed}
    present = {k for k in current if k in required}
    if present and present != set(required):
        raise HTTPException(
            status_code=422, detail=f"{' and '.join(required)} required for {platform}"
        )
    if credentials.is_snmp(platform, src.access):
        if present:
            _check_snmp(current)
        else:
            current = {}  # Protokolle/Context nur zusammen mit Benutzer und Passwoertern
    try:
        src.credentials = credentials.encrypt(current) if current else None
    except credentials.CredentialsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return True


_ACI_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# Avi-Tenant (NSX ALB, ADR 0027): Name wie im Controller, auch mit Leerzeichen
_AVI_NAME = re.compile(r"^[A-Za-z0-9_.:@ -]{1,128}$")


def _schedule(value: str | None) -> str | None:
    """Zeitplan normalisieren und pruefen (422 mit Grund); leer = kein Zeitplan."""
    if not value or not value.strip():
        return None
    try:
        return schedule.parse(value).expr
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"schedule: {exc}") from exc


def _set_schedule(src: SourceSystem, value: str | None) -> None:
    if value != src.schedule:
        src.schedule = value
        # ab jetzt zaehlen - ein neuer Zeitplan holt keinen vergangenen Termin nach
        src.schedule_last_at = datetime.now(UTC) if value else None


def _check_site(session: Session, site_id: int | None, tenant_id: int | None) -> None:
    if site_id is None:
        return
    site = session.get(Site, site_id)
    if site is None or site.tenant_id != tenant_id:
        raise HTTPException(status_code=422, detail="site belongs to another tenant")


_SWITCH_HOST = re.compile(r"^(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9_.:-]+?)(:\d{1,5})?$")
MAX_SWITCHES = 1000


def fingerprint(key_line: str) -> str:
    """SHA256-Fingerabdruck eines SSH-Host-Schluessels (`<typ> <base64>`) wie bei OpenSSH."""
    import base64
    import binascii
    import hashlib

    try:
        raw = base64.b64decode(key_line.split()[1], validate=True)
    except (IndexError, ValueError, binascii.Error):
        return "?"
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    return f"{key_line.split()[0]} SHA256:{digest}"


def _switch_hosts(value: str) -> str:
    """Switches (0.62.0): Adressen je Zeile bzw. kommagetrennt, `host`, `host:port`,
    `[v6]:port`; eine je Zeile gespeichert, ohne Doppelte."""
    hosts = list(dict.fromkeys(h for h in re.split(r"[\s,;]+", value or "") if h))
    if len(hosts) > MAX_SWITCHES:
        raise HTTPException(status_code=422, detail=f"at most {MAX_SWITCHES} switches")
    bad = [h for h in hosts if not _SWITCH_HOST.match(h) or len(h) > 255]
    if bad:
        raise HTTPException(status_code=422, detail=f"invalid switch address: {bad[0]}")
    return "\n".join(hosts)


def _switch_base_url(read_scope: str, access: str | None = None) -> str:
    """Adresse fuer „Verbindung pruefen“: der erste Switch per SSH bzw. SNMPv3 (ADR 0033)."""
    first = next(iter(read_scope.split()), "")
    scheme = "snmp" if access == "snmp" else "ssh"
    return f"{scheme}://{first}" if first else ""


def _read_scope(platform: Platform, value: str) -> str:
    """Lesebereich normalisieren: ACI- bzw. Avi-Tenant-Namen, kommagetrennt (Cisco ACI, NSX
    ALB) bzw. die Adressen der Switches (Cisco-Switches)."""
    if platform == Platform.CISCO_SWITCH:
        return _switch_hosts(value)
    names = [x.strip() for x in value.split(",") if x.strip()]
    if not names:
        return ""
    if platform == Platform.AVI:
        bad = [n for n in names if not _AVI_NAME.match(n)]
        if bad:
            raise HTTPException(status_code=422, detail=f"invalid Avi tenant name: {bad[0]}")
        return ",".join(dict.fromkeys(names))
    if platform != Platform.CISCO_ACI:
        detail = "read_scope only supported for cisco_aci/cisco_switch/avi"
        raise HTTPException(status_code=422, detail=detail)
    bad = [n for n in names if not _ACI_NAME.match(n)]
    if bad:
        raise HTTPException(status_code=422, detail=f"invalid ACI tenant name: {bad[0]}")
    return ",".join(dict.fromkeys(names))


def _apply_ref_role(session: Session, src: SourceSystem, role: str | None) -> None:
    """Rolle einer Referenzquelle setzen; nur ein fuehrendes IPAM je Mandant (andere werden zur
    Anreicherung). Default: Infoblox fuehrend, NetBox fuehrend nur ohne andere fuehrende Quelle."""
    if src.platform not in REFERENCE_PLATFORMS:
        if role is not None:
            raise HTTPException(status_code=422, detail="ref_role only for infoblox/netbox")
        src.ref_role = None
        return
    others = [
        s
        for s in session.scalars(
            select(SourceSystem).where(
                SourceSystem.tenant_id == src.tenant_id,
                SourceSystem.ref_role == ReferenceRole.PRIMARY.value,
            )
        )
        if s is not src
    ]
    if role is None:
        role = (
            ReferenceRole.PRIMARY.value
            if src.platform is Platform.INFOBLOX or not others
            else ReferenceRole.ENRICHMENT.value
        )
    if role == ReferenceRole.PRIMARY.value:
        for other in others:
            other.ref_role = ReferenceRole.ENRICHMENT.value
    src.ref_role = role


def _name_taken(session: Session, tenant_id: int | None, name: str, exclude: int | None) -> bool:
    stmt = select(SourceSystem.id).where(
        SourceSystem.tenant_id == tenant_id, SourceSystem.name == name
    )
    if exclude is not None:
        stmt = stmt.where(SourceSystem.id != exclude)
    return session.scalar(stmt) is not None


@router.get("", response_model=list[SystemOut])
def list_systems(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Quellsysteme (alphabetisch). Admin ohne Filter sieht alle inkl. nicht zugeordneter."""
    query = select(SourceSystem).order_by(SourceSystem.name, SourceSystem.id)
    if tenant_id is not None:
        tenant_scope(principal, tenant_id)
        query = query.where(SourceSystem.tenant_id == tenant_id)
    elif not principal.is_admin and principal.tenant_ids is not None:
        query = query.where(SourceSystem.tenant_id.in_(principal.tenant_ids))
    rules, nat = _counts(session, Rule), _counts(session, NatRule)
    sources = list(session.scalars(query))
    refs = _reference_stats(session, sources)
    hints = _site_hints(session, sources)
    vdoms = _vdoms(session, [s.id for s in sources if s.platform == Platform.FORTIGATE])
    return [_out(s, rules, nat, refs, hints.get(s.id), vdoms.get(s.id)) for s in sources]


class SystemLabelOut(BaseModel):
    """Nur die Bezeichnung eines Quellsystems - ohne Konfiguration (ADR 0018)."""

    id: int
    tenant_id: int | None
    name: str
    platform: str
    vdoms: list[str] = []
    parallel_group: str | None = None


@router.get("/labels", response_model=list[SystemLabelOut])
def system_labels(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Bezeichnungen der Quellsysteme fuer die Doku-Ansichten. Kunden-Benutzer sehen nur die
    Systeme ihrer Regeln, NAT und Objekte, die VDOMs nur aus ihren Regeln - keine URL, keinen
    Agenten, keinen Zeitplan."""
    tid = tenant_scope(principal, tenant_id)
    query = select(SourceSystem).order_by(SourceSystem.name, SourceSystem.id)
    if tid is not None:
        query = query.where(SourceSystem.tenant_id == tid)
    view = own_view(session, principal, tid) if tid is not None else None
    if principal.is_customer and view is None:
        raise HTTPException(status_code=403, detail="tenant required")
    if view is None:
        sources = list(session.scalars(query))
        vdoms = _vdoms(session, [s.id for s in sources if s.platform == Platform.FORTIGATE])
    else:
        rules = select(Rule.source_system_id, Rule.vdom).where(Rule.id.in_(view.ids("rule")))
        nat = select(NatRule.source_system_id).where(NatRule.id.in_(view.ids("nat")))
        objs = select(DeviceObject.source_system_id).where(
            DeviceObject.id.in_(view.device_objects())
        )
        vdoms_of: dict[int, set[str]] = {}
        for sid, vdom in session.execute(rules.distinct()):
            vdoms_of.setdefault(sid, set()).update([vdom] if vdom else [])
        ids = set(vdoms_of) | set(session.scalars(nat)) | set(session.scalars(objs))
        sources = list(session.scalars(query.where(SourceSystem.id.in_(ids)))) if ids else []
        vdoms = {sid: sorted(v) for sid, v in vdoms_of.items()}
    return [
        SystemLabelOut(
            id=s.id,
            tenant_id=s.tenant_id,
            name=s.name,
            platform=s.platform.value,
            vdoms=vdoms.get(s.id, []),
            parallel_group=s.parallel_group,
        )
        for s in sources
    ]


@router.post("/{system_id}/check")
def check_system(
    system_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Erreichbarkeit eines zentral gelesenen Quellsystems vom Orchestrator aus: Name, TCP,
    bei NetBox `GET /api/status/` mit dem Lese-Token (nur lesend). Agent-Systeme prüft der
    Agent selbst (`run-agent.ps1 -Check`)."""
    s = session.get(SourceSystem, system_id)
    if s is None:
        raise HTTPException(status_code=404, detail="source system not found")
    tenant_scope(principal, s.tenant_id)
    if s.agent_id is not None:
        raise HTTPException(
            status_code=409, detail="read by an agent - check on the agent (run-agent.ps1 -Check)"
        )
    try:
        secrets = credentials.decrypt(s.credentials) if s.credentials else {}
    except credentials.CredentialsUnavailable:
        secrets = {}
    result = reachability.check(
        s.base_url,
        s.platform.value,
        token=secrets.get("token"),
        verify_tls=s.verify_tls,
    )
    return {"system_id": s.id, "name": s.name, "from": "orchestrator", **result.out()}


@router.post("", response_model=SystemOut, status_code=201)
def create_system(
    body: SystemIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
):
    """Quellsystem für einen Mandanten anlegen - Admin. Name je Mandant eindeutig (409)."""
    tenant_scope(principal, body.tenant_id)
    if session.get(Tenant, body.tenant_id) is None:
        raise HTTPException(status_code=422, detail="tenant not found")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if _name_taken(session, body.tenant_id, name, None):
        raise HTTPException(status_code=409, detail="source system already exists for tenant")
    _check_agent(session, body.agent_id, body.tenant_id)
    _check_site(session, body.site_id, body.tenant_id)
    src = SourceSystem(
        tenant_id=body.tenant_id,
        name=name,
        platform=body.platform,
        base_url=body.base_url.strip(),
        agent_id=body.agent_id,
        verify_tls=body.verify_tls,
        read_scope=_read_scope(body.platform, body.read_scope),
        site_id=body.site_id,
        access=_access(body.platform, body.access),
    )
    if src.platform == Platform.CISCO_SWITCH:
        if not src.read_scope:
            raise HTTPException(status_code=422, detail="switch addresses required")
        src.base_url = src.base_url or _switch_base_url(src.read_scope, src.access)
    _set_schedule(src, _schedule(body.schedule))
    _apply_credentials(src, _credential_changes(body))
    session.add(src)
    session.flush()
    _apply_ref_role(session, src, body.ref_role)
    session.flush()
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="source.created",
        subject=str(src.id),
        detail={
            "source_id": src.id,
            "tenant_id": src.tenant_id,
            "name": src.name,
            "platform": src.platform.value,
            "agent_id": src.agent_id,
            "credentials_set": bool(src.credentials),
            "ref_role": src.ref_role,
            "site_id": src.site_id,
            "schedule": src.schedule,
            **({"access": src.access} if src.access else {}),
        },
    )
    return _out(src, {}, {}, {})


@router.patch("/{system_id}", response_model=SystemOut)
def update_system(
    background: BackgroundTasks,
    system_id: int,
    body: SystemPatch,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    """Quellsystem ändern - Admin. Mandantenwechsel nur, wenn keine Daten eines anderen
    Mandanten daran hängen (sonst stimmten Regeln und Quellsystem nicht mehr überein)."""
    src = _get_scoped(session, principal, system_id)
    data = body.model_dump(exclude_unset=True)
    tenant_id = data.get("tenant_id", src.tenant_id)
    if "tenant_id" in data and tenant_id != src.tenant_id:
        if tenant_id is None:
            raise HTTPException(status_code=422, detail="tenant_id must not be null")
        tenant_scope(principal, tenant_id)
        if session.get(Tenant, tenant_id) is None:
            raise HTTPException(status_code=422, detail="tenant not found")
        foreign = or_(
            select(Rule.id)
            .where(Rule.source_system_id == src.id, Rule.tenant_id != tenant_id)
            .exists(),
            select(NatRule.id)
            .where(NatRule.source_system_id == src.id, NatRule.tenant_id != tenant_id)
            .exists(),
        )
        if session.scalar(select(foreign)):
            raise HTTPException(status_code=409, detail="source system has data of another tenant")
    name = (data.get("name") or src.name).strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if (name != src.name or tenant_id != src.tenant_id) and _name_taken(
        session, tenant_id, name, src.id
    ):
        raise HTTPException(status_code=409, detail="source system already exists for tenant")
    agent_id = data["agent_id"] if "agent_id" in data else src.agent_id
    _check_agent(session, agent_id, tenant_id)
    site_id = data["site_id"] if "site_id" in data else src.site_id
    if "site_id" in data:
        _check_site(session, site_id, tenant_id)
    elif site_id is not None and tenant_id != src.tenant_id:
        site_id = None  # Standort gehoert zum bisherigen Mandanten
    src.tenant_id = tenant_id
    src.name = name
    src.agent_id = agent_id
    src.site_id = site_id
    if data.get("base_url") is not None:
        src.base_url = data["base_url"].strip()
    if data.get("enabled") is not None:
        src.enabled = data["enabled"]
    if data.get("verify_tls") is not None:
        src.verify_tls = data["verify_tls"]
    access_changed = False  # Zugang gewechselt (ADR 0033): bisherige Zugangsdaten verwerfen
    if data.get("access") is not None:
        access = _access(src.platform, data["access"])
        access_changed = (access or "ssh") != (src.access or "ssh")
        src.access = access
        if access_changed:
            src.base_url = _switch_base_url(src.read_scope, src.access)
    if data.get("read_scope") is not None:
        src.read_scope = _read_scope(src.platform, data["read_scope"])
        if src.platform == Platform.CISCO_SWITCH:
            if not src.read_scope:
                raise HTTPException(status_code=422, detail="switch addresses required")
            src.base_url = _switch_base_url(src.read_scope, src.access)
            hosts = set(src.read_scope.split())  # Schluessel entfernter Switches vergessen
            src.ssh_host_keys = {h: k for h, k in (src.ssh_host_keys or {}).items()
                                 if h in hosts} or None  # fmt: skip
    forgotten: list[str] = []
    if data.get("forget_host_keys"):
        keys = dict(src.ssh_host_keys or {})
        forgotten = [h for h in data["forget_host_keys"] if keys.pop(h, None) is not None]
        src.ssh_host_keys = keys or None
    if data.get("ref_role") is not None:
        _apply_ref_role(session, src, data["ref_role"])
    if "schedule" in data:
        _set_schedule(src, _schedule(data["schedule"]))
    regroup = "parallel_group" in data and (data["parallel_group"] or None) != src.parallel_group
    if "parallel_group" in data:
        src.parallel_group = (data["parallel_group"] or "").strip() or None
    credentials_changed = _apply_credentials(src, _credential_changes(body), reset=access_changed)
    session.flush()
    if regroup:
        if src.parallel_group and src.tenant_id is not None:  # von Hand gesetzt: Ablehnung weg
            from app.store import parallel

            mates = session.scalars(
                select(SourceSystem.id).where(
                    SourceSystem.tenant_id == src.tenant_id,
                    SourceSystem.parallel_group == src.parallel_group,
                )
            )
            parallel.unreject(session, src.tenant_id, list(mates))
        _regroup(session, src.tenant_id, background, factory)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="source.updated",
        subject=str(src.id),
        detail={
            "source_id": src.id,
            "tenant_id": src.tenant_id,
            "name": src.name,
            "enabled": src.enabled,
            "agent_id": src.agent_id,
            "credentials_set": bool(src.credentials) if credentials_changed else None,
            "ref_role": src.ref_role,
            "site_id": src.site_id,
            "schedule": src.schedule,
            "parallel_group": src.parallel_group,
            **({"host_keys_forgotten": forgotten} if forgotten else {}),
            **({"access": src.access or "ssh"} if access_changed else {}),
        },
    )
    return _single_out(session, src)


def _regroup(session: Session, tenant_id: int | None, background: BackgroundTasks,
             factory: sessionmaker) -> None:  # fmt: skip
    """Verbund geaendert: Zwillinge im Regelindex neu (0.18.0); die Befunde des Mandanten und
    der Applikationen (Verbund = ein Hop, 0.59.0) seit 0.108.0 hinter der Antwort."""
    if tenant_id is None:
        return
    from app.store import parallel

    parallel.mark(session, tenant_id)
    app_refresh.schedule_regroup(background, factory, tenant_id)


class ParallelIn(BaseModel):
    tenant_id: int
    group: str = Field(min_length=1, max_length=100)
    system_ids: list[int]  # alle Firewalls des Verbunds; leer = Verbund aufloesen


@router.get("/parallel-suggestions")
def parallel_suggestions(
    tenant_id: int,
    include_rejected: bool = False,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Firewalls, die parallel aussehen (Session-Sync bzw. mindestens halb gleiche Policies)
    und noch nicht im selben Verbund sind; abgelehnte nur mit `include_rejected`."""
    from app.store import parallel

    tenant_scope(principal, tenant_id)
    return parallel.suggestions(session, tenant_id, include_rejected)


@router.get("/parallel")
def parallel_groups(
    tenant_id: int,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Verbuende paralleler Firewalls mit dem Vergleich (0.72.0): je Bereich (Policies,
    Objekte, Policy-Routen, Routen, NAT) gleich, abweichend und fehlend - die Abweichungen
    stehen als Befunde unter Findings → Parallele Firewalls. Mit Kunde (0.76.0) nur Verbuende
    mit seinen Kontexten (als geteilt markiert: der Vergleich gilt fuer die ganze Firewall)."""
    from app.store import customer_infra, parallel

    tenant_scope(principal, tenant_id)
    groups = list(parallel.compare(session, tenant_id).groups.values())
    infra = customer_infra.of(session, customer_view(session, principal, tenant_id, customer_id))
    if infra is None:
        return groups
    fws = {sid for sid, _ in infra.contexts}
    return [{**g, "shared": True} for g in groups if {m["id"] for m in g["members"]} & fws]


class ParallelRejectIn(BaseModel):
    tenant_id: int
    system_ids: list[int] = []  # diese Firewalls sind nicht parallel
    group: str | None = Field(default=None, max_length=100)  # bzw. alle eines Verbunds


def _reject_ids(session: Session, body: ParallelRejectIn) -> list[int]:
    ids = set(body.system_ids)
    if body.group:
        ids |= set(
            session.scalars(
                select(SourceSystem.id).where(
                    SourceSystem.tenant_id == body.tenant_id,
                    SourceSystem.parallel_group == body.group.strip(),
                )
            )
        )
    known = set(
        session.scalars(
            select(SourceSystem.id).where(
                SourceSystem.tenant_id == body.tenant_id, SourceSystem.id.in_(ids)
            )
        )
    )
    if known != ids or len(ids) < 2:
        raise HTTPException(
            status_code=422,
            detail=L(
                "mindestens zwei Firewalls des Mandanten", "at least two firewalls of the tenant"
            ),
        )
    return sorted(ids)


@router.post("/parallel-reject")
def reject_parallel(
    background: BackgroundTasks,
    body: ParallelRejectIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
) -> dict:
    """Vorschlag ablehnen bzw. Verbund aufloesen - Admin (0.19.0): diese Firewalls sind nicht
    parallel und werden nicht mehr vorgeschlagen."""
    from app.store import parallel

    tenant_scope(principal, body.tenant_id)
    ids = _reject_ids(session, body)
    new = parallel.reject(session, body.tenant_id, ids)
    _regroup(session, body.tenant_id, background, factory)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="source.parallel_rejected",
        subject=",".join(map(str, ids)),
        detail={"tenant_id": body.tenant_id, "source_ids": ids, "pairs": len(new)},
    )
    return {"source_ids": ids, "rejected_pairs": len(new)}


@router.post("/parallel-reject/undo")
def unreject_parallel(
    body: ParallelRejectIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Ablehnung aufheben - Admin: die Firewalls werden wieder vorgeschlagen."""
    from app.store import parallel

    tenant_scope(principal, body.tenant_id)
    ids = _reject_ids(session, body)
    gone = parallel.unreject(session, body.tenant_id, ids)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="source.parallel_unrejected",
        subject=",".join(map(str, ids)),
        detail={"tenant_id": body.tenant_id, "source_ids": ids, "pairs": gone},
    )
    return {"source_ids": ids, "removed_pairs": gone}


@router.post("/parallel-group", response_model=list[SystemOut])
def set_parallel_group(
    background: BackgroundTasks,
    body: ParallelIn,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
    factory: sessionmaker = Depends(get_session_factory),
):
    """Verbund paralleler Firewalls setzen - Admin. Die Regeln der Firewalls werden
    zusammengefasst, Abweichungen erscheinen als Findings (0.18.0)."""
    tenant_scope(principal, body.tenant_id)
    group = body.group.strip()
    members = list(
        session.scalars(
            select(SourceSystem).where(
                SourceSystem.tenant_id == body.tenant_id,
                or_(SourceSystem.id.in_(body.system_ids), SourceSystem.parallel_group == group),
            )
        )
    )
    if {s.id for s in members} < set(body.system_ids):
        raise HTTPException(status_code=422, detail="source system not in tenant")
    for s in members:
        s.parallel_group = group if s.id in body.system_ids else None
    session.flush()
    from app.store import parallel

    parallel.unreject(session, body.tenant_id, body.system_ids)  # bewusst gesetzt
    _regroup(session, body.tenant_id, background, factory)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="source.parallel_group",
        subject=group,
        detail={"tenant_id": body.tenant_id, "group": group, "source_ids": sorted(body.system_ids)},
    )
    return [_single_out(session, s) for s in members if s.id in body.system_ids]


@router.delete("/{system_id}", response_model=SystemDeleted)
def delete_system(
    system_id: int,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_session, scope="function"),
):
    """Quellsystem löschen - Admin. Seine Regeln und NAT-Einträge werden mit gelöscht."""
    src = _get_scoped(session, principal, system_id)
    name, tenant_id = src.name, src.tenant_id
    counts = deletion.delete_source_system(session, src)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="source.deleted",
        subject=str(system_id),
        detail={"source_id": system_id, "tenant_id": tenant_id, "name": name, **counts},
    )
    return SystemDeleted(id=system_id, **counts)
