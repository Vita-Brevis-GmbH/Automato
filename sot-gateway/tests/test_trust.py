"""Zusaetzliche CAs fuer Orchestrator und Worker (1.10.0): ein Server mit Zertifikat einer eigenen
CA ist ohne die Datei in deploy/trust nicht vertraut, mit ihr schon - PEM oder DER, die
bisherigen CAs bleiben, eine kaputte Datei wird uebergangen."""

import ipaddress
import ssl
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import certifi
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sot_gateway import trust


def _ca_und_server(ordner: Path) -> tuple[x509.Certificate, Path, Path]:
    jetzt = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Interne CA Test")])
    ca = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(jetzt - timedelta(minutes=1)).not_valid_after(jetzt + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        # Python ab 3.13 prüft streng (VERIFY_X509_STRICT): Schlüsselverwendung und Kennungen
        .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                     key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=True, crl_sign=True,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )  # fmt: skip
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(jetzt - timedelta(minutes=1)).not_valid_after(jetzt + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )  # fmt: skip
    crt, schluessel = ordner / "server.pem", ordner / "server-key.pem"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    schluessel.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca, crt, schluessel


class _Ok(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


@pytest.fixture
def server(tmp_path) -> Iterator[tuple[str, x509.Certificate]]:
    ca, crt, schluessel = _ca_und_server(tmp_path)
    srv = HTTPServer(("127.0.0.1", 0), _Ok)
    kontext = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    kontext.load_cert_chain(crt, schluessel)
    srv.socket = kontext.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        # die IP, nicht localhost: das löst in der CI zuerst auf ::1 auf (Verbindung abgelehnt)
        yield f"https://127.0.0.1:{srv.server_address[1]}/", ca
    finally:
        srv.shutdown()


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch, tmp_path):
    # gesetzt und wieder entfernt: so stellt monkeypatch den Zustand nach dem Test wieder her
    monkeypatch.setenv("SSL_CERT_FILE", "")
    monkeypatch.delenv("SSL_CERT_FILE")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr("tempfile.tempdir", None)


def _abruf(url: str) -> str:
    """Status, `tls` nur bei einem nicht vertrauten Zertifikat - eine abgelehnte Verbindung ist
    etwas anderes und soll den Test nicht still bestehen lassen."""
    try:
        return str(httpx.get(url, timeout=5).status_code)
    except httpx.ConnectError as exc:
        if isinstance(exc.__cause__, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return "tls"
        return f"verbindung: {exc}"


@pytest.mark.parametrize("form", ["pem", "der"])
def test_ca_aus_dem_ordner_wird_vertraut(server, tmp_path, form):
    url, ca = server
    assert _abruf(url) == "tls"  # ohne die interne CA
    ordner = tmp_path / "trust"
    ordner.mkdir()
    if form == "pem":
        (ordner / "interne-ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    else:
        (ordner / "interne-ca.cer").write_bytes(ca.public_bytes(serialization.Encoding.DER))
    (ordner / "kaputt.pem").write_text(
        "-----BEGIN CERTIFICATE-----\nnein\n-----END CERTIFICATE-----\n"
    )
    (ordner / "notiz.txt").write_text("wird nicht gelesen")

    buendel = trust.zusaetzliche_cas(str(ordner))
    assert buendel is not None and Path(buendel).name == trust.BUENDEL
    assert _abruf(url) == "200"
    # die bisherigen CAs bleiben (certifi), die eigene kommt dazu - auch zweimal nur einmal
    inhalt = Path(buendel).read_text()
    assert Path(certifi.where()).read_text().strip()[:200] in inhalt
    trust.zusaetzliche_cas(str(ordner))
    assert Path(buendel).read_text().count("BEGIN CERTIFICATE") == inhalt.count("BEGIN CERTIFICATE")


def test_ohne_dateien_aendert_sich_nichts(tmp_path, monkeypatch):
    assert trust.zusaetzliche_cas(str(tmp_path / "gibt-es-nicht")) is None
    leer = tmp_path / "leer"
    leer.mkdir()
    assert trust.zusaetzliche_cas(str(leer)) is None
    import os

    assert "SSL_CERT_FILE" not in os.environ
