"""deploy/aufbau.sh (0.40.0): Pruefung des Aufbaus mit einem Ersatz fuer `docker compose` -
Container, Verbindungen aus dem Orchestrator-Container und die ufw-Regel zum Kopieren."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "aufbau.sh"

REPORT = {
    "status": "error",
    "problems": 1,
    "items": [
        {"key": "database", "area": "Plattform", "name": "Datenbank", "target": None,
         "status": "ok", "steps": [], "hint": None, "ufw": None},
        {"key": "source:1", "area": "Quellsysteme", "name": "nb (netbox)",
         "target": "http://host.docker.internal:8080", "status": "error",
         "steps": [{"step": "tcp", "ok": False,
                    "detail": "172.17.0.1:8080 keine Antwort nach 5 s"}],
         "hint": "Keine Antwort vom Docker-Host",
         "ufw": {"from": "172.20.0.0/16", "to": "172.17.0.1", "port": 8080,
                 "comment": "Automato -> nb", "command": "sudo ufw allow …"}},
    ],
}  # fmt: skip

pytestmark = pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("python3"), reason="bash/python3 fehlen"
)


def _run(tmp_path, *args) -> subprocess.CompletedProcess:
    stub = tmp_path / "compose"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        "  *' ps '*) printf 'orchestrator|running|healthy\\nredis|exited|\\n' ;;\n"
        f"  *check-connections*) echo '{json.dumps(REPORT, ensure_ascii=False)}'; exit 1 ;;\n"
        "esac\n"
    )
    stub.chmod(0o755)
    env = {**os.environ, "COMPOSE": str(stub), "AUTOMATO_HEALTH_URL": "http://127.0.0.1:9/x"}
    bash = shutil.which("bash") or "bash"
    return subprocess.run([bash, str(SCRIPT), *args], capture_output=True, text=True, env=env,  # noqa: S603 - Testskript des Repos
                          timeout=60, check=False)  # fmt: skip


def test_pruefen_meldet_container_und_verbindungen(tmp_path):
    out = _run(tmp_path, "pruefen")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "orchestrator (healthy)" in out.stdout
    assert "redis: exited" in out.stdout
    assert "nb (netbox)  http://host.docker.internal:8080" in out.stdout
    assert "172.17.0.1:8080 keine Antwort" in out.stdout
    assert "Es gibt Fehler" in out.stdout


def test_firewall_zeigt_die_schmale_regel_kopierbar(tmp_path):
    out = _run(tmp_path, "firewall")
    assert out.returncode == 0, out.stdout + out.stderr
    # Kommentar in Hochkommas: sonst leitet `->` in der Shell die Ausgabe um
    assert (
        "sudo ufw allow proto tcp from 172.20.0.0/16 to 172.17.0.1 port 8080 "
        "comment 'Automato -> nb'"
    ) in out.stdout
    assert "firewall --anwenden" in out.stdout
    assert _run(tmp_path, "gibtsnicht").returncode == 2
