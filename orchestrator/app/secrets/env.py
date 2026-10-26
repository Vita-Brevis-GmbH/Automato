"""Env-Provider: Secrets aus Umgebungsvariablen (Lab/Dev, wie `.env` heute)."""

import os

from app.secrets.base import SecretProvider


class EnvProvider(SecretProvider):
    name = "env"

    def get(self, locator: str) -> str | None:
        return os.environ.get(locator)
