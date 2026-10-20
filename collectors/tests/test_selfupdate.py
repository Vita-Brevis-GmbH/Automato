"""Selbst-Update des installierten Agenten (ADR 0011)."""

import hashlib
import subprocess

import httpx
import pytest

from collectors.common import selfupdate as su
from collectors.config import Settings

WHEEL = b"neues-wheel"
WRAPPER = b"# neuer Wrapper"


def _file(name: str, data: bytes) -> dict:
    return {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _manifest(version="v2", enabled=True, wheel=WHEEL):
    files = [_file("collectors-0.1.0-py3-none-any.whl", wheel), _file("run-agent.ps1", WRAPPER)]
    return {"version": version, "enabled": enabled, "files": files}


@pytest.fixture
def agent(monkeypatch, tmp_path):
    """Installierter Agent in tmp_path (venv + run-agent.ps1), Orchestrator gemockt."""
    (tmp_path / "run-agent.ps1").write_text("# alt")
    (tmp_path / "agent-version.txt").write_text("v1")
    monkeypatch.setattr(su, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(su, "get_settings", lambda: Settings(agent_token="tok"))  # noqa: S106
    state = {"manifest": _manifest(), "requests": [], "pip": [], "rerun": None}

    def get_json(url, params, headers):
        state["requests"].append((url, headers))
        if url.endswith("/internal/agent/update"):
            return httpx.Response(200, json=state["manifest"])
        name = url.rsplit("/", 1)[-1]
        return httpx.Response(200, content=WRAPPER if name == "run-agent.ps1" else WHEEL)

    def run(cmd, check=False):
        state["rerun"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(su.http, "get_json", get_json)
    monkeypatch.setattr(su, "_pip", lambda cmd, what="pip": state["pip"].append(cmd))
    monkeypatch.setattr(su.subprocess, "run", run)
    state["dir"] = tmp_path
    return state


def test_neue_version_wird_installiert_und_aufruf_neu_ausgefuehrt(agent):
    assert su.maybe_update(["--poll"]) == 0
    assert (agent["dir"] / "agent-version.txt").read_text() == "v2"
    assert (agent["dir"] / "run-agent.ps1").read_bytes() == WRAPPER
    first, second, check = agent["pip"]
    assert "--force-reinstall" in first and "--no-deps" in first
    assert "--find-links" in second
    assert check[-1] == "import collectors.run, sot_gateway"  # Startpruefung
    assert agent["rerun"][-3:] == ["collectors.run", "--poll", "--no-update"]
    # Agent meldet seine Version mit
    assert agent["requests"][0][1] == {"X-Agent-Token": "tok", "X-Agent-Version": "v1"}


def test_gleiche_version_oder_gesperrt_kein_update(agent):
    agent["manifest"] = _manifest(version="v1")
    assert su.maybe_update(["--poll"]) is None
    agent["manifest"] = _manifest(enabled=False)
    assert su.maybe_update(["--poll"]) is None
    assert agent["pip"] == [] and len(agent["requests"]) == 2  # nur Manifest abgefragt


def test_falsche_pruefsumme_verwirft_update(agent, capsys):
    agent["manifest"] = _manifest(wheel=b"etwas-anderes")
    assert su.maybe_update(["--poll"]) is None
    assert agent["pip"] == [] and (agent["dir"] / "agent-version.txt").read_text() == "v1"
    assert "Pruefsumme" in capsys.readouterr().err


def test_auf_der_station_abschaltbar(agent, monkeypatch):
    off = Settings(agent_token="tok", agent_auto_update=False)  # noqa: S106
    monkeypatch.setattr(su, "get_settings", lambda: off)
    assert su.maybe_update(["--poll"]) is None and agent["requests"] == []


def test_ohne_installation_kein_update(monkeypatch):
    monkeypatch.setattr(su, "get_settings", lambda: Settings(agent_token="tok"))  # noqa: S106
    monkeypatch.setattr(su, "install_dir", lambda: None)  # z. B. Repo/Dev
    assert su.maybe_update(["--poll"]) is None


def test_fehler_bei_pip_laeuft_mit_alter_version_weiter(agent, monkeypatch):
    def fail(cmd, what="pip"):
        raise RuntimeError("pip fehlgeschlagen: Zugriff verweigert")

    monkeypatch.setattr(su, "_pip", fail)
    assert su.maybe_update(["--poll"]) is None
    assert (agent["dir"] / "agent-version.txt").read_text() == "v1"


def test_unvollstaendige_installation_wird_bei_gleicher_version_repariert(agent, capsys):
    agent["manifest"] = _manifest(version="v1")  # gleich wie installiert
    assert su.maybe_update(["--check"]) is None  # ohne Not kein Update
    assert su.maybe_update(["--check"], force=True) == 0  # z. B. sot_gateway fehlt
    assert "unvollstaendig" in capsys.readouterr().out and len(agent["pip"]) == 3


def test_versionsanzeige():
    assert su.describe("0.2.0+ab12cd34") == "0.2.0 (Build ab12cd34)"
    assert su.describe("a98269c39daa0037") == "a98269c39daa0037"
    assert su.describe("") == "unbekannt"
