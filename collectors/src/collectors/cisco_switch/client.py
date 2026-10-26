"""SSH-Client fuer Cisco-Switches (IOS, IOS-XE, NX-OS), rein lesend (ADR 0025).

Je Befehl ein SSH-Exec-Aufruf (wie `ssh switch "show version"`): keine interaktive Shell, kein
Terminal (damit kein Umbruch nach Seiten und kein `terminal length`), kein Konfigurationsmodus.
Erlaubt sind nur die festen `show`-Befehle aus `COMMANDS`; jeder andere Befehl bricht vor dem
Senden ab (`ReadOnlyViolation`). Das Konto braucht nur Leserechte (IOS privilege 1 bzw. eine
View, NX-OS-Rolle `network-operator`).

Host-Schluessel: ist einer gespeichert (vom ersten Lauf, im Orchestrator je Adresse), muss der
Switch ihn vorweisen - sonst bricht die Verbindung vor der Anmeldung ab. Ohne gespeicherten
Schluessel wird der vorgewiesene uebernommen und mit dem Ergebnis gemeldet (der Orchestrator
haelt ihn danach fest).
"""

import base64
import hashlib
import time

import paramiko
from sot_gateway.readonly import ReadOnlyViolation

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
    ),
}
ALLOWED = frozenset(c for cmds in COMMANDS.values() for c in cmds)
MAX_OUTPUT = 32 * 1024 * 1024  # je Befehl
COMMAND_TIMEOUT = 120.0  # Sekunden je Befehl


class HostKeyChanged(Exception):
    """Der Switch weist einen anderen Host-Schluessel vor als gespeichert."""


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
            raise HostKeyChanged(
                f"{self.entry}: Host-Schluessel hat sich geaendert ({fingerprint(self.host_key)} "
                f"gespeichert, {fingerprint(f'{exc.key.get_name()} {exc.key.get_base64()}')} "
                "vorgewiesen) - Geraet pruefen und im Web-UI den Schluessel neu lernen lassen"
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
        if self._ssh is not None:
            self._ssh.close()
            self._ssh = None

    # -- Befehle ---------------------------------------------------------------------------

    def run(self, command: str) -> str:
        """Erlaubten `show`-Befehl ausfuehren und die Ausgabe zurueckgeben. Manche Switches
        beenden die Sitzung nach einem Exec-Aufruf - dann neu verbinden (gleicher Schluessel)."""
        if command not in ALLOWED:
            raise ReadOnlyViolation(f"nur lesend: Befehl „{command}“ blockiert")
        for attempt in (1, 2):
            transport = self._ssh.get_transport() if self._ssh is not None else None
            if transport is None or not transport.is_active():
                self.close()
                self._ssh = self._connect()
                transport = self._ssh.get_transport()
            try:
                return self._exec(transport, command)
            except (paramiko.SSHException, EOFError, OSError):
                if attempt == 2:
                    raise
                self.close()  # Sitzung zu - einmal neu verbinden
        raise RuntimeError("nicht erreichbar")  # pragma: no cover

    def _exec(self, transport: paramiko.Transport, command: str) -> str:
        chan = transport.open_session(timeout=self.timeout)
        try:
            chan.settimeout(self.timeout)
            chan.exec_command(command)  # ohne PTY: keine Seiten, kein Terminal
            out, size = [], 0
            deadline = time.monotonic() + COMMAND_TIMEOUT
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"„{command}“: keine vollstaendige Antwort")
                try:
                    data = chan.recv(65536)
                except TimeoutError:  # socket.timeout
                    continue
                if not data:
                    break
                size += len(data)
                if size > MAX_OUTPUT:
                    raise RuntimeError(f"„{command}“: Ausgabe zu gross")
                out.append(data)
            while chan.recv_stderr_ready():
                chan.recv_stderr(65536)
            return b"".join(out).decode("utf-8", errors="replace")
        finally:
            chan.close()
