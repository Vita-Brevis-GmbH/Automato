"""0.78.1: Anfragen des Agenten warten nicht auf eine gesperrte Agent-Zeile.

Rueckmeldung des Anwenders: Update-Pruefung, naechster Lauf und Ziele liefen in den ReadTimeout
(30 s) des Agenten. Ursache: das Einspielen eines Laufs schrieb das Lebenszeichen des Agenten
und hielt dessen Zeile bis zum Ende der (langen) Transaktion gesperrt - jede weitere Anfrage
des Agenten wartete darauf und belegte eine Verbindung der Datenbank.
"""

import os
import threading

import pytest
from app.models import Agent
from app.store import agents as agent_store
from sqlalchemy import select, update
from test_agents import _seed

PG = os.environ.get("AUTOMATO_TEST_DATABASE_URL", "").startswith("postgresql")
ADMIN = {"X-Dev-Roles": "admin"}


def _token(client, session_factory) -> str:
    seed = session_factory()
    tid, _ = _seed(seed)
    seed.commit()
    r = client.post("/agents", json={"name": "agent-win-01", "tenant_id": tid}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["token"]


def test_lebenszeichen_und_version(client, session_factory):
    token = _token(client, session_factory)
    headers = {"X-Agent-Token": token, "X-Agent-Version": "0.78.1+abc", "X-Agent-Update": "ok"}
    assert client.post("/internal/agent/heartbeat", headers=headers).status_code == 204
    with session_factory() as s:
        agent = s.scalars(select(Agent)).one()
        assert agent.last_seen is not None and agent.version == "0.78.1+abc"
        # das Nachschlagen allein schreibt nichts (Upload: keine Sperre waehrend des Einspielens)
        assert agent_store.agent_by_token(s, token) is agent
        assert not s.dirty


@pytest.mark.skipif(not PG, reason="nur gegen PostgreSQL (AUTOMATO_TEST_DATABASE_URL)")
def test_gesperrte_agent_zeile_haelt_nicht_auf(client, session_factory):
    token = _token(client, session_factory)
    headers = {"X-Agent-Token": token, "X-Agent-Version": "0.78.1+neu"}
    holder = session_factory()  # haelt die Zeile wie frueher ein laufendes Einspielen
    holder.execute(update(Agent).values(update_note="wird eingespielt"))
    out: dict[str, int] = {}

    def calls() -> None:
        out["heartbeat"] = client.post("/internal/agent/heartbeat", headers=headers).status_code
        out["next-run"] = client.get("/internal/agent/next-run", headers=headers).status_code
        out["targets"] = client.get("/internal/agent/targets", headers=headers).status_code

    worker = threading.Thread(target=calls)
    worker.start()
    worker.join(timeout=20)
    try:
        assert not worker.is_alive(), "Anfrage des Agenten wartet auf die gesperrte Zeile"
        assert out == {"heartbeat": 204, "next-run": 200, "targets": 200}
    finally:
        holder.rollback()
        holder.close()
        worker.join(timeout=30)
    # Zeile wieder frei: das Lebenszeichen samt Version kommt beim naechsten Aufruf an
    assert client.post("/internal/agent/heartbeat", headers=headers).status_code == 204
    with session_factory() as s:
        assert s.scalars(select(Agent)).one().version == "0.78.1+neu"
