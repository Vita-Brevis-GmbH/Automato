"""0.80.1: Laeufe stabil (Befund des Anwenders beim Einlesen aller Geraete).

„Einspielen fehlgeschlagen: DeadlockDetected … relation rule_index“ und HTTP 500 beim
Aktualisieren der letzten Laeufe: mehrere Ergebnisse wurden zugleich eingespielt und schrieben
denselben Zustand des Mandanten neu; wartende Uploads belegten die Verbindungen.
"""

import os
import threading

import pytest
from app.models import CollectorRun, RunStatus
from app.store import ingest, runs, tenant_lock
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from test_policy_check import ADMIN, FW, TOKEN

PG = os.environ.get("AUTOMATO_TEST_DATABASE_URL", "").startswith("postgresql")


class _Deadlock(Exception):
    sqlstate = "40P01"


def _run(client) -> tuple[int, str]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-a", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    return tid, client.post("/jobs/collect", json=run).json()["run_id"]


def test_deadlock_wird_erkannt():
    assert tenant_lock.transient(OperationalError("UPDATE rule_index", {}, _Deadlock()))
    assert not tenant_lock.transient(OperationalError("x", {}, Exception("anderer Fehler")))
    assert not tenant_lock.transient(ValueError("kein DB-Fehler"))


def test_einspielen_wiederholt_nach_deadlock(client, monkeypatch):
    tid, run_id = _run(client)
    real, calls = ingest.persist_result, []

    def flaky(session, run, result, sot):
        calls.append(run.id)
        if len(calls) == 1:  # erster Versuch: Deadlock mit einem anderen Einspielen
            raise OperationalError("UPDATE rule_index", {}, _Deadlock())
        return real(session, run, result, sot)

    monkeypatch.setattr(ingest, "persist_result", flaky)
    monkeypatch.setattr("app.api.internal.time.sleep", lambda _s: None)
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=FW)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed", r.json()
    assert len(calls) == 2
    assert client.get("/rules", params={"tenant_id": tid}).json()  # Regeln eingespielt


def test_einspielen_ohne_ende_bleibt_failed(client, monkeypatch):
    _, run_id = _run(client)

    def stuck(*_a):
        raise OperationalError("UPDATE rule_index", {}, _Deadlock())

    monkeypatch.setattr(ingest, "persist_result", stuck)
    monkeypatch.setattr("app.api.internal.time.sleep", lambda _s: None)
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN, json=FW)
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert r.json()["message"].startswith("Einspielen fehlgeschlagen: _Deadlock")


def test_nacheinander_einspielen():
    order: list[str] = []
    first_in = threading.Event()
    release = threading.Event()

    def first():
        with tenant_lock.slot("lauf-1"):
            first_in.set()
            release.wait(5)
            order.append("lauf-1")

    def second():
        with tenant_lock.slot("lauf-2"):
            order.append("lauf-2")

    a = threading.Thread(target=first)
    a.start()
    assert first_in.wait(5)
    b = threading.Thread(target=second)
    b.start()
    for _ in range(200):  # bis der zweite wartet
        if tenant_lock.state("lauf-2") == "waiting":
            break
        threading.Event().wait(0.01)
    assert tenant_lock.state("lauf-1") == "active"
    assert tenant_lock.state("lauf-2") == "waiting"
    assert tenant_lock.waiting_before("lauf-2") == 1
    release.set()
    a.join(5)
    b.join(5)
    assert order == ["lauf-1", "lauf-2"]
    assert tenant_lock.state("lauf-1") is None and tenant_lock.state("lauf-2") is None


def test_liste_zeigt_wartendes_ergebnis_und_beendet_es_nicht(client, monkeypatch):
    _, run_id = _run(client)
    monkeypatch.setitem(tenant_lock._waiting, run_id, 0.0)
    rows = client.get("/jobs", params={"status": "open"}).json()
    (row,) = (r for r in rows if r["id"] == run_id)
    assert row["message"] == "Ergebnis angekommen – wartet aufs Einspielen (0 davor)"
    assert row["status"] in ("queued", "running")


def test_liste_ohne_500_wenn_beenden_scheitert(client, monkeypatch):
    _run(client)

    def broken(_session, now=None):
        raise SQLAlchemyError("could not obtain lock")

    monkeypatch.setattr(runs, "expire_stale", broken)
    r = client.get("/jobs")
    assert r.status_code == 200 and len(r.json()) == 1


def test_haengender_lauf_mit_wartendem_ergebnis_bleibt_offen(client, session_factory, monkeypatch):
    _, run_id = _run(client)
    with session_factory() as s:
        run = s.get(CollectorRun, run_id)
        run.status = RunStatus.RUNNING
        run.started_at = run.queued_at.replace(year=2020)
        s.commit()
    monkeypatch.setitem(tenant_lock._active, run_id, 0.0)
    with session_factory() as s:
        assert runs.expire_stale(s) == 0
    monkeypatch.delitem(tenant_lock._active, run_id)
    with session_factory() as s:
        assert runs.expire_stale(s) == 1
        s.commit()
        assert s.scalars(select(CollectorRun.status)).one() is RunStatus.FAILED


@pytest.mark.skipif(not PG, reason="nur gegen PostgreSQL (AUTOMATO_TEST_DATABASE_URL)")
def test_sperre_des_mandanten(session_factory):
    holder, other = session_factory(), session_factory()
    try:
        tenant_lock.hold(holder, 7)
        tenant_lock.hold(holder, 7)  # in derselben Transaktion nochmals: gefahrlos
        got = other.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :tid)"),
            {"ns": tenant_lock.NAMESPACE, "tid": 7},
        ).scalar()
        assert got is False  # ein zweites Einspielen wartet
        free = other.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :tid)"),
            {"ns": tenant_lock.NAMESPACE, "tid": 8},
        ).scalar()
        assert free is True  # anderer Mandant: frei
        holder.commit()  # Ende der Transaktion gibt frei
        other.rollback()
        again = other.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :tid)"),
            {"ns": tenant_lock.NAMESPACE, "tid": 7},
        ).scalar()
        assert again is True
    finally:
        holder.rollback()
        other.rollback()
        holder.close()
        other.close()
