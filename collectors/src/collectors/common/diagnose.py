"""Verstaendliche Meldung, wenn ein Zielsystem die Anmeldung ablehnt (HTTP 401/403).

Ein nacktes `401 Unauthorized` sagt nicht, *warum*: falsches Passwort, Remote-Benutzer ohne
Login-Domaene, fehlendes API-Recht, gesperrtes Konto. Darum: Fehlertext des Geraets (APIC
`imdata.error`, WAPI `Error`/`text`, sonst Klartext) + Hinweis je Plattform.

Benutzername und Passwort-Hinweise (Leerzeichen am Rand, Nicht-ASCII-Zeichen) nur lokal
(`--check` auf der Station, `local=True`) - nie in die Lauf-Meldung an den Orchestrator,
dort sind Zugangsdaten nur schreibbar (ADR 0009). Das Passwort selbst erscheint nie.
"""

import re

import httpx

from collectors.common import http
from collectors.common.dto import Target
from collectors.config import get_settings

_HINTS = {
    "cisco_aci": (
        "APIC-Benutzer/-Passwort im Web-UI neu setzen; Remote-Benutzer (LDAP/RADIUS/TACACS) "
        "als apic#<Login-Domaene>\\<Benutzer>; Konto am APIC aktiv und Passwort nicht abgelaufen?"
    ),
    "infoblox": (
        "Infoblox-Benutzer/-Passwort im Web-UI neu setzen; die Admin-Gruppe des Benutzers braucht "
        "'API' unter Allowed Interfaces (AD-/RADIUS-Benutzer: Remote-Admin-Gruppe mit API-Recht); "
        "neues Konto: erst einmal in der NIOS-GUI anmelden (Passwortwechsel)"
    ),
    "fortigate": (
        "API-Token im Web-UI neu setzen; Trusted Hosts des REST-API-Admins muessen den "
        "Agent-Rechner enthalten"
    ),
    "netbox": "Lese-Token im Web-UI neu setzen (NetBox v2-Token: nbt_<Schluessel>.<Token>)",
}
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def device_error(
    exc: Exception, platform: str, target: Target | None = None, local: bool = False
) -> str:
    """Fehlermeldung eines Geraete-Zugriffs; bei 401/403 mit Ursache und Hinweis."""
    base = http.describe_error(exc)
    if not (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in (401, 403)
        and not _from_orchestrator(exc)  # z. B. Teil-Upload abgelehnt: kein Geraete-Login
    ):
        return base
    parts = [f"Anmeldung abgelehnt ({base})"]
    text = _device_text(exc.response)
    if text:
        parts.append(f"Geraet meldet: {text}")
    if local and target is not None:
        parts.extend(_credential_notes(platform, target))
    hint = _HINTS.get(platform)
    if hint:
        parts.append(f"Hinweis: {hint}")
    return ". ".join(parts)


def _from_orchestrator(exc: httpx.HTTPStatusError) -> bool:
    try:
        orchestrator = httpx.URL(get_settings().orchestrator_url).host
    except (httpx.InvalidURL, TypeError):
        return False
    return bool(orchestrator) and exc.request.url.host == orchestrator


def _device_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    text = ""
    if isinstance(body, dict):
        imdata = body.get("imdata")
        if isinstance(imdata, list) and imdata and isinstance(imdata[0], dict):
            text = ((imdata[0].get("error") or {}).get("attributes") or {}).get("text") or ""
        text = text or body.get("text") or body.get("Error") or body.get("detail") or ""
        text = str(text)
    elif body is None:
        text = _TAGS.sub(" ", response.text or "")
    return _SPACE.sub(" ", text).strip()[:200]


def _credential_notes(platform: str, target: Target) -> list[str]:
    notes = []
    user = target.username or ""
    if user:
        notes.append(f"Benutzer: '{user}'")
        if platform == "cisco_aci" and "\\" in user and not user.lower().startswith("apic#"):
            notes.append(
                "Benutzer mit Domaene: am APIC als apic#<Login-Domaene>\\<Benutzer> angeben"
            )
    password = target.password or ""
    if password and password != password.strip():
        notes.append("Passwort beginnt oder endet mit Leerzeichen (beim Einfuegen mitkopiert?)")
    if password and not password.isascii():
        notes.append(
            "Passwort enthaelt Zeichen ausserhalb ASCII (z. B. Umlaute) - manche Geraete "
            "lehnen sie bei der API-Anmeldung ab"
        )
    return notes
