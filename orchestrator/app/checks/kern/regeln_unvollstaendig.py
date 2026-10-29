"""Prüfung: Regel ohne Quelle bzw. Ziel (ACI: Contract ohne Consumer/Provider, nicht wirksam).

Steckbare Prüfung (0.110.0, ADR 0039) - aus `store/reconcile.py` hierher verschoben."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import Check
from app.models import (
    FindingType,
    Rule,
    RuleEndpoint,
    Severity,
    SubjectKind,
)
from app.store.reconcile import Issue, _source_names, vdom_suffix


def _rule_incomplete_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Regel ohne Quelle bzw. Ziel - ACI: Contract ohne Consumer/Provider, nicht wirksam."""
    roles: dict[int, set[str]] = defaultdict(set)
    for rule_id, role in session.execute(
        select(RuleEndpoint.rule_id, RuleEndpoint.role)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .where(Rule.tenant_id == tenant_id)
        .distinct()
    ):
        roles[rule_id].add(role.value)
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    for r in session.scalars(select(Rule).where(Rule.tenant_id == tenant_id)):
        missing = [side for side in ("src", "dst") if side not in roles.get(r.id, set())]
        if not missing:
            continue
        aci = r.domain.value == "aci_contract"
        raw = r.raw if isinstance(r.raw, dict) else {}
        label = (r.name or f"#{r.external_id}") + vdom_suffix(r.vdom)
        subject = f"{raw.get('tenant')}/{label}" if aci and raw.get("tenant") else label
        out.append(
            Issue(
                FindingType.RULE_INCOMPLETE,
                SubjectKind.RULE,
                subject,
                r.source_system_id,
                Severity.INFO if aci else Severity.WARNING,
                detail={
                    "rule_id": r.id,
                    "external_id": r.external_id,
                    "domain": r.domain.value,
                    "missing": missing,
                    "source_system_id": r.source_system_id,
                    "source": names.get(r.source_system_id),
                    **({"vdom": r.vdom} if r.vdom else {}),
                    "name": label,
                    "tenant": raw.get("tenant") if aci else None,
                },
            )
        )
    return out


CHECK = Check(
    "regeln_unvollstaendig",
    "kern",
    ("Regeln ohne Quelle bzw. Ziel", "Rules without source or destination"),
    (
        "Regel ohne Quelle bzw. Ziel; ACI: Contract ohne Consumer bzw. Provider (nicht wirksam)",
        "Rule without source or destination; ACI: contract without consumer or provider "
        "(not effective)",
    ),
    frozenset({FindingType.RULE_INCOMPLETE}),
    lambda session, tenant_id, cx: _rule_incomplete_issues(session, tenant_id),
    order=20,
)
