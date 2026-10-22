"""Regel-Endpunkte (ADR 0004).

Liest Regeln aus dem Rule & Service Store:
- `GET /rules`         Liste mit Zonen + Zaehlern (Quell-/Ziel-/Service-Anzahl, nicht
                       aufgeloeste Objekte), Namen wie auf der Firewall, Adressfamilie und
                       IPv4/IPv6-Gegenstueck - aus dem Regelindex (`store.rule_index`).
- `GET /rules/{id}`    Detail inkl. aufgeloester Endpunkte (IP/CIDR/Name, Zone,
                       Match-Status) und kanonischer Services, dazu die Objekt-Verweise
                       wie auf der Firewall (`refs`: Name, Gruppen mit Mitgliedern, Werte
                       mit Abgleich gegen das IPAM, ADR 0010).
- `POST /rules/{id}/pair`, `DELETE /rules/pair-decisions/{id}`  IPv4/IPv6-Paar bestaetigen
                       bzw. ablehnen und die Entscheidung zuruecknehmen (operator, 0.12.0).
"""

import ipaddress
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.api.customer_filter import customer_view, own_view, visible
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import (
    ObjectClass,
    Rule,
    RuleAction,
    RuleDomain,
    RuleIndex,
    RulePairDecision,
    SourceSystem,
)
from app.schemas.rules import (
    EndpointOut,
    PairDecisionOut,
    RuleDetailOut,
    RuleOut,
    RuleSummaryOut,
)
from app.store import epgs as epg_store
from app.store import nat as nat_store
from app.store import objects as objects_store
from app.store import relevance, rule_index, virtual
from app.store import rules as rules_store

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("/summary", response_model=RuleSummaryOut)
def rules_summary(
    tenant_id: int | None = None,
    customer_id: int | None = None,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Kennzahlen aller Regeln des Mandanten (Firewall + ACI) fuer die Uebersicht; mit
    `customer_id` nur die des Kunden (ADR 0018)."""
    tenant_id = tenant_scope(principal, tenant_id)
    view = customer_view(session, principal, tenant_id, customer_id)
    only = view.ids("rule") if view else None
    return RuleSummaryOut(**rules_store.summary(session, tenant_id=tenant_id, only=only))


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
    net: str | None = Query(
        None, description="Regeln eines Subnetzes (Quelle/Ziel ueberschneidet)"
    ),
    net_any: bool = Query(True, description="bei `net` auch Regeln mit any"),
    source_system_id: int | None = None,
    domain: RuleDomain | None = None,
    src_zone: str | None = None,
    dst_zone: str | None = None,
    action: RuleAction | None = None,
    family: str | None = Query(None, pattern="^(v4|v6|dual)$"),
    vdom: str | None = Query(None, description="FortiGate-VDOM der Policy"),
    pair: str | None = Query(
        None,
        pattern="^(same|similar|confirmed|any|none)$",
        description="IPv4/IPv6-Paar: gleich, aehnlich (offen), bestaetigt, irgendeins, keins",
    ),
    twins: bool = Query(
        False, description="Zwillinge paralleler Firewalls einzeln zeigen (sonst zusammengefasst)"
    ),
    parallel: str | None = Query(
        None, pattern="^(differs|missing)$", description="Verbund: abweichend bzw. fehlt"
    ),
    routable: bool = Query(
        False, description="Suche nach einer IP: routingtechnisch unmoegliche Regeln ausblenden"
    ),
    customer_id: int | None = Query(None, description="nur Regeln dieses Kunden (ADR 0018)"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    """Regeln seitenweise; Gesamtzahl (nach Filtern und Suche) im Header `X-Total-Count`.

    `q` sucht in allem, was zur Regel gehoert (Gruppenmitglieder, Werte, Services, Zonen,
    EPG-Lage, NAT, IP im Netz, Netz/CIDR); `object`/`object_id`/`nat` filtern exakt, `net`
    listet die Regeln eines Subnetzes - alles ueber den Regelindex per SQL.

    Ist `q` genau eine IP (0.26.0), traegt jede Firewall-Regel die Relevanz fuer sie (Routing
    ihrer Firewall, `relevance`); `X-Impossible-Count` = Regeln, die routingtechnisch nicht
    moeglich sind - mit `routable=true` ausgeblendet."""
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "rules")
    rule_index.ensure(session, tenant_id)
    q, object, nat = (v.strip() if v else None for v in (q, object, nat))
    conds = rule_index.conditions(q, object, object_id, nat, net, net_any)
    view = customer_view(session, principal, tenant_id, customer_id)
    if view is not None:
        conds.append(Rule.id.in_(view.ids("rule")))
    if family:
        conds.append(Rule.ip_family == family)
    if vdom:
        conds.append(Rule.vdom == vdom.strip())
    if pair:
        paired = select(RuleIndex.rule_id).where(
            RuleIndex.rule_id == Rule.id, RuleIndex.pair_rule_id.is_not(None)
        )
        if pair in ("same", "similar", "confirmed"):
            paired = paired.where(RuleIndex.pair_match == pair)
        conds.append(~exists(paired) if pair == "none" else exists(paired))
    # Parallele Firewalls (0.18.0): je gleicher Policy nur die fuehrende Regel - ausser mit
    # Filter auf ein Quellsystem (dann dessen Regeln) bzw. auf Wunsch
    if source_system_id is None and not twins:
        conds.append(rules_store.not_twin())
    if parallel:
        key = "differs" if parallel == "differs" else "missing"
        flagged = select(RuleIndex.rule_id, RuleIndex.parallel).where(
            RuleIndex.tenant_id == tenant_id, RuleIndex.parallel.is_not(None)
        )
        ids_ = [
            rid
            for rid, info in session.execute(flagged)
            if info and (info.get(key) or (key == "differs" and info.get("fields")))
        ]
        conds.append(Rule.id.in_(ids_))

    def scoped(stmt):
        stmt = rules_store.filtered(
            stmt, tenant_id, source_system_id, domain, src_zone, dst_zone, action
        )
        return stmt.where(*conds)

    ip = _single_ip(q)
    rel: dict[int, dict] = {}
    if ip is not None:
        rel = relevance.for_ip(
            session, tenant_id, list(session.scalars(scoped(select(Rule.id)))), ip
        )
        impossible = [rid for rid, x in rel.items() if x["state"] == relevance.IMPOSSIBLE]
        response.headers["X-Impossible-Count"] = str(len(impossible))
        if routable and impossible:
            conds.append(Rule.id.not_in(impossible))
    total = session.scalar(scoped(select(func.count(Rule.id)))) or 0
    ids = list(
        session.scalars(
            scoped(select(Rule.id)).order_by(*rules_store.ORDER).offset(offset).limit(limit)
        )
    )
    response.headers["X-Total-Count"] = str(total)
    out = rule_index.outs(session, rule_index.load_rules(session, ids))
    for r in out:
        r.relevance = rel.get(r.id)
    return out


def _single_ip(q: str | None) -> str | None:
    """Die Suche ist genau eine IP (ohne Netz/Bereich)?"""
    tokens = (q or "").split()
    if len(tokens) != 1:
        return None
    try:
        return str(ipaddress.ip_address(tokens[0]))
    except ValueError:
        return None


@router.get("/{rule_id}", response_model=RuleDetailOut)
def get_rule(
    rule_id: int,
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
):
    r = rules_store.get_rule(session, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="rule not found")
    tenant_scope(principal, r.tenant_id)  # Mandanten-Zugriff pruefen (403 bei fremd)
    visible(session, own_view(session, principal, r.tenant_id), "rule", r.id)  # ADR 0018
    reads.maybe_audit_read(session, principal, r.tenant_id, "rules")

    obj_ids = {e.network_object_id for e in r.endpoints if e.network_object_id}
    objs = rules_store.objects_by_ids(session, obj_ids)
    zones = rules_store.zone_names(session, {o.zone_id for o in objs.values() if o.zone_id})
    nat_map = nat_store.nat_by_name(session, r.tenant_id, r.source_system_id, r.vdom)
    epgs = epg_store.by_ids(session, {e.epg_id for e in r.endpoints if e.epg_id})
    epg_info = epg_store.details(session, r.tenant_id, list(epgs.values()))
    (base,) = rule_index.outs(session, [r])
    raw = r.raw if isinstance(r.raw, dict) else {}
    vms = virtual.Lookup(
        session, r.tenant_id, {o.value.split("/")[0] for o in objs.values() if o.value}
    )

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
                vm=vms.info(obj.value) if obj and obj.value else None,
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
            r.vdom,
        ),
        pair_decisions=_decisions_of(session, r),
        twins=_twins_of(session, r),
    )


def _twins_of(session: Session, r: Rule) -> list[dict]:
    """Gleiche Regeln der parallelen Firewalls (Verbund, 0.18.0) - in Listen zusammengefasst."""
    rows = session.execute(
        select(Rule.id, Rule.source_system_id, SourceSystem.name)
        .join(RuleIndex, RuleIndex.rule_id == Rule.id)
        .join(SourceSystem, SourceSystem.id == Rule.source_system_id)
        .where(RuleIndex.twin_of == r.id)
        .order_by(SourceSystem.name)
    )
    return [{"rule_id": rid, "source_system_id": sid, "source": name} for rid, sid, name in rows]


# -- IPv4/IPv6-Paare von Hand entscheiden (0.12.0) ---------------------------------


def _in_vdom(column, vdom: str | None):
    """Entscheidung im VDOM der Regel - oder ohne VDOM (vor 0.15.0, gilt fuer jeden VDOM)."""
    return column.is_(None) if vdom is None else or_(column == vdom, column.is_(None))


def _decisions_of(session: Session, r: Rule) -> list[PairDecisionOut]:
    if r.ip_family not in ("v4", "v6") or r.source_system_id is None:
        return []
    own = (
        RulePairDecision.v4_external_id if r.ip_family == "v4" else RulePairDecision.v6_external_id
    )
    out = []
    for d in session.scalars(
        select(RulePairDecision).where(
            RulePairDecision.source_system_id == r.source_system_id,
            own == r.external_id,
            _in_vdom(RulePairDecision.vdom, r.vdom),
        )
    ):
        other = d.v6_external_id if r.ip_family == "v4" else d.v4_external_id
        partner = session.scalars(
            select(Rule).where(
                Rule.source_system_id == r.source_system_id,
                Rule.vdom.is_(None) if r.vdom is None else Rule.vdom == r.vdom,
                Rule.external_id == other,
                Rule.ip_family != r.ip_family,
            )
        ).first()
        out.append(
            PairDecisionOut(
                id=d.id,
                decision=d.decision,
                partner_id=partner.id if partner else None,
                partner_name=(partner.name or f"#{partner.external_id}") if partner else None,
                partner_external_id=other,
                decided_by=d.decided_by,
                decided_at=d.decided_at,
            )
        )
    return out


class PairDecisionIn(BaseModel):
    partner_id: int
    decision: Literal["confirmed", "rejected"]


@router.post("/{rule_id}/pair", status_code=204)
def decide_pair(
    rule_id: int,
    body: PairDecisionIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    """IPv4/IPv6-Paar bestaetigen bzw. ablehnen; gilt sofort und fuer alle neuen Laeufe."""
    a, b = session.get(Rule, rule_id), session.get(Rule, body.partner_id)
    if a is None or b is None:
        raise HTTPException(status_code=404, detail="rule not found")
    tenant_scope(principal, a.tenant_id)
    if (
        a.domain is not RuleDomain.FIREWALL
        or b.tenant_id != a.tenant_id
        or b.source_system_id != a.source_system_id
        or b.vdom != a.vdom
        or a.source_system_id is None
        or {a.ip_family, b.ip_family} != {"v4", "v6"}
    ):
        raise HTTPException(
            status_code=422,
            detail="pair = one IPv4 and one IPv6 rule of the same firewall and VDOM",
        )
    v4, v6 = (a, b) if a.ip_family == "v4" else (b, a)
    # eine Entscheidung ohne VDOM (vor 0.15.0) wird zu einer im VDOM des Paares
    decision = session.scalars(
        select(RulePairDecision)
        .where(
            RulePairDecision.source_system_id == a.source_system_id,
            _in_vdom(RulePairDecision.vdom, a.vdom),
            RulePairDecision.v4_external_id == v4.external_id,
            RulePairDecision.v6_external_id == v6.external_id,
        )
        .order_by(RulePairDecision.vdom.is_(None))
    ).first()
    if decision is None:
        decision = RulePairDecision(
            tenant_id=a.tenant_id,
            source_system_id=a.source_system_id,
            v4_external_id=v4.external_id,
            v6_external_id=v6.external_id,
        )
        session.add(decision)
    decision.vdom = a.vdom
    decision.decision = body.decision
    decision.decided_by = principal.subject
    decision.decided_at = datetime.now(UTC)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="rule_pair.decided",
        subject=f"{v4.id}/{v6.id}",
        detail={
            "source_system_id": a.source_system_id,
            "vdom": a.vdom,
            "decision": body.decision,
        },
    )
    rows = {
        i.rule_id: i
        for i in session.scalars(select(RuleIndex).where(RuleIndex.rule_id.in_([a.id, b.id])))
    }
    together = all(rows.get(x.id) and rows[x.id].pair_rule_id == y.id for x, y in ((a, b), (b, a)))
    if together:  # schon als Paar markiert: nur die Markierung aendern (schnell)
        for row in rows.values():
            if body.decision == "confirmed":
                row.pair_match = "confirmed"
            else:
                row.pair_rule_id = row.pair_match = row.pair_diff = None
        session.flush()
    else:  # anderes Paar betroffen: Paare des Quellsystems neu bilden
        rule_index.rebuild(session, a.tenant_id, a.source_system_id)


@router.delete("/pair-decisions/{decision_id}", status_code=204)
def reset_pair(
    decision_id: int,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    """Entscheidung zuruecknehmen: das Paar wird wieder automatisch gebildet."""
    d = session.get(RulePairDecision, decision_id)
    if d is None:
        raise HTTPException(status_code=404, detail="decision not found")
    tenant_scope(principal, d.tenant_id)
    sid, tid = d.source_system_id, d.tenant_id
    session.delete(d)
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action="rule_pair.reset",
        subject=str(decision_id),
        detail={"source_system_id": sid},
    )
    session.flush()
    rule_index.rebuild(session, tid, sid)
