"""Konfiguration der Collector-Worker (Praefix AUTOMATO_)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATO_", env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    orchestrator_url: str = "http://localhost:8000"
    internal_api_token: str = "dev-only-token"  # noqa: S105 - nur lokaler Default

    # Zielsystem-Zugang FortiGate (read-only!). Ohne URL/Token liest der Collector
    # Beispieldaten (Lab noch nicht da). verify_tls im Lab i. d. R. false (selbstsigniert).
    fortigate_url: str = ""
    fortigate_token: str = ""
    fortigate_verify_tls: bool = True
    # Cisco ACI folgt in Phase 2: aci_url / aci_username / aci_password


@lru_cache
def get_settings() -> Settings:
    return Settings()
