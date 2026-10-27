"""Getrennte DB-Rollen (0.48.0, R14: „Migration vs. Betrieb, App ohne DDL“).

Die Migration laeuft als Eigentuemer der Tabellen (docker-compose: Dienst `migrate` mit dem
Postgres-Benutzer `automato`); der Orchestrator verbindet sich mit einer eigenen Rolle, die
Daten liest und schreibt, aber keine Tabellen anlegen, aendern oder loeschen kann.
`ensure_app_role` richtet sie ein - idempotent, nach jeder Migration (`automato db-roles`):

- Rolle mit LOGIN, ohne SUPERUSER/CREATEDB/CREATEROLE; das Passwort geht nur als SCRAM-Verifier
  an den Server (nie im Klartext, also auch in kein Statement-Log)
- CONNECT/TEMPORARY auf die Datenbank, USAGE auf das Schema, kein CREATE (auch nicht PUBLIC)
- SELECT/INSERT/UPDATE/DELETE auf alle Tabellen und USAGE/SELECT/UPDATE auf alle Sequenzen,
  dasselbe per DEFAULT PRIVILEGES fuer die Tabellen kuenftiger Migrationen; den
  Migrationsstand (`alembic_version`) nur lesen

`describe` sagt, mit welcher Rolle die laufende Verbindung arbeitet und ob sie DDL kann.
"""

import base64
import hashlib
import hmac
import re
import secrets

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.i18n import L

ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
ITERATIONS = 4096  # wie Postgres (scram_iterations)


class RoleError(ValueError):
    """App-Rolle nicht einrichtbar (Meldung fuer die CLI)."""


def scram_sha256(password: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    """SCRAM-SHA-256-Verifier im Format von Postgres (RFC 5802/7677)."""
    salt = salt or secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client = hmac.new(salted, b"Client Key", "sha256").digest()
    stored = hashlib.sha256(client).digest()
    server = hmac.new(salted, b"Server Key", "sha256").digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode()

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored)}:{b64(server)}"


def ensure_app_role(conn: Connection, role: str, password: str) -> list[str]:
    """App-Rolle anlegen bzw. nachfuehren; Rueckgabe = erledigte Schritte (fuer die CLI, in
    deren Sprache - nicht gespeichert)."""
    if conn.dialect.name != "postgresql":
        raise RoleError(L("getrennte Rollen gibt es nur mit PostgreSQL",
                          "separate roles exist only with PostgreSQL"))  # fmt: skip
    if not ROLE_NAME.match(role):
        raise RoleError(L(f"ungueltiger Rollenname „{role}“ (a-z, 0-9, _)",
                          f"invalid role name “{role}” (a-z, 0-9, _)"))  # fmt: skip
    if len(password) < 16:
        raise RoleError(
            L(
                "Passwort der App-Rolle zu kurz (mindestens 16 Zeichen)",
                "password of the app role too short (at least 16 characters)",
            )
        )
    owner, database, schema = conn.execute(
        text("SELECT current_user, current_database(), current_schema()")
    ).one()
    if role == owner:
        raise RoleError(L(f"die App-Rolle muss eine andere sein als der Eigentuemer „{owner}“",
                          f"the app role must differ from the owner “{owner}”"))  # fmt: skip
    quote = conn.dialect.identifier_preparer.quote
    r, d, s, o = quote(role), quote(database), quote(schema), quote(owner)
    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first()
    verb = "ALTER" if exists else "CREATE"
    # Verifier statt Klartext; exec_driver_sql, weil der Verifier ':' enthaelt (kein Bind-Parameter)
    conn.exec_driver_sql(
        f"{verb} ROLE {r} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
        f"PASSWORD '{scram_sha256(password)}'"
    )
    steps = [
        L(
            f"Rolle {role} {'nachgefuehrt' if exists else 'angelegt'} (LOGIN, ohne DDL)",
            f"role {role} {'updated' if exists else 'created'} (LOGIN, without DDL)",
        )
    ]
    for sql in (
        f"GRANT CONNECT, TEMPORARY ON DATABASE {d} TO {r}",
        f"REVOKE CREATE ON SCHEMA {s} FROM PUBLIC",
        f"REVOKE CREATE ON SCHEMA {s} FROM {r}",
        f"GRANT USAGE ON SCHEMA {s} TO {r}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {s} TO {r}",
        f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {s} TO {r}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA {s} "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA {s} "
        f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {r}",
    ):
        conn.exec_driver_sql(sql)
    steps.append(
        L(
            f"Daten lesen/schreiben in {database}.{schema}, kein CREATE im Schema",
            f"read/write data in {database}.{schema}, no CREATE in the schema",
        )
    )
    steps.append(
        L(
            f"kuenftige Tabellen von {owner} ebenso (DEFAULT PRIVILEGES)",
            f"future tables of {owner} likewise (DEFAULT PRIVILEGES)",
        )
    )
    version = conn.execute(
        text("SELECT 1 FROM pg_tables WHERE schemaname = :s AND tablename = 'alembic_version'"),
        {"s": schema},
    ).first()
    if version:
        conn.exec_driver_sql(f"REVOKE INSERT, UPDATE, DELETE ON {s}.alembic_version FROM {r}")
        steps.append(
            L(
                "Migrationsstand (alembic_version) nur lesen",
                "migration state (alembic_version) read-only",
            )
        )
    return steps


def describe(session: Session) -> dict | None:
    """Rolle der laufenden Verbindung; None ausserhalb von PostgreSQL (Tests mit SQLite)."""
    if session.get_bind().dialect.name != "postgresql":
        return None
    row = (
        session.execute(
            text(
                "SELECT current_user AS role, r.rolsuper AS superuser, "
                "r.rolcreaterole AS createrole, r.rolcreatedb AS createdb, "
                "has_schema_privilege(current_user, current_schema(), 'CREATE') AS can_create, "
                "(SELECT count(*) FROM pg_tables WHERE schemaname = current_schema() "
                " AND tableowner = current_user) AS owned "
                "FROM pg_roles r WHERE r.rolname = current_user"
            )
        )
        .mappings()
        .one()
    )
    ddl = bool(row["superuser"] or row["can_create"] or row["owned"])
    return {
        "role": row["role"],
        "superuser": bool(row["superuser"]),
        "ddl": ddl,
        "owns_tables": int(row["owned"]),
        "separated": not ddl and not row["createrole"] and not row["createdb"],
    }
