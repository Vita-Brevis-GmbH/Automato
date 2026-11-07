"""Unerwarteter Fehler (0.94.1): Antwort 500 mit Referenz, Ursache nur im Protokoll."""

import logging

from app.main import create_app
from fastapi.testclient import TestClient


def test_interner_fehler_mit_referenz(caplog):
    app = create_app()

    @app.get("/_boom")
    def boom():
        raise RuntimeError("geheime Einzelheit")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        r = client.get("/_boom")
    assert r.status_code == 500
    body = r.json()
    ref = body["ref"]
    assert len(ref) == 8 and ref in body["detail"]
    assert "geheime Einzelheit" not in r.text  # keine Details nach aussen
    logged = [rec for rec in caplog.records if ref in rec.getMessage()]
    assert logged and "/_boom" in logged[0].getMessage() and logged[0].exc_info
