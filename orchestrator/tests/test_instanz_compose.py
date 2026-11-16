"""Compose einer eigenen Automato-Instanz (1.16.0, ADR 0061): deploy/instanz/compose.yml zusammen
mit docker-compose.yml - keine Ports auf dem Host, kein Kunden-Portal, Anschluss gehärtet im Netz
des Front-Proxys, die Web-UI selbst nie in diesem Netz (sonst hiesse sie dort auch "web-ui"); die
Hauptinstanz bleibt unverändert, nur ihr Proxy hängt zusätzlich im Netz der Instanzen."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
NETZ = "automato-front-instanzen"


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!reset", lambda loader, node: "!reset")


def _yaml(name: str) -> dict:
    return yaml.load((REPO / name).read_text(), Loader=_Loader)  # noqa: S506 - SafeLoader + !reset


def test_zusatz_der_instanz():
    data = _yaml("deploy/instanz/compose.yml")
    dienste = data["services"]
    assert dienste["orchestrator"]["ports"] == "!reset"
    assert dienste["web-ui"]["ports"] == "!reset"
    assert dienste["orchestrator"]["environment"]["AUTOMATO_FRONT_STATE_DIR"] == ""
    assert "networks" not in dienste["web-ui"]  # nie im gemeinsamen Netz
    a = dienste["anschluss"]
    assert a["image"] == "caddy:2-alpine" and "build" not in a and "ports" not in a
    assert a["cap_drop"] == ["ALL"] and a["read_only"] is True
    assert a["security_opt"] == ["no-new-privileges:true"] and "cap_add" not in a
    assert set(a["tmpfs"]) == {"/tmp", "/data", "/config"}  # noqa: S108 - tmpfs im Container
    assert all(v.endswith(":ro") for v in a["volumes"])
    alias = a["networks"]["front-instanzen"]["aliases"]
    assert len(alias) == 1 and alias[0].startswith("automato-${AUTOMATO_INSTANZ:?")
    assert alias[0].endswith("}-web-ui")
    assert data["networks"]["front-instanzen"] == {"name": NETZ, "external": True}
    caddy = (REPO / "deploy/instanz/Caddyfile").read_text()
    assert "admin off" in caddy and "reverse_proxy web-ui:5173" in caddy


def test_hauptinstanz_besitzt_das_netz():
    data = _yaml("docker-compose.yml")
    assert data["networks"]["front-instanzen"] == {"name": NETZ, "internal": True}
    assert "front-instanzen" in data["services"]["proxy"]["networks"]
    # nur der Proxy - kein anderer Dienst der Hauptinstanz hängt im Netz der Instanzen
    for name, dienst in data["services"].items():
        if name != "proxy":
            assert "front-instanzen" not in (dienst.get("networks") or []), name


def _ohne_compose() -> bool:
    if not shutil.which("docker"):
        return True
    out = subprocess.run(["docker", "compose", "version"], capture_output=True, check=False)  # noqa: S603, S607
    return out.returncode != 0


@pytest.mark.skipif(_ohne_compose(), reason="docker compose fehlt")
def test_compose_config_der_instanz(tmp_path):
    env = tmp_path / "instanz.env"
    env.write_text("COMPOSE_PROJECT_NAME=automato-kunde-a\nAUTOMATO_INSTANZ=kunde-a\n"
                   "COMPOSE_PROFILES=core,collectors\n")  # fmt: skip
    out = subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", "docker-compose.yml", "-f", "deploy/instanz/compose.yml",  # noqa: S607
         "--env-file", str(env), "config", "--format", "json"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert out.returncode == 0, out.stderr
    cfg = json.loads(out.stdout)
    assert cfg["name"] == "automato-kunde-a"
    dienste = cfg["services"]
    assert set(dienste) == {"postgres", "redis", "migrate", "orchestrator", "web-ui", "collector",
                            "anschluss"}  # fmt: skip
    for name, dienst in dienste.items():
        assert not dienst.get("ports"), name  # keine Ports auf dem Host
        assert not dienst.get("container_name"), name  # keine festen Namen
    assert set(dienste["web-ui"]["networks"]) == {"default"}
    assert dienste["anschluss"]["networks"]["front-instanzen"]["aliases"] == [
        "automato-kunde-a-web-ui"
    ]
    assert cfg["networks"]["front-instanzen"]["external"] is True
    # ohne AUTOMATO_INSTANZ lehnt Compose ab (nie eine Instanz ohne Namen)
    env.write_text("COMPOSE_PROJECT_NAME=automato-x\n")
    out = subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", "docker-compose.yml", "-f", "deploy/instanz/compose.yml",  # noqa: S607
         "--env-file", str(env), "--profile", "core", "config", "--quiet"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert out.returncode != 0 and "AUTOMATO_INSTANZ fehlt" in out.stderr
