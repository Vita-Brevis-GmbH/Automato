"""Konfiguration - vollstaendig ueber Umgebungsvariablen (Praefix AUTOMATO_)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATO_", env_file=".env", extra="ignore")

    environment: str = "dev"
    database_url: str = "postgresql+psycopg://automato:automato@localhost:5432/automato"
    redis_url: str = "redis://localhost:6379/0"
    # Token, mit dem Collectors Ergebnisse an die interne API liefern.
    # In jeder echten Umgebung ueber ENV setzen - der Default gilt nur lokal.
    internal_api_token: str = "dev-only-token"  # noqa: S105


@lru_cache
def get_settings() -> Settings:
    return Settings()
