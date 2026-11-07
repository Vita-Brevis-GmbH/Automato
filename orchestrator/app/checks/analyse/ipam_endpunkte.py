"""Prüfung: Regel-Endpunkte gegen das IPAM (kanonische Werte-Ebene).

Steckbare Prüfung (0.110.0, ADR 0039); die Umsetzung bleibt in `store/reconcile.py`
(`_rule_endpoint_issues`, geteilte Helfer)."""

from app.checks import Check
from app.models import FindingType


def _run(session, tenant_id, cx):
    from app.store import reconcile

    return reconcile._rule_endpoint_issues(session, tenant_id, cx.refs, cx.prefixes)


CHECK = Check(
    "ipam_endpunkte",
    "analyse",
    ("Regel-Endpunkte im IPAM", "Rule endpoints in the IPAM"),
    (
        "Endpunkt einer Regel ohne Treffer im IPAM bzw. mit Namen ausserhalb der Konvention",
        "Endpoint of a rule without a match in the IPAM or with a name outside the convention",
    ),
    frozenset({FindingType.UNMATCHED_OBJECT, FindingType.INVALID_NAME}),
    _run,
    order=50,
)
