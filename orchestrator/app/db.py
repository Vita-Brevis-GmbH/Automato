"""SQLAlchemy-Engine und Session-Dependency."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI-Dependency: eine Session pro Request, Commit/Rollback zentral.

    Immer als `Depends(get_session, scope="function")` einbinden: FastAPI fuehrt den Teil nach
    `yield` sonst erst *nach* dem Senden der Antwort aus. Dann sieht der Aufrufer 2xx, bevor
    gespeichert ist - ein Agent schickte so sein Ergebnis, bevor der letzte Teil-Upload
    committet war (Dev-Befund 0.11.1), und ein fehlgeschlagener Commit kaeme nie beim
    Aufrufer an.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session_factory() -> sessionmaker[Session]:
    """FastAPI-Dependency fuer Hintergrund-Aufgaben, die eine eigene Session brauchen."""
    return get_sessionmaker()
