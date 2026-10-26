"""Optionale At-Rest-Verschlüsselung des Audit-Payloads (E1.4, ADR 0007).

Verteidigung in der Tiefe: Die *inhaltliche* Secret-Garantie liefert bereits die
Allowlist (`allowlist.py`) - nur benannte, unkritische Keys landen überhaupt im Log.
Diese Schicht verschlüsselt den (bereits gefilterten) `detail`-Payload zusätzlich
at-rest.

Gesteuert über `AUTOMATO_AUDIT_ENCRYPTION_KEY`:
- leer (Default, dev/Tests): Klartext-JSON -> Verhalten/Tests unverändert (SQLite).
- gesetzt: symmetrische Verschlüsselung (Fernet = AES-128-CBC + HMAC-SHA256). Der
  Schlüssel kommt später aus dem Secrets-/Vault-Provider (E1.5), nie aus dem Repo.

Warum app-seitig statt pgcrypto (Magister-Muster)? pgcrypto verschlüsselt DB-seitig
(`pgp_sym_encrypt`), ist aber Postgres-spezifisch und hielte den Schlüssel in DB-Nähe.
App-seitig bleibt der Schlüssel in der Anwendung/Vault, und die Tests bleiben
DB-agnostisch (SQLite). Die DB-seitige Variante bleibt als Option offen.
"""

import base64
import hashlib
import json
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_ENC_KEY = "_enc"  # Marker im gespeicherten detail-Dict: Wert = Fernet-Token


@lru_cache(maxsize=8)
def _fernet_for(secret: str) -> Fernet:
    # Beliebige Passphrase -> stabiler, gültiger 32-Byte-Fernet-Key.
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_detail(detail: dict | None) -> dict | None:
    """Verschlüsselt `detail`, falls ein Schlüssel konfiguriert ist; sonst unverändert."""
    if detail is None:
        return None
    secret = get_settings().audit_encryption_key
    if not secret:
        return detail  # Klartext (dev/Tests)
    token = _fernet_for(secret).encrypt(json.dumps(detail, sort_keys=True).encode())
    return {_ENC_KEY: token.decode()}


def decrypt_detail(stored: dict | None) -> dict | None:
    """Macht einen gespeicherten Payload lesbar (entschlüsselt, falls nötig)."""
    if not isinstance(stored, dict) or _ENC_KEY not in stored:
        return stored  # Klartext oder None
    secret = get_settings().audit_encryption_key
    if not secret:
        return stored  # Schlüssel fehlt -> verschlüsselt lassen, nicht raten
    try:
        raw = _fernet_for(secret).decrypt(stored[_ENC_KEY].encode())
    except InvalidToken:
        return stored  # falscher Schlüssel/korrupt -> Rohform zurück
    return json.loads(raw)
