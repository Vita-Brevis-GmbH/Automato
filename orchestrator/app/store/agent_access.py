"""Agent-Zugang aus der Oberflaeche (1.6.0, ADR 0052): ein ZIP mit allem, was die Agent-Station
braucht - `agent.env` mit neuem Agent-Token und, mit dem mTLS-Agent-Gateway, das Client-
Zertifikat des Agenten, sein Schluessel und die CA der Agenten.

- Die Agent-CA (`deploy/agent-gateway/pki`, `make-certs.sh ca`) liegt nur lesend im Container
  (`agent_ca_dir`). Lesen darf der Orchestrator ihren Schluessel erst nach
  `make-certs.sh oberflaeche` (Besitzer uid 1001); sonst ist sie `denied`.
- Zertifikat wie `make-certs.sh agent`: EC P-256, CN = Agent-Name, O = Automato, nur
  clientAuth, 825 Tage (hoechstens bis zum Ablauf der CA). Der Schluessel entsteht je Download
  neu, wird nie gespeichert und verlaesst den Orchestrator nur im ZIP.
- Das Token wird dabei erneuert (das bisherige gilt nicht mehr): Zertifikat und Token gehoeren
  so immer zusammen, und `agent.env` ist vollstaendig.
"""

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.config import Settings
from app.i18n import L

CA_CERT = "agents-ca.crt"
CA_KEY = "agents-ca.key"
CERT_DAYS = 825  # wie make-certs.sh
INSTALL_DIR = "C:\\Automato\\collector"  # Vorgabe von install.ps1 (-InstallDir)
CLIENT_CERT = "agent.crt"
CLIENT_KEY = "agent.key"
INTERNAL_CA = "automato-ca.crt"  # interne CA (TLS intern), wie im Agent-Paket
# Der Orchestrator liest den CN aus dem Subject, das das Gateway weiterreicht (app.auth.mtls):
# nur Zeichen, die dort nie maskiert werden und als Kopfzeile ASCII bleiben
CERT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()-]*$")
_URL = re.compile(r"^https?://[^\s/]+/?$")


class AccessError(Exception):
    """Zugang nicht moeglich - `reason` als Schluessel fuer die Oberflaeche."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True)
class Ca:
    cert: x509.Certificate
    key: object
    pem: bytes


def gateway_url(settings: Settings) -> str:
    """Adresse des mTLS-Agent-Gateways (1.5.1) - leer, wenn Agenten direkt auf :8000 gehen."""
    host = settings.agent_gateway_host.strip()
    if settings.internal_mtls_mode == "off" or not host or host == "localhost":
        return ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6
    port = settings.agent_gateway_port
    return f"https://{host}" if port == 443 else f"https://{host}:{port}"


def load_ca(settings: Settings) -> Ca:
    """Agent-CA aus `agent_ca_dir`; AccessError `ca_missing` | `ca_denied` | `ca_invalid`."""
    base = Path(settings.agent_ca_dir)
    try:
        pem = (base / CA_CERT).read_bytes()
        raw = (base / CA_KEY).read_bytes()
    except FileNotFoundError as exc:
        raise AccessError("ca_missing", "agent CA not found (make-certs.sh ca)") from exc
    except PermissionError as exc:
        raise AccessError(
            "ca_denied", "agent CA key not readable (make-certs.sh oberflaeche)"
        ) from exc
    try:
        cert = x509.load_pem_x509_certificate(pem)
        key = serialization.load_pem_private_key(raw, password=None)
    except (ValueError, TypeError) as exc:
        raise AccessError("ca_invalid", "agent CA cannot be read") from exc
    same = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) == key.public_key().public_bytes(  # type: ignore[attr-defined]
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    if not same:
        raise AccessError("ca_invalid", "agent CA key does not match the certificate")
    if cert.not_valid_after_utc <= datetime.now(UTC):
        raise AccessError("ca_invalid", "agent CA has expired")
    return Ca(cert=cert, key=key, pem=pem)


def ca_state(settings: Settings) -> str | None:
    """Fuer die Einrichtung: None (ohne Gateway) | ok | ca_missing | ca_denied | ca_invalid."""
    if settings.internal_mtls_mode == "off":
        return None
    try:
        load_ca(settings)
    except AccessError as exc:
        return exc.reason
    return "ok"


@dataclass(frozen=True)
class Issued:
    cert_pem: bytes
    key_pem: bytes
    serial: int
    not_after: datetime


def issue(ca: Ca, name: str, now: datetime | None = None) -> Issued:
    """Client-Zertifikat fuer den Agenten `name` (CN), signiert von der Agent-CA."""
    if not CERT_NAME.match(name) or name != name.strip():
        raise AccessError("name", "agent name not usable in a certificate")
    now = now or datetime.now(UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    not_after = min(now + timedelta(days=CERT_DAYS), ca.cert.not_valid_after_utc)
    signer = (
        None
        if isinstance(ca.key, ed25519.Ed25519PrivateKey | ed448.Ed448PrivateKey)
        else (hashes.SHA256())
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, name),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Automato"),
                ]
            )
        )  # fmt: skip
        .issuer_name(ca.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))  # Uhren der Stationen gehen nicht genau
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),  # fmt: skip
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.cert.public_key()),  # type: ignore[arg-type]
            critical=False,
        )
        .sign(ca.key, signer)  # type: ignore[arg-type]
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return Issued(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        key_pem=key_pem,
        serial=cert.serial_number,
        not_after=not_after,
    )


def orchestrator_url(settings: Settings, given: str) -> str:
    """Adresse fuer agent.env: mit Gateway immer dessen Adresse, sonst die angegebene."""
    if url := gateway_url(settings):
        return url
    given = given.strip()
    if not _URL.match(given):
        raise AccessError("url", "orchestrator address must be http(s)://host[:port]")
    return given.rstrip("/")


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode()


def agent_env(name: str, url: str, token: str, files: dict[str, str], pubkey: str) -> str:
    """agent.env (CRLF, ASCII) - `files` = Schluessel -> Dateiname im Installationsordner."""
    lines = [
        L(f"# Automato Collector-Agent \"{_ascii(name)}\" - Zugang aus dem Web-UI "
          "(Collector-Agenten), " + datetime.now(UTC).strftime("%Y-%m-%d"),
          f"# Automato collector agent \"{_ascii(name)}\" - access from the web UI "
          "(Collector agents), " + datetime.now(UTC).strftime("%Y-%m-%d")),  # fmt: skip
        L("# Enthaelt das Agent-Token: nur auf dieser Agent-Station ablegen, nicht weitergeben.",
          "# Contains the agent token: store only on this agent station, do not pass on."),
        L("# Geraete + Zugangsdaten kommen vom Orchestrator (Web-UI: Mandanten -> Quellsysteme).",
          "# Devices + credentials come from the orchestrator (web UI: Tenants -> Source "
          "systems)."),
        "",
        f"AUTOMATO_ORCHESTRATOR_URL={url}",
        f"AUTOMATO_AGENT_TOKEN={token}",
    ]  # fmt: skip
    if files:
        lines += ["", L(f"# Dateien aus demselben ZIP, abgelegt in {INSTALL_DIR}",
                        f"# Files from the same ZIP, stored in {INSTALL_DIR}")]  # fmt: skip
        lines += [f"{k}={INSTALL_DIR}\\{v}" for k, v in files.items()]
    if pubkey:
        lines += [
            "",
            L(
                "# Nur signierte Updates installieren (ADR 0013)",
                "# Install signed updates only (ADR 0013)",
            ),  # fmt: skip
            f"AUTOMATO_AGENT_SIGNING_PUBKEY={pubkey}",
        ]
    lines += ["", L("# Sprache der Agent-Konsole (de | en); Meldungen an den Orchestrator bleiben "
                    "deutsch", "# Language of the agent console (de | en); messages to the "
                    "orchestrator stay German"),
              L("# AUTOMATO_LANG=en", "AUTOMATO_LANG=en"), ""]  # fmt: skip
    return "\r\n".join(lines)


def readme(name: str, gateway: bool) -> str:
    text = L(
        f'Zugang fuer den Automato-Agenten "{_ascii(name)}"\r\n\r\n'
        "Bestehender Agent: alle Dateien nach {dir} kopieren (agent.env wird ersetzt), dann\r\n"
        "  powershell -ExecutionPolicy Bypass -File {dir}\\run-agent.ps1 -Check\r\n\r\n"
        "Neue Installation: alle Dateien in den entpackten Ordner automato-collector-agent\r\n"
        "(Agent-Paket aus dem Web-UI) legen und dort install.ps1 ausfuehren.\r\n\r\n"
        "Das bisherige Token dieses Agenten gilt nicht mehr.\r\n",
        f'Access for the Automato agent "{_ascii(name)}"\r\n\r\n'
        "Existing agent: copy all files to {dir} (agent.env is replaced), then\r\n"
        "  powershell -ExecutionPolicy Bypass -File {dir}\\run-agent.ps1 -Check\r\n\r\n"
        "New installation: put all files into the unpacked folder automato-collector-agent\r\n"
        "(agent package from the web UI) and run install.ps1 there.\r\n\r\n"
        "The previous token of this agent is no longer valid.\r\n",
    ).replace("{dir}", INSTALL_DIR)
    if gateway:
        text += L(
            "\r\nagent.key ist der private Schluessel des Agenten - nur auf diese Station.\r\n",
            "\r\nagent.key is the private key of the agent - only onto this station.\r\n",
        )
    return text


def bundle(name: str, url: str, token: str, settings: Settings, ca: Ca | None,
           issued: Issued | None, internal_ca: bytes | None) -> bytes:  # fmt: skip
    """ZIP ohne Unterordner: agent.env (+ Zertifikat, Schluessel, CA bzw. interne CA)."""
    files: dict[str, str] = {}
    extra: dict[str, bytes] = {}
    if ca is not None and issued is not None:
        files = {"AUTOMATO_ORCHESTRATOR_CA_BUNDLE": CA_CERT, "AUTOMATO_CLIENT_CERT": CLIENT_CERT,
                 "AUTOMATO_CLIENT_KEY": CLIENT_KEY}  # fmt: skip
        extra = {CA_CERT: ca.pem, CLIENT_CERT: issued.cert_pem, CLIENT_KEY: issued.key_pem}
    elif internal_ca is not None and url.startswith("https://") and url.endswith(":8000"):
        files = {"AUTOMATO_ORCHESTRATOR_CA_BUNDLE": INTERNAL_CA}  # TLS intern, direkt auf :8000
        extra = {INTERNAL_CA: internal_ca}
    env = agent_env(name, url, token, files, settings.agent_signing_pubkey.strip())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("agent.env", env.encode("ascii", "replace"))
        for fname, data in extra.items():
            zf.writestr(fname, data)
        zf.writestr(L("LIESMICH.txt", "README.txt"), readme(name, ca is not None).encode())
    return buf.getvalue()
