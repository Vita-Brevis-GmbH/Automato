"""Verstaendliche Meldung, wenn ein Zielsystem die Anmeldung ablehnt (HTTP 401/403).

Ein nacktes `401 Unauthorized` sagt nicht, *warum*: falsches Passwort, Remote-Benutzer ohne
Login-Domaene, fehlendes API-Recht, gesperrtes Konto. Darum: Fehlertext des Geraets (APIC
`imdata.error`, WAPI `Error`/`text`, sonst Klartext) + Hinweis je Plattform.

Benutzername und Passwort-Hinweise (Leerzeichen am Rand, Nicht-ASCII-Zeichen) nur lokal
(`--check` auf der Station, `local=True`) - nie in die Lauf-Meldung an den Orchestrator,
dort sind Zugangsdaten nur schreibbar (ADR 0009). Das Passwort selbst erscheint nie.

Zweisprachig (0.90.0, ADR 0028 Schritt 4): im Lauf deutsch (der Orchestrator speichert die
Meldung und uebersetzt beim Abruf, `orchestrator/app/store/run_text.py` - dort dieselben
englischen Texte), bei `--check` in der Sprache der Konsole (`AUTOMATO_LANG`).
"""

import re

import httpx

from collectors.common import http
from collectors.common.dto import Target
from collectors.common.i18n import L
from collectors.config import get_settings

# Hinweis je Plattform (deutsch, englisch) - Paare statt L() auf Modulebene (Sprache zur Laufzeit)
_HINTS = {
    "avi": (
        "Avi-Benutzer/-Passwort im Web-UI neu setzen; das Konto braucht eine Rolle mit nur "
        "Leserechten (Virtual Service, VS VIP, Pool, Pool Group, VRF Context, Application "
        "Profile, Tenant) und Zugriff auf die Tenants im Lesebereich",
        "set the Avi user/password again in the web UI; the account needs a role with read-only "
        "rights (Virtual Service, VS VIP, Pool, Pool Group, VRF Context, Application Profile, "
        "Tenant) and access to the tenants in the read scope",
    ),
    "vmware": (
        "vCenter-Benutzer/-Passwort im Web-UI neu setzen (SSO-Konto als benutzer@vsphere.local "
        "bzw. DOMAENE\\benutzer); das Konto braucht nur die Rolle Read-only (oberste Ebene, "
        "an untergeordnete Objekte weitergegeben)",
        "set the vCenter user/password again in the web UI (SSO account as user@vsphere.local "
        "or DOMAIN\\user); the account only needs the role Read-only (top level, propagated to "
        "child objects)",
    ),
    "cisco_aci": (
        "APIC-Benutzer/-Passwort im Web-UI neu setzen; Remote-Benutzer (LDAP/RADIUS/TACACS) "
        "als apic#<Login-Domaene>\\<Benutzer>; Konto am APIC aktiv und Passwort nicht abgelaufen?",
        "set the APIC user/password again in the web UI; remote users (LDAP/RADIUS/TACACS) as "
        "apic#<login domain>\\<user>; is the account active on the APIC and the password not "
        "expired?",
    ),
    "infoblox": (
        "Infoblox-Benutzer/-Passwort im Web-UI neu setzen; die Admin-Gruppe des Benutzers braucht "
        "'API' unter Allowed Interfaces (AD-/RADIUS-Benutzer: Remote-Admin-Gruppe mit API-Recht); "
        "neues Konto: erst einmal in der NIOS-GUI anmelden (Passwortwechsel)",
        "set the Infoblox user/password again in the web UI; the user's admin group needs 'API' "
        "under Allowed Interfaces (AD/RADIUS users: remote admin group with API permission); new "
        "account: log in to the NIOS GUI once first (password change)",
    ),
    "fortigate": (
        "API-Token im Web-UI neu setzen; Trusted Hosts des REST-API-Admins muessen den "
        "Agent-Rechner enthalten",
        "set the API token again in the web UI; the trusted hosts of the REST API admin must "
        "include the agent machine",
    ),
    "netbox": (
        "Lese-Token im Web-UI neu setzen (NetBox v2-Token: nbt_<Schluessel>.<Token>)",
        "set the read token again in the web UI (NetBox v2 token: nbt_<key>.<token>)",
    ),
    "cisco_switch": (
        "SSH-Benutzer/-Passwort im Web-UI neu setzen; das Konto braucht nur Leserechte (IOS "
        "privilege 1 bzw. eine View mit den show-Befehlen, NX-OS-Rolle network-operator, Junos-"
        "Klasse read-only, Catalyst 1200/1300 Stufe 1, Arista-Rolle network-operator, AOS-CX-"
        "Gruppe operators), Anmeldung mit Passwort "
        "(keyboard-interactive bzw. password) muss erlaubt sein",
        "set the SSH user/password again in the web UI; the account only needs read rights (IOS "
        "privilege 1 or a view with the show commands, NX-OS role network-operator, Junos class "
        "read-only, Catalyst 1200/1300 level 1, Arista role network-operator, AOS-CX group "
        "operators), login with password (keyboard-interactive or password) must be allowed",
    ),
}
# vCenter: Rolle Read-only reicht (System.View/System.Read) - fehlt sie, ist sie meist am
# falschen Ort oder ohne Weitergabe zugewiesen
_VSPHERE_PERMISSION = (
    "im vCenter dem Konto die Rolle Read-only auf dem vCenter-Objekt (oberste Ebene des "
    "Inventars) zuweisen, mit 'An untergeordnete Objekte weitergeben' (Propagate to children) - "
    "oder unter Verwaltung > Zugriffssteuerung > Globale Berechtigungen, ebenfalls mit "
    "Weitergabe; ohne Weitergabe gilt eine globale Berechtigung nicht fuer das Inventar. "
    "Ist das Konto ueber eine SSO-/AD-Gruppe berechtigt: Mitgliedschaft pruefen",
    "in vCenter assign the role Read-only to the account on the vCenter object (top level of "
    "the inventory), with 'Propagate to children' - or under Administration > Access Control > "
    "Global Permissions, also with propagation; without propagation a global permission does "
    "not apply to the inventory. If the account is authorized via an SSO/AD group: check the "
    "membership",
)


def _hint(platform: str) -> str | None:
    pair = _HINTS.get(platform)
    return L(*pair) if pair else None


_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def device_error(
    exc: Exception, platform: str, target: Target | None = None, local: bool = False
) -> str:
    """Fehlermeldung eines Geraete-Zugriffs; bei 401/403 mit Ursache und Hinweis. Sprache nach
    `collectors.common.i18n`: im Lauf deutsch (gespeichert), bei `--check` die der Konsole."""
    vsphere = _vsphere_fault(exc)
    if vsphere:
        return vsphere
    ssh = _ssh_error(exc, platform, target, local)
    if ssh:
        return ssh
    base = http.describe_error(exc)
    reports = L("Geraet meldet", "Device reports")
    if not (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in (401, 403)
        and not _from_orchestrator(exc)  # z. B. Teil-Upload abgelehnt: kein Geraete-Login
    ):
        # andere Ablehnung des Geraets (z. B. HTTP 400, 0.78.3): mit seiner Begruendung
        if isinstance(exc, httpx.HTTPStatusError) and not _from_orchestrator(exc):
            text = _device_text(exc.response)
            if text:
                return f"{base}. {reports}: {text}"
        return base
    parts = [L(f"Anmeldung abgelehnt ({base})", f"Login rejected ({base})")]
    text = _device_text(exc.response)
    if text:
        parts.append(f"{reports}: {text}")
    if local and target is not None:
        parts.extend(_credential_notes(platform, target))
    hint = _hint(platform)
    if hint:
        parts.append(f"{_note()}: {hint}")
    return ". ".join(parts)


def _note() -> str:
    return L("Hinweis", "Note")


def _ssh_error(exc: Exception, platform: str, target: Target | None, local: bool) -> str | None:
    """SSH (Switches, paramiko): Anmeldung, Host-Schluessel, Erreichbarkeit als ein Satz."""
    name = type(exc).__name__
    if name in ("AuthenticationException", "BadAuthenticationType"):
        parts = [L("Anmeldung abgelehnt (SSH: Benutzer oder Passwort falsch bzw. Passwort-"
                   "Anmeldung nicht erlaubt)",
                   "Login rejected (SSH: user or password wrong, or password login not "
                   "allowed)")]  # fmt: skip
        if local and target is not None:
            parts.extend(_credential_notes(platform, target))
        return ". ".join([*parts, f"{_note()}: {_hint('cisco_switch')}"])
    if name in ("HostKeyChanged", "UnsupportedDevice"):
        return str(exc)
    if name == "SessionLoginFailed":  # Catalyst 1200/1300: Anmeldung in der Sitzung (0.68.0)
        return (
            L(
                "Anmeldung abgelehnt (SSH-Sitzung: „User Name:“/„Password:“)",
                "Login rejected (SSH session: “User Name:”/“Password:”)",
            )
            + f". {_note()}: {_hint('cisco_switch')}"
        )
    if name == "NoValidConnectionsError":
        return L(
            "nicht erreichbar: Verbindung zu Port 22 abgelehnt (SSH aus bzw. Weg gesperrt)",
            "not reachable: connection to port 22 refused (SSH off or path blocked)",
        )
    if name in ("TimeoutError", "timeout") and platform == "cisco_switch":
        what = L("nicht erreichbar bzw. keine Antwort", "not reachable or no response")
        return f"{what}: {exc}".rstrip(": ")
    if name == "SSHException":
        return L(f"SSH-Fehler: {exc}", f"SSH error: {exc}")
    return None


def _vsphere_fault(exc: Exception) -> str | None:
    """vSphere-Fehler (pyVmomi, Klassenname wie `vim.fault.NoPermission`) als ein Satz statt
    des mehrzeiligen Fault-Abbilds."""
    name = type(exc).__name__.rsplit(".", 1)[-1]
    if name == "InvalidLogin":
        rejected = L(
            "Anmeldung abgelehnt (vCenter: Benutzer oder Passwort falsch)",
            "Login rejected (vCenter: user or password wrong)",
        )
        return f"{rejected}. {_note()}: {_hint('vmware')}"
    if name != "NoPermission":
        return None
    obj = getattr(exc, "object", None)
    moid = getattr(obj, "_moId", None)
    where = (
        f"{type(obj).__name__}:{moid}"
        if moid
        else L("einem Inventar-Objekt", "an inventory object")
    )
    if moid == "group-d1":  # Wurzelordner jedes vCenters
        where += L(" (oberste Ebene des Inventars)", " (top level of the inventory)")
    privileges = sorted(
        {
            p
            for entry in getattr(exc, "missingPrivileges", None) or []
            for p in getattr(entry, "privilegeIds", None) or []
        }
        or {getattr(exc, "privilegeId", None) or L("Leserecht", "read permission")}
    )
    missing = ", ".join(privileges)
    return (
        L(
            f"Keine Berechtigung im vCenter: dem Konto fehlt {missing} auf {where}",
            f"No permission in vCenter: the account lacks {missing} on {where}",
        )
        + f". {_note()}: {L(*_VSPHERE_PERMISSION)}"
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
        error = body.get("error")  # NSX ALB (Avi): {"error": "..."}
        text = (text or body.get("text") or body.get("Error") or body.get("detail")
                or (error if isinstance(error, str) else "") or "")  # fmt: skip
        text = str(text)
    elif body is None:
        text = _TAGS.sub(" ", response.text or "")
    return _SPACE.sub(" ", text).strip()[:200]


def _credential_notes(platform: str, target: Target) -> list[str]:
    """Nur lokal (`--check`): Hinweise zu Benutzer und Passwort, nie das Passwort selbst."""
    notes = []
    user = target.username or ""
    if user:
        notes.append(L(f"Benutzer: '{user}'", f"User: '{user}'"))
        if platform == "cisco_aci" and "\\" in user and not user.lower().startswith("apic#"):
            notes.append(
                L("Benutzer mit Domaene: am APIC als apic#<Login-Domaene>\\<Benutzer> angeben",
                  "User with domain: enter it on the APIC as apic#<login domain>\\<user>")
            )  # fmt: skip
    password = target.password or ""
    if password and password != password.strip():
        notes.append(
            L(
                "Passwort beginnt oder endet mit Leerzeichen (beim Einfuegen mitkopiert?)",
                "Password starts or ends with a space (copied along when pasting?)",
            )
        )
    if password and not password.isascii():
        notes.append(
            L("Passwort enthaelt Zeichen ausserhalb ASCII (z. B. Umlaute) - manche Geraete "
              "lehnen sie bei der API-Anmeldung ab",
              "Password contains characters outside ASCII (e.g. umlauts) - some devices reject "
              "them at the API login")
        )  # fmt: skip
    return notes
