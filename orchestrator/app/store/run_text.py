"""Meldungen der Laeufe in der Sprache der Anfrage (0.88.0, ADR 0028 Schritt 4).

Gespeichert bleibt deutsch: die Meldung eines Laufs (`collector_run.message`, vom Orchestrator
bzw. vom Collector/Agenten) und der Grund der Update-Pruefung eines Agenten (`agent.update_note`,
ASCII). `show` uebersetzt beim Abruf - Laeufe-Liste, Betriebszustand, Agentenliste, CLI und die
Quittung an den Agenten (`shown`, fuer seine Konsole).

Die Meldungen sind zusammengesetzt (Teile mit „ · “, „; “, „. “, Teilfehler je Switch, Ursache
hinter „Einspielen fehlgeschlagen: “). Darum ersetzt `PHRASES` bekannte *Teile*, nicht ganze
Meldungen: jede deutsche Form (mit Platzhaltern) wird an jeder Stelle zur englischen, in der
Reihenfolge der Tabelle (genauere vor allgemeinen). Was nicht in der Tabelle steht - Texte der
Geraete, Ausnahmen, Namen, Adressen -, bleibt unveraendert. Die englischen Texte der Teile, die
der Collector erzeugt, sind dieselben wie dort neben dem deutschen (`collectors/common/i18n.py`,
`L()`); `tests/test_i18n_runs.py` prueft beide gegeneinander.

Platzhalter: `{n}`/`{m}` = Zahl, sonst beliebiger Text bis zum folgenden festen Teil (am Ende bis
„ · “ bzw. zum Ende der Meldung); `^`/`$` binden an Anfang/Ende der Meldung. Werte einzelner
Platzhalter uebersetzt `VALUES` (z. B. „das Geraet“), Namen in „…“ bekommen englische
Anfuehrungszeichen. Nicht-ASCII-Zeichen passen auch als „?“ (Grund der Update-Pruefung, ASCII).
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.i18n import lang

_DIGITS = {"n", "m"}

# Teile bekannter Meldungen: (deutsch, englisch) - genauere vor allgemeinen
PHRASES: tuple[tuple[str, str], ...] = (
    # Hinweise je Plattform (collectors/common/diagnose.py _HINTS, _VSPHERE_PERMISSION) -
    # lange feste Texte zuerst: sie enthalten kurze Teile weiter unten
    ("Avi-Benutzer/-Passwort im Web-UI neu setzen; das Konto braucht eine Rolle mit nur "
     "Leserechten (Virtual Service, VS VIP, Pool, Pool Group, VRF Context, Application "
     "Profile, Tenant) und Zugriff auf die Tenants im Lesebereich",
     "set the Avi user/password again in the web UI; the account needs a role with read-only "
     "rights (Virtual Service, VS VIP, Pool, Pool Group, VRF Context, Application Profile, "
     "Tenant) and access to the tenants in the read scope"),
    ("vCenter-Benutzer/-Passwort im Web-UI neu setzen (SSO-Konto als benutzer@vsphere.local "
     "bzw. DOMAENE\\benutzer); das Konto braucht nur die Rolle Read-only (oberste Ebene, "
     "an untergeordnete Objekte weitergegeben)",
     "set the vCenter user/password again in the web UI (SSO account as user@vsphere.local "
     "or DOMAIN\\user); the account only needs the role Read-only (top level, propagated to "
     "child objects)"),
    ("APIC-Benutzer/-Passwort im Web-UI neu setzen; Remote-Benutzer (LDAP/RADIUS/TACACS) "
     "als apic#<Login-Domaene>\\<Benutzer>; Konto am APIC aktiv und Passwort nicht abgelaufen?",
     "set the APIC user/password again in the web UI; remote users (LDAP/RADIUS/TACACS) as "
     "apic#<login domain>\\<user>; is the account active on the APIC and the password not "
     "expired?"),
    ("Infoblox-Benutzer/-Passwort im Web-UI neu setzen; die Admin-Gruppe des Benutzers braucht "
     "'API' unter Allowed Interfaces (AD-/RADIUS-Benutzer: Remote-Admin-Gruppe mit API-Recht); "
     "neues Konto: erst einmal in der NIOS-GUI anmelden (Passwortwechsel)",
     "set the Infoblox user/password again in the web UI; the user's admin group needs 'API' "
     "under Allowed Interfaces (AD/RADIUS users: remote admin group with API permission); new "
     "account: log in to the NIOS GUI once first (password change)"),
    ("API-Token im Web-UI neu setzen; Trusted Hosts des REST-API-Admins muessen den "
     "Agent-Rechner enthalten",
     "set the API token again in the web UI; the trusted hosts of the REST API admin must "
     "include the agent machine"),
    ("Lese-Token im Web-UI neu setzen (NetBox v2-Token: nbt_<Schluessel>.<Token>)",
     "set the read token again in the web UI (NetBox v2 token: nbt_<key>.<token>)"),
    ("SSH-Benutzer/-Passwort im Web-UI neu setzen; das Konto braucht nur Leserechte (IOS "
     "privilege 1 bzw. eine View mit den show-Befehlen, NX-OS-Rolle network-operator, Junos-"
     "Klasse read-only, Catalyst 1200/1300 Stufe 1), Anmeldung mit Passwort "
     "(keyboard-interactive bzw. password) muss erlaubt sein",
     "set the SSH user/password again in the web UI; the account only needs read rights (IOS "
     "privilege 1 or a view with the show commands, NX-OS role network-operator, Junos class "
     "read-only, Catalyst 1200/1300 level 1), login with password (keyboard-interactive or "
     "password) must be allowed"),
    ("im vCenter dem Konto die Rolle Read-only auf dem vCenter-Objekt (oberste Ebene des "
     "Inventars) zuweisen, mit 'An untergeordnete Objekte weitergeben' (Propagate to children) - "
     "oder unter Verwaltung > Zugriffssteuerung > Globale Berechtigungen, ebenfalls mit "
     "Weitergabe; ohne Weitergabe gilt eine globale Berechtigung nicht fuer das Inventar. "
     "Ist das Konto ueber eine SSO-/AD-Gruppe berechtigt: Mitgliedschaft pruefen",
     "in vCenter assign the role Read-only to the account on the vCenter object (top level of "
     "the inventory), with 'Propagate to children' - or under Administration > Access Control > "
     "Global Permissions, also with propagation; without propagation a global permission does "
     "not apply to the inventory. If the account is authorized via an SSO/AD group: check the "
     "membership"),
    # Orchestrator: Abbruch, Ablauf, Einspielen, Zugangsdaten (store/runs.py, api/internal.py,
    # api/agents.py, credentials.py)
    ("^abgebrochen: kein Ergebnis nach {n} min - Agent beendet bzw. neu gestartet oder "
     "Ergebnis-Upload verloren; neu einlesen",
     "cancelled: no result after {n} min - agent stopped or restarted, or result upload lost; "
     "collect again"),
    ("^abgebrochen: nach {n} h nicht abgeholt - Agent offline bzw. kein zentraler Worker",
     "cancelled: not picked up after {n} h - agent offline or no central worker"),
    ("^abgebrochen von {who}$", "cancelled by {who}"),
    ("^Teil-Upload unvollstaendig ({n} von {m})", "partial upload incomplete ({n} of {m})"),
    ("^Einspielen fehlgeschlagen: ", "Ingest failed: "),
    ("^Zugangsdaten nicht lesbar: ", "Credentials not readable: "),
    ("kein Schlüssel für Zugangsdaten: ", "no key for credentials: "),
    ("Schlüsseldatei ungültig: ", "key file invalid: "),
    ("Schlüssel passt nicht zu den gespeicherten Zugangsdaten",
     "key does not match the stored credentials"),
    # Agent (collectors/run.py)
    ("Plattform '{p}' wird von diesem Agenten nicht unterstuetzt - Agent-Paket im Web-UI "
     "(Collector-Agenten) neu herunterladen und install.ps1 erneut ausfuehren",
     "platform '{p}' is not supported by this agent - download the agent package again in the "
     "web UI (Collector agents) and run install.ps1 again"),
    ("^Ergebnis-Upload fehlgeschlagen: ", "Result upload failed: "),
    (" (Details: docker compose logs orchestrator)",
     " (details: docker compose logs orchestrator)"),
    # Verbindung (collectors/common/http.py describe_error)
    (" - keine Antwort: Ziel aus oder Weg gesperrt (Firewall/Netz)",
     " - no response: target off or path blocked (firewall/network)"),
    (" - Verbindung abgelehnt bzw. Name unbekannt", " - connection refused or name unknown"),
    (" - spricht der Orchestrator HTTPS (TLS intern)? In agent.env "
     "AUTOMATO_ORCHESTRATOR_URL=https://… und AUTOMATO_ORCHESTRATOR_CA_BUNDLE=<Pfad zu "
     "automato-ca.crt> setzen (Web-UI: Collector-Agenten → Einrichtung)",
     " - does the orchestrator speak HTTPS (internal TLS)? In agent.env set "
     "AUTOMATO_ORCHESTRATOR_URL=https://… and AUTOMATO_ORCHESTRATOR_CA_BUNDLE=<path to "
     "automato-ca.crt> (web UI: Collector agents → Setup)"),
    # Anmeldung, SSH, vCenter (collectors/common/diagnose.py)
    ("Anmeldung abgelehnt (SSH: Benutzer oder Passwort falsch bzw. Passwort-Anmeldung nicht "
     "erlaubt)",
     "Login rejected (SSH: user or password wrong, or password login not allowed)"),
    ("Anmeldung abgelehnt (SSH-Sitzung: „User Name:“/„Password:“)",
     "Login rejected (SSH session: “User Name:”/“Password:”)"),
    ("Anmeldung abgelehnt (vCenter: Benutzer oder Passwort falsch)",
     "Login rejected (vCenter: user or password wrong)"),
    ("Anmeldung abgelehnt (", "Login rejected ("),
    ("nicht erreichbar: Verbindung zu Port 22 abgelehnt (SSH aus bzw. Weg gesperrt)",
     "not reachable: connection to port 22 refused (SSH off or path blocked)"),
    ("nicht erreichbar bzw. keine Antwort", "not reachable or no response"),
    ("nicht erreichbar", "not reachable"),
    ("SSH-Fehler: ", "SSH error: "),
    ("Keine Berechtigung im vCenter: dem Konto fehlt {privs} auf {where}. ",
     "No permission in vCenter: the account lacks {privs} on {where}. "),
    (" (oberste Ebene des Inventars)", " (top level of the inventory)"),
    ("Geraet meldet: ", "Device reports: "),
    ("Hinweis: ", "Note: "),
    # Quellsystem ohne Zugangsdaten bzw. Adresse (collectors/*/tasks.py)
    ("keine Zugangsdaten fuer {who} - im Web-UI beim Quellsystem hinterlegen",
     "no credentials for {who} - store them at the source system in the web UI"),
    ("keine Adresse fuer {who} - im Web-UI beim Quellsystem hinterlegen",
     "no address for {who} - store it at the source system in the web UI"),
    ("kein Lese-Token fuer {who} - im Web-UI beim Quellsystem unter 'API-Token (lesen)' "
     "hinterlegen (das Schreib-Token wird nie zum Lesen verwendet)",
     "no read token for {who} - store it at the source system in the web UI under 'API token "
     "(read)' (the write token is never used for reading)"),
    ("keine Switch-Adressen im Quellsystem (Lesebereich) hinterlegt",
     "no switch addresses stored in the source system (read scope)"),
    # Switches (cisco_switch/tasks.py, client.py, normalize.py, junos.py)
    ("^kein Switch gelesen$", "no switch read"),
    ("^Switches: {n} gelesen (", "Switches: {n} read ("),
    (" · nicht gelesen: ", " · not read: "),
    (" · Hinweise: ", " · Notes: "),
    (": Ausgabe nicht auswertbar (", ": output could not be parsed ("),
    ("MAC-Tabelle: {n} Eintraege ueber {m} nicht uebernommen",
     "MAC table: {n} entries above {m} not taken over"),
    ("Host-Schluessel hat sich geaendert ({stored} gespeichert, {shown} vorgewiesen) - Geraet "
     "pruefen und im Web-UI den Schluessel neu lernen lassen",
     "host key has changed ({stored} stored, {shown} presented) - check the device and let the "
     "web UI learn the key again"),
    ("nur lesend: Befehl „{command}“ blockiert", "read-only: command “{command}” blocked"),
    ("„{command}“: keine vollstaendige Antwort", "“{command}”: no complete response"),
    ("„{command}“: Ausgabe zu gross", "“{command}”: output too large"),
    ("Sitzung: kein Prompt", "session: no prompt"),
    ("Sitzung beendet", "session ended"),
    ("Ausgabe zu gross", "output too large"),
    # Referenzquellen, APIC, NSX ALB, vCenter
    ("^{n} Referenzeintraege gelesen$", "{n} reference entries read"),
    ("APIC-Login ohne Token (Benutzer/Passwort pruefen)",
     "APIC login without token (check user/password)"),
    ("ACI-Tenant nicht gefunden: ", "ACI tenant not found: "),
    ("Avi-Login abgelehnt (Benutzer/Passwort pruefen)", "Avi login rejected (check user/password)"),
    (" Einträge (VIP × Port)", " entries (VIP × port)"),
    ("pyvmomi fehlt - Agent neu installieren bzw. Selbst-Update abwarten",
     "pyvmomi missing - reinstall the agent or wait for the self-update"),
    ("nur lesend: vSphere-Methode {method} blockiert",
     "read-only: vSphere method {method} blocked"),
    # Routing nicht lesbar (fortigate/tasks.py, cisco_aci/tasks.py, avi/tasks.py, Clients)
    ("VDOM nicht lesbar: {skipped} - das Profil braucht Lesezugriff auf alle VDOMs; gelesen: ",
     "VDOM not readable: {skipped} - the profile needs read access to all VDOMs; read: "),
    ("Routing nicht lesbar: ", "Routing not readable: "),
    (" · nicht lesbar: ", " · not readable: "),
    ("Routing-Tabelle ", "routing table "),
    ("BGP-Pfade ", "BGP paths "),
    ("BGP-Nachbarn", "BGP neighbors"),
    ("statische Routen ", "static routes "),
    ("Policy-Routen ", "policy routes "),
    ("(zu gross: {n} Eintraege)", "(too large: {n} entries)"),
    ("zu gross: ", "too large: "),
    (": mehr als {n} Eintraege", ": more than {n} entries"),
    # Grund der Update-Pruefung (collectors/common/selfupdate.py, Header X-Agent-Update)
    ("Selbst-Update auf der Station abgeschaltet (AUTOMATO_AGENT_AUTO_UPDATE=false)",
     "Self-update switched off on the station (AUTOMATO_AGENT_AUTO_UPDATE=false)"),
    ("^Update-Pruefung nicht moeglich: ", "Update check not possible: "),
    ("^Update {version} nicht installiert: Signatur fehlt oder passt nicht zum hinterlegten "
     "Schluessel (Paket signieren, deploy/agent-signing)",
     "Update {version} not installed: signature missing or does not match the stored key "
     "(sign the package, deploy/agent-signing)"),
    ("^Selbst-Update fehlgeschlagen: ", "Self-update failed: "),
    ("ein anderer Agent-Prozess installiert gerade ein Update (update.lock)",
     "another agent process is installing an update right now (update.lock)"),
    ("Update-Sperre (update.lock) nicht moeglich", "update lock (update.lock) not possible"),
    ("ungueltiger Dateiname im Manifest: ", "invalid file name in the manifest: "),
    ("Pruefsumme von {name} stimmt nicht - Update verworfen",
     "checksum of {name} does not match - update discarded"),
    ("collectors-Wheel fehlt im Manifest", "collectors wheel missing in the manifest"),
    ("nach der Installation fehlen {n} Datei(en), z. B. {first} - Virenschutz (Schutzverlauf) "
     "bzw. einen zweiten Agent-Prozess pruefen",
     "after the installation {n} file(s) are missing, e.g. {first} - check the antivirus "
     "(protection history) or a second agent process"),
    ("Rueckfall fehlgeschlagen: ", "fallback failed: "),
    ("Startpruefung fehlgeschlagen: ", "start check failed: "),
    ("pip fehlgeschlagen: ", "pip failed: "),
    ("keine zuletzt guten Pakete fuer den Rueckfall - install.ps1 erneut ausfuehren",
     "no last good packages for the fallback - run install.ps1 again"),
    ("zuletzt gute Pakete wieder installiert", "last good packages reinstalled"),
)  # fmt: skip

# Werte einzelner Platzhalter (sonst unveraendert)
VALUES: dict[str, dict[str, str]] = {
    "who": {
        "das Geraet": "the device",
        "den APIC": "the APIC",
        "die Referenzquelle": "the reference source",
        "die Switches": "the switches",
        "den Avi-Controller": "the Avi controller",
        "das vCenter": "the vCenter",
    },
    "privs": {"Leserecht": "read permission"},
    "where": {"einem Inventar-Objekt": "an inventory object"},
    "version": {"unbekannt": "unknown"},
}

_QUOTED = re.compile(r"„([^“]*)“")


def _literal(text: str) -> str:
    """Fester Teil als Regex; Nicht-ASCII passt auch als „?“ (ASCII-Grund des Agenten)."""
    return "".join(re.escape(c) if c.isascii() else f"(?:{re.escape(c)}|\\?)" for c in text)


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[re.Pattern, str], ...]:
    out = []
    for de, en in PHRASES:
        start, end = de.startswith("^"), de.endswith("$")
        body = de[1 if start else 0 : -1 if end else None]
        parts = re.split(r"\{(\w+)\}", body)
        regex = "^" if start else ""
        for i, part in enumerate(parts):
            if i % 2 == 0:
                regex += _literal(part)
            elif part in _DIGITS:
                regex += f"(?P<{part}>\\d+)"
            elif i == len(parts) - 2 and parts[-1] == "" and not end:
                regex += f"(?P<{part}>.*?)(?= · |$)"  # am Ende: bis zum naechsten Teil
            else:
                regex += f"(?P<{part}>.+?)"
        out.append((re.compile(regex + ("$" if end else "")), en))
    return tuple(out)


def _value(name: str, value: str) -> str:
    known = VALUES.get(name, {}).get(value)
    if known is not None:
        return known
    return _QUOTED.sub(r"“\1”", value) if name == "who" else value


def translate(text: str) -> str:
    """Deutsche Meldung -> englisch (bekannte Teile; Unbekanntes bleibt)."""
    for pattern, en in _compiled():
        if pattern.search(text):
            text = pattern.sub(
                lambda m, en=en: en.format(**{k: _value(k, v) for k, v in m.groupdict().items()}),
                text,
            )
    return text


def show(text: str | None) -> str | None:
    """Gespeicherte (deutsche) Meldung in der Sprache der Anfrage."""
    if not text or lang() != "en":
        return text
    return translate(text)
