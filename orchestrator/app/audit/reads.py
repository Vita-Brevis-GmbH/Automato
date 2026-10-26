"""Per-Mandant zuschaltbares Lesezugriffs-Audit (E1, ADR 0007 D5).

Firewall-/Applikations-Doku kann sensibel sein. Ist `tenant.audit_reads` gesetzt, werden
Lesezugriffe auf die Doku dieses Mandanten protokolliert (`read.<resource>`). Nur bei
konkretem Mandanten (tenant_id gesetzt); mandantsuebergreifende Sichten (None) werden
nicht auditiert. Lesezugriffe von Kunden-Benutzern (ADR 0018) werden immer protokolliert, mit
ihren Kunden-IDs.
"""

from sqlalchemy.orm import Session

from app import audit
from app.auth.principal import Principal
from app.models import Tenant

# Auditierbare Lese-Ressourcen (muessen in audit/allowlist.py als read.<name> stehen).
RESOURCES = frozenset(
    {
        "rules",
        "nat",
        "findings",
        "applications",
        "published_services",
        "appdoc",
        "routing",
        "objects",
        "virtual",
        "customers",
        "zones",
    }
)


def maybe_audit_read(
    session: Session, principal: Principal, tenant_id: int | None, resource: str
) -> None:
    if tenant_id is None or resource not in RESOURCES:
        return
    detail: dict = {"tenant_id": tenant_id, "resource": resource}
    if principal.is_customer:  # Kunden-Benutzer: immer (ADR 0018)
        detail["customers"] = list(principal.customer_ids or ())
    else:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None or not tenant.audit_reads:
            return
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action=f"read.{resource}",
        subject=str(tenant_id),
        detail=detail,
    )
