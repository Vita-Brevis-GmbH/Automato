"""Konfiguration der Collector-Worker (Praefix AUTOMATO_)."""

from functools import lru_cache

from pydantic import model_validator
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

    # Adresse und Zugangsdaten der Geraete kommen immer vom Quellsystem im Web-UI (ADR 0009).
    # dev | staging | prod: Beispieldaten ohne Zugangsdaten nur im Labor (1.6.0, samples_allowed)
    environment: str = "dev"

    # TLS intern (0.49.0, ADR 0023) - nur der zentrale Worker im Compose-Netz: Orchestrator per
    # HTTPS und Redis per rediss://, beide gegen die interne CA geprueft. Agenten nutzen
    # weiter orchestrator_url/_ca_bundle (bzw. das Agent-Gateway).
    internal_tls: bool = False
    internal_tls_dir: str = "/etc/automato/tls"

    @model_validator(mode="after")
    def _internal_tls(self) -> "Settings":
        if not self.internal_tls:
            return self
        if self.orchestrator_url.startswith("http://"):
            self.orchestrator_url = "https://" + self.orchestrator_url.removeprefix("http://")
        if self.redis_url.startswith("redis://"):
            self.redis_url = "rediss://" + self.redis_url.removeprefix("redis://")
        if not self.orchestrator_ca_bundle:
            self.orchestrator_ca_bundle = f"{self.internal_tls_dir}/ca.crt"
        return self

    def broker_ssl(self) -> dict | None:
        """Celery `broker_use_ssl` fuer rediss:// (Pruefung gegen die interne CA)."""
        if not self.redis_url.startswith("rediss://"):
            return None
        import ssl

        return {
            "ssl_cert_reqs": ssl.CERT_REQUIRED,
            "ssl_ca_certs": f"{self.internal_tls_dir}/ca.crt",
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def samples_allowed() -> bool:
    """Beispieldaten statt eines Geraets (1.6.0): nur der zentrale Worker im Labor
    (AUTOMATO_ENVIRONMENT=dev). Ein Agent im Kundennetz und jeder Betrieb (staging/prod) liest
    nie Beispieldaten - ohne Zugangsdaten schlaegt der Lauf mit klarer Meldung fehl."""
    s = get_settings()
    return not s.agent_token and s.environment == "dev"
