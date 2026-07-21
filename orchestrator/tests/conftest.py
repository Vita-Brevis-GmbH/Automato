"""Test-Setup: SQLite in-memory statt Postgres, Celery-Dispatch gemockt."""

import pytest
from app.db import get_session
from app.main import create_app
from app.models import Base
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


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
    test_client = TestClient(app)
    test_client.dispatched = dispatched  # fuer Assertions in Tests
    return test_client
