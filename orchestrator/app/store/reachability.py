"""Erreichbarkeit eines zentral gelesenen Quellsystems prüfen (Dev-Befund 0.33.0).

„nicht erreichbar … ConnectTimeout“ nach drei Minuten sagt nicht, woran es liegt. Die Prüfung
läuft im Orchestrator-Container in Sekunden und trennt die Schritte:

1. **Name** auflösen (z. B. `host.docker.internal` → Gateway des Docker-Hosts),
2. **TCP-Verbindung** zum Port (Zeitüberschreitung = Ziel aus bzw. Weg gesperrt, abgelehnt =
   auf dem Port läuft nichts),
3. bei NetBox **`GET /api/status/`** mit dem Lese-Token (nur lesend, `sot_gateway.readonly`).

Jeder Schritt nennt einen Hinweis zur Abhilfe; Tokens erscheinen nie in der Antwort.
"""

import socket
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

import httpx
from sot_gateway.adapters.netbox import auth_header
from sot_gateway.readonly import enforce_read_only

CONNECT_TIMEOUT = 5.0


@dataclass
class Step:
    step: str  # dns | tcp | http
    ok: bool
    detail: str


@dataclass
class Result:
    ok: bool
    steps: list[Step] = field(default_factory=list)
    hint: str | None = None

    def out(self) -> dict:
        return {"ok": self.ok, "hint": self.hint, "steps": [asdict(s) for s in self.steps]}


def _ms(start: float) -> str:
    return f"{(time.monotonic() - start) * 1000:.0f} ms"


def _tcp(addr: str, port: int) -> tuple[bool, str, str | None]:
    """(ok, Detail, Fehlerart) für eine TCP-Verbindung."""
    start = time.monotonic()
    try:
        with socket.create_connection((addr, port), timeout=CONNECT_TIMEOUT):
            return True, f"{addr}:{port} verbunden ({_ms(start)})", None
    except TimeoutError:
        return False, f"{addr}:{port} keine Antwort nach {CONNECT_TIMEOUT:.0f} s", "timeout"
    except ConnectionRefusedError:
        return False, f"{addr}:{port} Verbindung abgelehnt", "refused"
    except OSError as exc:
        return False, f"{addr}:{port} {type(exc).__name__}: {exc.strerror or exc}", "error"


def check(
    base_url: str,
    platform: str,
    *,
    token: str | None = None,
    verify_tls: bool = True,
    transport: httpx.BaseTransport | None = None,
) -> Result:
    parts = urlsplit(base_url or "")
    host = parts.hostname
    if not host:
        return Result(False, hint="Keine gültige Adresse am Quellsystem hinterlegt.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    result = Result(True)

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addrs = list(dict.fromkeys(str(i[4][0]) for i in infos))
    except socket.gaierror as exc:
        result.ok = False
        result.steps.append(Step("dns", False, f"{host} nicht auflösbar ({exc.strerror or exc})"))
        result.hint = (
            "Der Name ist im Container unbekannt. Für host.docker.internal braucht der Container "
            "`extra_hosts: host.docker.internal:host-gateway` (docker-compose.yml) und muss danach "
            "neu erstellt werden (`docker compose up -d --force-recreate`)."
        )
        return result
    result.steps.append(Step("dns", True, f"{host} → {', '.join(addrs)}"))

    kind = None
    for addr in addrs[:2]:
        ok, detail, kind = _tcp(addr, port)
        result.steps.append(Step("tcp", ok, detail))
        if ok:
            break
    else:
        result.ok = False
        result.hint = {
            "timeout": (
                f"Keine Antwort von Port {port}: Läuft das Ziel? Läuft es, sperrt der Weg – eine "
                "Firewall auf dem Docker-Host (ufw/firewalld) oder die Trennung der Docker-Netze. "
                "Bei NetBox auf demselben Host: Automato an das Docker-Netz von NetBox "
                "anschliessen und `http://netbox:8080` als Adresse verwenden (docs/BETRIEB.md, "
                "Fehlerbilder)."
            ),
            "refused": (
                f"Auf Port {port} läuft nichts: Port des Ziels prüfen (bei NetBox `NETBOX_PORT`, "
                "`docker ps`) bzw. ob der Dienst gestartet ist."
            ),
        }.get(kind or "", "Verbindung fehlgeschlagen - Netzwerk des Containers prüfen.")
        return result

    if platform != "netbox":
        return result
    client = enforce_read_only(
        httpx.Client(
            headers={**(auth_header(token) if token else {}), "Accept": "application/json"},
            verify=verify_tls,
            timeout=httpx.Timeout(10.0, connect=CONNECT_TIMEOUT),
            transport=transport,
        )
    )
    start = time.monotonic()
    try:
        with client:
            response = client.get(f"{base_url.rstrip('/')}/api/status/")
    except httpx.HTTPError as exc:
        result.ok = False
        result.steps.append(Step("http", False, f"/api/status/: {type(exc).__name__}"))
        result.hint = (
            "TCP geht, HTTP nicht: Schema (http/https) und TLS-Prüfung am Quellsystem prüfen."
        )
        return result
    if response.status_code == 200:
        try:
            version = response.json().get("netbox-version")
        except ValueError:
            version = None
        text = f"NetBox {version}" if version else "HTTP 200"
        result.steps.append(Step("http", True, f"/api/status/: {text} ({_ms(start)})"))
        return result
    result.ok = False
    result.steps.append(Step("http", False, f"/api/status/: HTTP {response.status_code}"))
    result.hint = (
        "NetBox lehnt das Lese-Token ab – Token am Quellsystem neu hinterlegen."
        if response.status_code in (401, 403)
        else "NetBox antwortet mit einem Fehler – Logs des NetBox-Containers prüfen."
    )
    return result
