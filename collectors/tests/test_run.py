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
    import httpx

    import collectors.common.selfupdate as su
    import collectors.common.upload as up
    from collectors.common.dto import CollectorResult

    calls = []
    monkeypatch.setattr(up, "get_settings", lambda: AGENT)
    monkeypatch.setattr(su, "get_settings", lambda: AGENT)

    def post(url, json, headers):
        calls.append(headers)
        return httpx.Response(204)

    monkeypatch.setattr(up.http, "post_json", post)
    up.submit_result("r1", CollectorResult(status="completed"), via_agent=True)
    up.submit_result("r1", CollectorResult(status="completed"))
    assert calls == [{"X-Agent-Token": "agent-tok"}, {"X-Internal-Token": "dev-only-token"}]


def test_quittung_failed_vom_orchestrator_wird_uebernommen(monkeypatch, capsys):
    import collectors.fortigate.tasks as t

    ack = {"run_id": "abc", "status": "failed", "message": "Einspielen fehlgeschlagen: X"}
    monkeypatch.setattr(t, "submit_result", lambda run_id, result, via_agent=False: ack)
    assert main(["--platform", "fortigate", "--run-id", "abc"]) == 1
    captured = capsys.readouterr()
    assert "fortigate run abc: failed" in captured.out
    assert "Einspielen fehlgeschlagen" in captured.err


def test_http_fehler_beim_upload_ohne_traceback(monkeypatch, capsys):
    import httpx

    import collectors.fortigate.tasks as t

    def upload(run_id, result, via_agent=False):
        request = httpx.Request("POST", f"http://orch:8000/internal/collector-runs/{run_id}/result")
        response = httpx.Response(500, request=request, text="Internal Server Error")
        raise httpx.HTTPStatusError("500", request=request, response=response)

    monkeypatch.setattr(t, "submit_result", upload)
    import collectors.run as run

    monkeypatch.setattr(run, "submit_result", upload)  # auch die kurze Fehlermeldung scheitert
    assert main(["--platform", "fortigate", "--run-id", "abc"]) == 1
    err = capsys.readouterr().err
    assert "Orchestrator-Aufruf fehlgeschlagen: HTTP 500" in err and "Traceback" not in err
    assert "docker compose logs orchestrator" in err


def test_upload_liefert_quittung(monkeypatch):
    import httpx

    import collectors.common.upload as up
    from collectors.common.dto import CollectorResult

    monkeypatch.setattr(up, "get_settings", lambda: AGENT)
    ok = httpx.Response(200, json={"run_id": "r1", "status": "completed", "message": None})
    monkeypatch.setattr(up.http, "post_json", lambda url, json, headers: ok)
    assert up.submit_result("r1", CollectorResult(status="completed"))["status"] == "completed"
    monkeypatch.setattr(up.http, "post_json", lambda url, json, headers: httpx.Response(204))
    assert up.submit_result("r1", CollectorResult(status="completed")) is None  # alter Server


def test_poll_unbekannte_plattform_meldet_failed_statt_absturz(monkeypatch):
    """Orchestrator neuer als der Agent (z. B. neue Plattform): Lauf sauber abschliessen."""
    import collectors.run as run

    runs = iter([{"platform": "proxmox", "run_id": "r9", "target": None}, None])
    monkeypatch.setattr(run.agent_api, "next_run", lambda platform=None: next(runs))
    sent = {}

    def submit(run_id, result, via_agent=False):
        sent.update(run_id=run_id, status=result.status, message=result.message)
        return {"run_id": run_id, "status": "failed"}

    monkeypatch.setattr(run, "submit_result", submit)
    assert run.main(["--poll"]) == 1
    assert sent["run_id"] == "r9" and sent["status"] == "failed"
    assert "Agent-Paket" in sent["message"]


def _fortigate(monkeypatch, accepts_any_token: bool):
    """FortiGateClient mit MockTransport: echtes Token ok, ungueltiges je nach Geraet."""
    import httpx

    import collectors.fortigate.tasks as t

    seen = []

    def handler(request):
        token = request.headers.get("Authorization", "")
        seen.append(token)
        if "automato-r16-invalid-token" in token and not accepts_any_token:
            return httpx.Response(401, json={"status": "error"})
        return httpx.Response(200, json={"results": {"hostname": "fw"}})

    from collectors.fortigate.client import FortiGateClient as real

    def client(url, token, verify_tls=True):
        return real(
            url,
            token,
            verify_tls=verify_tls,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(t, "FortiGateClient", client)
    return seen


def test_strict_check_lehnt_ungueltiges_token_ab(monkeypatch, capsys):
    import collectors.run as run

    target = Target(source_system_id=1, name="fw-01", base_url="https://fw", token="echt")  # noqa: S106
    monkeypatch.setattr(run, "get_settings", lambda: AGENT)
    monkeypatch.setattr(run.agent_api, "targets", lambda: [target])

    seen = _fortigate(monkeypatch, accepts_any_token=False)
    assert run.main(["--check", "--strict", "--no-update"]) == 0
    assert (
        "fw-01 (fortigate) strict: OK - ungueltiges Token wird abgelehnt" in capsys.readouterr().out
    )
    assert len(seen) == 2  # genau ein Versuch mit ungueltigem Token

    _fortigate(monkeypatch, accepts_any_token=True)  # Trusted-Host-only (R16)
    assert run.main(["--check", "--strict", "--no-update"]) == 2
    assert "UNGUELTIGEM Token" in capsys.readouterr().out

    # ohne --strict kein Negativtest
    seen = _fortigate(monkeypatch, accepts_any_token=True)
    assert run.main(["--check", "--no-update"]) == 0 and len(seen) == 1


def test_strict_nur_mit_check():
    import pytest

    with pytest.raises(SystemExit):
        main(["--poll", "--strict", "--no-update"])


def test_watch_holt_neue_laeufe_ohne_neustart(monkeypatch, capsys):
    """--watch: alle N Sekunden fragen, ein spaeter angelegter Lauf wird sofort abgeholt;
    ein kurz nicht erreichbarer Orchestrator beendet die Schleife nicht."""
    import httpx

    import collectors.run as run

    now = [0.0]
    calls: list[float] = []
    queue = {20.0: [{"platform": "fortigate", "run_id": "r-spaet", "target": None}]}

    def next_run(platform=None):
        calls.append(now[0])
        if now[0] == 40.0:
            raise httpx.ConnectError("weg")
        pending = queue.get(now[0], [])
        return pending.pop(0) if pending else None

    executed = []
    monkeypatch.setattr(run.agent_api, "next_run", next_run)
    monkeypatch.setattr(run, "_execute", lambda p, rid, t, via_agent: executed.append(rid) or True)

    def sleep(s):
        now[0] += s

    rc = run._watch(None, 20, 1, clock=lambda: now[0], sleep=sleep)
    # Abfragen bei 0, 20 (Lauf + Nachfrage), 40 (Fehler), 60 (wieder erreichbar), dann Ende
    assert executed == ["r-spaet"]
    assert calls == [0.0, 20.0, 20.0, 40.0, 60.0]
    out = capsys.readouterr()
    assert "alle 20 s" in out.out and "Orchestrator-Aufruf fehlgeschlagen" in out.err
    assert rc == 0  # Fehler behoben -> kein Fehlerstatus fuer die Aufgabenplanung


def test_watch_nur_mit_poll_und_mindestintervall(monkeypatch):
    import pytest

    import collectors.run as run

    with pytest.raises(SystemExit):
        main(["--watch", "20", "--no-update"])
    now = [0.0]
    waits: list[float] = []
    monkeypatch.setattr(run.agent_api, "next_run", lambda platform=None: None)

    def sleep(s):
        waits.append(s)
        now[0] += s

    assert run._watch(None, 1, 0.5, clock=lambda: now[0], sleep=sleep) == 0
    assert set(waits) == {run.MIN_WATCH_SECONDS}


def test_ergebnis_upload_scheitert_lauf_wird_trotzdem_beendet(monkeypatch):
    """Dev-Befund 0.11.3: scheiterte der Ergebnis-Upload (z. B. HTTP 413, Zeitueberschreitung),
    blieb der Lauf offen und blockierte "Alle einlesen". Jetzt meldet der Agent kurz failed."""
    import httpx

    import collectors.run as run

    runs = iter([{"platform": "cisco_aci", "run_id": "r9", "target": None}])
    monkeypatch.setattr(run.agent_api, "next_run", lambda platform=None: next(runs, None))
    request = httpx.Request("POST", "https://automato.example.ch/internal/collector-runs/r9/result")

    def too_big(run_id, target, via_agent=False):
        raise httpx.HTTPStatusError(
            "too large", request=request, response=httpx.Response(413, request=request)
        )

    monkeypatch.setitem(run.RUNNERS, "cisco_aci", too_big)
    sent = {}

    def submit(run_id, result, via_agent=False):
        sent.update(run_id=run_id, status=result.status, message=result.message)
        return {"run_id": run_id, "status": "failed", "message": result.message}

    monkeypatch.setattr(run, "submit_result", submit)
    assert run.main(["--poll"]) == 1
    assert sent["run_id"] == "r9" and sent["status"] == "failed"
    assert sent["message"].startswith("Ergebnis-Upload fehlgeschlagen: HTTP 413")


def test_watch_prueft_periodisch_auf_updates(monkeypatch):
    """Dev-Befund 0.11.4: im Wach-Modus (14 min) pruefte der Agent nur beim Start auf eine neue
    Version. Jetzt alle 5 min; die neue Version uebernimmt die restliche Wachzeit."""
    import collectors.run as run

    now = [0.0]
    monkeypatch.setattr(run.agent_api, "next_run", lambda platform=None: None)
    asked: list[float] = []

    def update(minutes_left):
        asked.append(round(minutes_left, 1))
        return 0 if len(asked) == 2 else None  # beim zweiten Mal gibt es eine neue Version

    def sleep(s):
        now[0] += s

    assert run._watch(None, 60, 14, clock=lambda: now[0], sleep=sleep, update=update) == 0
    assert asked == [9.0, 4.0]  # nach 5 und 10 min, danach uebernimmt die neue Version
    assert now[0] == 600.0

    rest = run._updater(argparse_ns(watch=20, platform="fortigate"))
    called = {}
    monkeypatch.setattr(run.selfupdate, "maybe_update", lambda argv: called.update(argv=argv))
    rest(3.25)
    assert called["argv"] == ["--poll", "--watch", "20", "--for", "3.2", "--platform", "fortigate"]


def argparse_ns(**kw):
    import argparse

    return argparse.Namespace(**kw)


def test_agent_log_datei(tmp_path, monkeypatch):
    import io
    import os
    import sys

    from collectors.common import agentlog

    old = tmp_path / "logs" / "agent-2020-01-01.log"
    old.parent.mkdir()
    old.write_text("alt")
    os.utime(old, (0, 0))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    path = agentlog.setup(tmp_path)
    print("fortigate run r1 (fw-01): failed")
    print("FortiGate antwortet auf firewall/policy nicht mit JSON", file=sys.stderr)
    sys.stdout.flush()
    text = path.read_text(encoding="utf-8")
    assert "INFO  fortigate run r1 (fw-01): failed" in text
    assert "FEHLER FortiGate antwortet" in text
    assert out.getvalue().startswith("fortigate run r1")  # Konsole bekommt es weiterhin
    assert not old.exists()  # aelter als 14 Tage
    assert agentlog.setup(None) is None
