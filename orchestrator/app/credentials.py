"""Verschlüsselte Geräte-Zugangsdaten (ADR 0009).

Zugangsdaten eines Quellsystems (z. B. FortiGate-API-Token) werden zentral erfasst und dem
zugewiesenen Collector-Agenten beim Abholen eines Laufs übergeben. Gespeichert wird nur ein
Fernet-Token (AES-128-CBC + HMAC-SHA256); Klartext verlässt den Orchestrator ausschliesslich
über den authentisierten Agent-Kanal und nie über die Web-API, Logs oder das Audit-Log.

Schlüssel: `AUTOMATO_CREDENTIALS_KEY` (auch als Secret-Referenz, z. B. `openbao:…`) oder
eine beim ersten Start erzeugte Schlüsseldatei (`credentials_key_file`, eigenes Volume).
Ohne Schlüssel lassen sich keine Zugangsdaten speichern (fail-closed).
"""

import base64
import hashlib
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

log = logging.getLogger(__name__)


class CredentialsUnavailable(RuntimeError):
    """Kein (passender) Schlüssel - Zugangsdaten können nicht ver-/entschlüsselt werden."""


def _key_from_file(path: Path) -> bytes:
    try:
        if path.is_file():
            return path.read_bytes().strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        log.warning("Schlüssel für Geräte-Zugangsdaten neu erzeugt: %s (sichern!)", path)
        return key
    except FileExistsError:
        return path.read_bytes().strip()
    except OSError as exc:
        raise CredentialsUnavailable(f"kein Schlüssel für Zugangsdaten: {exc}") from exc


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    settings = get_settings()
    if settings.credentials_key:
        # Beliebige Passphrase -> stabiler 32-Byte-Fernet-Key
        digest = hashlib.sha256(settings.credentials_key.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))
    path = Path(settings.credentials_key_file)
    try:
        return Fernet(_key_from_file(path))
    except ValueError as exc:
        raise CredentialsUnavailable(f"Schlüsseldatei ungültig: {path}") from exc


def encrypt(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data, sort_keys=True).encode()).decode()


def decrypt(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise CredentialsUnavailable(
            "Schlüssel passt nicht zu den gespeicherten Zugangsdaten"
        ) from exc
