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

from app.auth import capabilities as caps
from app.auth.principal import Principal
from app.i18n import L

CUSTOMER_ROLE = "customer"  # Realm-Rolle der Kunden-Benutzer (ADR 0018)

log = logging.getLogger(__name__)


def _dig(claims: dict, dotted: str):
    cur = claims
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _parse_tenant_ids(raw) -> tuple[int, ...]:
    """IDs aus einem Claim: Zahl, Liste bzw. Text - auch kommagetrennt ("3,4", z. B. aus einem
    fest hinterlegten Attribut eines SAML-Brokers)."""
    if raw is None:
        return ()  # keine Zuweisung -> fail-closed (sieht nichts)
    if isinstance(raw, int | str):
        raw = [raw]
    out: list[int] = []
    for x in raw:
        for part in str(x).split(",") if isinstance(x, str) else [x]:
            try:
                out.append(int(part))
            except (TypeError, ValueError):
                continue
    return tuple(out)


def logins_of(claims: dict) -> tuple[str, ...]:
    """Kennungen fuer die Zuordnung in Automato: sub, Benutzername, E-Mail (klein)."""
    out: list[str] = []
    for key in ("sub", "preferred_username", "email"):
        value = str(claims.get(key) or "").strip().lower()
        if value and value not in out:
            out.append(value)
    return tuple(out)


def customer_role_of(roles: frozenset[str], claimed) -> str | None:
    """Kunden-Rolle aus dem Token (feinere Rollen, ADR 0018 Ergaenzung): der Claim
    `customer_role` (Text bzw. Liste - die hoechste; Unbekanntes = viewer), sonst aus den
    Realm-Rollen wie bis 0.87.0: `operator` = triage, `viewer` bzw. `customer` = viewer. Ohne
    beides keine Rolle (keine Rechte, fail-closed). `admin` gibt nie eine Kunden-Rolle."""
    if claimed not in (None, "", [], ()):
        values = claimed if isinstance(claimed, list | tuple) else str(claimed).split(",")
        return caps.highest(caps.normalize(v) for v in values) or caps.VIEWER
    if "operator" in roles:
        return caps.TRIAGE_ROLE
    if "viewer" in roles or CUSTOMER_ROLE in roles:
        return caps.VIEWER
    return None


def claims_to_principal(claims: dict, settings) -> Principal:
    subject = claims.get("sub") or claims.get("preferred_username") or "unknown"
    logins = logins_of(claims)
    raw_roles = _dig(claims, settings.oidc_roles_claim) or []
    roles = (
        frozenset(str(r) for r in raw_roles) if isinstance(raw_roles, list | tuple) else frozenset()
    )
    # Kunden-Benutzer (ADR 0018): Rolle `customer` bzw. Kunden im Attribut - nie Admin, immer
    # auf Mandanten und Kunden eingeschraenkt (ohne Zuordnung: nichts). Die Rolle `customer`
    # liest (viewer); operator erlaubt zusaetzlich die Triage eigener Findings; feiner ueber den
    # Claim `customer_role` (viewer, triage, editor, customer_admin).
    # Ein leerer Claim allein (Mapper ohne Attribut) macht niemanden zum Kunden-Benutzer.
    raw_customers = _dig(claims, settings.oidc_customers_claim)
    if CUSTOMER_ROLE in roles or raw_customers not in (None, "", [], ()):
        # Kunden-Rolle (feinere Rollen) aus dem Claim `customer_role` bzw. den Realm-Rollen -
        # gilt fuer alle Kunden des Claims
        role = customer_role_of(roles, _dig(claims, settings.oidc_customer_role_claim))
        if CUSTOMER_ROLE in roles:
            roles = roles | {"viewer"}
        roles = roles - {"admin", CUSTOMER_ROLE}
        customers = _parse_tenant_ids(raw_customers)
        return Principal(
            subject=str(subject),
            roles=roles,
            tenant_ids=_parse_tenant_ids(_dig(claims, settings.oidc_tenants_claim)),
            customer_ids=customers,
            logins=logins,
            customer_source="claim",
            customer_roles=tuple((c, role) for c in customers) if role else (),
        )
    tenant_ids = (
        None if "admin" in roles else _parse_tenant_ids(_dig(claims, settings.oidc_tenants_claim))
    )
    return Principal(subject=str(subject), roles=roles, tenant_ids=tenant_ids, logins=logins)


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


def _reject(status: int, detail: str, exc: Exception, en: str | None = None) -> HTTPException:
    """Abgelehnt: Log immer deutsch, Antwort in der Sprache der Anfrage (`en`, ADR 0028)."""
    log.warning("OIDC-Token abgelehnt: %s (%s: %s)", detail, type(exc).__name__, exc)
    return HTTPException(status_code=status, detail=L(detail, en or detail))


def principal_from_bearer(authorization: str | None, settings) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = verify_token(token, settings)
    except PyJWKClientConnectionError as exc:
        url = settings.oidc_jwks_url
        raise _reject(
            503,
            f"JWKS vom Orchestrator nicht erreichbar: {url}",
            exc,
            f"JWKS not reachable from the orchestrator: {url}",
        ) from exc
    except PyJWKClientError as exc:
        raise _reject(
            401,
            "Signaturschlüssel nicht in der JWKS (Realm/JWKS-URL passt nicht)",
            exc,
            "Signing key not in the JWKS (realm/JWKS URL does not match)",
        ) from exc
    except jwt.ExpiredSignatureError as exc:
        ago = _seconds_from_now(token, "exp")
        since = f" (seit {-ago} s)" if ago is not None else ""
        since_en = f" ({-ago} s ago)" if ago is not None else ""
        raise _reject(401, f"token expired{since}", exc, f"token expired{since_en}") from exc
    except jwt.ImmatureSignatureError as exc:
        ahead = _seconds_from_now(token, "iat")
        skew = f"ca. {ahead} s " if ahead is not None else ""
        skew_en = f"approx. {ahead} s " if ahead is not None else ""
        raise _reject(
            401,
            f"token not yet valid: die Uhr des Orchestrators geht {skew}nach gegenüber "
            "Keycloak - Zeitsynchronisation (NTP) beider VMs prüfen",
            exc,
            f"token not yet valid: the orchestrator's clock is {skew_en}behind "
            "Keycloak - check time synchronization (NTP) of both VMs",
        ) from exc
    except jwt.InvalidAudienceError as exc:
        audience = settings.oidc_audience
        raise _reject(
            401,
            f"token audience invalid (erwartet: {audience})",
            exc,
            f"token audience invalid (expected: {audience})",
        ) from exc
    except jwt.InvalidIssuerError as exc:
        issuer = settings.oidc_issuer
        raise _reject(
            401,
            f"token issuer invalid (erwartet: {issuer})",
            exc,
            f"token issuer invalid (expected: {issuer})",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise _reject(401, f"invalid token ({type(exc).__name__})", exc) from exc
    return claims_to_principal(claims, settings)
