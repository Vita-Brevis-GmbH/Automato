"""Verbindungen der Plattform pruefen (0.40.0, Dev-Befund 0.39.0: ufw sperrte den Weg zu NetBox).

Die Pruefung laeuft im Orchestrator-Container - auf genau dem Weg, den Einlesen (zentraler
Worker im selben Docker-Netz) und NetBox-Sync nehmen. Je Ziel ok | warn | error | info mit den
Schritten und einem Hinweis zur Abhilfe:

- **Plattform:** Datenbank (`SELECT 1`, seit 0.48.0 die Rolle: ohne DDL?), Redis (`PING`, Kanal
  zum zentralen Worker; seit 0.49.0 mit TLS intern je ein Schritt `tls`),
  Keycloak (bei `auth_mode=oidc`: JWKS des Issuers).
- **Quellsysteme:** jedes eingeschaltete, zentral gelesene Quellsystem ueber
  `reachability.check` (Name, TCP, bei NetBox `GET /api/status/` mit dem Lese-Token; nur
  lesend). Beim NetBox-Ziel des Syncs dazu, ob ein Schreib-Token hinterlegt ist.
- **Agenten:** Quellsysteme, die ein Agent liest, prueft der Agent selbst (`-Check`); sie stehen
  je Agent als Hinweis da.

Bei Zeitueberschreitung zu `host.docker.internal` (bzw. dem Docker-Host) nennt der Hinweis die
ufw-Regel mit dem Subnetz dieses Containers (`ufw`), die `deploy/aufbau.sh firewall` setzen
kann. Secrets erscheinen nie in der Antwort.
"""

import ipaddress
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import credentials, tls
from app.config import Settings
from app.i18n import L, lang, use
from app.models import Agent, SourceSystem
from app.store import db_roles, reachability

ORDER = {"ok": 0, "info": 0, "warn": 1, "error": 2}
TIMEOUT = 3.0
WORKERS = 8
DOCKER_HOST = "host.docker.internal"
ROUTES = Path("/proc/net/route")


@dataclass
class Item:
    key: str
    area: str  # Plattform | Anmeldung | Quellsysteme | Agenten
    name: str
    target: str | None
    status: str  # ok | warn | error | info
    steps: list[dict] = field(default_factory=list)
    hint: str | None = None
    ufw: dict | None = None  # {from, to, port, comment, command} - Regel fuer deploy/aufbau.sh


def run(
    session: Session,
    settings: Settings,
    tenant_ids: set[int] | None = None,
    *,
    redis_check=None,
    source_check=None,
    oidc_check=None,
) -> dict:
    """Alle Pruefungen; `tenant_ids` None = alle Mandanten (globaler Admin)."""
    network = own_network()
    redis_url = tls.redis_url(settings)
    redis = redis_check or (lambda url: _redis(url, tls.ca_file(settings)))
    items = [_database(session), redis(redis_url)]
    if settings.auth_mode == "oidc":
        items.append((oidc_check or _oidc)(settings))
    sources, agents = _sources(session, tenant_ids)
    check = source_check or _source
    code = lang()  # Threads beginnen ohne Sprache der Anfrage

    def one(s: dict) -> Item:
        with use(code):
            return check(s, network)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        items += list(pool.map(one, sources))
    items += agents
    worst = max((ORDER[i.status] for i in items), default=0)
    return {
        "status": next(k for k, v in ORDER.items() if v == worst and k != "info"),
        "problems": sum(1 for i in items if i.status in ("warn", "error")),
        "checked_at": datetime.now(UTC).isoformat(),
        "container": {"network": str(network) if network else None, "host": socket.gethostname()},
        "items": [asdict(i) for i in items],
    }


# -- Plattform --------------------------------------------------------------------------


def _database(session: Session) -> Item:
    start = time.monotonic()
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - jede Ursache ist ein Befund
        return Item("database", _platform(), L("Datenbank", "Database"), None, "error",
                    [_step("sql", False, type(exc).__name__)],
                    L("Container „postgres“ prüfen (docker compose ps, logs postgres).",
                      "Check container “postgres” (docker compose ps, logs postgres)."),
                    )  # fmt: skip
    steps = [_step("sql", True, f"SELECT 1 ({_ms(start)})")]
    try:
        role = db_roles.describe(session)
        secure = _db_tls(session)
    except Exception:  # noqa: BLE001 - Rolle und TLS sind nur ein Zusatz
        session.rollback()
        role = secure = None
    if secure is not None and secure[0]:
        steps.append(_step("tls", True, L(f"verschlüsselt ({secure[1]})",
                                          f"encrypted ({secure[1]})")))  # fmt: skip
    if role is None or role["separated"]:
        if role:
            steps.append(_step("rolle", True, L(f"{role['role']} ohne DDL",
                                                f"{role['role']} without DDL")))  # fmt: skip
        return Item("database", _platform(), L("Datenbank", "Database"), None, "ok", steps)
    what = (
        "Superuser"
        if role["superuser"]
        else L("Eigentümer der Tabellen bzw. mit CREATE", "owner of the tables or with CREATE")
    )
    steps.append(_step("rolle", False, f"{role['role']} ({what})"))
    return Item("database", _platform(), L("Datenbank", "Database"), None, "warn", steps,
                L("Getrennte Rollen einrichten: deploy/aufbau.sh db-rollen – Migration als "
                  "Eigentümer, Orchestrator ohne DDL.",
                  "Set up separate roles: deploy/aufbau.sh db-rollen – migration as owner, "
                  "orchestrator without DDL."))  # fmt: skip


def _db_tls(session: Session) -> tuple[bool, str | None] | None:
    """Nur bei TLS intern als Schritt gezeigt (`tls.db_tls`); eigene Funktion fuer Tests."""
    return tls.db_tls(session)


def _redis(url: str, cafile: str | None = None) -> Item:
    """Redis per TCP und `PING` (ohne Bibliothek; ein Passwort-Fehler heisst: erreichbar).
    `rediss://` (TLS intern, 0.49.0): TLS mit Pruefung gegen die interne CA."""
    parts = urlsplit(url or "")
    host, port = parts.hostname or "localhost", parts.port or 6379
    target = f"{host}:{port}"
    steps = []
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as raw:
            steps.append(_step("tcp", True, L(f"{target} verbunden ({_ms(start)})",
                                              f"{target} connected ({_ms(start)})")))  # fmt: skip
            sock = raw
            if parts.scheme == "rediss":
                sock = ssl.create_default_context(cafile=cafile).wrap_socket(
                    raw, server_hostname=host
                )
                checked = L(f"{sock.version()}, Zertifikat geprüft",
                            f"{sock.version()}, certificate verified")  # fmt: skip
                steps.append(_step("tls", True, checked))
            sock.settimeout(TIMEOUT)
            sock.sendall(b"PING\r\n")
            answer = sock.recv(64).decode(errors="replace").strip()
    except ssl.SSLError as exc:
        steps.append(_step("tls", False, getattr(exc, "verify_message", None) or exc.reason
                           or type(exc).__name__))  # fmt: skip
        return Item("redis", _platform(), _redis_name(), target, "error", steps,
                    L("TLS zu Redis scheitert – Zertifikate der internen CA prüfen: "
                      "deploy/aufbau.sh tls-intern",
                      "TLS to Redis fails – check the certificates of the internal CA: "
                      "deploy/aufbau.sh tls-intern"))  # fmt: skip
    except OSError as exc:
        steps.append(_step("tcp", False, f"{target} {type(exc).__name__}"))
        return Item("redis", _platform(), _redis_name(), target, "error", steps,
                    L("Container „redis“ prüfen – ohne Redis holt der zentrale Worker keine "
                      "Läufe ab.",
                      "Check container “redis” – without Redis the central worker does not pick "
                      "up runs."))  # fmt: skip
    ok = answer.startswith("+PONG") or "NOAUTH" in answer
    steps.append(_step("ping", ok, answer[:40] or L("keine Antwort", "no response")))
    return Item("redis", _platform(), _redis_name(), target, "ok" if ok else "warn", steps,
                None if ok else L("Redis antwortet unerwartet – Logs prüfen.",
                                  "Redis responds unexpectedly – check the logs."))  # fmt: skip


def _oidc(settings: Settings) -> Item:
    """Keycloak: JWKS des Issuers (ohne sie lehnt der Orchestrator jede Anmeldung ab)."""
    url = settings.oidc_jwks_url
    if not url:
        return Item("oidc", _login(), "Keycloak (JWKS)", None, "error", [],
                    L("AUTOMATO_OIDC_JWKS_URL ist nicht gesetzt.",
                      "AUTOMATO_OIDC_JWKS_URL is not set."))  # fmt: skip
    start = time.monotonic()
    try:
        r = httpx.get(url, timeout=httpx.Timeout(5.0, connect=TIMEOUT))
    except httpx.HTTPError as exc:
        return Item("oidc", _login(), "Keycloak (JWKS)", url, "error",
                    [_step("http", False, type(exc).__name__)],
                    L("Der Orchestrator erreicht Keycloak nicht – Name/extra_hosts, Firewall bzw. "
                      "Zertifikat prüfen (deploy/keycloak/PROD-INSTALL.md).",
                      "The orchestrator does not reach Keycloak – check name/extra_hosts, "
                      "firewall or certificate (deploy/keycloak/PROD-INSTALL.md)."))  # fmt: skip
    keys = 0
    if r.status_code == 200:
        try:
            keys = len(r.json().get("keys") or [])
        except ValueError:
            keys = 0
    ok = r.status_code == 200 and keys > 0
    detail = L(f"HTTP {r.status_code}, {keys} Schlüssel ({_ms(start)})",
               f"HTTP {r.status_code}, {keys} keys ({_ms(start)})")  # fmt: skip
    return Item("oidc", _login(), "Keycloak (JWKS)", url, "ok" if ok else "error",
                [_step("http", ok, detail)],
                None if ok else L("JWKS-Adresse bzw. Realm prüfen.",
                                  "Check the JWKS address or realm."))  # fmt: skip


# -- Quellsysteme -------------------------------------------------------------------------


def _sources(session: Session, tenant_ids: set[int] | None) -> tuple[list[dict], list[Item]]:
    stmt = select(SourceSystem).where(SourceSystem.enabled.is_(True))
    if tenant_ids is not None:
        stmt = stmt.where(SourceSystem.tenant_id.in_(sorted(tenant_ids)))
    central, by_agent = [], {}
    for s in session.scalars(stmt.order_by(SourceSystem.name)):
        if s.agent_id is not None:
            by_agent.setdefault(s.agent_id, []).append(s.name)
            continue
        if not (s.base_url or "").strip():
            continue
        try:
            secrets = credentials.decrypt(s.credentials) if s.credentials else {}
        except credentials.CredentialsUnavailable:
            secrets = {}
        central.append({
            "id": s.id, "name": s.name, "platform": s.platform.value, "url": s.base_url,
            "verify_tls": s.verify_tls, "token": secrets.get("token"),
            "write_token": bool(secrets.get("write_token")),
        })  # fmt: skip
    agents = {a.id: a.name for a in session.scalars(select(Agent).where(Agent.id.in_(by_agent)))}
    items = [
        Item(
            f"agent:{aid}",
            L("Agenten", "Agents"),
            L(f"Agent „{agents.get(aid, aid)}“", f"Agent “{agents.get(aid, aid)}”"),
            None,
            "info",
            [],
            L(
                f"{len(names)} Quellsysteme ({', '.join(names[:5])}"
                f"{' …' if len(names) > 5 else ''}) liest der Agent – Prüfung auf der Station: "
                ".\\run-agent.ps1 -Check",
                f"{len(names)} source systems ({', '.join(names[:5])}"
                f"{' …' if len(names) > 5 else ''}) are read by the agent – check on the "
                "station: .\\run-agent.ps1 -Check",
            ),
        )  # fmt: skip
        for aid, names in by_agent.items()
    ]
    return central, items


def _source(s: dict, network) -> Item:
    result = reachability.check(s["url"], s["platform"], token=s["token"],
                                verify_tls=s["verify_tls"])  # fmt: skip
    steps = [asdict(st) for st in result.steps]
    item = Item(f"source:{s['id']}", L("Quellsysteme", "Source systems"),
                f"{s['name']} ({s['platform']})", s["url"],
                "ok" if result.ok else "error", steps, result.hint)  # fmt: skip
    if result.ok and s["platform"] == "netbox":
        steps.append(_step("sync", s["write_token"],
                           L("Schreib-Token für den NetBox-Sync hinterlegt",
                             "Write token for the NetBox sync stored") if s["write_token"]
                           else L("kein Schreib-Token – NetBox-Sync nicht möglich",
                                  "no write token – NetBox sync not possible")))  # fmt: skip
    # Merkmal der Pruefung statt Text des Schritts (der folgt der Sprache, ADR 0028)
    timeout = getattr(result, "failure", None) == "timeout"
    if not result.ok and timeout:
        item.ufw = _ufw_rule(s, steps, network)
        if item.ufw:
            item.hint = L(
                "Keine Antwort vom Docker-Host: meist sperrt die Host-Firewall (ufw) den Weg vom "
                f"Container-Netz {item.ufw['from']}. Freigeben (nur dieser Port): "
                f"{item.ufw['command']} – oder `deploy/aufbau.sh firewall --anwenden`.",
                "No response from the Docker host: usually the host firewall (ufw) blocks the "
                f"path from the container network {item.ufw['from']}. Allow (only this port): "
                f"{item.ufw['command']} – or `deploy/aufbau.sh firewall --anwenden`.",
            )
    return item


def _ufw_rule(s: dict, steps: list[dict], network) -> dict | None:
    """ufw-Regel fuer ein Ziel auf dem Docker-Host: vom Netz dieses Containers zur Adresse und
    zum Port des Ziels - nur fuer `host.docker.internal` bzw. das Gateway des Containers."""
    parts = urlsplit(s["url"])
    port = parts.port or (443 if parts.scheme == "https" else 80)
    dns = next((st for st in steps if st["step"] == "dns" and st["ok"]), None)
    addr = dns["detail"].split("→", 1)[-1].split(",")[0].strip() if dns else None
    gateway = own_gateway()
    if parts.hostname != DOCKER_HOST and (gateway is None or addr != gateway):
        return None
    if network is None or not addr:
        return None
    comment = "Automato -> " + s["name"].replace("'", "")  # in Hochkommas - kopierbar
    command = f"sudo ufw allow proto tcp from {network} to {addr} port {port} comment '{comment}'"
    return {"from": str(network), "to": addr, "port": port, "comment": comment,
            "command": command}  # fmt: skip


# -- Netz dieses Containers ---------------------------------------------------------------


def _routes() -> list[tuple[str, int, int, int]]:
    """(Interface, Ziel, Gateway, Maske) aus /proc/net/route (Linux, Zahlen in Host-Byteorder)."""
    try:
        lines = ROUTES.read_text().splitlines()[1:]
    except OSError:
        return []
    out = []
    for line in lines:
        cols = line.split()
        if len(cols) >= 8:
            try:
                dest, gw, mask = (int.from_bytes(bytes.fromhex(c), "little")
                                  for c in (cols[1], cols[2], cols[7]))  # fmt: skip
            except ValueError:
                continue
            out.append((cols[0], dest, gw, mask))
    return out


def own_network():
    """Subnetz des Containers (erste Route ausser der Default-Route), z. B. 172.20.0.0/16."""
    for _iface, dest, _gw, mask in _routes():
        if dest and mask:
            try:
                return ipaddress.ip_network(
                    f"{ipaddress.IPv4Address(dest)}/{ipaddress.IPv4Address(mask)}", strict=False
                )
            except ValueError:
                continue
    return None


def own_gateway() -> str | None:
    for _iface, dest, gw, _mask in _routes():
        if dest == 0 and gw:
            return str(ipaddress.IPv4Address(gw))
    return None


def _platform() -> str:
    return L("Plattform", "Platform")


def _login() -> str:
    return L("Anmeldung", "Sign-in")


def _redis_name() -> str:
    return L("Redis (Warteschlange zum zentralen Worker)", "Redis (queue to the central worker)")


def _step(step: str, ok: bool, detail: str) -> dict:
    return {"step": step, "ok": ok, "detail": detail}


def _ms(start: float) -> str:
    return f"{(time.monotonic() - start) * 1000:.0f} ms"
