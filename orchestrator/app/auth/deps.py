"""FastAPI-Abhaengigkeiten fuer AuthN/RBAC + Mandanten-Scoping (E1, ADR 0007)."""

from fastapi import Depends, Header, HTTPException

from app.auth import roles as R
from app.auth.principal import Principal
from app.config import get_settings


def _parse_tenants(raw: str | None) -> tuple[int, ...] | None:
    if raw is None:
        return None
    return tuple(int(x) for x in raw.split(",") if x.strip().isdigit())


def get_principal(
    authorization: str | None = Header(default=None),
    x_dev_roles: str | None = Header(default=None),
    x_dev_tenants: str | None = Header(default=None),
) -> Principal:
    settings = get_settings()
    if settings.auth_mode == "oidc":
        # E1.1b: JWT gegen Keycloak validieren (Issuer/JWKS/Audience), Rollen/Mandanten
        # aus Claims. Bis dahin bewusst nicht aktiv (kein stiller Durchlass).
        raise HTTPException(status_code=501, detail="OIDC/Keycloak-Auth noch nicht aktiviert")
    # dev-Modus: Standard = Admin (alle Mandanten) -> unveraendertes Verhalten.
    # Override via X-Dev-Roles/X-Dev-Tenants zum Testen/Vorfuehren von RBAC + Scoping.
    if x_dev_roles is not None or x_dev_tenants is not None:
        rs = frozenset(r.strip() for r in (x_dev_roles or "").split(",") if r.strip())
        return Principal(subject="dev", roles=rs, tenant_ids=_parse_tenants(x_dev_tenants))
    return Principal(subject="dev-admin", roles=frozenset({R.ADMIN}), tenant_ids=None)


def require_role(required: str):
    def dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not R.satisfies(principal.roles, required):
            raise HTTPException(status_code=403, detail="insufficient role")
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
    if principal.is_admin or principal.tenant_ids is None:
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
