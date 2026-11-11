"""Verbindung eines Geräts vom Agenten aus prüfen (1.7.0) - für „Prüfen“ in der Oberfläche.

Wie die Prüfung des Orchestrators bei zentral gelesenen Systemen (`app/store/reachability.py`):
Name auflösen, TCP-Verbindung, dazu die Anmeldung mit den Zugangsdaten des Quellsystems über die
`check()`-Funktion der Plattform (read-only, dieselbe wie `run-agent.ps1 -Check`). Ergebnis als
Schritte `dns`, `tcp`, `login` - nie Zugangsdaten. Texte deutsch: der Orchestrator speichert sie.
"""

import socket
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from collectors.common.dto import Target

CONNECT_TIMEOUT = 5.0
PORTS = {"https": 443, "http": 80, "ssh": 22, "snmp": 161, "dns": 53}


def _step(step: str, ok: bool, detail: str) -> dict:
    return {"step": step, "ok": ok, "detail": detail[:500]}


def _tcp(addr: str, port: int) -> tuple[bool, str]:
    start = time.monotonic()
    try:
        with socket.create_connection((addr, port), timeout=CONNECT_TIMEOUT):
            ms = (time.monotonic() - start) * 1000
            return True, f"{addr}:{port} offen ({ms:.0f} ms)"
    except TimeoutError:
        return False, f"{addr}:{port} keine Antwort nach {CONNECT_TIMEOUT:.0f} s"
    except ConnectionRefusedError:
        return False, f"{addr}:{port} Verbindung abgelehnt"
    except OSError as exc:
        return False, f"{addr}:{port} {type(exc).__name__}: {exc.strerror or exc}"


def probe(
    target: Target, login: Callable[[Target], tuple[bool, str]] | None
) -> tuple[bool, list[dict], str | None]:
    """-> (ok, Schritte, Hinweis). `login` = Prüfung der Plattform (None: nicht unterstützt)."""
    parts = urlsplit(target.base_url or "")
    host = parts.hostname
    if not host:
        return False, [], "Keine gültige Adresse am Quellsystem hinterlegt."
    port = parts.port or PORTS.get(parts.scheme, 80)
    steps: list[dict] = []
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addrs = list(dict.fromkeys(str(i[4][0]) for i in infos))
    except socket.gaierror as exc:
        steps.append(_step("dns", False, f"{host} nicht auflösbar ({exc.strerror or exc})"))
        return False, steps, "Der Name ist auf der Station des Agenten unbekannt (DNS der Station)."
    steps.append(_step("dns", True, f"{host} → {', '.join(addrs)}"))

    if parts.scheme != "snmp":  # SNMPv3 läuft über UDP - nur die Anfrage selbst zeigt es
        reached = False
        for addr in addrs[:2]:
            ok, detail = _tcp(addr, port)
            steps.append(_step("tcp", ok, detail))
            if ok:
                reached = True
                break
        if not reached:
            return (
                False,
                steps,
                (
                    "Keine Verbindung von der Station des Agenten - Firewall zwischen Agent und "
                    f"Gerät (Port {port}) bzw. Adresse prüfen."
                ),
            )

    if login is None:
        return (
            False,
            steps,
            (f"Plattform „{target.platform}“ kennt dieser Agent nicht - Agent aktualisieren."),
        )
    ok, message = login(target)
    steps.append(_step("login", ok, message))
    return ok, steps, None
