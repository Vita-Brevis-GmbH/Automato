"""Regel-Endpunkte (ADR 0004).

Liest Regeln aus dem Rule & Service Store:
- `GET /rules`         Liste mit Zonen + Zaehlern (Quell-/Ziel-/Service-Anzahl, nicht
                       aufgeloeste Objekte), Namen wie auf der Firewall, Adressfamilie und
                       IPv4/IPv6-Gegenstueck - aus dem Regelindex (`store.rule_index`).
- `GET /rules/{id}`    Detail inkl. aufgeloester Endpunkte (IP/CIDR/Name, Zone,
                       Match-Status) und kanonischer Services, dazu die Objekt-Verweise
                       wie auf der Firewall (`refs`: Name, Gruppen mit Mitgliedern, Werte
                       mit Abgleich gegen das IPAM, ADR 0010).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import ObjectClass, Rule, RuleAction, RuleDomain
from app.schemas.rules import EndpointOut, RuleDetailOut, RuleOut, RuleSummaryOut
from app.store import epgs as epg_store
from app.store import nat as nat_store
from app.store import objects as objects_store
from app.store import rule_index
from app.store import rules as rules_store

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("/summary", response_model=RuleSummaryOut)
def rules_summary(
    tenant_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    """Kennzahlen aller Regeln des Mandanten (Firewall + ACI) fuer die Uebersicht."""
    tenant_id = tenant_scope(principal, tenant_id)
    return RuleSummaryOut(**rules_store.summary(session, tenant_id=tenant_id))


@router.get("", response_model=list[RuleOut])
def list_rules(
    response: Response,
    tenant_id: int | None = None,
    q: str | None = None,
    object: str | None = Query(None, description="Regeln, die dieses Objekt verwenden"),  # noqa: A002
    object_id: int | None = Query(
        None, description="Regeln mit diesem Objekt (auch ueber Gruppen)"
    ),
    nat: str | None = Query(None, description="Regeln mit diesem VIP bzw. SNAT-Pool"),
    source_system_id: int | None = None,
    domain: RuleDomain | None = None,
    src_zone: str | None = None,
    dst_zone: str | None = None,
    action: RuleAction | None = None,
    family: str | None = Query(None, pattern="^(v4|v6|dual)$"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    """Regeln seitenweise; Gesamtzahl (nach Filtern und Suche) im Header `X-Total-Count`.

    `q` sucht in allem, was zur Regel gehoert (Gruppenmitglieder, Werte, Services, Zonen,
    EPG-Lage, NAT, IP im Netz); `object`/`object_id`/`nat` filtern exakt - alles ueber den
    Regelindex per SQL, auch bei grossen Regelwerken."""
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "rules")
    rule_index.ensure(session, tenant_id)
    q, object, nat = (v.strip() if v else None for v in (q, object, nat))
    conds = rule_index.conditions(q, object, object_id, nat)
    if family:
        conds.append(Rule.ip_family == family)

    def scoped(stmt):
        stmt = rules_store.filtered(
            stmt, tenant_id, source_system_id, domain, src_zone, dst_zone, action
        )
        return stmt.where(*conds)

    total = session.scalar(scoped(select(func.count(Rule.id)))) or 0
    ids = list(
        session.scalars(
            scoped(select(Rule.id)).order_by(*rules_store.ORDER).offset(offset).limit(limit)
        )
    )
    response.headers["X-Total-Count"] = str(total)
    return rule_index.outs(session, rule_index.load_rules(session, ids))


@router.get("/{rule_id}", response_model=RuleDetailOut)
def get_rule(
    rule_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    r = rules_store.get_rule(session, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="rule not found")
    tenant_scope(principal, r.tenant_id)  # Mandanten-Zugriff pruefen (403 bei fremd)
    reads.maybe_audit_read(session, principal, r.tenant_id, "rules")

    obj_ids = {e.network_object_id for e in r.endpoints if e.network_object_id}
    objs = rules_store.objects_by_ids(session, obj_ids)
    zones = rules_store.zone_names(session, {o.zone_id for o in objs.values() if o.zone_id})
    nat_map = nat_store.nat_by_name(session, r.tenant_id)
    epgs = epg_store.by_ids(session, {e.epg_id for e in r.endpoints if e.epg_id})
    epg_info = epg_store.details(session, r.tenant_id, list(epgs.values()))
    (base,) = rule_index.outs(session, [r])
    raw = r.raw if isinstance(r.raw, dict) else {}

    endpoints: list[EndpointOut] = []
    for e in r.endpoints:
        obj = objs.get(e.network_object_id) if e.network_object_id else None
        nat = nat_map.get(obj.name) if obj and obj.object_class is ObjectClass.VIP else None
        epg = epg_info.get(e.epg_id) if e.epg_id else None
        endpoints.append(
            EndpointOut(
                role=e.role.value,
                kind=e.kind.value,
                name=obj.name if obj else None,
                value=(obj.value if obj and obj.value else e.inline_value),
                match_status=obj.match_status.value if obj else None,
                zone=zones.get(obj.zone_id) if obj and obj.zone_id else None,
                object_class=obj.object_class.value if obj else None,
                fqdn=obj.fqdn if obj else None,
                nat_target=nat_store.nat_target(nat) if nat else None,
                epg=epg,
            )
        )

    rule_nat = base.nat
    return RuleDetailOut(
        **base.model_dump(),
        external_id=r.external_id,
        sequence=r.sequence,
        refs=objects_store.rule_refs(session, r),
        endpoints=endpoints,
        services=[s.canonical_name for s in r.services],
        protections=r.protections or [],
        comment=str(raw.get("comments") or raw.get("comment") or "") or None,
        nat_entries=nat_store.briefs(
            session,
            r.tenant_id,
            r.source_system_id,
            [*rule_nat.pools, *rule_nat.vips] if rule_nat else [],
        ),
    )
