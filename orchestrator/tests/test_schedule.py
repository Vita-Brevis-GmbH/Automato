"""Zeitgesteuerte Läufe: Cron-Ausdruck (pure), Zeitplan am Quellsystem, Scheduler-Durchgang."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from app import schedule, scheduler
from app.models import CollectorRun, RunStatus, SourceSystem
from sqlalchemy import select
from test_reference_sources import ADMIN, _agent, _system, _tenant

ZRH = ZoneInfo("Europe/Zurich")
TZ = "Europe/Zurich"


def _local(*args) -> datetime:
    return datetime(*args, tzinfo=ZRH)


def test_cron_felder_und_fehler():
    c = schedule.parse("*/15 2-4 * * 1-5")
    assert c.minutes == {0, 15, 30, 45} and c.hours == {2, 3, 4}
    assert c.weekdays == {1, 2, 3, 4, 5}
    assert schedule.parse("0 3 * * 7").weekdays == {0}  # 7 = Sonntag
    assert schedule.parse("0 3 * jan sun").months == {1}
    assert schedule.parse("  0   2 * * *  ").expr == "0 2 * * *"
    for bad, why in [
        ("0 2 * *", "5 Felder"),
        ("60 * * * *", "ausserhalb"),
        ("0 2 * * x", "keine Zahl"),
        ("*/0 * * * *", "Schrittweite"),
        ("5-1 * * * *", "ausserhalb"),
    ]:
        with pytest.raises(ValueError, match=why):
            schedule.parse(bad)


def test_cron_vorheriger_und_naechster_termin():
    daily = schedule.parse("0 2 * * *")
    now = _local(2026, 10, 20, 13, 7)
    assert daily.previous(now) == _local(2026, 10, 20, 2, 0)
    assert daily.next_after(now) == _local(2026, 10, 21, 2, 0)
    assert daily.previous(_local(2026, 10, 20, 2, 0)) == _local(2026, 10, 20, 2, 0)

    weekdays = schedule.parse("30 6 * * 1-5")  # 2026-10-24 = Samstag
    assert weekdays.next_after(_local(2026, 10, 23, 7, 0)) == _local(2026, 10, 26, 6, 30)
    assert weekdays.previous(_local(2026, 10, 25, 12, 0)) == _local(2026, 10, 23, 6, 30)

    # Tag UND Wochentag eingeschränkt: einer genügt (wie cron) - 1. des Monats oder Montag
    either = schedule.parse("0 0 1 * 1")
    assert either.next_after(_local(2026, 10, 27, 0, 0)) == _local(2026, 11, 1, 0, 0)
    assert either.next_after(_local(2026, 11, 1, 0, 0)) == _local(2026, 11, 2, 0, 0)

    assert schedule.parse("0 0 31 2 *").next_after(now) is None  # nie (30./31. Februar)


def _src(session_factory, sid) -> SourceSystem:
    return session_factory().get(SourceSystem, sid)


def _runs(session_factory, sid) -> list[CollectorRun]:
    s = session_factory()
    return list(s.scalars(select(CollectorRun).where(CollectorRun.source_system_id == sid)))


def test_zeitplan_am_quellsystem_api(client):
    tid = _tenant(client)
    fw = _system(
        client, tid, name="fw", platform="fortigate", base_url="https://fw", schedule="0  2 * * *"
    )
    assert fw["schedule"] == "0 2 * * *" and fw["next_run_at"] is not None
    bad = client.patch(f"/systems/{fw['id']}", json={"schedule": "jeden Tag"}, headers=ADMIN)
    assert bad.status_code == 422 and bad.json()["detail"].startswith("schedule:")
    off = client.patch(f"/systems/{fw['id']}", json={"schedule": ""}, headers=ADMIN).json()
    assert off["schedule"] is None and off["next_run_at"] is None


def test_scheduler_loest_termin_einmal_aus(client, session_factory):
    tid = _tenant(client)
    fw = _system(
        client, tid, name="fw", platform="fortigate", base_url="https://fw", schedule="0 2 * * *"
    )
    sid = fw["id"]
    s = session_factory()
    s.get(SourceSystem, sid).schedule_last_at = _local(2026, 10, 20, 13, 0).astimezone(UTC)
    s.commit()

    # vor dem nächsten Termin: nichts
    assert scheduler.tick(session_factory, TZ, _local(2026, 10, 21, 1, 59)) == []
    # Termin 02:00 erreicht -> genau ein Lauf, zentral, Auslöser Zeitplan
    (run_id,) = scheduler.tick(session_factory, TZ, _local(2026, 10, 21, 2, 0, 30))
    (run,) = _runs(session_factory, sid)
    assert (run.id, run.trigger, run.execution) == (run_id, "schedule", "inprocess")
    assert client.dispatched == [("fortigate", run_id)]
    # zweiter Durchgang (oder anderer Prozess) im selben Termin: nichts mehr
    assert scheduler.tick(session_factory, TZ, _local(2026, 10, 21, 2, 1)) == []
    assert client.get("/jobs", params={"tenant_id": tid}).json()[0]["trigger"] == "schedule"

    # nächster Tag, Lauf wartet noch -> Termin fällt aus, kein Rückstau danach
    assert scheduler.tick(session_factory, TZ, _local(2026, 10, 22, 2, 0)) == []
    s = session_factory()
    s.get(CollectorRun, run_id).status = RunStatus.COMPLETED
    s.commit()
    assert scheduler.tick(session_factory, TZ, _local(2026, 10, 22, 9, 0)) == []
    # nach einem Ausfall über mehrere Termine: nur der letzte wird nachgeholt
    assert len(scheduler.tick(session_factory, TZ, _local(2026, 10, 25, 9, 0))) == 1
    assert len(_runs(session_factory, sid)) == 2


def test_scheduler_agent_extern_inaktiv_und_neuer_zeitplan(client, session_factory):
    tid = _tenant(client)
    agent_id, _ = _agent(client, tid)
    apic = _system(
        client,
        tid,
        name="apic",
        platform="cisco_aci",
        base_url="https://apic",
        agent_id=agent_id,
        schedule="*/30 * * * *",
    )
    off = _system(
        client, tid, name="fw", platform="fortigate", base_url="https://fw", schedule="* * * * *"
    )
    client.patch(f"/systems/{off['id']}", json={"enabled": False}, headers=ADMIN)
    s = session_factory()
    s.get(SourceSystem, apic["id"]).schedule_last_at = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
    s.commit()

    created = scheduler.tick(session_factory, TZ, datetime(2026, 10, 20, 9, 0, tzinfo=UTC))
    (run,) = _runs(session_factory, apic["id"])
    assert created == [run.id] and run.execution == "external"  # Agent holt ihn ab
    assert client.dispatched == []
    assert _runs(session_factory, off["id"]) == []  # deaktiviert

    # Zeitplan ohne Stand (Altbestand): ab jetzt zählen, nicht sofort auslösen
    s = session_factory()
    s.get(SourceSystem, off["id"]).enabled = True
    s.get(SourceSystem, off["id"]).schedule_last_at = None
    s.commit()
    now = datetime(2026, 10, 20, 9, 0, 20, tzinfo=UTC)
    assert scheduler.tick(session_factory, TZ, now) == []
    assert _src(session_factory, off["id"]).schedule_last_at is not None
