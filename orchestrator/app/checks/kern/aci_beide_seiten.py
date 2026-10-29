"""Prüfung: ACI: dieselbe EPG als Consumer und Provider.

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


def _aci_both_sides_issues(session: Session, tenant_id: int) -> list[Issue]:
    """EPG als Consumer und Provider desselben Contracts (0.79.1, Anwender): der Contract gilt
    zwischen allen EPGs, die ihn so tragen, in beide Richtungen; gegenueber sich selbst wirkt
    er nicht (Verkehr in der EPG ist ohnehin erlaubt). Ein Befund je Contract (alle Subjects)."""
    from app.store import epgs as epg_store

    epgs = {e.id: e for e in session.scalars(select(Epg).where(Epg.tenant_id == tenant_id))}
    sides: dict[int, dict[str, set[int]]] = defaultdict(lambda: {"src": set(), "dst": set()})
    for rule_id, role, epg_id in session.execute(
        select(RuleEndpoint.rule_id, RuleEndpoint.role, RuleEndpoint.epg_id)
        .join(Rule, Rule.id == RuleEndpoint.rule_id)
        .where(
            Rule.tenant_id == tenant_id,
            Rule.domain == RuleDomain.ACI_CONTRACT,
            RuleEndpoint.epg_id.is_not(None),
        )
    ):
        sides[rule_id][role.value].add(epg_id)
    both = {rid: s["src"] & s["dst"] for rid, s in sides.items() if s["src"] & s["dst"]}
    if not both:
        return []
    names = _source_names(session, tenant_id)
    contracts: dict[tuple, dict] = {}
    for r in session.scalars(select(Rule).where(Rule.id.in_(list(both))).order_by(Rule.id)):
        raw = r.raw if isinstance(r.raw, dict) else {}
        if raw.get("kind") in ("taboo", "preferred_group"):
            continue
        name = (r.name or f"#{r.external_id}").split(":", 1)[0]  # Contract ohne Subject
        key = (r.source_system_id, raw.get("dn") or name)
        entry = contracts.setdefault(key, {"rule": r, "name": name, "tenant": raw.get("tenant"),
                                           "epgs": [], "subjects": []})  # fmt: skip
        entry["subjects"].append(r.name or f"#{r.external_id}")
        for eid in sorted(both[r.id]):
            e = epgs.get(eid)
            if e is not None and e not in entry["epgs"]:
                entry["epgs"].append(e)
    out: list[Issue] = []
    for (sid, _dn), c in contracts.items():
        if not c["epgs"]:
            continue
        labels = [epg_store.inventory_label(e) for e in c["epgs"]]
        out.append(
            Issue(
                FindingType.ACI_CONTRACT_BOTH_SIDES,
                SubjectKind.RULE,
                f"{c['tenant']}/{c['name']}" if c["tenant"] else c["name"],
                sid,
                Severity.WARNING,
                detail={
                    "rule_id": c["rule"].id,
                    "name": c["name"],
                    "subjects": c["subjects"][:5],
                    "count": len(labels),
                    "epgs": labels[:10],
                    "tenant": c["tenant"],
                    "source": names.get(sid),
                },
            )
        )
    return out


CHECK = Check(
    "aci_beide_seiten",
    "kern",
    ("ACI: Contract auf beiden Seiten", "ACI: contract on both sides"),
    (
        "Dieselbe EPG konsumiert und stellt denselben Contract bereit",
        "The same EPG consumes and provides the same contract",
    ),
    frozenset({FindingType.ACI_CONTRACT_BOTH_SIDES}),
    lambda session, tenant_id, cx: _aci_both_sides_issues(session, tenant_id),
    order=41,
)
