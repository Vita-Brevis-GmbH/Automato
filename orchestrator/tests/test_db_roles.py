"""Getrennte DB-Rollen (0.48.0, R14): Migration als Eigentuemer, Orchestrator ohne DDL.

Der Test gegen PostgreSQL (CI-Job `postgres`) legt eine App-Rolle an, meldet sich mit ihr an
(SCRAM-Verifier statt Klartext-Passwort) und prueft: Daten lesen und schreiben ja, Tabellen
anlegen, aendern oder loeschen nein.
"""

import os
import re
import secrets

import pytest
from app.store import db_roles
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

PG = (os.environ.get("AUTOMATO_TEST_DATABASE_URL") or "").startswith("postgresql")


def test_scram_verifier_format():
    v = db_roles.scram_sha256("x" * 20, salt=b"0123456789abcdef")
    assert re.fullmatch(r"SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+", v)
    assert v == db_roles.scram_sha256("x" * 20, salt=b"0123456789abcdef")
    assert v != db_roles.scram_sha256("y" * 20, salt=b"0123456789abcdef")
    assert "x" * 20 not in v


def test_ohne_postgres_keine_rolle(session_factory):
    with session_factory() as s:
        if PG:
            assert db_roles.describe(s)["separated"] is False  # Test-Benutzer = Eigentuemer
        else:
            assert db_roles.describe(s) is None
            with pytest.raises(db_roles.RoleError):
                db_roles.ensure_app_role(s.connection(), "automato_app", "z" * 20)


@pytest.mark.skipif(not PG, reason="nur gegen PostgreSQL (AUTOMATO_TEST_DATABASE_URL)")
def test_app_rolle_ohne_ddl(session_factory):
    engine = session_factory.kw["bind"]
    role = f"automato_app_t{secrets.token_hex(3)}"
    password = secrets.token_hex(16)
    app_engine = None
    try:
        with engine.begin() as conn:
            with pytest.raises(db_roles.RoleError):
                db_roles.ensure_app_role(conn, "Boese; DROP", password)
            steps = db_roles.ensure_app_role(conn, role, password)
            assert steps[0].endswith("angelegt (LOGIN, ohne DDL)")
            again = db_roles.ensure_app_role(conn, role, password)  # idempotent
            assert "nachgefuehrt" in again[0]
        app_engine = create_engine(engine.url.set(username=role, password=password))
        with app_engine.connect() as c:
            c.execute(
                text(
                    "INSERT INTO audit_log (timestamp, actor, action, subject) "
                    "VALUES (now(), 'test', 'x', 'y')"
                )
            )
            assert c.execute(text("SELECT count(*) FROM audit_log")).scalar() >= 1
            c.execute(text("DELETE FROM audit_log WHERE actor = 'test'"))
            c.commit()
            for ddl in (
                "CREATE TABLE boese (id int)",
                "ALTER TABLE audit_log ADD COLUMN boese int",
                "DROP TABLE audit_log",
            ):
                with pytest.raises(DBAPIError):
                    c.execute(text(ddl))
                c.rollback()
        with Session(app_engine) as s:
            info = db_roles.describe(s)
            assert info["role"] == role and info["separated"] and not info["ddl"]
    finally:
        if app_engine is not None:
            app_engine.dispose()
        with engine.begin() as conn:
            if conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first():
                conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
                conn.exec_driver_sql(f'DROP ROLE "{role}"')
