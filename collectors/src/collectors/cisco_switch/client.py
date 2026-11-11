"""SSH-Client fuer Switches und Router, rein lesend (ADR 0025): Cisco IOS/IOS-XE (z. B. Catalyst
9200L, 3650, 4500, 9300), NX-OS (Nexus 9000), Cisco Business-CLI (Catalyst 1200/1300) und
Juniper Junos (MX, EX, QFX; seit 0.68.0), Arista EOS und HPE Aruba CX (AOS-CX; seit 0.94.0).

Je Befehl ein SSH-Exec-Aufruf (wie `ssh switch "show version"`): keine interaktive Shell, kein
Terminal (damit kein Umbruch nach Seiten und kein `terminal length`), kein Konfigurationsmodus.
Erlaubt sind nur die festen `show`-Befehle aus `COMMANDS`; jeder andere Befehl bricht vor dem
Senden ab (`ReadOnlyViolation`). Das Konto braucht nur Leserechte (IOS privilege 1 bzw. eine
View, NX-OS-Rolle `network-operator`, Junos-Klasse `read-only`, Catalyst 1300 Stufe 1, Arista-
Rolle `network-operator`, AOS-CX-Gruppe `operators`). Seit 0.94.0 je Plattform auch die
IPv6-Nachbarn (NDP); fehlt der Befehl (alte Software), bleibt der Teil ohne Hinweis leer.
Seit ADR 0032 auch das Routing der Router und L3-Switches (`routing.COMMANDS`, nur `show`): RIB,
BGP-Nachbarn und -Pfade - mit laengerer Wartezeit; eine zu grosse Text-Ausgabe (Internet-Fullfeed)
wird bei `MAX_OUTPUT` abgeschnitten und gekuerzt ausgewertet (`cut`) statt verworfen.

Geraete ohne Exec (Catalyst 1200/1300 und andere mit Business-CLI) liest seit 0.68.0 eine
**begrenzte Sitzung** (`_Shell`): Terminal mit Prompt, Anmeldung in der Sitzung („User Name:“),
danach nur die Zeilen der Positivliste - die `show`-Befehle und je Plattform ein Befehl, der den
Seitenumbruch nur fuer diese Sitzung abschaltet (`SESSION`); bei „More“ ein Leerzeichen. Nie
Konfigurationsmodus, nie eine Antwort auf Rueckfragen.

Seit 1.9.0 (Anwender: „Der Switchrun geht auch sehr lange“): viele IOS-Switches schliessen die
Verbindung nach jedem Exec - bisher je Befehl eine neue Anmeldung (bis zu 17 je Switch). Merkt
der Client das (Verbindung nach einem gelungenen Exec zu), liest er die uebrigen Befehle **einmal
angemeldet** in der begrenzten Sitzung, zuerst mit dem Befehl aus `SESSION` (Seitenumbruch aus).
Geht die Sitzung nicht, bleibt es beim Exec mit Neuverbindung. Der Exec-Versuch fuer
`show version` wartet nur noch `PROBE_TIMEOUT` auf die ersten Daten.

Host-Schluessel: ist einer gespeichert (vom ersten Lauf, im Orchestrator je Adresse), muss der
Switch ihn vorweisen - sonst bricht die Verbindung vor der Anmeldung ab. Ohne gespeicherten
Schluessel wird der vorgewiesene uebernommen und mit dem Ergebnis gemeldet (der Orchestrator
haelt ihn danach fest).
"""

import base64
import codecs
import hashlib
import re
import time

import paramiko
from sot_gateway.readonly import ReadOnlyViolation

from collectors.cisco_switch import routing
from collectors.common.i18n import L

# Feste Befehle je Plattform - nur lesend; `show version` erkennt die Plattform
COMMANDS: dict[str, tuple[str, ...]] = {
    "ios": (
        "show version",
        "show interfaces status",
        "show interfaces description",
        "show interfaces switchport",
        "show vlan brief",
        "show etherchannel summary",
        "show cdp neighbors detail",
        "show lldp neighbors detail",
        "show mac address-table",  # 0.63.0: Endpunkte je Port
        "show ip arp",
        "show spanning-tree",  # 0.65.0: blockierte Ports je VLAN
        "show ipv6 neighbors",  # 0.94.0: IPv6-Nachbarn (NDP) wie ARP
    ),
    "nxos": (
        "show version",
        "show interface status",
        "show interface description",
        "show interface switchport",
        "show vlan brief",
        "show port-channel summary",
        "show cdp neighbors detail",
        "show lldp neighbors detail",
        "show vpc",
        "show mac address-table",  # 0.63.0
        "show ip arp detail vrf all",
        "show spanning-tree",  # 0.65.0
        "show ipv6 neighbor vrf all",  # 0.94.0 (ohne ntc-Vorlage, eigene Auswertung)
    ),
    # 0.68.0: Cisco Business-CLI (Catalyst 1200/1300, CBS): Tabellen, eigene Auswertung
    "cbs": (
        "show version",
        "show system",
        "show inventory",
        "show interfaces status",
        "show interfaces description",
        "show vlan",
        "show interfaces port-channel",
        "show lldp neighbors",
        "show mac address-table",
        "show arp",
        "show ipv6 neighbors all",  # 0.94.0
    ),
    # 0.68.0: Juniper Junos (MX, EX, QFX) - strukturiert als JSON, `no-more` ohne Seiten
    "junos": (
        "show version",
        "show chassis hardware | display json | no-more",
        "show interfaces | display json | no-more",
        "show lldp neighbors | display json | no-more",
        "show vlans | display json | no-more",
        "show arp no-resolve | display json | no-more",
        "show ethernet-switching table | display json | no-more",
        "show bridge mac-table | display json | no-more",
        "show ipv6 neighbors | display json | no-more",  # 0.94.0
    ),
    # 0.94.0: Arista EOS - `show version` als Text erkennt die Plattform, sonst JSON (`| json`)
    "eos": (
        "show version",
        "show version | json",
        "show hostname | json",
        "show interfaces status | json",
        "show interfaces description | json",
        "show interfaces switchport | json",
        "show vlan | json",
        "show port-channel | json",
        "show lldp neighbors detail | json",
        "show mac address-table | json",
        "show ip arp vrf all | json",
        "show ipv6 neighbors vrf all | json",
    ),
    # 0.94.0: HPE Aruba CX (AOS-CX) - ntc-Vorlagen `aruba_aoscx`, IPv6-Nachbarn selbst
    "aoscx": (
        "show version",
        "show system",
        "show interface",
        "show vlan",
        "show lldp neighbor-info detail",
        "show mac-address-table",
        "show arp all-vrfs",
        "show ipv6 neighbors all-vrfs",
    ),
}
ALLOWED = frozenset(c for cmds in COMMANDS.values() for c in cmds) | routing.ALL
# nur in der begrenzten Sitzung: Seitenumbruch fuer diese Sitzung aus (keine Konfiguration)
SESSION: dict[str, str] = {"cbs": "terminal datadump", "ios": "terminal length 0",
                           "nxos": "terminal length 0", "eos": "terminal length 0",
                           "aoscx": "no page"}  # fmt: skip
MAX_OUTPUT = 32 * 1024 * 1024  # je Befehl
COMMAND_TIMEOUT = 120.0  # Sekunden je Befehl
# Erster Exec-Aufruf (`show version`): kommen so lange gar keine Daten, die begrenzte Sitzung
# versuchen. Ein Geraet mit Exec antwortet in Sekundenbruchteilen; bis 1.8.x waren es 20 s (und
# nach einer Neuverbindung noch einmal 20 s) - Anwender: „bleibt vor allem bei den Switches lange
# haengen“. Kommen Daten, gilt fuer den Rest `COMMAND_TIMEOUT` (kein Abbruch mitten in der Antwort).
PROBE_TIMEOUT = 7.0
ROUTING_TIMEOUT = 300.0  # Routing-Befehle (ADR 0032): grosse Tabellen brauchen laenger
SHELL_LOGIN_TIMEOUT = 20.0  # Wechsel in die Sitzung (1.9.0): bis zum Prompt, sonst Exec


class HostKeyChanged(Exception):
    """Der Switch weist einen anderen Host-Schluessel vor als gespeichert."""


class SessionLoginFailed(Exception):
    """Die Anmeldung in der Sitzung („User Name:“/„Password:“) wurde abgelehnt."""


class UnsupportedDevice(Exception):
    """Geraet, das Automato nicht liest (0.94.0: ArubaOS-Switch/ProCurve verlangt in der Sitzung
    einen Tastendruck und steuert den Bildschirm - abgebrochen, bevor etwas gesendet wird)."""


def fingerprint(key_line: str) -> str:
    """SHA256-Fingerabdruck wie bei OpenSSH (`SHA256:...`) aus `<typ> <base64>`."""
    try:
        raw = base64.b64decode(key_line.split()[1])
    except (IndexError, ValueError):
        return "?"
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


def split_host(entry: str) -> tuple[str, int]:
    """`host`, `host:port` bzw. `[v6]:port` -> (host, port)."""
    entry = entry.strip()
    if entry.startswith("[") and "]" in entry:
        host, _, rest = entry[1:].partition("]")
        return host, int(rest.lstrip(":") or 22)
    if entry.count(":") == 1:
        host, port = entry.split(":")
        return host, int(port or 22)
    return entry, 22


class _Learn(paramiko.MissingHostKeyPolicy):
    """Ohne gespeicherten Schluessel: vorgewiesenen merken (Trust on first use)."""

    def __init__(self, client: "SwitchClient"):
        self.client = client

    def missing_host_key(self, ssh, hostname, key) -> None:  # noqa: ARG002 - Signatur paramiko
        self.client.host_key = f"{key.get_name()} {key.get_base64()}"


class SwitchClient:
    """Verbindung zu einem Switch; `run(cmd)` fuehrt einen erlaubten Befehl aus."""

    def __init__(self, entry: str, username: str, password: str, known_key: str | None = None,
                 timeout: float = 20.0):  # fmt: skip
        self.entry = entry
        self.host, self.port = split_host(entry)
        self.username = username
        self.password = password
        self.known_key = known_key or None
        self.host_key: str | None = known_key or None
        self.timeout = timeout
        self._ssh: paramiko.SSHClient | None = None
        self._shell: _Shell | None = None  # offene begrenzte Sitzung (None: bei Bedarf oeffnen)
        # Befehle in der begrenzten Sitzung statt per Exec: Catalyst 1300 (kein Exec) bzw. seit
        # 1.9.0 ein Geraet, das die Verbindung nach jedem Exec schliesst
        self._in_session = False
        self._session_tried = False  # Wechsel nach dem Exec einmal versucht (gescheitert: Exec)
        self._served = 0  # gelungene Exec-Aufrufe auf der aktuellen Verbindung
        self.platform: str | None = None  # aus `prepare` - fuer `SESSION` beim spaeteren Wechsel
        self.cut: set[str] = set()  # Routing-Ausgaben, die bei MAX_OUTPUT abgeschnitten wurden

    @property
    def mode(self) -> str:
        return "shell" if self._in_session else "exec"

    # -- Verbindung ------------------------------------------------------------------------

    def _connect(self) -> paramiko.SSHClient:
        ssh = paramiko.SSHClient()
        if self.host_key:
            kind, b64 = self.host_key.split()[:2]
            key = paramiko.PKey.from_type_string(kind, base64.b64decode(b64))
            name = self.host if self.port == 22 else f"[{self.host}]:{self.port}"
            ssh.get_host_keys().add(name, kind, key)
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            ssh.set_missing_host_key_policy(_Learn(self))
        try:
            ssh.connect(
                self.host, port=self.port, username=self.username, password=self.password,
                timeout=self.timeout, banner_timeout=self.timeout, auth_timeout=self.timeout,
                look_for_keys=False, allow_agent=False,
            )  # fmt: skip
        except paramiko.BadHostKeyException as exc:
            ssh.close()
            stored = fingerprint(self.host_key)
            shown = fingerprint(f"{exc.key.get_name()} {exc.key.get_base64()}")
            raise HostKeyChanged(
                L(
                    f"{self.entry}: Host-Schluessel hat sich geaendert ({stored} gespeichert, "
                    f"{shown} vorgewiesen) - Geraet pruefen und im Web-UI den Schluessel neu "
                    "lernen lassen",
                    f"{self.entry}: host key has changed ({stored} stored, {shown} presented) - "
                    "check the device and let the web UI learn the key again",
                )
            ) from None
        except Exception:
            ssh.close()
            raise
        return ssh

    def __enter__(self) -> "SwitchClient":
        self._ssh = self._connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._shell is not None:
            self._shell.close()
            self._shell = None
        if self._ssh is not None:
            self._ssh.close()
            self._ssh = None
        self._served = 0

    # -- Befehle ---------------------------------------------------------------------------

    def version(self) -> str:
        """`show version` - per Exec; antwortet das Geraet so nicht (kein Exec, nur Anmeldung
        in der Sitzung), ueber die begrenzte Sitzung (0.68.0). Der Exec-Versuch wartet hoechstens
        `PROBE_TIMEOUT` auf die ersten Daten und wird nicht wiederholt (1.9.0)."""
        try:
            text = self._run("show version", COMMAND_TIMEOUT, first=PROBE_TIMEOUT)
            if text.strip() and not LOGIN.search(text):
                return text
        except (paramiko.SSHException, EOFError, OSError):  # auch TimeoutError: keine Daten
            pass
        self._open_session(COMMAND_TIMEOUT)
        return self._session_run("show version", COMMAND_TIMEOUT)

    def prepare(self, platform: str) -> None:
        """Plattform merken; in der begrenzten Sitzung den Seitenumbruch fuer die Sitzung
        abschalten. Wechselt der Client erst spaeter in die Sitzung (Geraet schliesst nach dem
        Exec), schickt `_open_session` den Befehl dann selbst."""
        self.platform = platform
        if self._shell is not None and platform in SESSION:
            self._shell.run(SESSION[platform], session=True)

    def run(self, command: str, timeout: float = COMMAND_TIMEOUT) -> str:
        """Erlaubten `show`-Befehl ausfuehren und die Ausgabe zurueckgeben. Manche Switches
        beenden die Verbindung nach einem Exec-Aufruf - dann die uebrigen Befehle in der
        begrenzten Sitzung (1.9.0), geht die nicht, je Befehl neu verbinden (gleicher
        Schluessel)."""
        return self._run(command, timeout)

    def _run(self, command: str, timeout: float, first: float | None = None) -> str:
        if command not in ALLOWED:
            raise ReadOnlyViolation(_blocked(command))
        if command in routing.ALL:
            timeout = max(timeout, ROUTING_TIMEOUT)
        if self._in_session:
            return self._session_run(command, timeout)
        attempts = (1,) if first is not None else (1, 2)  # Probe: nicht wiederholen
        for attempt in attempts:
            transport = self._ssh.get_transport() if self._ssh is not None else None
            if transport is None or not transport.is_active():
                if self._closed_after_exec():
                    return self._session_run(command, timeout)
                self.close()
                self._ssh = self._connect()
                transport = self._ssh.get_transport()
            try:
                text = self._exec(transport, command, timeout, first)
            except TimeoutError:
                raise  # Frist ohne (vollstaendige) Antwort: nicht noch einmal so lange warten
            except (paramiko.SSHException, EOFError, OSError):
                if self._closed_after_exec():
                    return self._session_run(command, timeout)
                if attempt == attempts[-1]:
                    raise
                self.close()  # Sitzung zu - einmal neu verbinden
                continue
            if not text and not transport.is_active() and self._closed_after_exec():
                # leer, und die Verbindung ist weg: Kanal noch angenommen, aber die Verbindung
                # endete vor der Antwort (Exec davor) - in der Sitzung nachholen statt leer lassen
                return self._session_run(command, timeout)
            self._served += 1
            return text
        raise RuntimeError("nicht erreichbar")  # pragma: no cover

    def _closed_after_exec(self) -> bool:
        """Die Verbindung ist nach einem gelungenen Exec zu bzw. nimmt keinen Kanal mehr an -
        das Geraet schliesst sie nach jedem Exec (viele Cisco-IOS-Switches). Dann einmal in die
        begrenzte Sitzung wechseln: die uebrigen Befehle mit **einer** Anmeldung statt je Befehl
        neu. Geht die Sitzung nicht (kein Terminal, kein Prompt), bleibt es beim Exec mit
        Neuverbindung (False) - ohne weiteren Versuch."""
        if not self._served or self._session_tried:
            return False
        self._session_tried = True
        try:
            self._open_session(SHELL_LOGIN_TIMEOUT)
        except (HostKeyChanged, paramiko.AuthenticationException):
            raise  # Verbindung selbst abgelehnt: nicht verschweigen
        except Exception:  # noqa: BLE001 - keine Sitzung: beim Exec bleiben
            self._in_session = False
            self.close()
            return False
        return True

    def _open_session(self, login_timeout: float) -> None:
        """Neu verbinden und die begrenzte Sitzung oeffnen; ist die Plattform bekannt (nach
        `prepare`), zuerst den Seitenumbruch fuer diese Sitzung abschalten (`SESSION`)."""
        self.close()
        self._ssh = self._connect()
        self._in_session = True
        self._shell = _Shell(self._ssh.get_transport(), self.username, self.password,
                             self.timeout, login_timeout)  # fmt: skip
        if self.platform in SESSION:
            self._shell.run(SESSION[self.platform], session=True)

    def _session_run(self, command: str, timeout: float) -> str:
        """Befehl in der begrenzten Sitzung. Ist die Sitzung nach einem Fehler bzw. einer
        gekuerzten Ausgabe nicht mehr im Takt (Rest der Ausgabe unterwegs), gilt sie als
        verbraucht: der naechste Befehl oeffnet eine neue (statt fremde Ausgaben zu lesen)."""
        if self._shell is None:
            self._open_session(COMMAND_TIMEOUT)
        shell = self._shell
        try:
            text = shell.run(command, timeout=timeout)
        except ReadOnlyViolation:
            raise  # nichts gesendet: Sitzung bleibt im Takt
        except Exception:
            self.close()
            raise
        if shell.cut:  # Routing-Text bei MAX_OUTPUT gekuerzt (ADR 0032) - wie per Exec
            self.cut.add(command)
            self.close()
        return text

    def _exec(self, transport: paramiko.Transport, command: str, timeout: float,
              first: float | None = None) -> str:  # fmt: skip
        """Ein Exec-Aufruf. `first` (Probe): Frist bis zu den ersten Daten - kommt bis dahin
        nichts, `TimeoutError`; sobald Daten kommen, gilt `timeout` fuer die ganze Antwort.
        Fragt das Geraet im Exec nach der Anmeldung („User Name:“), endet die Probe sofort."""
        chan = transport.open_session(timeout=self.timeout)
        try:
            chan.settimeout(min(self.timeout, timeout, first or timeout))
            chan.exec_command(command)  # ohne PTY: keine Seiten, kein Terminal
            out, size, tail = [], 0, b""
            start = time.monotonic()
            deadline = start + timeout
            while True:
                now = time.monotonic()
                if now > deadline or (first is not None and not out and now > start + first):
                    raise TimeoutError(
                        L(
                            f"„{command}“: keine vollstaendige Antwort",
                            f"“{command}”: no complete response",
                        )
                    )
                try:
                    data = chan.recv(65536)
                except TimeoutError:  # socket.timeout
                    continue
                if not data:
                    break
                size += len(data)
                if size > MAX_OUTPUT:
                    if command in routing.PARTIAL:  # Text: bis hier auswerten (ADR 0032)
                        self.cut.add(command)
                        out.append(data[: len(data) - (size - MAX_OUTPUT)])  # bis zur Grenze
                        text = b"".join(out).decode("utf-8", errors="replace")
                        return text[: text.rfind("\n") + 1]
                    raise RuntimeError(
                        L(f"„{command}“: Ausgabe zu gross", f"“{command}”: output too large")
                    )
                out.append(data)
                if first is not None:
                    tail = (tail + data)[-200:]
                    if LOGIN.search(tail.decode("utf-8", errors="replace").replace("\r", "")):
                        break  # Anmeldung im Exec: kein Exec-Geraet - `version` nimmt die Sitzung
            while chan.recv_stderr_ready():
                chan.recv_stderr(65536)
            return b"".join(out).decode("utf-8", errors="replace")
        finally:
            _quiet_close(chan)


def _quiet_close(chan) -> None:
    """Kanal schliessen; ist die Verbindung schon zu (das Geraet schliesst sie nach dem Exec),
    scheitert das Senden des Abschlusses - die Antwort ist dann schon gelesen bzw. der eigentliche
    Fehler unterwegs (bis 1.8.x ersetzte dieser Fehler die gelesene Antwort: Befehl doppelt)."""
    try:
        chan.close()
    except (paramiko.SSHException, EOFError, OSError):
        pass


def _blocked(command: str) -> str:
    return L(f"nur lesend: Befehl „{command}“ blockiert", f"read-only: command “{command}” blocked")


# -- begrenzte Sitzung (0.68.0) -------------------------------------------------------------

LOGIN = re.compile(r"(?:User\s*Name|Username|login)\s*:\s*$", re.I | re.M)
PASSWORD = re.compile(r"Password\s*:\s*$", re.I)
MORE = re.compile(r"(--\s*More\s*--|More:\s*<space>.*?$)", re.I | re.M)
PROMPT = re.compile(r"(?:^|\n)([^\s#>()]{1,80})([#>])\s*$")
ANY_KEY = re.compile(r"Press any key to continue", re.I)  # ArubaOS-Switch/ProCurve (0.94.0)
_NOISE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _at_prompt(buf: str) -> re.Match | None:
    """Prompt am Ende des Puffers. Gesucht wird nur im Ende (der Prompt steht in der letzten
    Zeile): sonst wuerde jede empfangene Portion die ganze Ausgabe neu durchsuchen - bei grossen
    Routing-Tabellen in der Sitzung (1.9.0) quadratisch. Ab einer Position > 0 passt `^` nicht
    mehr, der Prompt beginnt dann hinter einem Zeilenumbruch - wie im ganzen Puffer."""
    return PROMPT.search(buf, max(0, len(buf) - 512))


class _Shell:
    """Terminal-Sitzung mit Prompt fuer Geraete ohne Exec bzw. (1.9.0) fuer Geraete, die die
    Verbindung nach jedem Exec schliessen. Gesendet werden nur: Benutzername und Passwort auf die
    Anmeldefragen, die erlaubten Befehle (`ALLOWED`, `SESSION`) und ein Leerzeichen bei „More“.

    Die Ausgaben entsprechen denen per Exec: Echo des Befehls und Prompt entfernt. Routing-Befehle
    (ADR 0032) laufen hier mit ihrem langen Zeitlimit (der Aufrufer gibt `ROUTING_TIMEOUT`); eine
    zu grosse Text-Ausgabe (`routing.PARTIAL`) wird bei `MAX_OUTPUT` wie per Exec bis zur letzten
    ganzen Zeile gekuerzt (`cut`). Weil der Rest der Ausgabe dann noch unterwegs ist, schliesst der
    Client die Sitzung danach und oeffnet fuer den naechsten Befehl eine neue - darum bleiben auch
    die Routing-Befehle in der Sitzung statt je Befehl neu anzumelden."""

    def __init__(self, transport: paramiko.Transport, username: str, password: str,
                 timeout: float, login_timeout: float = COMMAND_TIMEOUT) -> None:  # fmt: skip
        self.username, self.password, self.timeout = username, password, timeout
        self.cut = False  # letzte Ausgabe bei MAX_OUTPUT gekuerzt (Rest nicht gelesen)
        # Zeichen ueber mehrere Portionen (Umlaute in Beschreibungen) richtig zusammensetzen
        self._decode = codecs.getincrementaldecoder("utf-8")(errors="replace").decode
        self.chan = transport.open_session(timeout=timeout)
        try:
            self.chan.get_pty(term="vt100", width=511, height=0)
        except paramiko.SSHException:
            pass  # ohne Terminal weiter (dann mit „More“ umgehen)
        self.chan.invoke_shell()
        self.chan.settimeout(0.5)
        self.prompt = ""
        self._read(login=True, timeout=login_timeout)

    def close(self) -> None:
        _quiet_close(self.chan)

    def run(self, command: str, session: bool = False, timeout: float = COMMAND_TIMEOUT) -> str:
        allowed = command in SESSION.values() if session else command in ALLOWED
        if not allowed or "\n" in command or "\r" in command:
            raise ReadOnlyViolation(_blocked(command))
        self.cut = False
        self.chan.send(command + "\n")
        text = self._read(timeout=timeout, partial=command in routing.PARTIAL)
        lines = text.split("\n")
        if lines and command in lines[0]:  # Echo des Befehls
            lines = lines[1:]
        if self.cut:  # bis zur letzten ganzen Zeile (wie `_exec`); kein Prompt am Ende
            return "\n".join(lines[:-1]).strip("\n") + "\n"
        if lines and PROMPT.search("\n" + lines[-1]):
            lines = lines[:-1]
        return "\n".join(lines).strip("\n") + "\n"

    def _read(self, login: bool = False, timeout: float = COMMAND_TIMEOUT,
              partial: bool = False) -> str:  # fmt: skip
        """Lesen bis zum Prompt; Anmeldung in der Sitzung nur beim Oeffnen. `partial`: zu grosse
        Ausgabe bis hier zurueckgeben (`cut`) statt abzubrechen."""
        buf, size = "", 0
        sent_user = sent_pass = False
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(L("Sitzung: kein Prompt", "session: no prompt"))
            try:
                data = self.chan.recv(65536)
            except TimeoutError:  # socket.timeout
                data = None
            if data == b"":
                raise EOFError(L("Sitzung beendet", "session ended"))
            if data:
                size += len(data)
                if size > MAX_OUTPUT:
                    if partial:  # bis zur Grenze, `run` kuerzt auf die letzte ganze Zeile
                        self.cut = True
                        part = data[: len(data) - (size - MAX_OUTPUT)]
                        return buf + _NOISE.sub("", self._decode(part).replace("\r", ""))
                    raise RuntimeError(L("Ausgabe zu gross", "output too large"))
                buf += _NOISE.sub("", self._decode(data).replace("\r", ""))
            tail = buf[-200:]
            if login and ANY_KEY.search(tail):
                raise UnsupportedDevice(L(
                    "ArubaOS-Switch/ProCurve wird nicht gelesen (kein Exec, Sitzung nur mit "
                    "Tastendruck und Bildschirmsteuerung) - unterstuetzt ist Aruba CX (AOS-CX)",
                    "ArubaOS-Switch/ProCurve is not read (no exec, session only with key press "
                    "and screen control) - supported is Aruba CX (AOS-CX)",
                ))  # fmt: skip
            if MORE.search(tail):
                buf = MORE.sub("", buf)
                self.chan.send(" ")
                continue
            if login and LOGIN.search(tail):
                if sent_user:
                    raise SessionLoginFailed("Anmeldung in der Sitzung abgelehnt")
                self.chan.send(self.username + "\n")
                sent_user, buf = True, ""
                continue
            if login and PASSWORD.search(tail):
                if sent_pass:
                    raise SessionLoginFailed("Anmeldung in der Sitzung abgelehnt")
                self.chan.send(self.password + "\n")
                sent_pass, buf = True, ""
                continue
            m = _at_prompt(buf)
            if m and (login or m.group(1) + m.group(2) == self.prompt):
                if login:
                    self.prompt = m.group(1) + m.group(2)
                return buf
