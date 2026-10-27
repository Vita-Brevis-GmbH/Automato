"""FastAPI-Abhaengigkeiten fuer AuthN/RBAC + Mandanten-Scoping (E1, ADR 0007)."""

import logging

from fastapi import Depends, Header, HTTPException, Request

from app.auth import assignments, customer_routes, oidc
from app.auth import capabilities as caps
from app.auth import roles as R
from app.auth.principal import Principal
from app.config import get_settings
from app.db import get_session_factory
from app.i18n import L

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
    x_dev_customer_role: str | None = Header(default=None),
    factory=Depends(get_session_factory),
) -> Principal:
    """Aufrufer aus dem Token (bzw. im dev-Modus aus den Kopfzeilen) - dazu die Zuordnung
    Benutzer -> Kunde aus Automato (0.53.0), die einen Benutzer zum Kunden-Benutzer macht."""
    principal = _authenticate(authorization, x_dev_roles, x_dev_tenants, x_dev_customers,
                              x_dev_user, x_dev_customer_role)  # fmt: skip
    return assignments.resolve(factory, principal)


def _authenticate(authorization, x_dev_roles, x_dev_tenants, x_dev_customers,
                  x_dev_user, x_dev_customer_role=None) -> Principal:  # fmt: skip
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
        customers = _parse_tenants(x_dev_customers) or ()
        # Kunden-Rolle wie aus dem Token: X-Dev-Customer-Role (Claim `customer_role`), sonst
        # aus den Rollen (viewer/customer -> viewer, operator -> triage)
        role = oidc.customer_role_of(rs, x_dev_customer_role)
        return Principal(
            subject="dev-customer",
            roles=rs - {R.ADMIN},
            tenant_ids=_parse_tenants(x_dev_tenants) or (),
            customer_ids=customers,
            customer_roles=tuple((c, role) for c in customers) if role else (),
        )
    if x_dev_roles is not None or x_dev_tenants is not None:
        rs = frozenset(r.strip() for r in (x_dev_roles or "").split(",") if r.strip())
        return Principal(subject="dev", roles=rs, tenant_ids=_parse_tenants(x_dev_tenants))
    return Principal(subject="dev-admin", roles=frozenset({R.ADMIN}), tenant_ids=None)


def require_role(required: str):
    def dep(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
        if principal.is_customer:  # Kunden-Benutzer: Capabilities statt Rollen (fail-closed)
            _customer_gate(request, principal, required)
            return principal
        if not R.satisfies(principal.roles, required):
            raise HTTPException(status_code=403, detail="insufficient role")
        return principal

    return dep


def _customer_gate(request: Request, principal: Principal, required: str) -> None:
    """Kunden-Benutzer: nur freigegebene Endpunkte, je mit der benoetigten Capability fuer
    mindestens einen ihrer Kunden (feinere Rollen, ADR 0018 Ergaenzung). Ein schreibender
    Endpunkt (`required` ueber viewer) laesst sich nie mit dem Leserecht oeffnen - auch nicht
    durch einen falschen Eintrag in der Freigabeliste. Ob das Objekt zum Kunden gehoert, prueft
    der Endpunkt."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    need = customer_routes.capability(request.method, path) if path is not None else None
    if need is None or (required != R.VIEWER and need == caps.READ):
        raise HTTPException(status_code=403, detail="not available for customer users")
    if not principal.can(need):
        raise HTTPException(status_code=403, detail="insufficient role")
    # Antwort ohne Geraetenamen (0.58.0, `customer_alias`, Middleware in `main.py`)
    request.state.customer_tenants = tuple(principal.tenant_ids or ())


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
    raise HTTPException(
        status_code=400,
        detail=L(
            "tenant_id erforderlich (mehrere Mandanten)", "tenant_id required (several tenants)"
        ),
    )
