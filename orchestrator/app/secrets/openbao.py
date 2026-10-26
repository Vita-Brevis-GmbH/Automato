"""OpenBao-Provider: Secrets aus OpenBao/Vault (KV v2) über HTTP.

OpenBao ist der MPL-lizenzierte Vault-Fork (Open-Source-Grundsatz, CLAUDE.md). Gelesen
wird genau ein KV-v2-Dokument (`{addr}/v1/{mount}/data/{path}`); der Locator wählt das
Feld darin: `feld` (nutzt den Standardpfad) oder `pfad#feld`. Token wie bei Vault
(`X-Vault-Token`), zur Boot-Zeit via env/Datei bereitgestellt. Nur stdlib (`urllib`),
damit kein zusätzliches Runtime-Dependency nötig ist; `_read` ist überschreibbar (Tests).
"""

import json
import urllib.request

from app.secrets.base import SecretProvider, SecretsError


class OpenBaoProvider(SecretProvider):
    name = "openbao"

    def __init__(self, addr: str, token: str, kv_mount: str, default_path: str) -> None:
        self._addr = addr.rstrip("/")
        self._token = token
        self._mount = kv_mount
        self._default_path = default_path

    def get(self, locator: str) -> str | None:
        if not self._addr or not self._token:
            raise SecretsError(
                "OpenBao nicht konfiguriert (AUTOMATO_OPENBAO_ADDR / AUTOMATO_OPENBAO_TOKEN)"
            )
        path, sep, field = locator.partition("#")
        if not sep:
            path, field = self._default_path, path
        doc = self._read(path)
        try:
            return doc["data"]["data"][field]
        except (KeyError, TypeError) as exc:
            raise SecretsError(f"Secret-Feld '{field}' unter '{path}' nicht gefunden") from exc

    def _read(self, path: str) -> dict:
        """HTTP-Zugriff auf OpenBao; in Tests gemockt."""
        url = f"{self._addr}/v1/{self._mount}/data/{path}"
        # S310: URL ist die konfigurierte OpenBao-Adresse (http/https), kein User-Input.
        req = urllib.request.Request(url, headers={"X-Vault-Token": self._token})  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                return json.loads(resp.read())
        except OSError as exc:
            raise SecretsError(f"OpenBao nicht erreichbar ({url}): {exc}") from exc
