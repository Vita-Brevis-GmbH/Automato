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
    "vmware": (
        "vCenter-Benutzer/-Passwort im Web-UI neu setzen (SSO-Konto als benutzer@vsphere.local "
        "bzw. DOMAENE\\benutzer); das Konto braucht nur die Rolle Read-only (oberste Ebene, "
        "an untergeordnete Objekte weitergegeben)"
    ),
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
    "cisco_switch": (
        "SSH-Benutzer/-Passwort im Web-UI neu setzen; das Konto braucht nur Leserechte (IOS "
        "privilege 1 bzw. eine View mit den show-Befehlen, NX-OS-Rolle network-operator, Junos-"
        "Klasse read-only, Catalyst 1200/1300 Stufe 1), Anmeldung mit Passwort "
        "(keyboard-interactive bzw. password) muss erlaubt sein"
    ),
}
# vCenter: Rolle Read-only reicht (System.View/System.Read) - fehlt sie, ist sie meist am
# falschen Ort oder ohne Weitergabe zugewiesen
_VSPHERE_PERMISSION = (
    "im vCenter dem Konto die Rolle Read-only auf dem vCenter-Objekt (oberste Ebene des "
    "Inventars) zuweisen, mit 'An untergeordnete Objekte weitergeben' (Propagate to children) - "
    "oder unter Verwaltung > Zugriffssteuerung > Globale Berechtigungen, ebenfalls mit "
    "Weitergabe; ohne Weitergabe gilt eine globale Berechtigung nicht fuer das Inventar. "
    "Ist das Konto ueber eine SSO-/AD-Gruppe berechtigt: Mitgliedschaft pruefen"
)
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def device_error(
    exc: Exception, platform: str, target: Target | None = None, local: bool = False
) -> str:
    """Fehlermeldung eines Geraete-Zugriffs; bei 401/403 mit Ursache und Hinweis."""
    vsphere = _vsphere_fault(exc)
    if vsphere:
        return vsphere
    ssh = _ssh_error(exc, platform, target, local)
    if ssh:
        return ssh
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


def _ssh_error(exc: Exception, platform: str, target: Target | None, local: bool) -> str | None:
    """SSH (Switches, paramiko): Anmeldung, Host-Schluessel, Erreichbarkeit als ein Satz."""
    name = type(exc).__name__
    if name in ("AuthenticationException", "BadAuthenticationType"):
        parts = ["Anmeldung abgelehnt (SSH: Benutzer oder Passwort falsch bzw. Passwort-Anmeldung "
                 "nicht erlaubt)"]  # fmt: skip
        if local and target is not None:
            parts.extend(_credential_notes(platform, target))
        return ". ".join([*parts, f"Hinweis: {_HINTS['cisco_switch']}"])
    if name == "HostKeyChanged":
        return str(exc)
    if name == "SessionLoginFailed":  # Catalyst 1200/1300: Anmeldung in der Sitzung (0.68.0)
        return (
            f"Anmeldung abgelehnt (SSH-Sitzung: „User Name:“/„Password:“). Hinweis: "
            f"{_HINTS['cisco_switch']}"
        )
    if name == "NoValidConnectionsError":
        return "nicht erreichbar: Verbindung zu Port 22 abgelehnt (SSH aus bzw. Weg gesperrt)"
    if name in ("TimeoutError", "timeout") and platform == "cisco_switch":
        return f"nicht erreichbar bzw. keine Antwort: {exc}".rstrip(": ")
    if name == "SSHException":
        return f"SSH-Fehler: {exc}"
    return None


def _vsphere_fault(exc: Exception) -> str | None:
    """vSphere-Fehler (pyVmomi, Klassenname wie `vim.fault.NoPermission`) als ein Satz statt
    des mehrzeiligen Fault-Abbilds."""
    name = type(exc).__name__.rsplit(".", 1)[-1]
    if name == "InvalidLogin":
        hint = _HINTS["vmware"]
        return f"Anmeldung abgelehnt (vCenter: Benutzer oder Passwort falsch). Hinweis: {hint}"
    if name != "NoPermission":
        return None
    obj = getattr(exc, "object", None)
    moid = getattr(obj, "_moId", None)
    where = f"{type(obj).__name__}:{moid}" if moid else "einem Inventar-Objekt"
    if moid == "group-d1":  # Wurzelordner jedes vCenters
        where += " (oberste Ebene des Inventars)"
    privileges = sorted(
        {
            p
            for entry in getattr(exc, "missingPrivileges", None) or []
            for p in getattr(entry, "privilegeIds", None) or []
        }
        or {getattr(exc, "privilegeId", None) or "Leserecht"}
    )
    return (
        f"Keine Berechtigung im vCenter: dem Konto fehlt {', '.join(privileges)} auf {where}. "
        f"Hinweis: {_VSPHERE_PERMISSION}"
    )


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
