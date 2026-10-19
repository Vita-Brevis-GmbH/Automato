"""OIDC/Keycloak-JWT-Validierung + Claim-Mapping (E1.1b, ADR 0007).

Validiert ein Bearer-JWT gegen Keycloak (JWKS/Issuer/Audience) und bildet die Claims
auf einen `Principal` ab: Rollen aus `oidc_roles_claim` (Default Keycloak-Realm-Rollen
`realm_access.roles`), Mandanten aus `oidc_tenants_claim`. Admin ⇒ alle Mandanten
(Global-Admin-Gesamtsicht); sonst nur die zugewiesenen (fehlt die Zuweisung ⇒ keine).

Abgelehnte Tokens liefern einen konkreten Grund (abgelaufen, Issuer/Audience passt nicht,
Schlüssel unbekannt) und werden geloggt - ohne Token-Inhalt. Ist die JWKS von hier aus
nicht erreichbar, ist das ein Server-Problem (503), kein Anmeldefehler: ein erneuter
Login würde nichts ändern.
"""

import logging
import time
from functools import lru_cache

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from app.auth.principal import Principal

log = logging.getLogger(__name__)


def _dig(claims: dict, dotted: str):
    cur = claims
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _parse_tenant_ids(raw) -> tuple[int, ...]:
    if raw is None:
        return ()  # keine Zuweisung -> fail-closed (sieht nichts)
    if isinstance(raw, int | str):
        raw = [raw]
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def claims_to_principal(claims: dict, settings) -> Principal:
    subject = claims.get("sub") or claims.get("preferred_username") or "unknown"
    raw_roles = _dig(claims, settings.oidc_roles_claim) or []
    roles = (
        frozenset(str(r) for r in raw_roles) if isinstance(raw_roles, list | tuple) else frozenset()
    )
    tenant_ids = (
        None if "admin" in roles else _parse_tenant_ids(_dig(claims, settings.oidc_tenants_claim))
    )
    return Principal(subject=str(subject), roles=roles, tenant_ids=tenant_ids)


@lru_cache(maxsize=8)
def _jwks_client(url: str) -> PyJWKClient:
    # Wiederverwenden: PyJWKClient cached das Key-Set (sonst ein JWKS-Abruf pro Request).
    return PyJWKClient(url, timeout=10)


def verify_token(token: str, settings) -> dict:
    """JWT gegen die Keycloak-JWKS validieren; wirft bei ungueltigem Token."""
    signing_key = _jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.oidc_audience or None,
        issuer=settings.oidc_issuer or None,
        leeway=settings.oidc_clock_skew_seconds,
        options={"verify_aud": bool(settings.oidc_audience)},
    )


def _seconds_from_now(token: str, claim: str) -> int | None:
    """Abstand eines Zeit-Claims zur eigenen Uhr - nur fuer die Fehlermeldung.

    Wird erst aufgerufen, nachdem die Signatur geprueft wurde (PyJWT prueft Zeit-Claims
    nach der Signatur), der Wert dient ausschliesslich der Diagnose.
    """
    try:
        value = jwt.decode(token, options={"verify_signature": False}).get(claim)
    except jwt.InvalidTokenError:
        return None
    return int(value - time.time()) if isinstance(value, int | float) else None


def _reject(status: int, detail: str, exc: Exception) -> HTTPException:
    log.warning("OIDC-Token abgelehnt: %s (%s: %s)", detail, type(exc).__name__, exc)
    return HTTPException(status_code=status, detail=detail)


def principal_from_bearer(authorization: str | None, settings) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = verify_token(token, settings)
    except PyJWKClientConnectionError as exc:
        raise _reject(
            503, f"JWKS vom Orchestrator nicht erreichbar: {settings.oidc_jwks_url}", exc
        ) from exc
    except PyJWKClientError as exc:
        raise _reject(
            401, "Signaturschlüssel nicht in der JWKS (Realm/JWKS-URL passt nicht)", exc
        ) from exc
    except jwt.ExpiredSignatureError as exc:
        ago = _seconds_from_now(token, "exp")
        since = f" (seit {-ago} s)" if ago is not None else ""
        raise _reject(401, f"token expired{since}", exc) from exc
    except jwt.ImmatureSignatureError as exc:
        ahead = _seconds_from_now(token, "iat")
        skew = f"ca. {ahead} s " if ahead is not None else ""
        raise _reject(
            401,
            f"token not yet valid: die Uhr des Orchestrators geht {skew}nach gegenüber "
            "Keycloak - Zeitsynchronisation (NTP) beider VMs prüfen",
            exc,
        ) from exc
    except jwt.InvalidAudienceError as exc:
        raise _reject(
            401, f"token audience invalid (erwartet: {settings.oidc_audience})", exc
        ) from exc
    except jwt.InvalidIssuerError as exc:
        raise _reject(401, f"token issuer invalid (erwartet: {settings.oidc_issuer})", exc) from exc
    except jwt.InvalidTokenError as exc:
        raise _reject(401, f"invalid token ({type(exc).__name__})", exc) from exc
    return claims_to_principal(claims, settings)
