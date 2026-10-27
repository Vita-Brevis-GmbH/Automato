"""Konsole des Agenten auf Englisch (0.90.0, ADR 0028 Schritt 4): `AUTOMATO_LANG=en` in
agent.env bzw. der Umgebung. Was an den Orchestrator geht (Ergebnis, Meldung, Grund der
Update-Pruefung), bleibt deutsch - der Orchestrator speichert es und uebersetzt beim Abruf."""

import pytest
from test_selfupdate import _manifest, agent  # noqa: F401 - Fixture

from collectors.common import i18n
from collectors.common import selfupdate as su
from collectors.common.dto import Target
from collectors.config import Settings

AGENT = Settings(agent_token="agent-tok", fortigate_url="", fortigate_token="")  # noqa: S106
FW = {"source_system_id": 7, "name": "fw-01", "platform": "fortigate"}  # ohne Zugangsdaten


@pytest.fixture
def english(monkeypatch):
    monkeypatch.setenv("AUTOMATO_LANG", "en")
    monkeypatch.setattr(su, "_note", "")  # Grund der Update-Pruefung nicht in andere Tests tragen


def test_sprache_aus_automato_lang(monkeypatch):
    monkeypatch.delenv("AUTOMATO_LANG", raising=False)
    assert i18n.console() == "de" and i18n.L("ja", "yes") == "ja"
    for value, code in (("en", "en"), ("EN-gb", "en"), ("en_US", "en"), ("fr", "de"), ("", "de")):
        monkeypatch.setenv("AUTOMATO_LANG", value)
        assert i18n.console() == code
    monkeypatch.setenv("AUTOMATO_LANG", "en")
    assert i18n.L("ja", "yes") == "yes"
    with i18n.german():
        assert i18n.L("ja", "yes") == "ja"
    assert i18n.both(lambda: i18n.L("ja", "yes")) == ("ja", "yes")
    failure = i18n.Failure(lambda: i18n.L("kaputt", "broken"))
    assert str(failure) == "broken"
    with i18n.german():
        assert str(failure) == "kaputt"


def test_check_auf_englisch(english, monkeypatch, capsys):
    import collectors.fortigate.tasks as t
    import collectors.run as run

    monkeypatch.setattr(run, "get_settings", lambda: AGENT)
    monkeypatch.setattr(t, "get_settings", lambda: AGENT)
    monkeypatch.setattr(run.agent_api, "targets", lambda: [Target(**FW)])
    assert run.main(["--check", "--no-update"]) == 2
    out = capsys.readouterr().out
    assert out.startswith("Agent version: ")
    assert "fw-01 (fortigate) check: ERROR - no credentials for “fw-01” - store them at the " \
           "source system in the web UI" in out  # fmt: skip


def test_check_ohne_sprache_deutsch(monkeypatch, capsys):
    import collectors.fortigate.tasks as t
    import collectors.run as run

    monkeypatch.delenv("AUTOMATO_LANG", raising=False)
    monkeypatch.setattr(run, "get_settings", lambda: AGENT)
    monkeypatch.setattr(t, "get_settings", lambda: AGENT)
    monkeypatch.setattr(run.agent_api, "targets", lambda: [Target(**FW)])
    assert run.main(["--check", "--no-update"]) == 2
    assert "check: FEHLER - keine Zugangsdaten fuer „fw-01“" in capsys.readouterr().out


def test_poll_ohne_lauf_englisch(english, monkeypatch, capsys):
    import collectors.run as run

    monkeypatch.setattr(run.agent_api, "next_run", lambda platform=None: None)
    assert run.main(["--poll", "--no-update"]) == 0
    assert capsys.readouterr().out.strip() == "no open runs"


def _poll(monkeypatch, ack):
    """Ein Lauf ohne Zugangsdaten auf einem Agenten; `ack` = Quittung des Orchestrators."""
    import collectors.fortigate.tasks as t
    import collectors.run as run

    runs = iter([{"platform": "fortigate", "run_id": "r1", "target": FW}, None])
    monkeypatch.setattr(run.agent_api, "next_run", lambda platform=None: next(runs))
    monkeypatch.setattr(t, "get_settings", lambda: AGENT)
    sent = {}

    def submit(run_id, result, via_agent=False):
        sent.update(message=result.message, dumped=result.model_dump())
        return ack(result.message) if ack else None

    monkeypatch.setattr(t, "submit_result", submit)
    assert run.main(["--poll", "--no-update"]) == 1
    return sent


def test_lauf_meldung_deutsch_an_orchestrator_konsole_aus_quittung(english, monkeypatch, capsys):
    shown = "no credentials for “fw-01” - store them at the source system in the web UI"
    sent = _poll(monkeypatch, lambda message: {"run_id": "r1", "status": "failed",
                                               "message": message, "shown": shown})  # fmt: skip
    # an den Orchestrator: deutsch, ohne die Anzeige der Konsole
    assert sent["message"].startswith("keine Zugangsdaten fuer „fw-01“")
    assert "shown" not in sent["dumped"]
    captured = capsys.readouterr()
    assert "fortigate run r1 (fw-01): failed" in captured.out
    assert shown in captured.err and "keine Zugangsdaten" not in captured.err


def test_alter_orchestrator_ohne_uebersetzung_deutsch(english, monkeypatch, capsys):
    _poll(monkeypatch, None)  # 204 ohne Quittung
    assert "keine Zugangsdaten fuer „fw-01“" in capsys.readouterr().err


def test_upload_fehler_deutsch_an_orchestrator_konsole_englisch(english, monkeypatch, capsys):
    import httpx

    import collectors.fortigate.tasks as t
    import collectors.run as run

    request = httpx.Request("POST", "http://orch:8000/internal/collector-runs/r1/result")

    def too_large(run_id, result, via_agent=False):
        raise httpx.ReadTimeout("timed out", request=request)

    sent = {}

    def short(run_id, result, via_agent=False):
        sent["message"] = result.message
        return {"run_id": run_id, "status": "failed", "message": result.message}

    monkeypatch.setattr(t, "submit_result", too_large)
    monkeypatch.setattr(run, "submit_result", short)
    assert run.main(["--platform", "fortigate", "--run-id", "r1"]) == 1
    assert sent["message"].startswith("Ergebnis-Upload fehlgeschlagen: nicht erreichbar http://")
    assert "Result upload failed: not reachable http://" in capsys.readouterr().err


def test_switches_in_threads_bleiben_im_lauf_deutsch(english, monkeypatch):
    import collectors.cisco_switch.tasks as t

    class Refused(Exception):
        pass

    Refused.__name__ = "NoValidConnectionsError"

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise Refused()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(t, "SwitchClient", Client)
    sent = {}

    def submit(run_id, result, via_agent=False):
        sent["m"] = result.message

    monkeypatch.setattr(t, "submit_result", submit)
    target = Target(name="sw", platform="cisco_switch", username="ro", password="pw",  # noqa: S106
                    read_scope="10.0.0.1 10.0.0.2")  # fmt: skip
    t.collect_once("r1", target)
    assert sent["m"] == (
        "10.0.0.1: nicht erreichbar: Verbindung zu Port 22 abgelehnt (SSH aus bzw. Weg "
        "gesperrt); 10.0.0.2: nicht erreichbar: Verbindung zu Port 22 abgelehnt (SSH aus bzw. "
        "Weg gesperrt)"
    )
    ok, msg = t.check(target)  # Konsole: englisch
    assert not ok and msg.startswith("10.0.0.1: not reachable: connection to port 22 refused")


def test_update_grund_deutsch_konsole_englisch(english, agent, monkeypatch, capsys):  # noqa: F811
    pub = "MCowBQYDK2VwAyEA" + "A" * 43 + "="  # beliebiger Schluessel: Signatur fehlt
    settings = Settings(agent_token="tok", agent_signing_pubkey=pub)  # noqa: S106
    monkeypatch.setattr(su, "get_settings", lambda: settings)
    agent["manifest"] = _manifest()
    assert su.maybe_update(["--poll"]) is None
    assert "Update v2 not installed: signature missing" in capsys.readouterr().err
    assert su.note().startswith("Update v2 nicht installiert: Signatur fehlt")  # Orchestrator
    headers = su.agent_headers()
    assert headers["X-Agent-Update"].startswith("Update v2 nicht installiert")
    assert headers["Accept-Language"] == "en"  # Quittung in der Sprache der Konsole


def test_fehlende_dateien_grund_deutsch_konsole_englisch(english, agent, capsys):  # noqa: F811
    agent["missing"] = [["collectors/run.py"], ["collectors/run.py"]]
    assert su.maybe_update(["--poll"]) is None
    err = capsys.readouterr().err
    assert "Self-update failed: after the installation 1 file(s) are missing" in err
    assert "(no last good packages for the fallback - run install.ps1 again)" in err
    assert su.note().startswith("Selbst-Update fehlgeschlagen: nach der Installation fehlen 1")
    assert "keine zuletzt guten Pakete fuer den Rueckfall" in su.note()


def test_hilfe_englisch(english, capsys):
    import collectors.run as run

    with pytest.raises(SystemExit):
        run.main(["--help"])
    out = capsys.readouterr().out
    assert "fetch the next external run from the agent queue" in out
    assert "External standalone collector" in out
