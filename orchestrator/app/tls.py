"""TLS intern (0.49.0, ADR 0023): eigene CA fuer die Verbindungen zwischen den Containern.

Mit `AUTOMATO_INTERNAL_TLS=on` sprechen die Dienste im Compose-Netz verschluesselt und pruefen
die Gegenstelle gegen die interne CA (`<internal_tls_dir>/ca.crt`):

- Orchestrator/Migration -> Postgres mit `sslmode=verify-full`; Postgres nimmt ueber das Netz
  nur noch TLS an (`hostssl`, deploy/tls/postgres-start.sh)
- Orchestrator/Collector -> Redis ueber `rediss://` (deploy/tls/redis-start.sh)
- der Orchestrator selbst per HTTPS (`app.serve`); Collector, Web-UI, Proxy und Agent-Gateway
  pruefen sein Zertifikat, der Proxy ebenso das der Web-UI

`issue` erzeugt CA und Server-Zertifikate (`automato tls-init`; `deploy/aufbau.sh tls-intern`
ruft es als root im Dienst migrate auf). Ablage je Dienst in einem eigenen Ordner, damit jeder
Container nur seinen Schluessel sieht; der CA-Schluessel liegt getrennt in `ca/` und wird in
keinen Container gelegt. Vorhandenes bleibt, solange es noch `RENEW_DAYS` gilt und die Namen
passen.
"""

import ipaddress
import os
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_DAYS = 3650
SERVER_DAYS = 825
RENEW_DAYS = 60  # vorher neu ausstellen (aufbau.sh pruefen warnt ab 30 Tagen)
# Dienste mit eigenem Server-Zertifikat: Namen im Compose-Netz; dazu localhost/127.0.0.1 fuer
# Healthchecks und Aufrufe auf der VM
SERVICES = ("postgres", "redis", "orchestrator", "web-ui")
LOCAL = ("localhost", "127.0.0.1")
# Dienstbenutzer im Image, der den Schluessel direkt liest (Postgres/Redis kopieren ihn beim
# Start als root, Web-UI und Caddy laufen als root)
OWNER = {"orchestrator": 1001}
CLIENT = "client"  # nur die CA: Migration, Collector, Proxy, Agent-Gateway, Agenten


def ca_file(settings) -> str:
    return str(Path(settings.internal_tls_dir) / "ca.crt")


def db_connect_args(settings) -> dict:
    """libpq: TLS mit Pruefung von CA und Hostname (nur mit TLS intern)."""
    if not settings.internal_tls:
        return {}
    return {"sslmode": "verify-full", "sslrootcert": ca_file(settings)}


def redis_url(settings) -> str:
    """`redis://` wird mit TLS intern zu `rediss://`."""
    url = settings.redis_url
    if settings.internal_tls and url.startswith("redis://"):
        return "rediss://" + url.removeprefix("redis://")
    return url


def redis_ssl(settings) -> dict | None:
    """TLS-Parameter fuer Celery (`broker_use_ssl`) bzw. redis-py."""
    if not redis_url(settings).startswith("rediss://"):
        return None
    return {"ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_ca_certs": ca_file(settings)}


def server_files(settings) -> tuple[str, str]:
    base = Path(settings.internal_tls_dir)
    return str(base / "server.crt"), str(base / "server.key")


def https_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(scheme="https")) if parts.scheme == "http" else url


def client_context(settings) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_file(settings))


def db_tls(session) -> tuple[bool, str | None] | None:
    """Ist die eigene Datenbank-Verbindung verschluesselt? None ausserhalb von PostgreSQL."""
    from sqlalchemy import text

    if session.get_bind().dialect.name != "postgresql":
        return None
    row = session.execute(
        text("SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
    ).first()
    return (bool(row[0]), row[1]) if row else (False, None)


# -- Zertifikate ------------------------------------------------------------------------


@dataclass
class CertInfo:
    subject: str
    not_after: datetime
    names: list[str]

    def days_left(self, now: datetime | None = None) -> int:
        return (self.not_after - (now or datetime.now(UTC))).days


def cert_info(path: str | Path) -> CertInfo | None:
    try:
        cert = x509.load_pem_x509_certificate(Path(path).read_bytes())
    except (OSError, ValueError):
        return None
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(n.value) for n in san]
    except x509.ExtensionNotFound:
        names = []
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return CertInfo(str(cn[0].value) if cn else "", cert.not_valid_after_utc, names)


def _key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _write_key(path: Path, key, uid: int | None = None) -> None:
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    if uid is not None and os.geteuid() == 0:
        os.chown(tmp, uid, uid)
    os.replace(tmp, path)


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    path.chmod(0o644)


def _name(cn: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Automato"),
        ]
    )


def _san(names: list[str]) -> x509.SubjectAlternativeName:
    entries: list[x509.GeneralName] = []
    for n in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            entries.append(x509.DNSName(n))
    return x509.SubjectAlternativeName(entries)


def _ca(base: Path, now: datetime) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate, bool]:
    key_path, crt_path = base / "ca" / "ca.key", base / "ca" / "ca.crt"
    if key_path.is_file() and crt_path.is_file():
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        return key, x509.load_pem_x509_certificate(crt_path.read_bytes()), False
    key = _key()
    name = _name("Automato Internal CA")
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .sign(key, hashes.SHA256())
    )
    _write_key(key_path, key)
    _write_cert(crt_path, cert)
    return key, cert, True


def _server(ca_key, ca_cert: x509.Certificate, service: str, names: list[str], now: datetime):
    key = _key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(service))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=SERVER_DAYS))
        .add_extension(_san(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, False, False),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def names_for(service: str, extra: list[str]) -> list[str]:
    """Namen im Zertifikat: Dienstname, localhost/127.0.0.1, beim Orchestrator und der Web-UI
    zusaetzlich die Adressen der VM (Agenten bzw. Browser direkt auf :8000/:5173)."""
    names = [service, *LOCAL]
    if service in ("orchestrator", "web-ui"):
        names += extra
    return list(dict.fromkeys(n for n in names if n))


def issue(
    directory: str | Path,
    extra_names: list[str] | None = None,
    *,
    renew: bool = False,
    now: datetime | None = None,
) -> list[str]:
    """CA und Server-Zertifikate anlegen bzw. erneuern; Rueckgabe = erledigte Schritte."""
    base = Path(directory)
    now = now or datetime.now(UTC)
    ca_key, ca_cert, created = _ca(base, now)
    steps = [("CA angelegt" if created else "CA vorhanden") + f" (gültig bis {_day(ca_cert)})"]
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    for folder in (*SERVICES, CLIENT):
        (base / folder).mkdir(parents=True, exist_ok=True)
        (base / folder / "ca.crt").write_bytes(ca_pem)
        (base / folder / "ca.crt").chmod(0o644)
    for service in SERVICES:
        names = names_for(service, extra_names or [])
        crt_path, key_path = base / service / "server.crt", base / service / "server.key"
        info = cert_info(crt_path)
        fresh = (
            info is not None
            and key_path.is_file()
            and info.days_left(now) > RENEW_DAYS
            and set(names) <= set(info.names)
            and _signed_by(crt_path, ca_cert)
        )
        if fresh and not renew:
            steps.append(f"{service}: vorhanden (gültig bis {info.not_after:%d.%m.%Y})")
            continue
        key, cert = _server(ca_key, ca_cert, service, names, now)
        _write_key(key_path, key, OWNER.get(service))
        _write_cert(crt_path, cert)
        steps.append(f"{service}: ausgestellt für {', '.join(names)} (gültig bis {_day(cert)})")
    return steps


def _day(cert: x509.Certificate) -> str:
    return f"{cert.not_valid_after_utc:%d.%m.%Y}"


def _signed_by(crt_path: Path, ca_cert: x509.Certificate) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(crt_path.read_bytes())
        cert.verify_directly_issued_by(ca_cert)
    except (OSError, ValueError, TypeError, InvalidSignature):
        return False
    return True
