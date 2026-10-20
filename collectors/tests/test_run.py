"""Standalone-Collector-Runner (externer Agent, ADR 0008/0009)."""

from collectors.common.dto import Target
from collectors.config import Settings
from collectors.run import main

AGENT = Settings(agent_token="agent-tok", fortigate_url="", fortigate_token="")  # noqa: S106


def _capture(sent: dict):
    def submit(run_id, result, via_agent=False):
        sent.update(run_id=run_id, status=result.status, via_agent=via_agent)

    return submit


def test_runner_fortigate_submit_und_exit0(monkeypatch):
    import collectors.fortigate.tasks as t

    sent = {}
    monkeypatch.setattr(t, "submit_result", _capture(sent))
    assert main(["--platform", "fortigate", "--run-id", "abc"]) == 0
    assert sent == {"run_id": "abc", "status": "completed", "via_agent": False}


def test_runner_cisco_aci_submit(monkeypatch):
    import collectors.cisco_aci.tasks as t

    sent = {}
    monkeypatch.setattr(t, "submit_result", _capture(sent))
    assert main(["--platform", "cisco_aci", "--run-id", "xyz"]) == 0
    assert sent["run_id"] == "xyz" and sent["status"] == "completed"


def test_runner_failed_exit1(monkeypatch):
    import collectors.fortigate.tasks as t

    def boom(target=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(t, "gather", boom)
    monkeypatch.setattr(t, "submit_result", lambda run_id, result, via_agent=False: None)
    assert main(["--platform", "fortigate", "--run-id", "abc"]) == 1


def test_runner_poll_nutzt_zugangsdaten_vom_orchestrator(monkeypatch):
    import collectors.fortigate.tasks as t
    import collectors.run as run

    target = {
        "source_system_id": 7,
        "name": "fw-01",
        "platform": "fortigate",
        "base_url": "https://fw-01.kunde.lan",
        "verify_tls": False,
        "token": "fgt-geheim",
    }
    monkeypatch.setattr(
        run.agent_api,
        "next_run",
        lambda platform=None: {"platform": "fortigate", "run_id": "r1", "target": target},
    )
    used = {}

    class FakeClient:
        def __init__(self, url, token, verify_tls=True):
            used.update(url=url, token=token, verify_tls=verify_tls)

        def gather(self):
            return {k: [] for k in ("policies", "addresses", "addresses6", "groups", "groups6")} | {
                "services": [],
                "vips": [],
                "ippools": [],
            }

    monkeypatch.setattr(t, "FortiGateClient", FakeClient)
    monkeypatch.setattr(t, "get_settings", lambda: AGENT)
    sent = {}
    monkeypatch.setattr(t, "submit_result", _capture(sent))
    assert run.main(["--poll"]) == 0
    assert used == {"url": "https://fw-01.kunde.lan", "token": "fgt-geheim", "verify_tls": False}
    assert sent == {"run_id": "r1", "status": "completed", "via_agent": True}


def test_agent_ohne_zugangsdaten_liest_keine_beispieldaten(monkeypatch):
    import collectors.fortigate.tasks as t
    import collectors.run as run

    target = {"source_system_id": 7, "name": "fw-01", "platform": "fortigate"}
    monkeypatch.setattr(
        run.agent_api,
        "next_run",
        lambda platform=None: {"platform": "fortigate", "run_id": "r1", "target": target},
    )
    monkeypatch.setattr(t, "get_settings", lambda: AGENT)
    sent = {}

    def submit(run_id, result, via_agent=False):
        sent.update(status=result.status, message=result.message, rules=len(result.rules))

    monkeypatch.setattr(t, "submit_result", submit)
    assert run.main(["--poll"]) == 1
    assert sent["status"] == "failed" and sent["rules"] == 0
    assert "fw-01" in sent["message"] and "Web-UI" in sent["message"]


def test_runner_poll_ohne_lauf(monkeypatch):
    import collectors.run as run

    monkeypatch.setattr(run.agent_api, "next_run", lambda platform=None: None)
    assert run.main(["--poll"]) == 0


def test_check_ok_exit0(monkeypatch):
    import collectors.run as run

    monkeypatch.setitem(run.CHECKERS, "fortigate", lambda target=None: (True, "erreichbar"))
    assert run.main(["--check", "--platform", "fortigate"]) == 0


def test_check_fehler_exit2(monkeypatch):
    import collectors.run as run

    monkeypatch.setitem(run.CHECKERS, "fortigate", lambda target=None: (False, "nope"))
    assert run.main(["--check", "--platform", "fortigate"]) == 2


def test_check_agent_prueft_alle_zugewiesenen_geraete(monkeypatch, capsys):
    import collectors.run as run

    devices = [
        Target(source_system_id=1, name="fw-01", base_url="https://a", token="x"),  # noqa: S106
        Target(source_system_id=2, name="fw-02", base_url="https://b", token="y"),  # noqa: S106
    ]
    monkeypatch.setattr(run, "get_settings", lambda: AGENT)
    monkeypatch.setattr(run.agent_api, "targets", lambda: devices)
    checked = []

    def checker(target=None):
        checked.append(target.name)
        return (target.name == "fw-01", "ok" if target.name == "fw-01" else "401")

    monkeypatch.setitem(run.CHECKERS, "fortigate", checker)
    assert run.main(["--check"]) == 2  # fw-02 schlaegt fehl
    assert checked == ["fw-01", "fw-02"]
    out = capsys.readouterr().out
    assert "fw-01 (fortigate) check: OK" in out and "fw-02 (fortigate) check: FEHLER" in out


def test_check_agent_ohne_geraete_prueft_lokal(monkeypatch, capsys):
    import collectors.run as run

    monkeypatch.setattr(run, "get_settings", lambda: AGENT)
    monkeypatch.setattr(run.agent_api, "targets", lambda: [])
    monkeypatch.setitem(run.CHECKERS, "fortigate", lambda target=None: (False, "keine Creds"))
    assert run.main(["--check"]) == 2
    assert "keine Geraete zugewiesen" in capsys.readouterr().out


def test_fortigate_check_ohne_creds(monkeypatch):
    import collectors.fortigate.tasks as t

    monkeypatch.setattr(t, "get_settings", lambda: Settings(fortigate_url="", fortigate_token=""))
    ok, msg = t.check()
    assert ok is False and "Creds" in msg


def test_upload_header_je_rueckkanal(monkeypatch):
    import collectors.common.upload as up
    from collectors.common.dto import CollectorResult

    calls = []
    monkeypatch.setattr(up, "get_settings", lambda: AGENT)
    monkeypatch.setattr(up.http, "post_json", lambda url, json, headers: calls.append(headers))
    up.submit_result("r1", CollectorResult(status="completed"), via_agent=True)
    up.submit_result("r1", CollectorResult(status="completed"))
    assert calls == [{"X-Agent-Token": "agent-tok"}, {"X-Internal-Token": "dev-only-token"}]
