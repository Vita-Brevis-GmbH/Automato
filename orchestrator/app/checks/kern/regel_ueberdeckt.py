"""Prüfung: Regel überdeckt durch Policy X (ADR 0071).

Anwender 2026-11-18 (Regel-Zuordnung nach echtem Treffer): eine ganz überdeckte Regel ist kein
Zugriff - als Befund ausgewiesen. Auf der FortiGate entscheidet die erste passende Policy in
Listen-Reihenfolge; eine Policy, deren ganzen Verkehr frühere sichere Policies schon entscheiden
(Ein- und Ausgang, Quelle, Ziel enthalten; die Dienste zusammen gedeckt), trifft nie Verkehr
(`store/rule_match.Matcher.shadowed_rules`). Nicht prüfbare frühere Policies (FQDN, Internet
Service, Benutzer, Zeitplan) überdecken nie - kein Befund auf Vermutung. Warnung, wenn die
überdeckende Policy anders entscheidet (z. B. Sperre vor einer Freigabe), sonst Hinweis
(doppelte Regel). Gleiche Policies paralleler Firewalls einmal. Steckbare Prüfung (ADR 0039),
nur lesend."""

from sqlalchemy.orm import Session

from app.checks import Check, Context
from app.models import FindingType, Severity, SubjectKind
from app.store.reconcile import Issue, _source_names, vdom_suffix


def _shadowed_issues(session: Session, tenant_id: int, cx: Context) -> list[Issue]:
    from app.store import parallel, rule_match  # spaet: rule_match -> policy_check -> paths

    matcher = rule_match.for_tenant(session, tenant_id)
    names = _source_names(session, tenant_id)
    found: list[tuple] = []
    for sid, vdom in matcher.scopes():
        by_id = {p.id: p for p in matcher.checker.policies(sid, vdom)}
        for rid, by in matcher.shadowed_rules(sid, vdom).items():
            found.append((sid, vdom, by_id[rid], [by_id[i] for i in by if i in by_id]))
    twins = parallel.twin_ids(session, {p.id for _s, _v, p, _by in found})
    out: list[Issue] = []
    for sid, vdom, p, by in found:
        if p.id in twins or not by:
            continue
        label = (p.name or f"#{p.external_id}") + vdom_suffix(vdom)
        differs = any(q.allow != p.allow for q in by)
        out.append(
            Issue(
                FindingType.RULE_SHADOWED,
                SubjectKind.RULE,
                f"{names.get(sid) or sid}|{label}|{p.external_id}",
                sid,
                Severity.WARNING if differs else Severity.INFO,
                detail={
                    "rule_id": p.id,
                    "external_id": p.external_id,
                    "name": label,
                    "action": "allow" if p.allow else "deny",
                    "source_system_id": sid,
                    "source": names.get(sid),
                    **({"vdom": vdom} if vdom else {}),
                    "by": [
                        {
                            "rule_id": q.id,
                            "external_id": q.external_id,
                            "name": q.name,
                            "action": "allow" if q.allow else "deny",
                        }
                        for q in by[:5]
                    ],  # fmt: skip
                    "by_count": len(by),
                },
            )
        )
    return out


CHECK = Check(
    "regel_ueberdeckt",
    "kern",
    ("Regel überdeckt", "Rule shadowed"),
    (
        "Firewall-Policy, deren ganzen Verkehr frühere Policies in der Reihenfolge der Firewall "
        "schon entscheiden – sie trifft nie Verkehr (überdeckt durch Policy X)",
        "Firewall policy whose entire traffic is already decided by earlier policies in the "
        "order of the firewall – it never matches traffic (shadowed by policy X)",
    ),
    frozenset({FindingType.RULE_SHADOWED}),
    _shadowed_issues,
    order=32,
)
