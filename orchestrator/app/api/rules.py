"""Regel-Endpunkte (ADR 0004).

Liest Regeln aus dem Rule & Service Store:
- `GET /rules`         Liste mit Zonen + Zaehlern (Quell-/Ziel-/Service-Anzahl, nicht
                       aufgeloeste Objekte) und Anzeigenamen-Fallback.
- `GET /rules/{id}`    Detail inkl. aufgeloester Endpunkte (IP/CIDR/Name, Zone,
                       Match-Status) und kanonischer Services.
Filter nach System/Netzwerk/Zone folgen mit dem UI-Ausbau (M9).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import EndpointRole, MatchStatus, ObjectClass
from app.schemas.rules import EndpointOut, RuleDetailOut, RuleOut
from app.store import nat as nat_store
from app.store import rules as rules_store

router = APIRouter(prefix="/rules", tags=["rules"])


def _display_name(rule) -> str:
    return rule.name or f"#{rule.external_id}"


def _is_unmatched(obj) -> bool:
    return obj is not None and obj.match_status is MatchStatus.UNMATCHED


def _count_unmatched(endpoints, objs) -> int:
    return sum(1 for e in endpoints if _is_unmatched(objs.get(e.network_object_id)))


def _rule_matches_q(rule, objs, ql: str) -> bool:
    if ql in rule.name.lower() or ql in (rule.external_id or "").lower():
        return True
    for e in rule.endpoints:
        obj = objs.get(e.network_object_id) if e.network_object_id else None
        for s in ((obj.name if obj else None), (obj.value if obj else None), e.inline_value):
            if s and ql in s.lower():
                return True
    return False


@router.get("", response_model=list[RuleOut])
def list_rules(
    tenant_id: int | None = None,
    q: str | None = None,
    session: Session = Depends(get_session),
):
    rules = rules_store.list_rules(session, tenant_id=tenant_id)
    obj_ids = {e.network_object_id for r in rules for e in r.endpoints if e.network_object_id}
    objs = rules_store.objects_by_ids(session, obj_ids)
    if q:
        ql = q.strip().lower()
        rules = [r for r in rules if _rule_matches_q(r, objs, ql)]
    zone_ids = {r.src_zone_id for r in rules} | {r.dst_zone_id for r in rules}
    zone_ids |= {o.zone_id for o in objs.values() if o.zone_id}
    zones = rules_store.zone_names(session, zone_ids)

    out: list[RuleOut] = []
    for r in rules:
        out.append(
            RuleOut(
                id=r.id,
                tenant_id=r.tenant_id,
                source_system_id=r.source_system_id,
                domain=r.domain.value,
                name=_display_name(r),
                action=r.action.value,
                section_label=r.section_label,
                src_zone=zones.get(r.src_zone_id),
                dst_zone=zones.get(r.dst_zone_id),
                num_src=sum(1 for e in r.endpoints if e.role is EndpointRole.SRC),
                num_dst=sum(1 for e in r.endpoints if e.role is EndpointRole.DST),
                num_services=len(r.services),
                num_unmatched=_count_unmatched(r.endpoints, objs),
            )
        )
    return out


@router.get("/{rule_id}", response_model=RuleDetailOut)
def get_rule(rule_id: int, session: Session = Depends(get_session)):
    r = rules_store.get_rule(session, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="rule not found")

    obj_ids = {e.network_object_id for e in r.endpoints if e.network_object_id}
    objs = rules_store.objects_by_ids(session, obj_ids)
    zone_ids = {r.src_zone_id, r.dst_zone_id} | {o.zone_id for o in objs.values() if o.zone_id}
    zones = rules_store.zone_names(session, zone_ids)
    nat_map = nat_store.nat_by_name(session, r.tenant_id)

    endpoints: list[EndpointOut] = []
    for e in r.endpoints:
        obj = objs.get(e.network_object_id) if e.network_object_id else None
        nat = nat_map.get(obj.name) if obj and obj.object_class is ObjectClass.VIP else None
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
            )
        )

    return RuleDetailOut(
        id=r.id,
        tenant_id=r.tenant_id,
        source_system_id=r.source_system_id,
        domain=r.domain.value,
        name=_display_name(r),
        action=r.action.value,
        section_label=r.section_label,
        src_zone=zones.get(r.src_zone_id),
        dst_zone=zones.get(r.dst_zone_id),
        num_src=sum(1 for e in r.endpoints if e.role is EndpointRole.SRC),
        num_dst=sum(1 for e in r.endpoints if e.role is EndpointRole.DST),
        num_services=len(r.services),
        num_unmatched=_count_unmatched(r.endpoints, objs),
        external_id=r.external_id,
        sequence=r.sequence,
        endpoints=endpoints,
        services=[s.canonical_name for s in r.services],
    )
