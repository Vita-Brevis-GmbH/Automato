"""Konfiguration der Collector-Worker (Praefix AUTOMATO_)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATO_", env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    orchestrator_url: str = "http://localhost:8000"
    internal_api_token: str = "dev-only-token"  # noqa: S105 - nur lokaler Default
    # Agent-Token fuer das Pull-Modell (ADR 0008); nur fuer den externen Standalone-Agent.
    agent_token: str = ""
    # Selbst-Update des installierten Agenten (ADR 0011); false = nur manuell aktualisieren
    agent_auto_update: bool = True
    # Oeffentlicher Ed25519-Schluessel (Base64): gesetzt = nur signierte Updates installieren
    agent_signing_pubkey: str = ""
    # TLS zum Orchestrator (ADR 0008): optionale mTLS-Client-Zertifikate + CA-Pin.
    orchestrator_verify_tls: bool = True
    orchestrator_ca_bundle: str = ""  # CA-Pfad zur Server-Verifikation (Pinning)
    client_cert: str = ""  # mTLS Client-Zertifikat (PEM)
    client_key: str = ""  # mTLS Client-Key (PEM)

    # Zielsystem-Zugang lokal - nur Rueckfall: normalerweise kommen Adresse und Zugangsdaten
    # vom Quellsystem im Web-UI (ADR 0009). Zentral/Lab ohne beides: Beispieldaten.
    # verify_tls im Lab i. d. R. false (selbstsigniert). Alles read-only!
    fortigate_url: str = ""
    fortigate_token: str = ""
    fortigate_verify_tls: bool = True
    aci_url: str = ""
    aci_username: str = ""
    aci_password: str = ""
    aci_verify_tls: bool = True
    aci_tenants: str = ""  # kommagetrennt, leer = alle ausser infra/mgmt


@lru_cache
def get_settings() -> Settings:
    return Settings()
