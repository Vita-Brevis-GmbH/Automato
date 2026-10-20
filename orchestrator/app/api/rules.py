"""Regel-Endpunkte (ADR 0004).

Liest Regeln aus dem Rule & Service Store:
- `GET /rules`         Liste mit Zonen + Zaehlern (Quell-/Ziel-/Service-Anzahl, nicht
                       aufgeloeste Objekte) und Anzeigenamen-Fallback.
- `GET /rules/{id}`    Detail inkl. aufgeloester Endpunkte (IP/CIDR/Name, Zone,
                       Match-Status) und kanonischer Services, dazu die Objekt-Verweise
                       wie auf der Firewall (`refs`: Name, Gruppen mit Mitgliedern, Werte
                       mit Abgleich gegen das IPAM, ADR 0010).
Filter nach System/Netzwerk/Zone folgen mit dem UI-Ausbau (M9).
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.audit import reads
from app.auth.deps import require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.models import EndpointRole, MatchStatus, ObjectClass, RefRole, RuleAction, RuleDomain
from app.schemas.rules import (
    AciContractOut,
    EndpointOut,
    RuleDetailOut,
    RuleNatOut,
    RuleOut,
    RuleSummaryOut,
)
from app.store import epgs as epg_store
from app.store import nat as nat_store
from app.store import objects as objects_store
from app.store import rule_search
from app.store import rules as rules_store

router = APIRouter(prefix="/rules", tags=["rules"])


def _display_name(rule) -> str:
    return rule.name or f"#{rule.external_id}"


def _is_unmatched(obj) -> bool:
    return obj is not None and obj.match_status is MatchStatus.UNMATCHED


def _count_unmatched(endpoints, objs) -> int:
    return sum(1 for e in endpoints if _is_unmatched(objs.get(e.network_object_id)))


def _endpoint_label(e, objs, epgs=None, home_tenant: str | None = None) -> str:
    """Anzeigename eines Endpunkts: Objektname (Konvention) bevorzugt, ACI-EPG als AP/EPG,
    sonst Wert/inline."""
    obj = objs.get(e.network_object_id) if e.network_object_id else None
    if obj:
        return obj.name or obj.value or "?"
    epg = (epgs or {}).get(e.epg_id) if e.epg_id else None
    if epg is not None:
        return epg_store.label(epg, home_tenant)
    return e.inline_value or e.kind.value


def _ref_names(rule, role: RefRole) -> list[str]:
    return [ref.name for ref in rule.refs if ref.role is role]


def _labels(rule, role: RefRole, endpoint_role: EndpointRole, objs, epgs=None) -> list[str]:
    """Objektnamen wie auf der Firewall; ohne Verweise (ACI, Altbestand) die Endpunkte."""
    names = _ref_names(rule, role)
    if names:
        return names
    home = _ACI_TENANT.search(rule.contract_dn or (rule.raw or {}).get("dn") or "")
    home_tenant = home.group(1) if home else None
    return [
        _endpoint_label(e, objs, epgs, home_tenant)
        for e in rule.endpoints
        if e.role is endpoint_role
    ]


_ACI_TENANT = re.compile(r"(?:^|/)tn-([^/]+)")


def _aci(rule) -> AciContractOut | None:
    """Contract/Subject/Tenant/Scope eines ACI-Contracts (Spalten, sonst aus dem Rohdatensatz)."""
    if rule.domain is not RuleDomain.ACI_CONTRACT:
        return None
    raw = rule.raw if isinstance(rule.raw, dict) else {}
    dn = rule.contract_dn or raw.get("dn") or ""
    subject = rule.subject
    if subject is None and dn and rule.external_id.startswith(f"{dn}/"):
        subject = rule.external_id[len(dn) + 1 :]
    tenant = _ACI_TENANT.search(dn)
    return AciContractOut(
        tenant=tenant.group(1) if tenant else None,
        contract=raw.get("name") or rule.name.partition(":")[0] or None,
        subject=subject,
        scope=rule.aci_scope or raw.get("scope"),
        dn=dn or None,
        kind=raw.get("kind") or "contract",
    )


def _nat(rule, search: rule_search.RuleSearch) -> RuleNatOut | None:
    if rule.domain is not RuleDomain.FIREWALL:
        return None
    pools = rule_search.snat_pools(rule)
    vips = search.vips(rule)
    if pools is None and not vips:
        return None
    return RuleNatOut(snat=pools is not None, pools=pools or [], vips=vips)


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
    nat: str | None = Query(None, description="Regeln mit diesem VIP bzw. SNAT-Pool"),
    source_system_id: int | None = None,
    domain: RuleDomain | None = None,
    src_zone: str | None = None,
    dst_zone: str | None = None,
    action: RuleAction | None = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session),
):
    """Regeln seitenweise; Gesamtzahl (nach Filtern und Suche) im Header `X-Total-Count`.

    `q` sucht in allem, was zur Regel gehoert (Gruppenmitglieder, Werte, Services, Zonen,
    EPG-Lage, NAT, IP im Netz - `store.rule_search`); `object`/`nat` filtern exakt."""
    tenant_id = tenant_scope(principal, tenant_id)
    reads.maybe_audit_read(session, principal, tenant_id, "rules")
    flt = {
        "tenant_id": tenant_id,
        "source_system_id": source_system_id,
        "domain": domain,
        "src_zone": src_zone,
        "dst_zone": dst_zone,
        "action": action,
    }
    q, object, nat = (v.strip() if v else None for v in (q, object, nat))
    if q or object or nat:
        # Suche ueber alle Regeln, erst danach die Seite schneiden (sonst fehlen Treffer)
        rules = rules_store.list_rules(session, limit=None, **flt)
        search = rule_search.RuleSearch(session, rules)
        rules = [
            r
            for r in rules
            if (not q or search.matches(r, q))
            and (not object or search.uses_object(r, object))
            and (not nat or search.uses_nat(r, nat))
        ]
        total = len(rules)
        rules = rules[offset : offset + limit]
    else:
        total = rules_store.count_rules(session, **flt)
        rules = rules_store.list_rules(session, limit=limit, offset=offset, **flt)
        search = rule_search.RuleSearch(session, rules)
    response.headers["X-Total-Count"] = str(total)
    return rule_outs(session, rules, search)


def rule_outs(session: Session, rules: list, search: rule_search.RuleSearch) -> list[RuleOut]:
    """Listenzeilen (Namen wie auf der Firewall, Zonen, NAT, ACI) - auch fuer NAT-Detail."""
    objs = search.objs
    epgs = epg_store.by_ids(session, {e.epg_id for r in rules for e in r.endpoints if e.epg_id})
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
                sources=_labels(r, RefRole.SRC, EndpointRole.SRC, objs, epgs),
                destinations=_labels(r, RefRole.DST, EndpointRole.DST, objs, epgs),
                service_names=_ref_names(r, RefRole.SERVICE)
                or [s.canonical_name for s in r.services],
                aci=_aci(r),
                nat=_nat(r, search),
            )
        )
    return out


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
    zone_ids = {r.src_zone_id, r.dst_zone_id} | {o.zone_id for o in objs.values() if o.zone_id}
    zones = rules_store.zone_names(session, zone_ids)
    nat_map = nat_store.nat_by_name(session, r.tenant_id)
    epgs = epg_store.by_ids(session, {e.epg_id for e in r.endpoints if e.epg_id})
    epg_info = epg_store.details(session, r.tenant_id, list(epgs.values()))
    search = rule_search.RuleSearch(session, [r])
    rule_nat = _nat(r, search)
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
        sources=_labels(r, RefRole.SRC, EndpointRole.SRC, objs, epgs),
        destinations=_labels(r, RefRole.DST, EndpointRole.DST, objs, epgs),
        service_names=_ref_names(r, RefRole.SERVICE) or [s.canonical_name for s in r.services],
        aci=_aci(r),
        external_id=r.external_id,
        sequence=r.sequence,
        refs=objects_store.rule_refs(session, r),
        endpoints=endpoints,
        services=[s.canonical_name for s in r.services],
        protections=r.protections or [],
        nat=rule_nat,
        comment=str(raw.get("comments") or raw.get("comment") or "") or None,
        nat_entries=nat_store.briefs(
            session,
            r.tenant_id,
            r.source_system_id,
            [*rule_nat.pools, *rule_nat.vips] if rule_nat else [],
        ),
    )
