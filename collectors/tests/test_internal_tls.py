"""TLS intern (0.49.0, ADR 0023) im zentralen Worker: Orchestrator per HTTPS, Redis per
rediss://, beide gegen die interne CA - Agenten bleiben bei ihrer eigenen Einstellung."""

import ssl

from collectors.config import Settings


def test_zentraler_worker_mit_tls_intern(monkeypatch):
    monkeypatch.delenv("AUTOMATO_ORCHESTRATOR_CA_BUNDLE", raising=False)
    s = Settings(_env_file=None, internal_tls=True, internal_tls_dir="/etc/automato/tls",
                 orchestrator_url="http://orchestrator:8000",
                 redis_url="redis://redis:6379/0")  # fmt: skip
    assert s.orchestrator_url == "https://orchestrator:8000"
    assert s.redis_url == "rediss://redis:6379/0"
    assert s.orchestrator_ca_bundle == "/etc/automato/tls/ca.crt"
    assert s.broker_ssl() == {"ssl_cert_reqs": ssl.CERT_REQUIRED,
                              "ssl_ca_certs": "/etc/automato/tls/ca.crt"}  # fmt: skip


def test_ohne_tls_intern_unveraendert():
    s = Settings(_env_file=None, orchestrator_url="http://orchestrator:8000",
                 redis_url="redis://redis:6379/0")  # fmt: skip
    assert (s.orchestrator_url, s.redis_url, s.orchestrator_ca_bundle) == (
        "http://orchestrator:8000",
        "redis://redis:6379/0",
        "",
    )
    assert s.broker_ssl() is None
    # Agent mit eigener CA (Agent-Gateway): bleibt, auch wenn TLS intern gesetzt waere
    agent = Settings(_env_file=None, internal_tls=True, orchestrator_url="https://gw:8443",
                     orchestrator_ca_bundle="C:/agents-ca.crt")  # fmt: skip
    assert (agent.orchestrator_url, agent.orchestrator_ca_bundle) == (
        "https://gw:8443",
        "C:/agents-ca.crt",
    )
