"""FastAPI-Abhaengigkeiten fuer AuthN/RBAC + Mandanten-Scoping (E1, ADR 0007)."""

import logging

from fastapi import Depends, Header, HTTPException, Request

from app.auth import assignments, customer_routes, oidc
from app.auth import roles as R
from app.auth.principal import Principal
from app.config import get_settings
from app.db import get_session_factory

log = logging.getLogger(__name__)


def _parse_tenants(raw: str | None) -> tuple[int, ...] | None:
    if raw is None:
        return None
    return tuple(int(x) for x in raw.split(",") if x.strip().isdigit())


def get_principal(
    authorization: str | None = Header(default=None),
    x_dev_roles: str | None = Header(default=None),
    x_dev_tenants: str | None = Header(default=None),
    x_dev_customers: str | None = Header(default=None),
    x_dev_user: str | None = Header(default=None),
    factory=Depends(get_session_factory),
) -> Principal:
    """Aufrufer aus dem Token (bzw. im dev-Modus aus den Kopfzeilen) - dazu die Zuordnung
    Benutzer -> Kunde aus Automato (0.53.0), die einen Benutzer zum Kunden-Benutzer macht."""
    principal = _authenticate(authorization, x_dev_roles, x_dev_tenants, x_dev_customers,
                              x_dev_user)  # fmt: skip
    return assignments.resolve(factory, principal)


def _authenticate(authorization, x_dev_roles, x_dev_tenants, x_dev_customers,
                  x_dev_user) -> Principal:  # fmt: skip
    settings = get_settings()
    if settings.auth_mode == "oidc":
        try:
            return oidc.principal_from_bearer(authorization, settings)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - jede weitere Validierungs-Panne -> 401
            log.warning("OIDC-Token abgelehnt: %s: %s", type(exc).__name__, exc)
            raise HTTPException(status_code=401, detail="invalid token") from exc
    if x_dev_user is not None:  # dev: angemeldeter Benutzer (Rollen und Kunden aus Automato)
        login = x_dev_user.strip().lower()
        rs = frozenset(r.strip() for r in (x_dev_roles or "").split(",") if r.strip())
        return Principal(subject=login or "dev-user", roles=rs,
                         tenant_ids=_parse_tenants(x_dev_tenants) or (),
                         logins=(login,) if login else ())  # fmt: skip
    # dev-Modus: Standard = Admin (alle Mandanten) -> unveraendertes Verhalten.
    # Override via X-Dev-Roles/X-Dev-Tenants zum Testen/Vorfuehren von RBAC + Scoping.
    if x_dev_customers is not None:  # Kunden-Benutzer (ADR 0018): nie Admin, fail-closed
        rs = frozenset(r.strip() for r in (x_dev_roles or "viewer").split(",") if r.strip())
        return Principal(
            subject="dev-customer",
            roles=rs - {R.ADMIN},
            tenant_ids=_parse_tenants(x_dev_tenants) or (),
            customer_ids=_parse_tenants(x_dev_customers) or (),
        )
    if x_dev_roles is not None or x_dev_tenants is not None:
        rs = frozenset(r.strip() for r in (x_dev_roles or "").split(",") if r.strip())
        return Principal(subject="dev", roles=rs, tenant_ids=_parse_tenants(x_dev_tenants))
    return Principal(subject="dev-admin", roles=frozenset({R.ADMIN}), tenant_ids=None)


def require_role(required: str):
    def dep(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
        if not R.satisfies(principal.roles, required):
            raise HTTPException(status_code=403, detail="insufficient role")
        if principal.is_customer:  # Kunden-Benutzer: nur freigegebene Endpunkte (fail-closed)
            route = request.scope.get("route")
            path = getattr(route, "path", None)
            if path is None or not customer_routes.allowed(request.method, path):
                raise HTTPException(status_code=403, detail="not available for customer users")
        return principal

    return dep


require_viewer = require_role(R.VIEWER)
require_operator = require_role(R.OPERATOR)
require_admin = require_role(R.ADMIN)


def tenant_scope(principal: Principal, requested: int | None) -> int | None:
    """Effektiver tenant_id-Filter unter Beachtung der erlaubten Mandanten.

    Admin (tenant_ids=None): `requested` unveraendert (None = alle). Nicht-Admin:
    nur innerhalb der erlaubten Mandanten; ein `requested` ausserhalb -> 403; ohne
    `requested` wird auf den einzigen erlaubten Mandanten verengt (sonst 400/403).
    """
    if principal.is_admin or (principal.tenant_ids is None and not principal.is_customer):
        return requested
    allowed = principal.tenant_ids
    if requested is not None:
        if requested not in allowed:
            raise HTTPException(status_code=403, detail="tenant not allowed")
        return requested
    if len(allowed) == 1:
        return allowed[0]
    if not allowed:
        raise HTTPException(status_code=403, detail="no tenant assigned")
    raise HTTPException(status_code=400, detail="tenant_id erforderlich (mehrere Mandanten)")
