"""Prüfung: Firewall-Objekte gegen die Namenskonvention (Namen, Typ, doppelte Werte, unbenutzt,
Services, vordefinierte Services in erlaubenden Regeln).

Steckbare Prüfung (0.110.0, ADR 0039); die Umsetzung teilt Helfer mit dem Abgleich und bleibt in
`store/reconcile.py` (`_device_object_issues`)."""

from app.checks import Check
from app.models import FindingType


def _run(session, tenant_id, cx):
    from app.store import reconcile

    return reconcile._device_object_issues(session, tenant_id, cx.prefixes)


CHECK = Check(
    "objekte",
    "kern",
    ("Firewall-Objekte und Namenskonvention", "Firewall objects and naming convention"),
    (
        "Name passt nicht zur Konvention, Präfix sagt Host bzw. Netz, doppelter Wert, Objekt ohne "
        "Regel, Service-Name passt nicht zu Protokoll/Port, vordefinierter Service in erlaubender "
        "Regel",
        "Name does not match the convention, prefix says host or network, duplicate value, object "
        "without rule, service name does not match protocol/port, predefined service in accepting "
        "rule",
    ),
    frozenset(
        {
            FindingType.INVALID_NAME,
            FindingType.NAME_TYPE_MISMATCH,
            FindingType.DUPLICATE_VALUE,
            FindingType.UNUSED_OBJECT,
            FindingType.SERVICE_MISMATCH,
            FindingType.PREDEFINED_SERVICE_USED,
        }
    ),
    _run,
    order=10,
)
