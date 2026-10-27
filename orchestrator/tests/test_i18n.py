"""Sprache der Texte vom Server (0.87.0, ADR 0028): Accept-Language bzw. ?lang= je Anfrage,
ohne Anfrage und beim Speichern immer Deutsch."""

import asyncio
import threading

from app import i18n


def test_parse_accept_language():
    assert i18n.parse("en-GB,en;q=0.9,de;q=0.8") == "en"
    assert i18n.parse("de-CH,de;q=0.9,en;q=0.8") == "de"
    assert i18n.parse("fr, en;q=0.4, de;q=0.5") == "de"
    assert i18n.parse("en;q=0.5, de;q=0.5") == "en"  # gleiches Gewicht: Reihenfolge
    assert i18n.parse("") == "de"
    assert i18n.parse(None) == "de"
    assert i18n.parse("*") == "de"
    assert i18n.parse("en;q=abc") == "de"


def test_L_and_use():
    assert i18n.L("gesperrt", "blocked") == "gesperrt"
    with i18n.use("en"):
        assert i18n.L("gesperrt", "blocked") == "blocked"
        assert i18n.lang() == "en"
        with i18n.german():
            assert i18n.L("gesperrt", "blocked") == "gesperrt"
        assert i18n.lang() == "en"
    with i18n.use("fr"):  # unbekannt -> Deutsch
        assert i18n.lang() == "de"
    assert i18n.lang() == "de"


def test_german_only_and_threads():
    @i18n.german_only
    def stored():
        return i18n.L("deutsch", "english")

    seen = []
    with i18n.use("en"):
        assert stored() == "deutsch"
        # neue Threads (z. B. app_refresh) beginnen ohne Sprache = Deutsch
        t = threading.Thread(target=lambda: seen.append(i18n.lang()))
        t.start()
        t.join()
    assert seen == ["de"]


def test_term():
    assert i18n.term(None) is None
    assert i18n.term("unbekannter Schlüssel") == "unbekannter Schlüssel"
    with i18n.use("en"):
        assert i18n.term("unbekannter Schlüssel") == "unbekannter Schlüssel"


def test_middleware_sets_language():
    seen = []

    async def app(scope, receive, send):
        seen.append(i18n.lang())

    mw = i18n.middleware(app)

    def call(headers=(), query=b""):
        scope = {"type": "http", "headers": list(headers), "query_string": query}
        asyncio.run(mw(scope, None, None))
        return seen[-1]

    assert call() == "de"
    assert call([(b"accept-language", b"en-US,en;q=0.9")]) == "en"
    assert call([(b"accept-language", b"en")], b"lang=de") == "de"  # ?lang= geht vor
    assert call(query=b"tenant_id=1&lang=en") == "en"
    assert call(query=b"lang=xx") == "de"
    assert i18n.lang() == "de"  # nach der Anfrage zurueck


def test_api_follows_accept_language(client):
    """Fehlermeldung der API und Text eines sync-Endpunkts (Threadpool) folgen der Sprache."""
    de = client.get("/health")
    en = client.get("/health", headers={"Accept-Language": "en"})
    assert de.status_code == en.status_code == 200
