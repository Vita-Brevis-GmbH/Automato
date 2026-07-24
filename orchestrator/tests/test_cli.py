"""CLI-Auslöser: idempotentes Anlegen + Dispatch (ohne Celery/DB-Server)."""

from app import cli
from app.models import CollectorRun, Platform, Tenant


def _dispatch_recorder(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        cli.celery_app,
        "dispatch_collect",
        lambda platform, run_id: calls.append((platform, run_id)),
    )
    return calls


def test_ensure_ist_idempotent(session_factory):
    session = session_factory()
    t1 = cli.ensure_tenant(session, "kunde-a", "netbox", "active_directory")
    t2 = cli.ensure_tenant(session, "kunde-a", "netbox", "active_directory")
    assert t1.id == t2.id
    assert session.query(Tenant).count() == 1

    s1 = cli.ensure_source(session, "fw-01", "fortigate", "https://fw-01")
    s2 = cli.ensure_source(session, "fw-01", "fortigate", "https://fw-01.neu")
    assert s1.id == s2.id
    assert s2.base_url == "https://fw-01.neu"  # base_url wird aktualisiert
    session.close()


def test_queue_collect_legt_run_an_und_dispatcht(session_factory, monkeypatch):
    calls = _dispatch_recorder(monkeypatch)
    session = session_factory()
    tenant = cli.ensure_tenant(session, "kunde-a", "netbox", "active_directory")
    source = cli.ensure_source(session, "fw-01", "fortigate", "https://fw-01")
    session.flush()

    run = cli.queue_collect(session, "fortigate", tenant, source)

    assert run.tenant_id == tenant.id
    assert run.source_system_id == source.id
    assert calls == [("fortigate", run.id)]  # genau einmal dispatcht

    persisted = session.get(CollectorRun, run.id)
    assert persisted is not None and persisted.platform == Platform.FORTIGATE
    session.close()
