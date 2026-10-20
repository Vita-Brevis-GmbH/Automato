"""Löschen von Quellsystemen und Mandanten inkl. ihrer gelesenen Daten.

Die Reihenfolge folgt den Fremdschlüsseln (Regeln vor Zonen/Objekten/Läufen, EPG-Kinder vor
EPGs, Objekte vor Standorten). Gemeinsame Daten bleiben: kanonische Services
(`service_object`) gehören keinem Mandanten, das Audit-Log ist unveränderlich. Rückgabe
sind die Anzahlen je Art - fürs Audit und die Anzeige im UI.
"""

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import (
    INVENTORY_MODELS,
    Agent,
    Application,
    ApplicationMember,
    CollectorRun,
    DeviceObject,
    Epg,
    EpgEndpoint,
    EpgSubnet,
    Finding,
    NatRule,
    NetworkObject,
    ReferenceRecordRow,
    Rule,
    RuleEndpoint,
    Site,
    SiteLink,
    SourceSystem,
    SyncPlan,
    Tenant,
    Zone,
)


def _delete_rules(session: Session, condition) -> int:
    # ORM-Delete kaskadiert Endpunkte und Service-Zuordnungen (wie beim Ingest)
    rules = list(session.scalars(select(Rule).where(condition)))
    for rule in rules:
        session.delete(rule)
    session.flush()
    return len(rules)


def _count(session: Session, stmt) -> int:
    return session.execute(stmt).rowcount or 0


def source_usage(session: Session, source_id: int) -> dict[str, int]:
    """Wie viele gelesene Daten hängen an einem Quellsystem (für die Lösch-Rückfrage)."""
    rules = session.scalars(select(Rule.id).where(Rule.source_system_id == source_id)).all()
    nat = session.scalars(select(NatRule.id).where(NatRule.source_system_id == source_id)).all()
    return {"rules": len(rules), "nat_rules": len(nat)}


def _delete_stale_findings(session: Session, tenant_id: int) -> int:
    """Findings neu berechnen (abgeleiteter Zustand) - Befunde gelöschter Daten fallen weg."""
    from app.store import reconcile  # spät importiert (reconcile importiert Modelle/Stores)

    before = _count_findings(session, tenant_id)
    reconcile.reconcile_tenant(session, tenant_id)
    return max(before - _count_findings(session, tenant_id), 0)


def _count_findings(session: Session, tenant_id: int) -> int:
    return len(session.scalars(select(Finding.id).where(Finding.tenant_id == tenant_id)).all())


def _delete_source_epgs(session: Session, source_id: int) -> int:
    """EPGs eines APICs, auf die keine Regel mehr verweist; die übrigen bleiben ohne Quelle."""
    referenced = select(RuleEndpoint.epg_id).where(RuleEndpoint.epg_id.is_not(None))
    drop = list(
        session.scalars(
            select(Epg.id).where(Epg.source_system_id == source_id, Epg.id.not_in(referenced))
        )
    )
    if drop:
        session.execute(delete(EpgSubnet).where(EpgSubnet.epg_id.in_(drop)))
        session.execute(delete(EpgEndpoint).where(EpgEndpoint.epg_id.in_(drop)))
        session.execute(delete(Epg).where(Epg.id.in_(drop)))
    session.execute(
        update(Epg).where(Epg.source_system_id == source_id).values(source_system_id=None)
    )
    return len(drop)


def delete_source_system(session: Session, source: SourceSystem) -> dict[str, int]:
    """Quellsystem mit seinen Regeln, NAT-Einträgen und den dadurch verwaisten Findings
    löschen; Läufe bleiben als Historie."""
    session.execute(delete(SyncPlan).where(SyncPlan.source_system_id == source.id))
    counts = {"rules": _delete_rules(session, Rule.source_system_id == source.id)}
    counts["nat_rules"] = _count(
        session, delete(NatRule).where(NatRule.source_system_id == source.id)
    )
    session.execute(delete(DeviceObject).where(DeviceObject.source_system_id == source.id))
    for model in INVENTORY_MODELS:
        session.execute(delete(model).where(model.source_system_id == source.id))
    counts["epgs"] = _delete_source_epgs(session, source.id)
    source.reference_run_id = None
    session.flush()
    counts["references"] = _count(
        session, delete(ReferenceRecordRow).where(ReferenceRecordRow.source_system_id == source.id)
    )
    counts["findings"] = (
        _delete_stale_findings(session, source.tenant_id) if source.tenant_id is not None else 0
    )
    session.execute(
        update(CollectorRun)
        .where(CollectorRun.source_system_id == source.id)
        .values(source_system_id=None)
    )
    session.delete(source)
    session.flush()
    return counts


def delete_tenant(session: Session, tenant: Tenant) -> dict[str, int]:
    """Mandant mit allen seinen Daten löschen (Regeln, NAT, Objekte, Zonen, EPGs, Findings,
    Applikationen, Standorte, Quellsysteme, Läufe, Agenten). Agenten werden gelöscht statt
    entkoppelt - ein Agent ohne Mandant dürfte sonst Läufe aller Mandanten abholen."""
    tid = tenant.id
    session.execute(delete(SyncPlan).where(SyncPlan.tenant_id == tid))
    counts = {"rules": _delete_rules(session, Rule.tenant_id == tid)}
    counts["nat_rules"] = _count(session, delete(NatRule).where(NatRule.tenant_id == tid))
    counts["findings"] = _count(session, delete(Finding).where(Finding.tenant_id == tid))
    session.execute(delete(DeviceObject).where(DeviceObject.tenant_id == tid))
    for model in INVENTORY_MODELS:
        session.execute(delete(model).where(model.tenant_id == tid))
    session.execute(
        update(SourceSystem).where(SourceSystem.tenant_id == tid).values(reference_run_id=None)
    )
    counts["references"] = _count(
        session, delete(ReferenceRecordRow).where(ReferenceRecordRow.tenant_id == tid)
    )

    epg_ids = select(Epg.id).where(Epg.tenant_id == tid)
    session.execute(delete(EpgSubnet).where(EpgSubnet.epg_id.in_(epg_ids)))
    session.execute(delete(EpgEndpoint).where(EpgEndpoint.epg_id.in_(epg_ids)))
    counts["epgs"] = _count(session, delete(Epg).where(Epg.tenant_id == tid))

    counts["objects"] = _count(session, delete(NetworkObject).where(NetworkObject.tenant_id == tid))
    counts["zones"] = _count(session, delete(Zone).where(Zone.tenant_id == tid))
    app_ids = select(Application.id).where(Application.tenant_id == tid)
    session.execute(delete(ApplicationMember).where(ApplicationMember.application_id.in_(app_ids)))
    counts["applications"] = _count(
        session, delete(Application).where(Application.tenant_id == tid)
    )
    session.execute(update(SourceSystem).where(SourceSystem.tenant_id == tid).values(site_id=None))
    site_ids = select(Site.id).where(Site.tenant_id == tid)
    session.execute(
        delete(SiteLink).where(
            SiteLink.virtual_id.in_(site_ids) | SiteLink.physical_id.in_(site_ids)
        )
    )
    counts["sites"] = _count(session, delete(Site).where(Site.tenant_id == tid))

    source_ids = select(SourceSystem.id).where(SourceSystem.tenant_id == tid)
    session.execute(
        update(CollectorRun)
        .where(CollectorRun.source_system_id.in_(source_ids))
        .values(source_system_id=None)
    )
    counts["source_systems"] = _count(
        session, delete(SourceSystem).where(SourceSystem.tenant_id == tid)
    )
    counts["runs"] = _count(session, delete(CollectorRun).where(CollectorRun.tenant_id == tid))
    agent_ids = select(Agent.id).where(Agent.tenant_id == tid)
    session.execute(
        update(SourceSystem).where(SourceSystem.agent_id.in_(agent_ids)).values(agent_id=None)
    )
    session.execute(
        update(CollectorRun).where(CollectorRun.agent_id.in_(agent_ids)).values(agent_id=None)
    )
    counts["agents"] = _count(session, delete(Agent).where(Agent.tenant_id == tid))
    session.delete(tenant)
    session.flush()
    return counts
