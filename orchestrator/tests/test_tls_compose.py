"""TLS intern gegen die echten Container (0.49.0, ADR 0023) - CI-Job `postgres`: Postgres und
Redis aus docker-compose.yml mit AUTOMATO_INTERNAL_TLS=on und den Zertifikaten aus
`automato tls-init`. Geprueft wird, was im Betrieb gilt: verschluesselt mit Pruefung von CA und
Name ja, Klartext und fremde CA nein.

Lokal: `automato tls-init --dir deploy/tls/pki`, dann
`AUTOMATO_INTERNAL_TLS=on docker compose -p tlsci --profile core up -d --wait postgres redis` und
`AUTOMATO_TEST_TLS_COMPOSE=tlsci pytest orchestrator/tests/test_tls_compose.py`.
"""

import os
import socket
import ssl
import subprocess
from pathlib import Path

import psycopg
import pytest

PROJECT = os.environ.get("AUTOMATO_TEST_TLS_COMPOSE")
PKI = Path(__file__).resolve().parents[2] / "deploy" / "tls" / "pki"
CA = PKI / "client" / "ca.crt"
PASSWORD = os.environ.get("POSTGRES_PASSWORD", "automato")

pytestmark = pytest.mark.skipif(not PROJECT, reason="nur mit AUTOMATO_TEST_TLS_COMPOSE (CI)")


def _ip(service: str) -> str:
    fmt = "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"
    out = subprocess.run(["docker", "inspect", "-f", fmt, f"{PROJECT}-{service}-1"],  # noqa: S603, S607
                         capture_output=True, text=True, check=True)  # fmt: skip
    return out.stdout.strip()


def _pg(sslmode: str, root: Path = CA):
    return psycopg.connect(host="postgres", hostaddr=_ip("postgres"), dbname="automato",
                           user="automato", password=PASSWORD, sslmode=sslmode,
                           sslrootcert=str(root), connect_timeout=5)  # fmt: skip


def test_postgres_nur_mit_tls(tmp_path):
    with _pg("verify-full") as conn:
        ssl_on, version = conn.execute(
            "SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
        ).fetchone()
    assert ssl_on and version in ("TLSv1.2", "TLSv1.3")
    with pytest.raises(psycopg.OperationalError, match="no encryption"):
        _pg("disable")
    from app import tls

    tls.issue(tmp_path, [])  # andere CA
    with pytest.raises(psycopg.OperationalError, match="certificate verify failed"):
        _pg("verify-full", tmp_path / "client" / "ca.crt")


def _redis(context: ssl.SSLContext | None) -> bytes:
    with socket.create_connection((_ip("redis"), 6379), timeout=5) as raw:
        sock = context.wrap_socket(raw, server_hostname="redis") if context else raw
        sock.sendall(b"PING\r\n")
        return sock.recv(64)


def test_redis_nur_mit_tls():
    assert _redis(ssl.create_default_context(cafile=str(CA))).startswith(b"+PONG")
    try:
        answer = _redis(None)
    except OSError:
        answer = b""
    assert not answer.startswith(b"+PONG")  # Klartext: Redis spricht nur TLS
