"""Test-Setup: SQLite in-memory, Celery-Dispatch gemockt.

Mit `AUTOMATO_TEST_DATABASE_URL` (z. B. `postgresql+psycopg://…`) laufen dieselben Tests gegen
eine echte Datenbank (CI-Job `postgres`). Das Schema wird je Datenbank einmal neu angelegt (beim
ersten Test), danach leert jeder Test vorab alle Tabellen samt Zaehlern (`TRUNCATE … RESTART
IDENTITY`) - derselbe Ausgangszustand wie mit neuem Schema, ohne die DDL von rund 90 Tabellen je
Test. `AUTOMATO_TEST_DB_RESET=schema` legt das Schema wie bis 0.103.0 vor jedem Test neu an.

Parallel mit pytest-xdist (`pytest -n auto`): SQLite ist je Test eine eigene In-Memory-DB; gegen
Postgres bekommt jeder Worker eine eigene Datenbank `<name>_<worker>` (z. B.
`automato_test_gw0`, beim ersten Test angelegt) - Tabellen, Sperren (`pg_advisory_xact_lock`
gilt je Datenbank) und Verbindungen der Worker kommen sich nicht in die Quere.
"""

import os
from functools import lru_cache

import pytest
from app.db import get_session, get_session_factory
from app.main import create_app
from app.models import Base
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, event, make_url, text
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


@pytest.fixture(autouse=True)
def _central_worker_answers(monkeypatch):
    """Verbindungspruefung (0.109.1): der zentrale Worker antwortet - ohne echten Broker;
    `tests/test_run_redispatch.py` prueft die anderen Faelle."""
    from app.store import connections, runs

    monkeypatch.setattr(connections, "_worker_ping", lambda settings: ["celery@test"])
    runs._dispatched.clear()
    runs._claims.clear()


@pytest.fixture(autouse=True)
def _doc_snapshots_off():
    """Doku als Stand (0.108.0) in Tests aus: viele Tests aendern Daten direkt in der Datenbank
    (ohne Lauf bzw. Audit) - `tests/test_doc_snapshots.py` schaltet ihn ein;
    `AUTOMATO_TEST_DOC_SNAPSHOTS=1` laesst alle Tests mit dem Stand laufen (Fingerabdruck)."""
    from app.config import get_settings

    settings = get_settings()
    previous = settings.doc_snapshots
    settings.doc_snapshots = os.environ.get("AUTOMATO_TEST_DOC_SNAPSHOTS") == "1"
    yield
    settings.doc_snapshots = previous


def _worker_url(url: str):
    """Eigene Postgres-Datenbank je xdist-Worker; ohne xdist die Datenbank aus der URL."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")  # gw0, gw1, ... (nur in xdist-Workern)
    parsed = make_url(url)
    if not worker or not parsed.drivername.startswith("postgresql"):
        return parsed
    name = f"{parsed.database}_{worker}"
    admin = create_engine(parsed, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            query = text("SELECT 1 FROM pg_database WHERE datname = :name")
            if conn.execute(query, {"name": name}).first() is None:
                conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    finally:
        admin.dispose()
    return parsed.set(database=name)


@lru_cache(maxsize=1)
def _external_engine(url: str):
    return create_engine(_worker_url(url), pool_pre_ping=True)


_fresh: set[str] = set()  # Datenbanken mit neu angelegtem Schema (in diesem Prozess)


def _reset_external(engine) -> None:
    """Ausgangszustand je Test: Schema einmal neu anlegen, danach nur alle Tabellen leeren."""
    key = engine.url.render_as_string()
    full = os.environ.get("AUTOMATO_TEST_DB_RESET") == "schema"
    if key not in _fresh or full or engine.dialect.name != "postgresql":
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        _fresh.add(key)
        return
    quote = engine.dialect.identifier_preparer.quote
    tables = ", ".join(quote(table.name) for table in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")


@pytest.fixture
def session_factory():
    url = os.environ.get("AUTOMATO_TEST_DATABASE_URL")
    if url:
        engine = _external_engine(url)
        _reset_external(engine)
    else:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        # Fremdschluessel wie in Postgres pruefen (SQLite tut das sonst nicht)
        event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    # Offene Test-Sessions schliessen - eine "idle in transaction"-Verbindung blockiert sonst
    # das TRUNCATE bzw. DROP TABLE des naechsten Tests (Postgres)
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
