"""Agent-Zugang Ende zu Ende (1.6.0, ADR 0052): der Zugang aus dem Web-UI (ZIP mit agent.env,
Zertifikat, Schlüssel, Agent-CA) gegen das echte mTLS-Agent-Gateway (deploy/agent-gateway/
Caddyfile in caddy:2-alpine), veröffentlicht auf einem eigenen Port wie
AUTOMATO_AGENT_GATEWAY_PORT (1.5.1).

Geprüft wird, was die Agent-Station erlebt: mit den Dateien aus dem ZIP (agent.env gelesen wie
run-agent.ps1, TLS wie collectors.common.http) nimmt das Gateway den Agenten an und reicht
Subject und Gateway-Secret weiter; der Orchestrator erkennt daraus den Agenten (Modus bind).
Ohne Zertifikat, mit fremder CA oder ausserhalb der Agent-Pfade geht nichts durch, gefälschte
Kopfzeilen überschreibt das Gateway.

Braucht Docker und das Image caddy:2-alpine - darum nur mit AUTOMATO_TEST_TOR=1 (CI: Job docker).
"""

import io
import os
import shutil
import socket
import ssl
import subprocess
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from app.auth.mtls import common_name
from app.config import get_settings
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from test_agent_access import ADMIN, SECRET, make_ca

from collectors.common.http import client_kwargs
from collectors.config import Settings as AgentSettings

REPO = Path(__file__).resolve().parents[2]
CADDY = "caddy:2-alpine"
NETZ = "automato-gw-e2e"
PORT = 46100  # wie AUTOMATO_AGENT_GATEWAY_PORT beim Anwender (Port auf der VM)
HOST = "automato.example.ch"
NAME = "Agent Nord 1"  # mit Leerzeichen: so heissen Agenten in der Oberfläche

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTOMATO_TEST_TOR") != "1" or not shutil.which("docker"),
    reason="nur mit AUTOMATO_TEST_TOR=1 und Docker (CI-Job docker)",
)


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), check=check, capture_output=True, text=True)  # noqa: S603


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    """Gateway (Caddyfile aus dem Repo) + Echo statt Orchestrator, Agent-CA von make-certs.sh."""
    d = tmp_path_factory.mktemp("gw")
    pki = make_ca(d)
    out = _run("bash", str(d / "make-certs.sh"), "server", HOST)
    assert "gateway.crt" in out.stdout
    echo = d / "echo.caddy"  # „Orchestrator“: gibt zurück, was das Gateway weiterreicht
    echo.write_text(
        ':8000 {\n\trespond "{http.request.header.X-Client-Cert-Subject}|'
        '{http.request.header.X-Gateway-Secret}|{http.request.uri.path}"\n}\n'
    )
    leer = d / "client-tls"
    leer.mkdir()
    namen = [f"{NETZ}-orchestrator", f"{NETZ}-gateway"]
    netz_neu = _run("docker", "network", "create", NETZ, check=False).returncode == 0
    echt = socket.getaddrinfo
    try:
        _run("docker", "run", "-d", "--name", namen[0], "--network", NETZ, "--network-alias",
             "orchestrator", "-v", f"{echo}:/etc/caddy/Caddyfile:ro", CADDY)  # fmt: skip
        _run("docker", "run", "-d", "--name", namen[1], "--network", NETZ,
             "-p", f"127.0.0.1:{PORT}:8443",
             "-e", f"AUTOMATO_AGENT_GATEWAY_HOST={HOST}",
             "-e", f"AUTOMATO_AGENT_GATEWAY_SECRET={SECRET}",
             "-e", "AUTOMATO_INTERNAL_TLS=off",
             "-v", f"{REPO / 'deploy' / 'agent-gateway' / 'Caddyfile'}:/etc/caddy/Caddyfile:ro",
             "-v", f"{pki}:/etc/caddy/pki:ro",
             "-v", f"{REPO / 'deploy' / 'tls' / 'caddy'}:/etc/caddy/automato-tls:ro",
             "-v", f"{leer}:/etc/automato/tls:ro", CADDY)  # fmt: skip

        def aufloesen(host, *a, **kw):  # automato.example.ch → dieser Rechner
            return echt("127.0.0.1" if host == HOST else host, *a, **kw)

        socket.getaddrinfo = aufloesen
        for _ in range(40):
            try:
                with socket.create_connection(("127.0.0.1", PORT), timeout=1):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            logs = _run("docker", "logs", namen[1], check=False)
            pytest.fail("Gateway antwortet nicht: " + logs.stdout + logs.stderr)
        time.sleep(1)
        yield pki
    finally:
        socket.getaddrinfo = echt
        for c in namen:
            _run("docker", "rm", "-f", c, check=False)
        if netz_neu:
            _run("docker", "network", "rm", NETZ, check=False)


@pytest.fixture
def station(client, monkeypatch, gateway, tmp_path):
    """Zugang aus der Oberfläche herunterladen und auf der „Station“ ablegen; die Pfade
    C:\\Automato\\collector\\… zeigen hier auf den Ordner der Station."""
    s = get_settings()
    for key, value in {"internal_mtls_mode": "bind", "agent_gateway_secret": SECRET,
                       "agent_gateway_host": HOST, "agent_gateway_port": PORT,
                       "agent_ca_dir": str(gateway)}.items():  # fmt: skip
        monkeypatch.setattr(s, key, value)
    aid = client.post("/agents", json={"name": NAME}, headers=ADMIN).json()["id"]
    r = client.post(f"/agents/{aid}/access", json={}, headers=ADMIN)
    assert r.status_code == 200, r.text
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(tmp_path)
    env = {}
    for line in (tmp_path / "agent.env").read_text().splitlines():  # wie run-agent.ps1
        if line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().replace("C:\\Automato\\collector\\", f"{tmp_path}/")
    return env


def _agent_client(env: dict) -> httpx.Client:
    """TLS genau wie der Agent (collectors.common.http.client_kwargs)."""
    settings = AgentSettings(orchestrator_ca_bundle=env["AUTOMATO_ORCHESTRATOR_CA_BUNDLE"],
                             client_cert=env["AUTOMATO_CLIENT_CERT"],
                             client_key=env["AUTOMATO_CLIENT_KEY"])  # fmt: skip
    return httpx.Client(**client_kwargs(settings), trust_env=False)


def test_zugang_aus_der_oberflaeche_kommt_durch_das_gateway(client, station):
    url = station["AUTOMATO_ORCHESTRATOR_URL"]
    assert url == f"https://{HOST}:{PORT}"
    with _agent_client(station) as c:
        falsch = {"X-Gateway-Secret": "gefaelscht", "X-Client-Cert-Subject": "CN=boese"}
        r = c.post(f"{url}/internal/agent/heartbeat", headers=falsch)
    assert r.status_code == 200, r.text
    subject, secret, path = r.text.split("|")
    assert secret == SECRET and path == "/internal/agent/heartbeat"  # Gateway überschreibt
    assert common_name(subject) == NAME, subject
    # was das Gateway weiterreicht, lässt den Agenten mit seinem neuen Token herein (bind)
    token = station["AUTOMATO_AGENT_TOKEN"]
    weiter = {"X-Agent-Token": token, "X-Gateway-Secret": secret, "X-Client-Cert-Subject": subject}
    ok = client.post("/internal/agent/heartbeat", headers=weiter)
    assert ok.status_code == 204


def test_ohne_passendes_zertifikat_geht_nichts(station, tmp_path):
    url = station["AUTOMATO_ORCHESTRATOR_URL"]
    ca = station["AUTOMATO_ORCHESTRATOR_CA_BUNDLE"]
    ohne = httpx.Client(verify=ssl.create_default_context(cafile=ca), trust_env=False)
    with ohne, pytest.raises(httpx.TransportError):
        ohne.post(f"{url}/internal/agent/heartbeat")  # „certificate required“
    # Zertifikat einer fremden CA mit demselben Namen
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NAME)])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))  # fmt: skip
    (tmp_path / "fremd.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "fremd.key").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))  # fmt: skip
    fremd = _agent_client({**station, "AUTOMATO_CLIENT_CERT": str(tmp_path / "fremd.crt"),
                           "AUTOMATO_CLIENT_KEY": str(tmp_path / "fremd.key")})  # fmt: skip
    with fremd, pytest.raises(httpx.TransportError):
        fremd.post(f"{url}/internal/agent/heartbeat")
    # mit Zertifikat, aber ausserhalb der Agent-Pfade (z. B. die Oberfläche, der Worker-Pfad)
    with _agent_client(station) as c:
        assert c.get(f"{url}/agents").status_code == 404
        assert c.get(f"{url}/internal/collector-runs/x/target").status_code == 404
