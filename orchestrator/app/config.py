"""Konfiguration - vollstaendig ueber Umgebungsvariablen (Praefix AUTOMATO_)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Nur fuer lokale Entwicklung gueltiger Default; ausserhalb "dev" abgelehnt
# (siehe check_security_config, ADR 0005).
DEFAULT_INTERNAL_TOKEN = "dev-only-token"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATO_", env_file=".env", extra="ignore")

    environment: str = "dev"
    database_url: str = "postgresql+psycopg://automato:automato@localhost:5432/automato"
    redis_url: str = "redis://localhost:6379/0"
    # Token, mit dem Collectors Ergebnisse an die interne API liefern.
    # In jeder echten Umgebung ueber ENV setzen - der Default gilt nur lokal.
    internal_api_token: str = DEFAULT_INTERNAL_TOKEN
    # Erlaubte CORS-Origins fuers Web-UI; in Produktion explizit setzen.
    cors_origins: list[str] = ["http://localhost:5173"]
    # Die oeffentliche Read-API (/rules, /findings, /nat, /applications) ist noch
    # NICHT authentifiziert (AuthN/RBAC = Folge-ADR, ADR 0005). Ausserhalb "dev" muss
    # ein bewusstes Opt-in gesetzt werden, sonst faellt der Start fail-closed aus -
    # verhindert versehentliche Exposition echter Kundendaten ohne RBAC.
    allow_unauthenticated_read_api: bool = False

    # AuthN/RBAC (E1, ADR 0007). dev: Standard-Admin-Principal (unveraendertes Verhalten),
    # Override via X-Dev-Roles/X-Dev-Tenants fuer Tests/Demo. oidc: JWT gegen Keycloak
    # (Issuer/JWKS/Audience, Rollen/Mandanten aus Claims) - Folgeschritt E1.1b.
    auth_mode: str = "dev"  # dev | oidc
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_roles_claim: str = "realm_access.roles"  # Keycloak-Realm-Rollen
    oidc_tenants_claim: str = "tenant_ids"
    oidc_web_client_id: str = "automato-web"  # public Client fuer das SPA (PKCE)

    # SoT-Zugaenge (lesend) - je Mandant wird der passende Adapter gebaut (ADR 0004).
    # Model 2 (NetBox) zuerst; Infoblox (Model 1) in Phase 2.
    netbox_url: str = ""
    netbox_token: str = ""
    netbox_verify_tls: bool = True
    infoblox_url: str = ""
    infoblox_username: str = ""
    infoblox_password: str = ""
    infoblox_verify_tls: bool = True


def check_security_config(settings: "Settings") -> None:
    """Fail-closed: verhindert unsichere Defaults ausserhalb der Entwicklung (ADR 0005)."""
    if settings.environment != "dev" and settings.internal_api_token == DEFAULT_INTERNAL_TOKEN:
        raise RuntimeError(
            "AUTOMATO_INTERNAL_API_TOKEN nutzt den Entwicklungs-Default in einer "
            f"'{settings.environment}'-Umgebung. Bitte ein echtes Token setzen "
            "(z. B. openssl rand -hex 32)."
        )
    if settings.environment != "dev" and not settings.allow_unauthenticated_read_api:
        raise RuntimeError(
            "Die oeffentliche Read-API ist ohne AuthN/RBAC in einer "
            f"'{settings.environment}'-Umgebung nicht freigegeben (ADR 0005). Entweder RBAC "
            "umsetzen oder bewusst AUTOMATO_ALLOW_UNAUTHENTICATED_READ_API=true setzen "
            "(nur mit Netzsegmentierung/Reverse-Proxy-Auth davor)."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
