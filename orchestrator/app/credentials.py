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


# Zugangsdaten-Felder je Plattform (alle nur schreibbar). Ein Geraet hat entweder alle
# Felder seiner Plattform oder keine.
FIELDS: dict[str, tuple[str, ...]] = {
    "fortigate": ("token",),  # REST-API-Token (read-only Admin-Profil)
    "cisco_aci": ("username", "password"),  # APIC-Login (read-only Rolle)
    "infoblox": ("username", "password"),  # WAPI-Benutzer (read-only, ADR 0010)
    "netbox": ("token",),  # Lese-Token
    "vmware": ("username", "password"),  # vCenter-Konto mit Rolle Read-only (ADR 0017)
    "cisco_switch": ("username", "password"),  # SSH-Konto nur mit Leserechten (ADR 0025)
    "avi": ("username", "password"),  # NSX ALB: Rolle nur mit Leserechten (ADR 0027)
}
# Optionale Felder: NetBox-Schreib-Token nur fuer den NetBox-Sync des Orchestrators - wird nie
# an einen Agenten ausgeliefert (siehe store.agents.target_for), ADR 0010.
OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {"netbox": ("write_token",)}

# Switches & Router mit Zugang SNMPv3 (ADR 0033): nur authPriv - Benutzer und beide Passwoerter
# Pflicht; Protokolle (sonst Vorgabe) und Context optional. Kein v1/v2c, keine Community.
SNMP_FIELDS: tuple[str, ...] = ("username", "auth_password", "priv_password")
SNMP_OPTIONAL: tuple[str, ...] = ("auth_protocol", "priv_protocol", "context")
SNMP_AUTH_PROTOCOLS = ("sha", "sha224", "sha256", "sha384", "sha512")  # HMAC-SHA (RFC 3414/7860)
SNMP_PRIV_PROTOCOLS = ("aes128", "aes192", "aes256")  # AES-CFB (RFC 3826)
SNMP_DEFAULTS = {"auth_protocol": "sha256", "priv_protocol": "aes128"}
SNMP_MIN_PASSWORD = 8  # RFC 3414: kuerzere Passwoerter ergeben keinen sicheren Schluessel


def is_snmp(platform: str, access: str | None) -> bool:
    """Quellsystem „Switches & Router“ mit Zugang SNMPv3 (statt SSH)."""
    return platform == "cisco_switch" and access == "snmp"


def fields(platform: str, access: str | None = None) -> tuple[str, ...]:
    """Pflichtfelder der Zugangsdaten je Plattform und Zugang."""
    return SNMP_FIELDS if is_snmp(platform, access) else FIELDS.get(platform, ())


def optional_fields(platform: str, access: str | None = None) -> tuple[str, ...]:
    return SNMP_OPTIONAL if is_snmp(platform, access) else OPTIONAL_FIELDS.get(platform, ())


def complete(platform: str, token: str | None, access: str | None = None) -> bool:
    """Sind die Pflichtfelder der Plattform hinterlegt (z. B. NetBox: Lese-Token)? Ein
    NetBox-Quellsystem nur mit Schreib-Token ist damit *nicht* vollstaendig. Ohne lesbaren
    Schluessel zaehlt, ob ueberhaupt etwas gespeichert ist."""
    if not token:
        return False
    try:
        data = decrypt(token)
    except CredentialsUnavailable:
        return True
    return all(data.get(f) for f in fields(platform, access))


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
