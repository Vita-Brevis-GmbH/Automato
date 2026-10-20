"""Per-Mandant zuschaltbares Lesezugriffs-Audit (E1, ADR 0007 D5).

Firewall-/Applikations-Doku kann sensibel sein. Ist `tenant.audit_reads` gesetzt, werden
Lesezugriffe auf die Doku dieses Mandanten protokolliert (`read.<resource>`). Nur bei
konkretem Mandanten (tenant_id gesetzt); mandantsuebergreifende Sichten (None) werden
nicht auditiert.
"""

from sqlalchemy.orm import Session

from app import audit
from app.auth.principal import Principal
from app.models import Tenant

# Auditierbare Lese-Ressourcen (muessen in audit/allowlist.py als read.<name> stehen).
RESOURCES = frozenset({"rules", "nat", "findings", "applications", "published_services", "appdoc"})


def maybe_audit_read(
    session: Session, principal: Principal, tenant_id: int | None, resource: str
) -> None:
    if tenant_id is None or resource not in RESOURCES:
        return
    tenant = session.get(Tenant, tenant_id)
    if tenant is None or not tenant.audit_reads:
        return
    audit.record(
        session,
        actor=f"user:{principal.subject}",
        action=f"read.{resource}",
        subject=str(tenant_id),
        detail={"tenant_id": tenant_id, "resource": resource},
    )
