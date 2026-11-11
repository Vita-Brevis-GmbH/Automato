"""Agent-Zugang aus dem Web-UI (1.6.0, ADR 0052; Anwender 2026-11-11: „ich will das Zertifikat und
die .env Config im WebUI herunterladen können und es muss funktionieren“): ein ZIP je Agent mit
agent.env (neues Token) und - mit dem mTLS-Gateway - Client-Zertifikat, Schlüssel und Agent-CA.

Die Agent-CA entsteht hier mit dem echten deploy/agent-gateway/make-certs.sh (wie auf der VM).
"""

import io
import json
import os
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.config import get_settings
from app.models import AuditLog
from app.store import agent_access
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

REPO = Path(__file__).resolve().parents[2]
ADMIN = {"X-Dev-Roles": "admin"}
SECRET = "test-" * 4  # Testwert (>= 16 Zeichen), kein echtes Secret


def make_ca(d: Path) -> Path:
    """Agent-CA mit dem echten make-certs.sh (in einer Kopie, pki/ daneben)."""
    shutil.copy(REPO / "deploy" / "agent-gateway" / "make-certs.sh", d / "make-certs.sh")
    out = subprocess.run(["bash", str(d / "make-certs.sh"), "ca"],  # noqa: S603, S607
                         capture_output=True, text=True, check=False)  # fmt: skip
    assert out.returncode == 0, out.stdout + out.stderr
    return d / "pki"


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    """mTLS-Modus bind mit Gateway automato.example.ch:46100 und echter Agent-CA."""
    pki = make_ca(tmp_path)
    s = get_settings()
    for key, value in {"internal_mtls_mode": "bind", "agent_gateway_secret": SECRET,
                       "agent_gateway_host": "automato.example.ch", "agent_gateway_port": 46100,
                       "agent_ca_dir": str(pki)}.items():  # fmt: skip
        monkeypatch.setattr(s, key, value)
    return pki


def _agent(client, name: str) -> tuple[int, str]:
    r = client.post("/agents", json={"name": name}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"], r.json()["token"]


def _files(r) -> dict[str, bytes]:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["cache-control"] == "no-store"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert all("/" not in n for n in z.namelist())  # ohne Unterordner: direkt in den Agent-Ordner
    return {n: z.read(n) for n in z.namelist()}


def _env(data: bytes) -> dict[str, str]:
    """Wie run-agent.ps1: Kommentare/Leerzeilen weg, Schluessel=Wert am ersten '='."""
    text = data.decode("ascii")
    assert "\r\n" in text  # Windows-Zeilenenden
    out = {}
    for line in text.splitlines():
        if line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _heartbeat(client, token: str, subject: str | None = None) -> int:
    headers = {"X-Agent-Token": token}
    if subject is not None:  # wie das Gateway: Secret + Subject des geprüften Zertifikats
        headers |= {"X-Gateway-Secret": SECRET, "X-Client-Cert-Subject": subject}
    return client.post("/internal/agent/heartbeat", headers=headers).status_code


def test_zugang_ohne_gateway(client):
    """Agenten direkt auf :8000 (Labor): agent.env mit neuem Token, ohne Zertifikat."""
    aid, alt = _agent(client, "agent-labor")
    body = {"orchestrator_url": "http://10.0.12.12:8000/"}
    assert client.post(f"/agents/{aid}/access", json=body,
                       headers={"X-Dev-Roles": "operator"}).status_code == 403  # fmt: skip
    files = _files(client.post(f"/agents/{aid}/access", json=body, headers=ADMIN))
    assert set(files) == {"agent.env", "LIESMICH.txt"}
    env = _env(files["agent.env"])
    assert env["AUTOMATO_ORCHESTRATOR_URL"] == "http://10.0.12.12:8000"
    assert set(env) == {"AUTOMATO_ORCHESTRATOR_URL", "AUTOMATO_AGENT_TOKEN"}  # Sprache: Kommentar
    neu = env["AUTOMATO_AGENT_TOKEN"]
    assert neu != alt and _heartbeat(client, neu) == 204
    assert _heartbeat(client, alt) == 401  # das bisherige Token gilt nicht mehr
    bad = client.post(f"/agents/{aid}/access", json={"orchestrator_url": "10.0.12.12"},
                      headers=ADMIN)  # fmt: skip
    assert bad.status_code == 422 and bad.json()["detail"]["reason"] == "url"
    assert _heartbeat(client, neu) == 204  # abgelehnt = Token unverändert
    assert client.post("/agents/999/access", json=body, headers=ADMIN).status_code == 404
    en = _files(client.post(f"/agents/{aid}/access", json=body,
                            headers={**ADMIN, "Accept-Language": "en"}))  # fmt: skip
    assert _env(en["agent.env"])["AUTOMATO_LANG"] == "en" and "README.txt" in en


def test_zugang_mit_tls_intern_direkt(client, monkeypatch, tmp_path):
    """TLS intern ohne Gateway: https://…:8000 bekommt die interne CA mit."""
    (tmp_path / "ca.crt").write_text("-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n")
    s = get_settings()
    monkeypatch.setattr(s, "internal_tls", True)
    monkeypatch.setattr(s, "internal_tls_dir", str(tmp_path))
    monkeypatch.setattr(s, "agent_signing_pubkey", "PUBKEY-BASE64")
    aid, _ = _agent(client, "agent-tls")
    files = _files(client.post(f"/agents/{aid}/access", json={"orchestrator_url":
                               "https://automato.example.ch:8000"}, headers=ADMIN))  # fmt: skip
    env = _env(files["agent.env"])
    assert env["AUTOMATO_ORCHESTRATOR_CA_BUNDLE"] == "C:\\Automato\\collector\\automato-ca.crt"
    assert files["automato-ca.crt"].startswith(b"-----BEGIN CERTIFICATE-----")
    assert env["AUTOMATO_AGENT_SIGNING_PUBKEY"] == "PUBKEY-BASE64"  # signierte Updates


def test_zugang_mit_gateway_und_zertifikat(client, session_factory, gateway):
    aid, alt = _agent(client, "Agent Nord 1")
    info = client.get("/agents/package/info", headers=ADMIN).json()
    assert info["gateway_url"] == "https://automato.example.ch:46100" and info["agent_ca"] == "ok"
    # die Adresse aus dem Formular zählt mit Gateway nicht
    r = client.post(f"/agents/{aid}/access", json={"orchestrator_url": "http://x:8000"},
                    headers=ADMIN)  # fmt: skip
    files = _files(r)
    assert set(files) == {"agent.env", "agent.crt", "agent.key", "agents-ca.crt", "LIESMICH.txt"}
    assert 'filename="automato-zugang-Agent_Nord_1.zip"' in r.headers["content-disposition"]
    env = _env(files["agent.env"])
    assert env == {
        "AUTOMATO_ORCHESTRATOR_URL": "https://automato.example.ch:46100",
        "AUTOMATO_AGENT_TOKEN": env["AUTOMATO_AGENT_TOKEN"],
        "AUTOMATO_ORCHESTRATOR_CA_BUNDLE": "C:\\Automato\\collector\\agents-ca.crt",
        "AUTOMATO_CLIENT_CERT": "C:\\Automato\\collector\\agent.crt",
        "AUTOMATO_CLIENT_KEY": "C:\\Automato\\collector\\agent.key",
    }
    ca = x509.load_pem_x509_certificate((gateway / "agents-ca.crt").read_bytes())
    assert files["agents-ca.crt"] == (gateway / "agents-ca.crt").read_bytes()
    cert = x509.load_pem_x509_certificate(files["agent.crt"])
    cert.verify_directly_issued_by(ca)  # von der Agent-CA signiert
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "Agent Nord 1"
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [ExtendedKeyUsageOID.CLIENT_AUTH]
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert cert.not_valid_after_utc - cert.not_valid_before_utc <= timedelta(days=826)
    key = serialization.load_pem_private_key(files["agent.key"], password=None)
    pub = serialization.PublicFormat.SubjectPublicKeyInfo
    der = serialization.Encoding.DER
    assert key.public_key().public_bytes(der, pub) == cert.public_key().public_bytes(der, pub)
    # Gateway reicht das Subject so weiter wie Go (pkix.Name.String: umgekehrte Reihenfolge)
    token = env["AUTOMATO_AGENT_TOKEN"]
    assert _heartbeat(client, token, "O=Automato,CN=Agent Nord 1") == 204
    assert _heartbeat(client, token, "O=Automato,CN=anderer") == 403  # bind: CN = Agent
    assert _heartbeat(client, alt, "O=Automato,CN=Agent Nord 1") == 401
    assert _heartbeat(client, token) == 403  # nie direkt
    with session_factory() as s:
        entry = s.query(AuditLog).filter_by(action="agent.access_issued").one()
        assert entry.detail["certificate"] is True and entry.detail["serial"] == format(
            cert.serial_number, "x"
        )
        dump = json.dumps(entry.detail)
        assert token not in dump and "PRIVATE KEY" not in dump  # nichts Geheimes im Audit


def test_zugang_nicht_moeglich(client, monkeypatch, gateway):
    """Kein Zugang = Token bleibt; die Einrichtung sagt, woran es liegt."""
    aid, token = _agent(client, "Agent, Nord")  # Komma: im Subject maskiert
    r = client.post(f"/agents/{aid}/access", json={}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "name"
    assert _heartbeat(client, token) == 403  # Token gilt weiter (sonst 401), nur nie direkt

    ok, _ = _agent(client, "agent-sued")
    monkeypatch.setattr(get_settings(), "agent_gateway_host", "")
    r = client.post(f"/agents/{ok}/access", json={}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "gateway_host"

    monkeypatch.setattr(get_settings(), "agent_gateway_host", "automato.example.ch")
    monkeypatch.setattr(get_settings(), "agent_ca_dir", str(gateway.parent / "fehlt"))
    r = client.post(f"/agents/{ok}/access", json={}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "ca_missing"
    assert client.get("/agents/package/info", headers=ADMIN).json()["agent_ca"] == "ca_missing"


def test_zustand_der_agent_ca(monkeypatch, gateway):
    s = get_settings()
    assert agent_access.ca_state(s) == "ok"
    monkeypatch.setattr(s, "internal_mtls_mode", "off")
    assert agent_access.ca_state(s) is None  # ohne Gateway kein Zertifikat
    monkeypatch.setattr(s, "internal_mtls_mode", "require")
    echt = Path.read_bytes

    def gesperrt(self):
        if self.name == agent_access.CA_KEY:
            raise PermissionError(13, "Permission denied")
        return echt(self)

    monkeypatch.setattr(Path, "read_bytes", gesperrt)
    assert agent_access.ca_state(s) == "ca_denied"  # make-certs.sh oberflaeche fehlt
    monkeypatch.setattr(Path, "read_bytes", echt)
    fremd = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())  # fmt: skip
    (gateway / "agents-ca.key").chmod(0o600)
    (gateway / "agents-ca.key").write_bytes(fremd)
    assert agent_access.ca_state(s) == "ca_invalid"  # Schlüssel passt nicht zum Zertifikat


def test_zertifikat_endet_spaetestens_mit_der_ca(gateway):
    ca = agent_access.load_ca(get_settings())
    spaet = ca.cert.not_valid_after_utc - timedelta(days=10)
    issued = agent_access.issue(ca, "agent-a", now=spaet)
    assert issued.not_after == ca.cert.not_valid_after_utc
    with pytest.raises(agent_access.AccessError):
        agent_access.issue(ca, " agent-a")
    assert agent_access.issue(ca, "Agent (Nord) 1.2_x-y", now=datetime.now(UTC)).serial > 0


def test_make_certs_gibt_die_ca_frei_und_sperrt_sie(tmp_path):
    """make-certs.sh ca gibt frei (Orchestrator uid 1001), `oberflaeche aus` sperrt wieder;
    der Schlüssel des Gateways bleibt immer gesperrt."""
    pki = make_ca(tmp_path)
    script = tmp_path / "make-certs.sh"

    def run(*args: str) -> str:
        out = subprocess.run(["bash", str(script), *args], capture_output=True, text=True,  # noqa: S603, S607
                             check=False)  # fmt: skip
        assert out.returncode == 0, out.stdout + out.stderr
        return out.stdout

    mode = lambda p: p.stat().st_mode & 0o777  # noqa: E731
    assert mode(pki) == 0o711 and mode(pki / "agents-ca.key") == 0o400
    assert mode(pki / "agents-ca.crt") == 0o644
    if os.geteuid() == 0:
        assert (pki / "agents-ca.key").stat().st_uid == 1001
    run("server", "automato.example.ch")
    assert mode(pki / "gateway.key") == 0o600 and mode(pki) == 0o711  # bleibt freigegeben
    assert "gesperrt" in run("oberflaeche", "aus")
    assert mode(pki) == 0o700 and mode(pki / "agents-ca.key") == 0o600
    if os.geteuid() == 0:
        assert (pki / "agents-ca.key").stat().st_uid == 0
    assert "freigegeben" in run("oberflaeche")
    assert mode(pki) == 0o711
    # der Orchestrator liest die mit openssl erzeugte CA
    agent_access.load_ca(get_settings().model_copy(update={"agent_ca_dir": str(pki)}))
