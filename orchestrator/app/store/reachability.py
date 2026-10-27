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

from app.i18n import L

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
    # Art des Fehlers (dns | timeout | refused | error | http) - Merkmal statt Text der Schritte
    failure: str | None = None

    def out(self) -> dict:
        return {"ok": self.ok, "hint": self.hint, "steps": [asdict(s) for s in self.steps]}


def _ms(start: float) -> str:
    return f"{(time.monotonic() - start) * 1000:.0f} ms"


def _tcp(addr: str, port: int) -> tuple[bool, str, str | None]:
    """(ok, Detail, Fehlerart) für eine TCP-Verbindung."""
    start = time.monotonic()
    try:
        with socket.create_connection((addr, port), timeout=CONNECT_TIMEOUT):
            return True, L(f"{addr}:{port} verbunden ({_ms(start)})",
                           f"{addr}:{port} connected ({_ms(start)})"), None  # fmt: skip
    except TimeoutError:
        wait = f"{CONNECT_TIMEOUT:.0f} s"
        return False, L(f"{addr}:{port} keine Antwort nach {wait}",
                        f"{addr}:{port} no response after {wait}"), "timeout"  # fmt: skip
    except ConnectionRefusedError:
        return False, L(f"{addr}:{port} Verbindung abgelehnt",
                        f"{addr}:{port} connection refused"), "refused"  # fmt: skip
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
        return Result(
            False,
            hint=L(
                "Keine gültige Adresse am Quellsystem hinterlegt.",
                "No valid address stored on the source system.",
            ),
        )
    port = parts.port or {"https": 443, "ssh": 22}.get(parts.scheme, 80)
    result = Result(True)

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addrs = list(dict.fromkeys(str(i[4][0]) for i in infos))
    except socket.gaierror as exc:
        result.ok, result.failure = False, "dns"
        why = exc.strerror or exc
        result.steps.append(
            Step(
                "dns", False, L(f"{host} nicht auflösbar ({why})", f"{host} not resolvable ({why})")
            )
        )
        result.hint = L(
            "Der Name ist im Container unbekannt. Für host.docker.internal braucht der Container "
            "`extra_hosts: host.docker.internal:host-gateway` (docker-compose.yml) und muss danach "
            "neu erstellt werden (`docker compose up -d --force-recreate`).",
            "The name is unknown in the container. For host.docker.internal the container needs "
            "`extra_hosts: host.docker.internal:host-gateway` (docker-compose.yml) and must then "
            "be recreated (`docker compose up -d --force-recreate`).",
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
        result.ok, result.failure = False, kind
        result.hint = {
            "timeout": L(
                f"Keine Antwort von Port {port}: Läuft das Ziel? Läuft es, sperrt der Weg – eine "
                "Firewall auf dem Docker-Host (ufw/firewalld) oder die Trennung der Docker-Netze. "
                "Bei NetBox auf demselben Host: Automato an das Docker-Netz von NetBox "
                "anschliessen und `http://netbox:8080` als Adresse verwenden (docs/BETRIEB.md, "
                "Fehlerbilder).",
                f"No response from port {port}: is the target running? If it is, the path is "
                "blocked – a firewall on the Docker host (ufw/firewalld) or the separation of the "
                "Docker networks. With NetBox on the same host: connect Automato to the Docker "
                "network of NetBox and use `http://netbox:8080` as the address (docs/BETRIEB.md, "
                "troubleshooting).",
            ),
            "refused": L(
                f"Auf Port {port} läuft nichts: Port des Ziels prüfen (bei NetBox `NETBOX_PORT`, "
                "`docker ps`) bzw. ob der Dienst gestartet ist.",
                f"Nothing is listening on port {port}: check the port of the target (for NetBox "
                "`NETBOX_PORT`, `docker ps`) and whether the service is started.",
            ),
        }.get(
            kind or "",
            L(
                "Verbindung fehlgeschlagen - Netzwerk des Containers prüfen.",
                "Connection failed - check the network of the container.",
            ),
        )
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
        result.ok, result.failure = False, "http"
        result.steps.append(Step("http", False, f"/api/status/: {type(exc).__name__}"))
        result.hint = L(
            "TCP geht, HTTP nicht: Schema (http/https) und TLS-Prüfung am Quellsystem prüfen.",
            "TCP works, HTTP does not: check scheme (http/https) and TLS verification on the "
            "source system.",
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
    result.ok, result.failure = False, "http"
    result.steps.append(Step("http", False, f"/api/status/: HTTP {response.status_code}"))
    result.hint = (
        L(
            "NetBox lehnt das Lese-Token ab – Token am Quellsystem neu hinterlegen.",
            "NetBox rejects the read token – store the token on the source system again.",
        )
        if response.status_code in (401, 403)
        else L(
            "NetBox antwortet mit einem Fehler – Logs des NetBox-Containers prüfen.",
            "NetBox responds with an error – check the logs of the NetBox container.",
        )
    )
    return result
