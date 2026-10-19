"""Löschen von Quellsystemen und Mandanten inkl. ihrer gelesenen Daten.

Die Reihenfolge folgt den Fremdschlüsseln (Regeln vor Zonen/Objekten/Läufen, EPG-Kinder vor
EPGs, Objekte vor Standorten). Gemeinsame Daten bleiben: kanonische Services
(`service_object`) gehören keinem Mandanten, das Audit-Log ist unveränderlich. Rückgabe
sind die Anzahlen je Art - fürs Audit und die Anzeige im UI.
"""

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import (
    Agent,
    CollectorRun,
    Epg,
    EpgEndpoint,
    EpgSubnet,
    Finding,
    NatRule,
    NetworkObject,
    Rule,
    RuleEndpoint,
    Site,
    SourceSystem,
    SubjectKind,
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
    """Findings zu Objekten, die keine Regel des Mandanten mehr verwendet (abgeleiteter
    Zustand - sonst blieben Befunde gelöschter Regeln stehen)."""
    still_used = (
        select(NetworkObject.name)
        .join(RuleEndpoint, RuleEndpoint.network_object_id == NetworkObject.id)
        .where(NetworkObject.tenant_id == tenant_id)
    )
    return _count(
        session,
        delete(Finding).where(
            Finding.tenant_id == tenant_id,
            Finding.subject_kind == SubjectKind.NETWORK_OBJECT,
            Finding.subject_id.not_in(still_used),
        ),
    )


def delete_source_system(session: Session, source: SourceSystem) -> dict[str, int]:
    """Quellsystem mit seinen Regeln, NAT-Einträgen und den dadurch verwaisten Findings
    löschen; Läufe bleiben als Historie."""
    counts = {"rules": _delete_rules(session, Rule.source_system_id == source.id)}
    counts["nat_rules"] = _count(
        session, delete(NatRule).where(NatRule.source_system_id == source.id)
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
    Standorte, Quellsysteme, Läufe, Agenten). Agenten werden gelöscht statt entkoppelt -
    ein Agent ohne Mandant dürfte sonst Läufe aller Mandanten abholen."""
    tid = tenant.id
    counts = {"rules": _delete_rules(session, Rule.tenant_id == tid)}
    counts["nat_rules"] = _count(session, delete(NatRule).where(NatRule.tenant_id == tid))
    counts["findings"] = _count(session, delete(Finding).where(Finding.tenant_id == tid))

    epg_ids = select(Epg.id).where(Epg.tenant_id == tid)
    session.execute(delete(EpgSubnet).where(EpgSubnet.epg_id.in_(epg_ids)))
    session.execute(delete(EpgEndpoint).where(EpgEndpoint.epg_id.in_(epg_ids)))
    counts["epgs"] = _count(session, delete(Epg).where(Epg.tenant_id == tid))

    counts["objects"] = _count(session, delete(NetworkObject).where(NetworkObject.tenant_id == tid))
    counts["zones"] = _count(session, delete(Zone).where(Zone.tenant_id == tid))
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
    counts["agents"] = _count(session, delete(Agent).where(Agent.tenant_id == tid))
    session.delete(tenant)
    session.flush()
    return counts
