"""Prüfauftrag aus der Oberfläche am Agenten (1.7.0): Name, Verbindung, Anmeldung read-only
prüfen und das Ergebnis melden - Anwender 2026-11-11: „beim Collector-Agent kann ich die
Verbindung nicht prüfen“."""

import socket

import pytest

from collectors import run
from collectors.common import agent as agent_api
from collectors.common import reach
from collectors.common.dto import Target
from collectors.common.i18n import L, use


@pytest.fixture
def listener():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    yield srv.getsockname()[1]
    srv.close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # danach lauscht dort niemand: Verbindung abgelehnt
    return port


def test_name_verbindung_anmeldung(listener):
    t = Target(name="fw", platform="fortigate", base_url=f"https://127.0.0.1:{listener}")
    seen = []
    ok, steps, hint = reach.probe(t, lambda target: seen.append(target) or (True, "erreichbar"))
    assert ok and hint is None and seen == [t]
    assert [(s["step"], s["ok"]) for s in steps] == [("dns", True), ("tcp", True), ("login", True)]
    assert f"127.0.0.1:{listener} offen" in steps[1]["detail"]

    ok, steps, hint = reach.probe(t, lambda target: (False, "HTTP 401 - Token abgelehnt"))
    assert not ok and steps[-1] == {"step": "login", "ok": False,
                                    "detail": "HTTP 401 - Token abgelehnt"}  # fmt: skip


def test_ohne_verbindung_keine_anmeldung():
    t = Target(name="fw", platform="fortigate", base_url=f"https://127.0.0.1:{_free_port()}")
    ok, steps, hint = reach.probe(t, lambda target: pytest.fail("Anmeldung ohne Verbindung"))
    assert not ok and [s["step"] for s in steps] == ["dns", "tcp"]
    assert "Verbindung abgelehnt" in steps[1]["detail"] and "Firewall zwischen Agent" in hint


def test_name_unbekannt_und_ohne_adresse(monkeypatch):
    def fail(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(reach.socket, "getaddrinfo", fail)
    ok, steps, hint = reach.probe(Target(base_url="https://fw.example.ch"), None)
    assert not ok and steps[0]["step"] == "dns" and "nicht auflösbar" in steps[0]["detail"]
    assert "Station des Agenten" in hint
    ok, steps, hint = reach.probe(Target(base_url=""), None)
    assert not ok and steps == [] and "Keine gültige Adresse" in hint


def test_snmp_ohne_tcp_und_unbekannte_plattform(monkeypatch):
    monkeypatch.setattr(reach.socket, "getaddrinfo",
                        lambda *a, **k: [(0, 0, 0, "", ("10.0.7.1", 161))])  # fmt: skip
    t = Target(name="sw", platform="cisco_switch", base_url="snmp://sw.example.ch")
    ok, steps, _ = reach.probe(t, lambda target: (True, "sysName sw-01"))
    assert ok and [s["step"] for s in steps] == ["dns", "login"]
    ok, steps, hint = reach.probe(t, None)
    assert not ok and "kennt dieser Agent nicht" in hint


def test_next_run_liefert_pruefauftraege_einmal(monkeypatch):
    class Resp:
        def json(self):
            return {"run": None, "checks": [{"check_id": 7, "target": {"name": "fw"}}, "kaputt"]}

    monkeypatch.setattr(agent_api.http, "get_json", lambda *a, **k: Resp())
    assert agent_api.next_run() is None
    assert agent_api.take_checks() == [{"check_id": 7, "target": {"name": "fw"}}]
    assert agent_api.take_checks() == []


def test_poll_fuehrt_pruefauftraege_aus_und_meldet_deutsch(monkeypatch, capsys):
    """Auch bei englischer Konsole meldet der Agent deutsch (der Orchestrator speichert)."""
    target = {"name": "fw-c", "platform": "fortigate", "base_url": "https://fw-c.example.ch",
              "token": "x"}  # fmt: skip
    jobs = [{"check_id": 3, "target": target}]
    monkeypatch.setattr(run.agent_api, "next_run", lambda platform=None: None)
    monkeypatch.setattr(run.agent_api, "take_checks", lambda: jobs.copy() if jobs else [])
    sent = []

    def report(cid, ok, steps, hint):
        sent.append((cid, ok, steps, hint))

    monkeypatch.setattr(run.agent_api, "report_check", report)

    def probe(target, login):
        assert target.name == "fw-c" and login is run.CHECKERS["fortigate"]
        jobs.clear()
        return False, [{"step": "login", "ok": False,
                        "detail": L("Anmeldung abgelehnt", "login rejected")}], None  # fmt: skip

    monkeypatch.setattr(run.reach, "probe", probe)
    with use("en"):
        assert run._poll_once(None) == (True, 0)
    assert sent == [(3, False, [{"step": "login", "ok": False, "detail": "Anmeldung abgelehnt"}],
                     None)]  # fmt: skip
    assert "fw-c (fortigate) check (web UI): ERROR" in capsys.readouterr().out
