"""Rücklink zum Kunden-Portal (1.17.0, Anwender: „Ein Shortcut wäre schön, wenn ich einfach wieder
zu dem Kundenportal gelangen könnte.“): unter `<kurzname>-doc.<domain>` nennt `/auth/config` die
Startseite des Kunden – in der Hauptinstanz nur für einen Kunden des Kunden-Portals mit Doku, in
einer eigenen Instanz (ADR 0061) aus ihrer .env. Sonst leer: die Oberfläche zeigt keinen Link."""

import pytest
from app.config import Settings, get_settings
from app.models import PortalCustomer
from app.store import portal


def _config(client, settings: Settings, host: str | None) -> dict:
    client.app.dependency_overrides[get_settings] = lambda: settings
    r = client.get("/auth/config", params={"host": host} if host is not None else {})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def kunden(session_factory):
    with session_factory() as s:
        for short, doc in (("kunde-a", True), ("kunde-b", False)):
            s.add(PortalCustomer(short_name=short, name=short, doc=doc, links=[], customer_ids=[]))
        s.commit()


@pytest.mark.parametrize(
    ("host", "url"),
    [
        ("kunde-a-doc.mgmt.example.ch", "https://kunde-a.mgmt.example.ch"),
        ("Kunde-A-Doc.Mgmt.Example.ch:443", "https://kunde-a.mgmt.example.ch"),
        ("kunde-a-doc.mgmt.example.ch.", "https://kunde-a.mgmt.example.ch"),
        ("kunde-b-doc.mgmt.example.ch", ""),  # ohne Doku im Portal
        ("kunde-x-doc.mgmt.example.ch", ""),  # kein Kunde des Portals - nie geraten
        ("kunde-a-doc.andere.example.ch", ""),  # andere Domain
        ("kunde-a.mgmt.example.ch", ""),  # nicht die Oberfläche
        ("kunde-a-nb.mgmt.example.ch", ""),
        ("-doc.mgmt.example.ch", ""),
        ("automato.int.example.ch", ""),  # Adresse der Betreiber
        ("", ""),
        (None, ""),
    ],
)  # fmt: skip
def test_hauptinstanz_aus_dem_kunden_portal(client, kunden, host, url):
    settings = Settings(front_domain="mgmt.example.ch")
    assert _config(client, settings, host)["portal_url"] == url


def test_hauptinstanz_ohne_domain_kein_link(client, kunden):
    assert _config(client, Settings(), "kunde-a-doc.mgmt.example.ch")["portal_url"] == ""
    # eine Domain, die kein Name ist, ergibt keine Adresse
    kaputt = Settings(front_domain="mgmt.example.ch/x")
    assert _config(client, kaputt, "kunde-a-doc.mgmt.example.ch/x")["portal_url"] == ""


@pytest.mark.parametrize(
    ("host", "url"),
    [
        ("kunde-f-doc.mgmt.example.ch", "https://kunde-f.mgmt.example.ch"),
        ("kunde-a-doc.mgmt.example.ch", ""),  # ein anderer Kunde: nie dessen Startseite
        ("kunde-f-doc.andere.example.ch", ""),
        ("kunde-f.mgmt.example.ch", ""),
        (None, ""),
    ],
)  # fmt: skip
def test_eigene_instanz_aus_ihrer_env(client, kunden, host, url):
    """ADR 0061: die Instanz kennt ihren Kunden aus der .env - das Kunden-Portal (Hauptinstanz)
    zählt hier nicht, auch nicht ein gleichnamiger Kunde in der eigenen Datenbank."""
    settings = Settings(instanz_kunde="kunde-f", instanz_domain="mgmt.example.ch",
                        front_domain="mgmt.example.ch")  # fmt: skip
    assert _config(client, settings, host)["portal_url"] == url


def test_eigene_instanz_aus_der_umgebung(monkeypatch):
    """Die .env der Instanz (betrieb.sh installieren) kommt über deploy/instanz/compose.yml als
    AUTOMATO_INSTANZ_KUNDE/_DOMAIN in den Orchestrator."""
    monkeypatch.setenv("AUTOMATO_INSTANZ_KUNDE", "kunde-f")
    monkeypatch.setenv("AUTOMATO_INSTANZ_DOMAIN", "mgmt.example.ch")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.instanz_kunde, s.instanz_domain) == ("kunde-f", "mgmt.example.ch")
    assert portal.instance_start("kunde-f-doc.mgmt.example.ch", s.instanz_kunde,
                                 s.instanz_domain) == "https://kunde-f.mgmt.example.ch"  # fmt: skip


def test_ohne_datenbank_kein_link(client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def kaputt(*_a, **_k):
        raise OperationalError("select", {}, Exception("weg"))

    monkeypatch.setattr(portal, "start_for_host", kaputt)
    settings = Settings(front_domain="mgmt.example.ch")
    assert _config(client, settings, "kunde-a-doc.mgmt.example.ch")["portal_url"] == ""
