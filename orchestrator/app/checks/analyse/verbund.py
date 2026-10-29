"""Prüfung: parallele Firewalls im Verbund (ADR 0020, `store/parallel.compare`).

Steckbare Prüfung (0.110.0, ADR 0039). Der Vergleich bleibt für den Regelindex (Zwillinge) in der
Session liegen, der gleich danach neu aufgebaut wird (`parallel.mark` rechnet ihn sonst selbst)."""

from app.checks import Check
from app.models import FindingType


def _run(session, tenant_id, cx):
    from app.store import parallel

    comparison = parallel.compare(session, tenant_id)
    session.info[("parallel", tenant_id)] = comparison
    return comparison.issues


CHECK = Check(
    "verbund",
    "analyse",
    ("Parallele Firewalls (Verbund)", "Parallel firewalls (group)"),
    (
        "Regeln, Objekte, Policy-Routen, Routing und NAT der Firewalls eines Verbunds weichen ab",
        "Rules, objects, policy routes, routing and NAT of the firewalls of a group differ",
    ),
    frozenset(
        {
            FindingType.PARALLEL_RULE_MISMATCH,
            FindingType.PARALLEL_RULE_MISSING,
            FindingType.PARALLEL_OBJECT_MISMATCH,
            FindingType.PARALLEL_POLICY_ROUTE_MISMATCH,
            FindingType.PARALLEL_ROUTE_MISMATCH,
            FindingType.PARALLEL_NAT_MISMATCH,
        }
    ),
    _run,
    order=80,
)
