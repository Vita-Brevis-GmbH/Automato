"""Secrets-Provider (E1.5, ADR 0007 D4): Secrets nie im Klartext/Repo.

Konfigurationswerte dürfen als **Referenz** `schema:locator` vorliegen und werden beim
Start aufgelöst:

- `env:VARNAME`            – Umgebungsvariable
- `file:NAME` / `file:/pfad` – Datei (Docker-/K8s-Secret, SOPS/age-entschlüsselt)
- `openbao:FELD` / `openbao:pfad#feld` – OpenBao/Vault (KV v2)
- `literal:WERT`           – Escape-Hatch für Klartext, der ein `:` enthält

Werte **ohne** bekanntes Schema bleiben unverändert → `.env`/Dev verhält sich wie bisher.
`resolve_settings` löst genau die secret-tragenden Felder der Settings einmalig beim
App-Start auf (siehe `app.main.create_app`). So bleibt der Rest der Anwendung ahnungslos
und nutzt `settings.netbox_token` etc. unverändert (Adapter-Pattern wie beim SoT-Gateway).
"""

from app.secrets.base import SecretProvider, SecretsError
from app.secrets.env import EnvProvider
from app.secrets.file import FileProvider
from app.secrets.openbao import OpenBaoProvider

# Felder der Settings, die ein Secret tragen und beim Start aufgelöst werden.
SECRET_FIELDS: tuple[str, ...] = (
    "internal_api_token",
    "netbox_token",
    "infoblox_username",
    "infoblox_password",
    "audit_encryption_key",
)

_SCHEMES = frozenset({"env", "file", "openbao", "literal"})


def _provider_for(scheme: str, settings) -> SecretProvider:
    if scheme == "env":
        return EnvProvider()
    if scheme == "file":
        return FileProvider(settings.secrets_file_dir)
    if scheme == "openbao":
        return OpenBaoProvider(
            settings.openbao_addr,
            settings.openbao_token,
            settings.openbao_kv_mount,
            settings.openbao_path,
        )
    raise SecretsError(f"unbekanntes Secret-Schema: {scheme}")  # pragma: no cover


def resolve(value: str, settings) -> str:
    """Löst eine `schema:locator`-Referenz auf; Klartext bleibt unverändert."""
    if not value:
        return value
    scheme, sep, rest = value.partition(":")
    if not sep or scheme not in _SCHEMES:
        return value  # Klartext (kein bekanntes Schema) -> unverändert
    if scheme == "literal":
        return rest
    resolved = _provider_for(scheme, settings).get(rest)
    if resolved is None:
        raise SecretsError(f"Secret '{value}' nicht gefunden")
    return resolved


def resolve_settings(settings) -> None:
    """Löst alle secret-tragenden Felder in `settings` in-place auf (idempotent)."""
    for field in SECRET_FIELDS:
        raw = getattr(settings, field, "")
        if isinstance(raw, str) and raw:
            setattr(settings, field, resolve(raw, settings))


__all__ = [
    "SecretProvider",
    "SecretsError",
    "EnvProvider",
    "FileProvider",
    "OpenBaoProvider",
    "SECRET_FIELDS",
    "resolve",
    "resolve_settings",
]
