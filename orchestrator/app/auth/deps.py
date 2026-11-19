"""FastAPI-Abhaengigkeiten fuer AuthN/RBAC + Mandanten-Scoping (E1, ADR 0007)."""

import logging

from fastapi import Depends, Header, HTTPException, Request

from app.auth import capabilities as caps
from app.auth import customer_routes, oidc, realms, tenant_routes, verwaltung
from app.auth import roles as R
from app.auth.principal import Principal
from app.config import get_settings
from app.db import get_session_factory
from app.i18n import L
from app.store import verwaltung as verwaltung_store

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
    x_dev_own_tenant: str | None = Header(default=None),
    factory=Depends(get_session_factory),
) -> Principal:
    """Aufrufer aus dem Token (bzw. im dev-Modus aus den Kopfzeilen) - dazu die Zuordnung
    Benutzer -> Kunde aus Automato (0.53.0), die einen Benutzer zum Kunden-Benutzer macht."""
    principal = _authenticate(authorization, x_dev_roles, x_dev_tenants, x_dev_customers,
                              x_dev_user, x_dev_customer_role, factory,
                              x_dev_own_tenant)  # fmt: skip
    if (principal.customer_source or "").startswith("realm:"):
        # Kunden-Realm (1.3.0): Ausschnitt nur aus dem Portal, keine Zuordnung ueber Kennungen;
        # Kunden aus dem Cockpit vermerken (1.14.0, ADR 0058: dort keine Rechte vergeben)
        return verwaltung.realm(factory, principal)
    # Zuordnung in Automato - bei Kunden aus dem Cockpit ohne die eines Kunden-Admins (1.14.0)
    return verwaltung.resolve(factory, principal)


def _authenticate(authorization, x_dev_roles, x_dev_tenants, x_dev_customers,
                  x_dev_user, x_dev_customer_role=None, factory=None,
                  x_dev_own_tenant=None) -> Principal:  # fmt: skip
    settings = get_settings()
    if settings.auth_mode == "oidc":
        try:
            scopes = realms.scopes(factory, settings) if factory is not None else None
            return oidc.principal_from_bearer(authorization, settings, scopes)
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
        tenants = _parse_tenants(x_dev_tenants) or ()
        own = (x_dev_own_tenant or "").strip().lower() in ("1", "true", "ja")
        if own and len(tenants) == 1 and role in caps.TENANT_ROLE_CAPABILITIES:
            # Mandanten-Benutzer vorfuehren (ADR 0068): wie ein Kunden-Realm im eigenen Mandanten
            return Principal(subject="dev-customer", roles=frozenset({R.VIEWER}),
                             tenant_ids=tenants, tenant_role=role)  # fmt: skip
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
        if principal.is_tenant_user:  # Mandanten-Benutzer (ADR 0068): Positivliste, nie Rollen
            _tenant_gate(request, principal, required)
            return principal
        if not R.satisfies(principal.roles, required):
            raise HTTPException(status_code=403, detail="insufficient role")
        return principal

    return dep


def assignment_missing_text(principal: Principal) -> str:
    """Meldung fuer Benutzer eines Kunden-Realms ohne Zuordnung in Automato (1.13.0)."""
    kurz, name = principal.realm_customer or ("?", "?")
    return L(
        f"Für {name} ({kurz}) ist in Automato noch kein Mandant zugeordnet. Ein Admin trägt ihn "
        f"unter Administration → Kunden-Portal → {kurz} ein (Mandant, Kunden, Rolle) – bei "
        "Kunden aus dem Cockpit im Cockpit beim Kunden.",
        f"No tenant is assigned to {name} ({kurz}) in Automato yet. An admin sets it under "
        f"Administration → Customer portal → {kurz} (tenant, customers, role) – for customers from "
        "the cockpit in the cockpit at the customer.",
    )


def role_missing_text(principal: Principal) -> str:
    """Meldung fuer Benutzer eines Kunden-Realms, denen das Cockpit keine Rolle gibt (1.14.0,
    ADR 0058: `benutzer` in den Freigaben)."""
    kurz, name = principal.realm_customer or ("?", "?")
    return L(
        f"Für Sie ist in Automato keine Rolle vergeben ({name}, {kurz}) – das macht der Betreiber "
        "im Cockpit beim Kunden.",
        f"No role is assigned to you in Automato ({name}, {kurz}) – the operator does this in the "
        "cockpit at the customer.",
    )


def without_rights_text(principal: Principal) -> str:
    return (role_missing_text(principal) if principal.realm_role_missing
            else assignment_missing_text(principal))  # fmt: skip


#: auch ohne Zuordnung erreichbar (die Oberflaeche zeigt damit die Meldung statt Fehlern)
_OHNE_ZUORDNUNG = frozenset({("GET", "/auth/me")})


def _customer_gate(request: Request, principal: Principal, required: str) -> None:
    """Kunden-Benutzer: nur freigegebene Endpunkte, je mit der benoetigten Capability fuer
    mindestens einen ihrer Kunden (feinere Rollen, ADR 0018 Ergaenzung). Ein schreibender
    Endpunkt (`required` ueber viewer) laesst sich nie mit dem Leserecht oeffnen - auch nicht
    durch einen falschen Eintrag in der Freigabeliste. Ob das Objekt zum Kunden gehoert, prueft
    der Endpunkt. Ohne Zuordnung im Kunden-Portal (1.13.0) nur `/auth/me`, sonst 403 mit einer
    klaren Meldung statt „insufficient role“ - Rechte gibt es weiterhin keine."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    need = customer_routes.capability(request.method, path) if path is not None else None
    if principal.without_rights:  # ohne Zuordnung bzw. ohne Rolle aus dem Cockpit (1.14.0)
        if (request.method, path) in _OHNE_ZUORDNUNG and required == R.VIEWER:
            request.state.customer_tenants = ()
            return
        raise HTTPException(status_code=403, detail=without_rights_text(principal))
    if need is None or (required != R.VIEWER and need == caps.READ):
        raise HTTPException(status_code=403, detail="not available for customer users")
    if not principal.can(need):
        if need == caps.USERS_MANAGE and any(
            caps.USERS_MANAGE in caps.capabilities(principal.customer_role(c))
            for c in principal.cockpit_customers
        ):  # Kunden-Admin bei Kunden aus dem Cockpit (1.14.0, ADR 0058): Rechte nur dort
            raise HTTPException(status_code=409, detail=verwaltung_store.benutzer_text())
        raise HTTPException(status_code=403, detail="insufficient role")
    # Antwort ohne Geraetenamen (0.58.0, `customer_alias`, Middleware in `main.py`)
    request.state.customer_tenants = tuple(principal.tenant_ids or ())


#: Antwort fuer Endpunkte ausserhalb der Positivliste der Mandanten-Benutzer (ADR 0068)
NOT_FOR_TENANT_USERS = "not available in the own tenant"


def _tenant_gate(request: Request, principal: Principal, required: str) -> None:
    """Mandanten-Benutzer (ADR 0068): nur Endpunkte der Positivliste `auth/tenant_routes.py`, je
    mit dem genannten Recht ihrer Mandanten-Rolle. Ein Endpunkt fuer Betreuer-Operatoren oeffnet
    sich nie mit dem Leserecht, einer fuer Admins nur mit `mandant.manage` - auch nicht durch einen
    falschen Eintrag in der Liste. Ein `tenant_id` eines anderen Mandanten in der Anfrage wird
    gleich abgewiesen; Objekte prueft der Endpunkt (`tenant_scope`). Ohne Mandant (Zuordnung
    fehlt) nur `/auth/me` wie bei Kunden-Benutzern."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if principal.without_rights or not principal.tenant_ids:
        if (request.method, path) in _OHNE_ZUORDNUNG and required == R.VIEWER:
            return
        raise HTTPException(status_code=403, detail=without_rights_text(principal))
    need = tenant_routes.capability(request.method, path) if path is not None else None
    if (need is None
            or (required == R.OPERATOR and need == caps.TENANT_READ)
            or (required == R.ADMIN and need != caps.TENANT_MANAGE)):  # fmt: skip
        raise HTTPException(status_code=403, detail=NOT_FOR_TENANT_USERS)
    if need not in principal.tenant_capabilities:
        raise HTTPException(status_code=403, detail="insufficient role")
    raw = (request.query_params.get("tenant_id") or "").strip()
    if raw and (not raw.isdigit() or int(raw) not in principal.tenant_ids):
        raise HTTPException(status_code=403, detail="tenant not allowed")


require_viewer = require_role(R.VIEWER)
require_operator = require_role(R.OPERATOR)
require_admin = require_role(R.ADMIN)


def tenant_scope(principal: Principal, requested: int | None) -> int | None:
    """Effektiver tenant_id-Filter unter Beachtung der erlaubten Mandanten.

    Admin (tenant_ids=None): `requested` unveraendert (None = alle). Nicht-Admin:
    nur innerhalb der erlaubten Mandanten; ein `requested` ausserhalb -> 403; ohne
    `requested` wird auf den einzigen erlaubten Mandanten verengt (sonst 400/403).
    """
    if principal.is_admin or (principal.tenant_ids is None and not principal.is_customer
                              and not principal.is_tenant_user):  # fmt: skip
        return requested
    allowed = principal.tenant_ids or ()
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
