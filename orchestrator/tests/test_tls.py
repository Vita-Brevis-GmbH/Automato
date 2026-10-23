"""TLS intern (0.49.0, ADR 0023): eigene CA und Zertifikate je Dienst, Einstellungen fuer
Datenbank, Redis und Orchestrator - und echte TLS-Verbindungen (HTTPS, Redis-PING) gegen die
ausgestellten Zertifikate, mit einer fremden CA als Gegenprobe."""

import json
import os
import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from app import serve, tls
from app.store import connections
from cryptography import x509

NAMES = ["10.1.2.3", "vm-a.example.ch"]


def _settings(base, service="orchestrator", on=True):
    return SimpleNamespace(internal_tls=on, internal_tls_dir=str(base / service),
                           redis_url="redis://redis:6379/0")  # fmt: skip


def _cert(path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_ca_und_zertifikate_je_dienst(tmp_path):
    steps = tls.issue(tmp_path, NAMES)
    assert steps[0].startswith("CA angelegt") and len(steps) == 1 + len(tls.SERVICES)
    ca = _cert(tmp_path / "ca" / "ca.crt")
    assert oct((tmp_path / "ca" / "ca.key").stat().st_mode & 0o777) == "0o400"
    for service in tls.SERVICES:
        folder = tmp_path / service
        _cert(folder / "server.crt").verify_directly_issued_by(ca)
        assert (folder / "ca.crt").read_bytes() == (tmp_path / "ca" / "ca.crt").read_bytes()
        assert oct((folder / "server.key").stat().st_mode & 0o777) == "0o400"
        names = tls.cert_info(folder / "server.crt").names
        assert {service, "localhost", "127.0.0.1"} <= set(names)
        # Adressen der VM nur dort, wo man direkt hinkommt (Agenten :8000, Browser :5173)
        assert ("10.1.2.3" in names) == (service in ("orchestrator", "web-ui"))
    if os.geteuid() == 0:  # als root (Dienst migrate): Schluessel gehoert dem Dienstbenutzer
        assert (tmp_path / "orchestrator" / "server.key").stat().st_uid == 1001
    # Clients (Migration, Collector, Proxy, Gateway, Agenten) bekommen nur die CA
    assert [p.name for p in (tmp_path / "client").iterdir()] == ["ca.crt"]

    # zweiter Aufruf: alles bleibt
    before = (tmp_path / "orchestrator" / "server.crt").read_bytes()
    again = tls.issue(tmp_path, NAMES)
    assert again[0].startswith("CA vorhanden") and all("vorhanden" in s for s in again)
    assert (tmp_path / "orchestrator" / "server.crt").read_bytes() == before
    # neuer Name -> nur Orchestrator und Web-UI neu
    more = tls.issue(tmp_path, [*NAMES, "10.1.2.4"])
    assert [s.split(":")[0] for s in more if "ausgestellt" in s] == ["orchestrator", "web-ui"]
    # erneuern bzw. kurz vor Ablauf: neu ausgestellt, dieselbe CA
    assert sum("ausgestellt" in s for s in tls.issue(tmp_path, NAMES, renew=True)) == 4
    later = datetime.now(UTC) + timedelta(days=tls.SERVER_DAYS - tls.RENEW_DAYS + 1)
    assert sum("ausgestellt" in s for s in tls.issue(tmp_path, NAMES, now=later)) == 4
    _cert(tmp_path / "redis" / "server.crt").verify_directly_issued_by(ca)


def test_einstellungen(tmp_path):
    on, off = _settings(tmp_path), _settings(tmp_path, on=False)
    ca = str(tmp_path / "orchestrator" / "ca.crt")
    assert tls.db_connect_args(on) == {"sslmode": "verify-full", "sslrootcert": ca}
    assert tls.db_connect_args(off) == {}
    assert tls.redis_url(on) == "rediss://redis:6379/0" and tls.redis_url(off).startswith("redis:")
    assert tls.redis_ssl(on) == {"ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_ca_certs": ca}
    assert tls.redis_ssl(off) is None
    assert serve.options(off) == {}
    own = tmp_path / "orchestrator"
    assert serve.options(on) == {"ssl_certfile": str(own / "server.crt"),
                                 "ssl_keyfile": str(own / "server.key")}  # fmt: skip
    assert tls.https_url("http://orchestrator:8000") == "https://orchestrator:8000"


def _server_context(base, service):
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(base / service / "server.crt", base / service / "server.key")
    return ctx


@pytest.fixture
def pki(tmp_path):
    tls.issue(tmp_path / "pki", NAMES)
    tls.issue(tmp_path / "fremd", NAMES)  # andere CA: muss scheitern
    return tmp_path


def test_https_healthcheck_gegen_die_interne_ca(pki):
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    server.socket = _server_context(pki / "pki", "orchestrator").wrap_socket(
        server.socket, server_side=True
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"https://127.0.0.1:{server.server_address[1]}/health"
    try:
        assert serve.check(_settings(pki / "pki"), url) is True
        assert serve.check(_settings(pki / "fremd"), url) is False  # fremde CA
        assert serve.check(_settings(pki / "pki"), url.replace("https", "http")) is False
    finally:
        server.shutdown()


def test_redis_ping_ueber_tls(pki):
    listener = socket.create_server(("127.0.0.1", 0))
    ctx = _server_context(pki / "pki", "redis")

    def answer():
        for _ in range(2):
            conn, _addr = listener.accept()
            try:
                with ctx.wrap_socket(conn, server_side=True) as tls_conn:
                    tls_conn.recv(64)
                    tls_conn.sendall(b"+PONG\r\n")
            except (ssl.SSLError, OSError):
                conn.close()

    threading.Thread(target=answer, daemon=True).start()
    url = f"rediss://localhost:{listener.getsockname()[1]}/0"
    good = connections._redis(url, str(pki / "pki" / "client" / "ca.crt"))
    assert good.status == "ok"
    assert [s["step"] for s in good.steps] == ["tcp", "tls", "ping"]
    assert good.steps[1]["detail"].startswith("TLSv1")
    bad = connections._redis(url, str(pki / "fremd" / "client" / "ca.crt"))
    assert bad.status == "error" and bad.steps[-1]["step"] == "tls"
    assert "deploy/aufbau.sh tls-intern" in bad.hint
    listener.close()


def test_cli_tls_init(tmp_path, capsys):
    from app import cli

    args = ["tls-init", "--dir", str(tmp_path), "--name", "10.1.2.3"]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "OK     CA angelegt" in out and "orchestrator: ausgestellt" in out
    assert "10.1.2.3" in tls.cert_info(tmp_path / "orchestrator" / "server.crt").names
