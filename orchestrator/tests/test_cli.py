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
    assert persisted.execution == "inprocess"
    session.close()


def test_queue_collect_external_ohne_dispatch(session_factory, monkeypatch):
    # ADR 0008: externer Lauf geht NICHT an Celery - der Agent holt ihn per Pull.
    calls = _dispatch_recorder(monkeypatch)
    session = session_factory()
    tenant = cli.ensure_tenant(session, "kunde-a", "netbox", "active_directory")
    source = cli.ensure_source(session, "fw-01", "fortigate", "https://fw-01")
    session.flush()

    run = cli.queue_collect(session, "fortigate", tenant, source, "external")

    assert calls == []
    persisted = session.get(CollectorRun, run.id)
    assert persisted is not None and persisted.execution == "external"
    session.close()


def test_cli_collect_external_end_to_end(session_factory, monkeypatch, capsys):
    calls = _dispatch_recorder(monkeypatch)
    session = session_factory()
    cli.ensure_tenant(session, "kunde-a", "netbox", "active_directory")
    cli.ensure_source(session, "fw-01", "fortigate", "https://fw-01")
    session.commit()
    args = cli.build_parser().parse_args(
        ["collect", "--tenant", "kunde-a", "--source", "fw-01", "--execution", "external"]
    )

    assert cli._cmd_collect(session, args) == 0

    assert calls == []
    assert "Externer Lauf" in capsys.readouterr().out
    run = session.query(CollectorRun).one()
    assert run.execution == "external"
    session.close()
