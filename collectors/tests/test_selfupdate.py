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
    # Dateien, die nach pip fehlen (je Pruefung eine Liste; leer = vollstaendig)
    state["missing"] = []
    monkeypatch.setattr(
        su, "_missing_files", lambda wheels: state["missing"].pop(0) if state["missing"] else []
    )
    state["dir"] = tmp_path
    return state


def test_neue_version_wird_installiert_und_aufruf_neu_ausgefuehrt(agent):
    assert su.maybe_update(["--poll"]) == 0
    assert (agent["dir"] / "agent-version.txt").read_text() == "v2"
    assert (agent["dir"] / "run-agent.ps1").read_bytes() == WRAPPER
    assert not (agent["dir"] / "run-agent.ps1.new").exists()
    first, second, check = agent["pip"]
    assert "--force-reinstall" in first and "--no-deps" in first
    assert "--find-links" in second
    assert check[-1] == "import collectors.run, sot_gateway"  # Startpruefung
    assert agent["rerun"][-3:] == ["collectors.run", "--poll", "--no-update"]
    # Agent meldet seine Version mit
    assert agent["requests"][0][1] == {
        "X-Agent-Token": "tok",
        "X-Agent-Version": "v1",
        "X-Agent-Update": "ok",  # Ergebnis der letzten Pruefung (Agentenliste, 0.11.4)
    }


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


def _signed(manifest: dict, key=None) -> tuple[str, dict]:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = key or Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    sig = base64.b64encode(key.sign(su.signed_message(manifest))).decode()
    return base64.b64encode(pub).decode(), {**manifest, "signature": sig}


def test_signierte_nachricht_wie_im_orchestrator():
    info = {"version": "0.5.0+ab", "files": [_file("run-agent.ps1", b"x"), _file("a.whl", b"y")]}
    lines = su.signed_message(info).decode().splitlines()
    assert lines[:2] == ["automato-agent-update/v1", "0.5.0+ab"]
    assert [x.split(":")[0] for x in lines[2:]] == ["a.whl", "run-agent.ps1"]  # sortiert


def test_mit_schluessel_nur_signierte_updates(agent, monkeypatch, capsys):
    pub, signed = _signed(_manifest())
    settings = Settings(agent_token="tok", agent_signing_pubkey=pub)  # noqa: S106
    monkeypatch.setattr(su, "get_settings", lambda: settings)

    agent["manifest"] = _manifest()  # ohne Signatur
    assert su.maybe_update(["--poll"]) is None and agent["pip"] == []
    assert "Signatur fehlt oder passt nicht" in capsys.readouterr().err
    # der Grund geht mit jedem Aufruf an den Orchestrator (Agentenliste, Dev-Befund 0.11.4)
    assert su.agent_headers()["X-Agent-Update"].startswith("Update v2 nicht installiert: Signatur")

    _, foreign = _signed(_manifest())  # anderer Schluessel
    agent["manifest"] = foreign
    assert su.maybe_update(["--poll"]) is None and agent["pip"] == []

    tampered = {**signed, "files": _manifest(wheel=b"boese")["files"]}  # Datei getauscht
    agent["manifest"] = tampered
    assert su.maybe_update(["--poll"]) is None and agent["pip"] == []

    agent["manifest"] = signed
    assert su.maybe_update(["--poll"]) == 0
    assert (agent["dir"] / "agent-version.txt").read_text() == "v2"


def test_update_status_im_header(agent, monkeypatch):
    agent["manifest"] = _manifest(version="v1")  # gleiche Version: nichts zu tun
    assert su.maybe_update(["--poll"]) is None
    assert su.agent_headers()["X-Agent-Update"] == "ok"

    def offline(url, params, headers):
        raise httpx.ConnectError("weg")

    monkeypatch.setattr(su.http, "get_json", offline)
    assert su.maybe_update(["--poll"]) is None
    assert su.agent_headers()["X-Agent-Update"].startswith("Update-Pruefung nicht moeglich")
    monkeypatch.setattr(
        su,
        "get_settings",
        lambda: Settings(agent_token="tok", agent_auto_update=False),  # noqa: S106
    )
    assert su.maybe_update(["--poll"]) is None
    assert "abgeschaltet" in su.agent_headers()["X-Agent-Update"]


def test_fehlende_datei_wird_einmal_nachinstalliert(agent):
    """Dev-Befund 0.16.0: nach dem Update fehlte collectors/common/selfupdate.py (zweiter
    Agent-Prozess bzw. Virenschutz) - der Agent haette danach nicht mehr gestartet."""
    agent["missing"] = [["collectors/common/selfupdate.py"], []]
    assert su.maybe_update(["--poll"]) == 0
    reinstall = [c for c in agent["pip"] if "--force-reinstall" in c]
    assert len(reinstall) == 2  # einmal wiederholt
    assert (agent["dir"] / "agent-version.txt").read_text() == "v2"
    kept = sorted(p.name for p in (agent["dir"] / "packages").iterdir())
    assert kept == ["collectors-0.1.0-py3-none-any.whl"]  # zuletzt gute Pakete
    assert not (agent["dir"] / "update.lock").exists()


def test_bleibt_es_unvollstaendig_zurueck_auf_die_guten_pakete(agent, capsys):
    good = agent["dir"] / "packages"
    good.mkdir()
    (good / "collectors-0.0.9-py3-none-any.whl").write_bytes(b"gut")
    agent["missing"] = [["collectors/common/selfupdate.py"], ["collectors/common/selfupdate.py"]]
    assert su.maybe_update(["--poll"]) is None  # mit der bisherigen Version weiter
    assert (agent["dir"] / "agent-version.txt").read_text() == "v1"
    restore = agent["pip"][-1]
    assert restore[-1].endswith("collectors-0.0.9-py3-none-any.whl") and "--no-deps" in restore
    assert not any("import collectors.run" in c[-1] for c in agent["pip"])  # keine Startpruefung
    note = su.note()
    assert "fehlen 1 Datei(en), z. B. collectors/common/selfupdate.py" in note
    assert "zuletzt gute Pakete wieder installiert" in note
    assert [p.name for p in good.iterdir()] == ["collectors-0.0.9-py3-none-any.whl"]
    assert not (agent["dir"] / "update.lock").exists()


def test_nur_ein_update_gleichzeitig(agent):
    import os
    import time

    lock = agent["dir"] / "update.lock"
    lock.write_text("4711")  # z. B. die geplante Aufgabe installiert gerade
    assert su.maybe_update(["--poll"]) is None
    assert agent["pip"] == [] and "anderer Agent-Prozess" in su.note()
    old = time.time() - su.LOCK_STALE - 60  # abgebrochenes Update: Sperre wird uebernommen
    os.utime(lock, (old, old))
    assert su.maybe_update(["--poll"]) == 0 and not lock.exists()


def test_dateipruefung_gegen_das_wheel(tmp_path, monkeypatch):
    import zipfile

    wheel = tmp_path / "collectors-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        for name in ("collectors/__init__.py", "collectors/common/selfupdate.py",
                     "collectors-0.1.0.dist-info/RECORD"):  # fmt: skip
            z.writestr(name, "x")
    site = tmp_path / "site-packages"
    (site / "collectors").mkdir(parents=True)
    (site / "collectors" / "__init__.py").write_text("x")
    monkeypatch.setattr(su, "_site_packages", lambda: site)
    assert su._missing_files([str(wheel)]) == ["collectors/common/selfupdate.py"]
    (site / "collectors" / "common").mkdir()
    (site / "collectors" / "common" / "selfupdate.py").write_text("x")
    assert su._missing_files([str(wheel)]) == []
