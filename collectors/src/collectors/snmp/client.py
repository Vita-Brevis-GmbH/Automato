"""SNMPv3-Client fuer Switches und Router, strikt lesend (ADR 0033).

- **Nur SNMPv3 mit authPriv**: Benutzer, Auth-Protokoll SHA bzw. SHA-2 (HMAC, RFC 3414/7860) mit
  Passwort, Priv-Protokoll AES (RFC 3826) mit Passwort, optional ein Context. Kein v1/v2c, keine
  Community, kein noAuth/authNoPriv, kein MD5/DES - fehlt etwas, bricht der Client vor dem ersten
  Paket ab (`SnmpConfigError`).
- **Nur GET und GETBULK** auf die Positivliste (`oids.py`): `get` fuer einzelne Werte, `walk` fuer
  eine Spalte (GETBULK, Fortsetzung nur innerhalb der Spalte). Eine Funktion zum Schreiben (SET)
  gibt es nicht und wird aus pysnmp nicht importiert; jede andere Abfrage ist ein
  `ReadOnlyViolation`, geprueft im Client und im Transport noch einmal vor dem Senden.
- Je Spalte hoechstens `limit` Zeilen und `WALK_SECONDS` Sekunden (sonst gekappt, Hinweis im Lauf).

pysnmp (BSD-2-Clause) wird erst beim Verbinden geladen: ohne pysnmp laufen die uebrigen Collector
und die Tests (Transport als Attrappe) weiter. pysnmp ab 7.1 spricht asyncio; je Switch eine
eigene Ereignisschleife im Thread des Lesers.
"""

import asyncio
import contextlib
import ipaddress
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from collectors.common.i18n import L
from collectors.snmp import oids

PORT = 161  # UDP
TIMEOUT = 5.0  # Sekunden je Anfrage
RETRIES = 1
MAX_REPETITIONS = 25  # Zeilen je GETBULK (Antwort bleibt unter der MTU)
MAX_ROWS = 20_000  # Zeilen je Spalte (Ports, VLANs, Nachbarn)
WALK_SECONDS = 120.0  # je Spalte; danach gekappt (wie 120 s je Befehl bei SSH)

# erlaubte Protokolle: Name -> Namen der Konstanten in pysnmp (neu, alt)
AUTH_PROTOCOLS: dict[str, tuple[str, ...]] = {
    "sha": ("USM_AUTH_HMAC96_SHA", "usmHMACSHAAuthProtocol"),
    "sha224": ("USM_AUTH_HMAC128_SHA224", "usmHMAC128SHA224AuthProtocol"),
    "sha256": ("USM_AUTH_HMAC192_SHA256", "usmHMAC192SHA256AuthProtocol"),
    "sha384": ("USM_AUTH_HMAC256_SHA384", "usmHMAC256SHA384AuthProtocol"),
    "sha512": ("USM_AUTH_HMAC384_SHA512", "usmHMAC384SHA512AuthProtocol"),
}
PRIV_PROTOCOLS: dict[str, tuple[str, ...]] = {
    "aes128": ("USM_PRIV_CFB128_AES", "usmAesCfb128Protocol"),
    "aes192": ("USM_PRIV_CFB192_AES", "usmAesCfb192Protocol"),
    "aes256": ("USM_PRIV_CFB256_AES", "usmAesCfb256Protocol"),
}
DEFAULT_AUTH, DEFAULT_PRIV = "sha256", "aes128"
MIN_PASSWORD = 8  # RFC 3414: kuerzere Passwoerter ergeben keinen sicheren Schluessel


class SnmpConfigError(ValueError):
    """Zugangsdaten passen nicht zu SNMPv3 authPriv (vor dem ersten Paket)."""


class SnmpUnavailable(RuntimeError):
    """pysnmp ist nicht installiert."""


class SnmpTimeout(Exception):
    """Keine Antwort (Geraet nicht erreichbar, UDP gesperrt, SNMP aus bzw. Benutzer unbekannt)."""


class SnmpAuthError(Exception):
    """Das Geraet lehnt die SNMPv3-Anmeldung ab (Benutzer, Digest, Entschluesselung)."""


class SnmpError(Exception):
    """Andere Fehlermeldung des Geraets bzw. von pysnmp."""


@dataclass(frozen=True)
class SnmpCredentials:
    """SNMPv3-Benutzer mit authPriv; Passwoerter nie in `repr` (Logs, Tracebacks)."""

    user: str
    auth_password: str = field(repr=False)
    priv_password: str = field(repr=False)
    auth_protocol: str = DEFAULT_AUTH
    priv_protocol: str = DEFAULT_PRIV
    context: str = ""


def credentials(target) -> SnmpCredentials:
    """Zugangsdaten eines Ziels (`Target`) pruefen: nur authPriv mit SHA/SHA-2 und AES."""
    user = (target.username or "").strip()
    auth, priv = target.auth_password or "", target.priv_password or ""
    if not (user and auth and priv):
        raise SnmpConfigError(
            L(
                "SNMPv3 nur mit authPriv: Benutzer, Auth- und Priv-Passwort noetig",
                "SNMPv3 only with authPriv: user, auth and priv password needed",
            )
        )
    auth_protocol = (target.auth_protocol or DEFAULT_AUTH).strip().lower()
    priv_protocol = (target.priv_protocol or DEFAULT_PRIV).strip().lower()
    if auth_protocol not in AUTH_PROTOCOLS:
        raise SnmpConfigError(
            L(
                f"SNMPv3: Auth-Protokoll „{auth_protocol}“ nicht erlaubt (SHA, SHA-224, SHA-256, "
                "SHA-384, SHA-512)",
                f"SNMPv3: auth protocol “{auth_protocol}” not allowed (SHA, SHA-224, SHA-256, "
                "SHA-384, SHA-512)",
            )
        )
    if priv_protocol not in PRIV_PROTOCOLS:
        raise SnmpConfigError(
            L(
                f"SNMPv3: Priv-Protokoll „{priv_protocol}“ nicht erlaubt (AES-128, AES-192, "
                "AES-256)",
                f"SNMPv3: priv protocol “{priv_protocol}” not allowed (AES-128, AES-192, AES-256)",
            )
        )
    if len(auth) < MIN_PASSWORD or len(priv) < MIN_PASSWORD:
        raise SnmpConfigError(
            L(
                "SNMPv3: Auth- und Priv-Passwort mit mindestens 8 Zeichen",
                "SNMPv3: auth and priv password with at least 8 characters",
            )
        )
    return SnmpCredentials(user, auth, priv, auth_protocol, priv_protocol,
                           (target.context or "").strip())  # fmt: skip


def split_host(entry: str) -> tuple[str, int]:
    """`host`, `host:port` bzw. `[v6]:port`, IPv6 ohne Klammern -> (host, port), Standard 161."""
    entry = entry.strip()
    if entry.startswith("[") and "]" in entry:
        host, _, rest = entry[1:].partition("]")
        return host, int(rest.lstrip(":") or PORT)
    if entry.count(":") == 1:
        host, port = entry.split(":")
        return host, int(port or PORT)
    return entry, PORT


class Transport(Protocol):
    """Verbindung zu einem Agenten: nur GET und GETBULK (Tests: Attrappe mit Walk-Daten)."""

    def open(self) -> None: ...

    def close(self) -> None: ...

    def get(self, oid_list: list[str]) -> dict[str, Any]: ...

    def walk(self, base: str, limit: int, seconds: float) -> tuple[list[tuple[str, Any]], bool]:
        """Zeilen (OID, Wert) der Spalte `base` in OID-Reihenfolge; True = gekappt."""
        ...


class SnmpClient:
    """Ein Switch; `get` liest Werte aus `SCALARS`, `walk` eine Spalte aus `WALKS`."""

    def __init__(self, entry: str, creds: SnmpCredentials, transport: Transport | None = None,
                 timeout: float = TIMEOUT, retries: int = RETRIES):  # fmt: skip
        self.entry = entry
        self.host, self.port = split_host(entry)
        self.creds = creds
        self.timeout, self.retries = timeout, retries
        self._transport = transport

    def __enter__(self) -> "SnmpClient":
        if self._transport is None:
            self._transport = PysnmpTransport(self.host, self.port, self.creds, self.timeout,
                                              self.retries)  # fmt: skip
        try:
            self._transport.open()
        except BaseException:
            self._transport.close()  # Ereignisschleife/Engine auch beim Fehlschlag schliessen
            raise
        return self

    def __exit__(self, *exc) -> None:
        if self._transport is not None:
            self._transport.close()

    def get(self, *names: str) -> dict[str, Any]:
        """Werte aus `SCALARS` (Name -> Wert, None = nicht vorhanden)."""
        wanted = {n: oids.SCALARS.get(n, n) for n in names}
        for oid in wanted.values():
            oids.check("get", oid)
        values = self._transport.get(list(wanted.values()))
        return {n: values.get(oid) for n, oid in wanted.items()}

    def get_row(self, column: str, index: str) -> Any:
        """Eine Zeile einer Spalte aus `INSTANCE_COLUMNS` (z. B. Modell des Chassis)."""
        oid = f"{oids.INSTANCE_COLUMNS.get(column, column)}.{index}"
        oids.check("get", oid)
        return self._transport.get([oid]).get(oid)

    def walk(self, name: str, limit: int = MAX_ROWS) -> tuple[dict[str, Any], bool]:
        """Spalte `name` -> {Index: Wert} (Index = Rest der OID) und ob sie gekappt wurde."""
        base = oids.WALKS.get(name, name)
        oids.check("walk", base)
        rows, cut = self._transport.walk(base, limit, WALK_SECONDS)
        prefix = base + "."
        return {oid[len(prefix) :]: v for oid, v in rows if oid.startswith(prefix)}, cut


# -- pysnmp ---------------------------------------------------------------------------------


def _load() -> dict:
    """Nur die lesenden Teile von pysnmp, einzeln mit Namen (kein SET, nicht das ganze Modul)."""
    try:
        from pysnmp.entity import config as usm
        from pysnmp.hlapi.v3arch.asyncio import (
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            Udp6TransportTarget,
            UdpTransportTarget,
            UsmUserData,
            bulk_cmd,
            get_cmd,
        )
    except ImportError as exc:
        raise SnmpUnavailable(
            L(
                "pysnmp fehlt - Agent neu installieren bzw. Selbst-Update abwarten",
                "pysnmp missing - reinstall the agent or wait for the self-update",
            )
        ) from exc
    return {
        "usm": usm, "ContextData": ContextData, "ObjectIdentity": ObjectIdentity,
        "ObjectType": ObjectType, "SnmpEngine": SnmpEngine, "Udp6": Udp6TransportTarget,
        "Udp": UdpTransportTarget, "UsmUserData": UsmUserData, "bulk": bulk_cmd, "get": get_cmd,
    }  # fmt: skip


def _protocol(usm, names: tuple[str, ...]):
    """Konstante des Protokolls (Kennung der USM) in pysnmp - neue bzw. alte Schreibweise."""
    for name in names:
        value = getattr(usm, name, None)
        if value is not None:
            return value
    raise SnmpUnavailable(f"pysnmp: {names[-1]}")  # pragma: no cover - zu alte Version


_EMPTY = {"NoSuchObject", "NoSuchInstance", "EndOfMibView", "Null"}
_AUTH_ERRORS = {"UnknownUserName", "UnknownSecurityName", "WrongDigest", "WrongDigests",
                "DecryptionError", "UnsupportedSecLevel", "AuthenticationError",
                "AuthenticationFailure", "UnknownEngineID", "NotInTimeWindow"}  # fmt: skip


def native(value) -> Any:
    """pysnmp-Wert -> int | bytes | str (OID) | None (nicht vorhanden, Ende der MIB)."""
    kind = type(value).__name__
    if value is None or kind in _EMPTY:
        return None
    if kind in ("ObjectIdentifier", "ObjectName"):
        return str(value)
    if hasattr(value, "asOctets"):
        return bytes(value.asOctets())
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _is_row(item) -> bool:
    """Zeile einer Tabelle (Liste von Variablen) statt einer Variable (Name, Wert)."""
    if isinstance(item, list):
        return True
    return (isinstance(item, tuple) and bool(item) and not hasattr(item[0], "asTuple")
            and not hasattr(item[0], "getOid"))  # fmt: skip


def _binds(var_binds):
    """(OID, Wert) aus der Antwort - flach bzw. als Tabelle (je nach pysnmp-Version)."""
    for item in var_binds or ():
        if _is_row(item):
            yield from _binds(item)
            continue
        name, value = item[0], item[1]
        if hasattr(name, "getOid"):
            name = name.getOid()
        yield str(name), value


def _error(indication) -> Exception:
    kind, text = type(indication).__name__, str(indication)
    if kind == "RequestTimedOut" or "timeout" in text.lower():
        return SnmpTimeout(text)
    if kind in _AUTH_ERRORS or any(w in text.lower() for w in ("digest", "user name", "decrypt")):
        return SnmpAuthError(text)
    return SnmpError(text)


def _oid_key(oid: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in oid.split("."))
    except ValueError:
        return ()


class PysnmpTransport:
    """SNMPv3 authPriv ueber UDP mit pysnmp; nur `get_cmd` und `bulk_cmd`."""

    def __init__(self, host: str, port: int, creds: SnmpCredentials, timeout: float,
                 retries: int) -> None:  # fmt: skip
        self.host, self.port, self.creds = host, port, creds
        self.timeout, self.retries = timeout, retries
        self._api: dict | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._engine = self._target = self._auth = self._context = None

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def open(self) -> None:
        api = self._api = _load()
        usm = api["usm"]
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._engine = api["SnmpEngine"]()
        try:
            v6 = ipaddress.ip_address(self.host).version == 6
        except ValueError:
            v6 = False
        target = api["Udp6"] if v6 else api["Udp"]
        self._target = self._run(target.create((self.host, self.port), timeout=self.timeout,
                                               retries=self.retries))  # fmt: skip
        c = self.creds
        self._auth = api["UsmUserData"](
            c.user, authKey=c.auth_password, privKey=c.priv_password,
            authProtocol=_protocol(usm, AUTH_PROTOCOLS[c.auth_protocol]),
            privProtocol=_protocol(usm, PRIV_PROTOCOLS[c.priv_protocol]),
        )  # fmt: skip
        context = api["ContextData"]
        self._context = context(contextName=c.context) if c.context else context()

    def close(self) -> None:
        engine, loop = self._engine, self._loop
        self._engine = self._loop = None
        if engine is not None:
            closer = getattr(engine, "close_dispatcher", None) or getattr(
                engine, "closeDispatcher", None
            )
            if closer is not None:
                with contextlib.suppress(Exception):  # beim Schliessen nichts mehr melden
                    closer()
        if loop is not None:
            # offene Tasks von pysnmp (Zeitgeber des Dispatchers) beenden - sonst meldet asyncio
            # beim Schliessen „Task was destroyed but it is pending“ im Protokoll des Agenten
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                with contextlib.suppress(Exception):
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            asyncio.set_event_loop(None)
            loop.close()

    def _var(self, oid: str):
        api = self._api
        return api["ObjectType"](api["ObjectIdentity"](oid))

    def _answer(self, result) -> list[tuple[str, Any]]:
        indication, status, _index, var_binds = result
        if indication:
            raise _error(indication)
        if status:
            text = status.prettyPrint() if hasattr(status, "prettyPrint") else str(status)
            if "noSuchName" in text:  # alte Agenten: Spalte nicht vorhanden
                return []
            raise SnmpError(text)
        return [(oid, native(v)) for oid, v in _binds(var_binds)]

    def get(self, oid_list: list[str]) -> dict[str, Any]:
        for oid in oid_list:
            oids.check("get", oid)
        binds = [self._var(o) for o in oid_list]
        result = self._run(self._api["get"](self._engine, self._auth, self._target, self._context,
                                            *binds, lookupMib=False))  # fmt: skip
        return dict(self._answer(result))

    def walk(self, base: str, limit: int, seconds: float) -> tuple[list[tuple[str, Any]], bool]:
        oids.check("walk", base)
        rows: list[tuple[str, Any]] = []
        current, last = base, _oid_key(base)
        deadline = time.monotonic() + seconds
        while len(rows) < limit:
            if time.monotonic() > deadline:
                return rows, True
            oids.check("next", current, base)
            result = self._run(self._api["bulk"](
                self._engine, self._auth, self._target, self._context, 0, MAX_REPETITIONS,
                self._var(current), lookupMib=False,
            ))  # fmt: skip
            answer = self._answer(result)
            if not answer:
                return rows, False
            for oid, value in answer:
                key = _oid_key(oid)
                if value is None or not oid.startswith(base + ".") or key <= last:
                    return rows, False  # Ende der Spalte (bzw. Agent ohne Fortschritt)
                rows.append((oid, value))
                current, last = oid, key
                if len(rows) >= limit:
                    return rows, True
        return rows, True
