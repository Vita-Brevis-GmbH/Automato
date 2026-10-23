"""Aufbewahrung (0.48.0, R14): alte Laeufe, NetBox-Sync-Vorschauen und Audit-Eintraege nach
ihrer Frist loeschen - der aktuelle Stand bleibt immer (Laeufe, auf die Regeln zeigen, der neuste
und der neuste erfolgreiche Lauf je Quellsystem, offene Laeufe, die neuste Vorschau je NetBox)."""

from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.models import AuditLog, CollectorRun, ReferenceRecordRow, Rule, RunStatus, SyncPlan
from app.store import retention
from sqlalchemy import select
from test_snat import ADMIN, _seed


def _run(s, tid, sid, status, days, now, finished=True):
    at = now - timedelta(days=days)
    run = CollectorRun(platform="fortigate", tenant_id=tid, source_system_id=sid, status=status,
                       queued_at=at, finished_at=at if finished else None)  # fmt: skip
    s.add(run)
    s.flush()
    return run.id


def test_fristen_loeschen_nur_altes(client, session_factory):
    tid, sid = _seed(client)
    now = datetime.now(UTC)
    with session_factory() as s:
        (current,) = s.scalars(select(CollectorRun).where(CollectorRun.source_system_id == sid))
        current.queued_at = current.finished_at = now - timedelta(days=200)  # Regeln zeigen darauf
        gone = [
            _run(s, tid, sid, RunStatus.COMPLETED, 150, now),
            _run(s, tid, sid, RunStatus.FAILED, 120, now),
            _run(s, None, None, RunStatus.COMPLETED, 300, now),  # nur gezaehlt, ohne Quellsystem
        ]
        s.add(ReferenceRecordRow(tenant_id=tid, source_system_id=sid, collector_run_id=gone[1],
                                 kind="prefix", value="10.9.0.0/24"))  # fmt: skip
        kept = [
            current.id,
            _run(s, tid, sid, RunStatus.QUEUED, 150, now, finished=False),  # offen
            _run(s, tid, sid, RunStatus.COMPLETED, 100, now),  # neuster erfolgreicher
            _run(s, tid, sid, RunStatus.FAILED, 10, now),  # neuster, innerhalb der Frist
        ]
        for status, days in (("superseded", 200), ("planning", 200), ("ready", 150)):
            s.add(SyncPlan(tenant_id=tid, source_system_id=sid, status=status, created_by="t",
                           created_at=now - timedelta(days=days)))  # fmt: skip
        s.add(AuditLog(actor="t", action="x", subject="alt", timestamp=now - timedelta(days=400)))
        s.commit()

    st = client.get("/system/retention", headers=ADMIN).json()
    assert st["policy"] == {"runs_days": 90, "sync_days": 90, "audit_days": 365}
    assert st["due"] == {"runs": 3, "sync_plans": 1, "audit": 1} and st["last"] is None

    r = client.post("/system/retention/purge", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == {"runs": 3, "sync_plans": 1, "audit": 1}
    assert body["due"] == {"runs": 0, "sync_plans": 0, "audit": 0}
    assert body["last"]["counts"] == body["deleted"] and body["last"]["actor"].startswith("user:")
    with session_factory() as s:
        left = set(s.scalars(select(CollectorRun.id)))
        assert left == set(kept)
        assert s.scalars(select(ReferenceRecordRow)).all() == []
        assert sorted(p.status for p in s.scalars(select(SyncPlan))) == ["planning", "ready"]
        assert s.scalars(select(AuditLog).where(AuditLog.subject == "alt")).all() == []
        assert s.scalars(select(Rule).where(Rule.collector_run_id == kept[0])).first()
    # Zeitplan: hoechstens einmal am Tag
    assert retention.maybe_purge(session_factory, get_settings(), now) is None
    later = now + timedelta(hours=25)
    assert retention.maybe_purge(session_factory, get_settings(), later) == {
        "runs": 0,
        "sync_plans": 0,
        "audit": 0,
    }
    checks = {c["key"]: c for c in client.get("/system/health").json()["checks"]}
    assert checks["retention"]["status"] == "ok"
    assert "zuletzt aufgeräumt" in checks["retention"]["title"]


def test_frist_null_heisst_nie(client, session_factory, monkeypatch):
    tid, sid = _seed(client)
    settings = get_settings()
    for field in ("retention_runs_days", "retention_sync_days", "retention_audit_days"):
        monkeypatch.setattr(settings, field, 0)
    now = datetime.now(UTC)
    with session_factory() as s:
        _run(s, tid, sid, RunStatus.COMPLETED, 900, now)
        s.commit()
    assert client.get("/system/retention", headers=ADMIN).json()["due"]["runs"] == 0
    assert client.post("/system/retention/purge", headers=ADMIN).status_code == 409
    assert retention.maybe_purge(session_factory, settings, now) is None
    # Admin-Funktion
    assert (
        client.post("/system/retention/purge", headers={"X-Dev-Roles": "viewer"}).status_code == 403
    )
