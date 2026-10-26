"""Test-Setup: SQLite in-memory, Celery-Dispatch gemockt.

Mit `AUTOMATO_TEST_DATABASE_URL` (z. B. `postgresql+psycopg://…`) laufen dieselben Tests gegen
eine echte Datenbank (CI-Job `postgres`); das Schema wird je Test neu angelegt.
"""

import os
from functools import lru_cache

import pytest
from app.db import get_session, get_session_factory
from app.main import create_app
from app.models import Base
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, event
from sqlalchemy.orm import close_all_sessions, sessionmaker


@pytest.fixture(autouse=True)
def _credentials_key():
    """Schluessel fuer Geraete-Zugangsdaten (ADR 0009) - sonst wuerde eine Schluesseldatei
    unter /var/lib/automato angelegt."""
    from app import credentials
    from app.config import get_settings

    settings = get_settings()
    previous = settings.credentials_key
    settings.credentials_key = "test-only-credentials-key"
    credentials._fernet.cache_clear()
    yield
    settings.credentials_key = previous
    credentials._fernet.cache_clear()


@lru_cache(maxsize=1)
def _external_engine(url: str):
    return create_engine(url, pool_pre_ping=True)


@pytest.fixture
def session_factory():
    url = os.environ.get("AUTOMATO_TEST_DATABASE_URL")
    if url:
        engine = _external_engine(url)
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        # Fremdschluessel wie in Postgres pruefen (SQLite tut das sonst nicht)
        event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    # Offene Test-Sessions schliessen - eine "idle in transaction"-Verbindung blockiert sonst
    # das DROP TABLE des naechsten Tests (Postgres)
    close_all_sessions()


@pytest.fixture
def client(session_factory, monkeypatch):
    # Celery nicht wirklich ansprechen - Dispatch nur aufzeichnen
    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.api.jobs.celery_app.dispatch_collect",
        lambda platform, run_id: dispatched.append((platform, run_id)),
    )

    def override_session():
        session = session_factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    test_client = TestClient(app)
    test_client.dispatched = dispatched  # fuer Assertions in Tests
    return test_client
