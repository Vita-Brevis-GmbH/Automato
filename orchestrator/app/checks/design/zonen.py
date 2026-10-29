"""Prüfung: Logische Zonen (0.51.0–0.106.0).

Steckbare Prüfung (0.110.0, ADR 0039) - aus `store/reconcile.py` hierher verschoben."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import Check
from app.models import (
    FindingType,
    Rule,
    RuleDomain,
    Severity,
    SubjectKind,
)
from app.store.reconcile import Issue, _source_names, vdom_suffix


def _zone_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Logische Zonen (0.51.0, `store/zones.checks`): erlaubende Regel zwischen Zonen mit
    Absicht „gesperrt“ (je Regel ein Befund mit allen Paaren), dasselbe Praefix in mehreren
    Zonen."""
    from app.store import zones

    items = zones.checks(session, tenant_id)
    rule_ids = [i["rule_id"] for i in items if i.get("rule_id")]
    rules = {
        r.id: r for r in session.scalars(select(Rule).where(Rule.id.in_(rule_ids)))
    } if rule_ids else {}  # fmt: skip
    names = _source_names(session, tenant_id)
    out: list[Issue] = []
    for item in items:
        if item["type"] == "zone_overlap":
            detail = {k: v for k, v in item.items() if k not in ("type", "subject")}
            out.append(Issue(FindingType.ZONE_OVERLAP, SubjectKind.SUBNET, item["subject"],
                             None, Severity.WARNING, detail=detail))  # fmt: skip
            continue
        r = rules.get(item["rule_id"])
        if r is None:
            continue
        raw = r.raw if isinstance(r.raw, dict) else {}
        aci = r.domain is RuleDomain.ACI_CONTRACT
        label = (r.name or f"#{r.external_id}") + vdom_suffix(r.vdom)
        subject = f"{raw.get('tenant')}/{label}" if aci and raw.get("tenant") else label
        out.append(
            Issue(
                FindingType.ZONE_POLICY_VIOLATION,
                SubjectKind.RULE,
                subject,
                r.source_system_id,
                Severity.WARNING,
                detail={
                    "rule_id": r.id,
                    "external_id": r.external_id,
                    "domain": r.domain.value,
                    "name": label,
                    "source_system_id": r.source_system_id,
                    "source": names.get(r.source_system_id),
                    **({"vdom": r.vdom} if r.vdom else {}),
                    "tenant": raw.get("tenant") if aci else None,
                    "pairs": item["pairs"],
                    "notes": item["notes"],
                },
            )
        )
    out += _schema_issues(session, tenant_id)
    out += _exit_issues(session, tenant_id)
    return out


def _exit_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Netze der Zonen, die die Firewall nicht ueber einen Ausgang der Zone lernt (0.105.0)."""
    from app.store import zone_exits

    out = []
    for item in zone_exits.checks(session, tenant_id):
        detail = {k: v for k, v in item.items() if k not in ("type", "subject", "severity")}
        out.append(Issue(FindingType.ZONE_NET_EXIT, SubjectKind.ZONE, item["subject"],
                         item["source_system_id"], Severity.WARNING, detail=detail))  # fmt: skip
    return out


def _schema_issues(session: Session, tenant_id: int) -> list[Issue]:
    """Zonen ausserhalb des Grundschemas im Namensprofil (0.78.0): Hinweis je Zone."""
    from app.store import zone_detect

    profile, outside = zone_detect.outside_schema(session, tenant_id)
    out = []
    for z in outside:
        detail = {"zone_id": z.id, "name": z.name, "code": z.code, "kind": z.kind,
                  "profile": profile}  # fmt: skip
        out.append(Issue(FindingType.ZONE_OUTSIDE_SCHEMA, SubjectKind.ZONE, z.name, None,
                         Severity.INFO, detail=detail))  # fmt: skip
    return out


CHECK = Check(
    "zonen",
    "design",
    ("Zonen", "Zones"),
    (
        "Regel gegen die Absicht der Zonenmatrix, Netz in mehreren Zonen, Zone ausserhalb des "
        "Grundschemas, Netz nicht über den Übergang zur Zone",
        "Rule against the intent of the zone matrix, network in several zones, zone outside the "
        "base schema, network not via the transition to the zone",
    ),
    frozenset(
        {
            FindingType.ZONE_POLICY_VIOLATION,
            FindingType.ZONE_OVERLAP,
            FindingType.ZONE_OUTSIDE_SCHEMA,
            FindingType.ZONE_NET_EXIT,
        }
    ),
    lambda session, tenant_id, cx: _zone_issues(session, tenant_id),
    step="zonen",
    order=70,
)
