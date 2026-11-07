"""Prüfung: SNAT auf einen IP-Pool, den die Firewall im VDOM nicht hat.

Steckbare Prüfung (0.110.0, ADR 0039) - aus `store/reconcile.py` hierher verschoben."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import Check
from app.models import (
    FindingType,
    Rule,
    RuleAction,
    RuleDomain,
    Severity,
    SubjectKind,
)
from app.store.reconcile import Issue, _source_names, vdom_suffix


def _snat_pool_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Erlaubende Policies mit SNAT auf einen IP-Pool, den die Firewall im VDOM nicht hat
    (0.47.0): die Quelle danach ist unbekannt."""
    from app.models import NatRule
    from app.store import rule_view

    pools = {
        (sid, vdom, name)
        for sid, vdom, name in session.execute(
            select(NatRule.source_system_id, NatRule.vdom, NatRule.name).where(
                NatRule.tenant_id == tenant_id, NatRule.nat_type == "snat_pool"
            )
        )
    }
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    for r in session.scalars(
        select(Rule).where(
            Rule.tenant_id == tenant_id,
            Rule.domain == RuleDomain.FIREWALL,
            Rule.action == RuleAction.ALLOW,
        )
    ):
        missing = [
            p
            for p in rule_view.snat_pools(r) or []
            if (r.source_system_id, r.vdom, p) not in pools
            and (r.source_system_id, None, p) not in pools
        ]
        if not missing:
            continue
        label = (r.name or f"#{r.external_id}") + vdom_suffix(r.vdom)
        out.append(
            Issue(
                FindingType.SNAT_POOL_MISSING,
                SubjectKind.RULE,
                label,
                r.source_system_id,
                Severity.WARNING,
                detail={
                    "rule_id": r.id,
                    "external_id": r.external_id,
                    "name": label,
                    "pools": missing,
                    "source": names.get(r.source_system_id),
                    **({"vdom": r.vdom} if r.vdom else {}),
                },
            )
        )
    return out


CHECK = Check(
    "snat_pool",
    "kern",
    ("SNAT-Pool fehlt", "SNAT pool missing"),
    (
        "Erlaubende Policy mit SNAT auf einen IP-Pool, den die Firewall im VDOM nicht hat",
        "Accepting policy with SNAT to an IP pool the firewall does not have in the VDOM",
    ),
    frozenset({FindingType.SNAT_POOL_MISSING}),
    lambda session, tenant_id, cx: _snat_pool_issues(session, tenant_id),
    order=30,
)
