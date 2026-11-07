"""Prüfung: ACI: Contract-Scope ohne Gegenstück.

Steckbare Prüfung (0.110.0, ADR 0039) - aus `store/reconcile.py` hierher verschoben."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import Check
from app.models import (
    Epg,
    FindingType,
    Rule,
    RuleDomain,
    RuleEndpoint,
    Severity,
    SubjectKind,
)
from app.store.reconcile import Issue, _source_names


def _aci_scope_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Contract mit Consumern und Providern, die sein Scope (AP, VRF, Tenant) nicht verbindet
    (0.45.1, Anwender): keine Gegenstelle im Scope = fuer diese EPGs ohne Wirkung. Ohne
    gelesenen Scope kein Befund."""
    from app.store import aci_relevance  # spaet: aci_relevance -> paths -> ...
    from app.store import epgs as epg_store

    cls = aci_relevance.Classifier(session, tenant_id)
    sides: dict[int, dict[str, list[Epg]]] = defaultdict(lambda: {"src": [], "dst": []})
    epgs = {e.id: e for e in session.scalars(select(Epg).where(Epg.tenant_id == tenant_id))}
    for rule_id, role, epg_id in session.execute(
        select(RuleEndpoint.rule_id, RuleEndpoint.role, RuleEndpoint.epg_id)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .where(
            Rule.tenant_id == tenant_id,
            Rule.domain == RuleDomain.ACI_CONTRACT,
            Rule.aci_scope.in_(("application-profile", "context", "tenant")),
            RuleEndpoint.epg_id.is_not(None),
        )
    ):
        epg = epgs.get(epg_id)
        if epg is not None and epg not in sides[rule_id][role.value]:
            sides[rule_id][role.value].append(epg)
    if not sides:
        return []
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    for r in session.scalars(select(Rule).where(Rule.id.in_(list(sides)))):
        raw = r.raw if isinstance(r.raw, dict) else {}
        both = sides[r.id]
        if raw.get("kind") in ("taboo", "preferred_group") or not (both["src"] and both["dst"]):
            continue  # ohne Consumer bzw. Provider: Befund rule_incomplete
        pairs, unpaired = cls.scope_pairs(r.aci_scope, both["src"], both["dst"])
        if not unpaired:
            continue
        label = r.name or f"#{r.external_id}"
        lone = [e for e in both["src"] + both["dst"] if e.id in unpaired]
        reason = next(
            (cls.scope_reason(r.aci_scope, e, o) for e in lone
             for o in (both["dst"] if e in both["src"] else both["src"])),
            None,
        )  # fmt: skip
        out.append(
            Issue(
                FindingType.ACI_SCOPE_UNPAIRED,
                SubjectKind.RULE,
                f"{raw.get('tenant')}/{label}" if raw.get("tenant") else label,
                r.source_system_id,
                Severity.WARNING if not pairs else Severity.INFO,
                detail={
                    "rule_id": r.id,
                    "name": label,
                    "scope": r.aci_scope,
                    "pairs": len(pairs),
                    "unpaired": len(lone),
                    "epgs": [epg_store.inventory_label(e) for e in lone[:5]],
                    "reason": reason,
                    "source": names.get(r.source_system_id),
                    "tenant": raw.get("tenant"),
                },
            )
        )
    return out


CHECK = Check(
    "aci_scope",
    "kern",
    ("ACI: Scope ohne Gegenstück", "ACI: scope without counterpart"),
    (
        "Contract mit Scope, dessen Consumer und Provider nicht im selben Bereich liegen",
        "Contract with a scope whose consumer and provider are not in the same scope",
    ),
    frozenset({FindingType.ACI_SCOPE_UNPAIRED}),
    lambda session, tenant_id, cx: _aci_scope_issues(session, tenant_id),
    order=40,
)
