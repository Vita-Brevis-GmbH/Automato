"""Konfiguration der Collector-Worker (Praefix AUTOMATO_)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATO_", env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    orchestrator_url: str = "http://localhost:8000"
    internal_api_token: str = "dev-only-token"  # noqa: S105 - nur lokaler Default

    # Zielsystem-Zugaenge (read-only!) folgen mit M7/Lab, z. B.:
    # fortigate_url / fortigate_api_token, aci_url / aci_username / aci_password


@lru_cache
def get_settings() -> Settings:
    return Settings()
