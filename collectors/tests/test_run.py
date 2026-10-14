"""Standalone-Collector-Runner (externer Agent, ADR 0008)."""

from collectors.run import main


def test_runner_fortigate_submit_und_exit0(monkeypatch):
    import collectors.fortigate.tasks as t

    sent = {}
    monkeypatch.setattr(
        t, "submit_result", lambda run_id, result: sent.update(run_id=run_id, status=result.status)
    )
    assert main(["--platform", "fortigate", "--run-id", "abc"]) == 0
    assert sent == {"run_id": "abc", "status": "completed"}


def test_runner_cisco_aci_submit(monkeypatch):
    import collectors.cisco_aci.tasks as t

    sent = {}
    monkeypatch.setattr(
        t, "submit_result", lambda run_id, result: sent.update(run_id=run_id, status=result.status)
    )
    assert main(["--platform", "cisco_aci", "--run-id", "xyz"]) == 0
    assert sent["run_id"] == "xyz" and sent["status"] == "completed"


def test_runner_failed_exit1(monkeypatch):
    import collectors.fortigate.tasks as t

    monkeypatch.setattr(t, "gather", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(t, "submit_result", lambda run_id, result: None)
    assert main(["--platform", "fortigate", "--run-id", "abc"]) == 1


def test_runner_poll_holt_und_fuehrt_aus(monkeypatch):
    import collectors.fortigate.tasks as t
    import collectors.run as run

    monkeypatch.setattr(
        run.agent_api, "next_run", lambda platform=None: {"platform": "fortigate", "run_id": "r1"}
    )
    sent = {}
    monkeypatch.setattr(
        t, "submit_result", lambda run_id, result: sent.update(run_id=run_id, status=result.status)
    )
    assert run.main(["--poll"]) == 0
    assert sent == {"run_id": "r1", "status": "completed"}


def test_runner_poll_ohne_lauf(monkeypatch):
    import collectors.run as run

    monkeypatch.setattr(run.agent_api, "next_run", lambda platform=None: None)
    assert run.main(["--poll"]) == 0
