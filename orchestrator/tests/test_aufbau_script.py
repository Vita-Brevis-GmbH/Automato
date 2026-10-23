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


def _stub(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)
    return path


def _run(tmp_path, *args, **env_extra) -> subprocess.CompletedProcess:
    stub = _stub(
        tmp_path / "compose",
        'case "$*" in\n'
        "  *' ps '*) printf 'orchestrator|running|healthy|a-orch-1\\n"
        "redis|exited||a-redis-1\\n' ;;\n"
        f"  *check-connections*) echo '{json.dumps(REPORT, ensure_ascii=False)}'; exit 1 ;;\n"
        "esac\n",
    )
    env = {**os.environ, "COMPOSE": str(stub), "AUTOMATO_HEALTH_URL": "http://127.0.0.1:9/x",
           "AUTOMATO_NETBOX_DIR": str(tmp_path / "keine-netbox"), **env_extra}  # fmt: skip
    bash = shutil.which("bash") or "bash"
    return subprocess.run([bash, str(SCRIPT), *args], capture_output=True, text=True, env=env,  # noqa: S603 - Testskript des Repos
                          timeout=60, check=False)  # fmt: skip


def _netbox(tmp_path) -> dict:
    """Lokale NetBox wie nach dem Befund 0.40.0: Container aus, ohne Neustart-Regel."""
    nb = tmp_path / "netbox-docker"
    nb.mkdir()
    (nb / "docker-compose.override.yml").write_text(
        'services:\n  netbox:\n    ports:\n      - "59999:8080"\n'
    )
    compose = _stub(
        tmp_path / "nb-compose",
        'case "$*" in\n'
        "  'ps -aq') printf 'id1\\nid2\\n' ;;\n"
        "  *ps*) printf 'netbox|exited||nb-1\\nnetbox-worker|created||nb-w\\n' ;;\n"
        "  'up -d') exit 0 ;;\n"
        "esac\n",
    )
    # docker: Neustart-Regel "no", update gelingt, sonst nichts
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir / "docker",
          'case "$1" in inspect) echo no ;; update) exit 0 ;; *) exit 1 ;; esac\n')  # fmt: skip
    return {"AUTOMATO_NETBOX_DIR": str(nb), "NETBOX_COMPOSE": str(compose),
            "PATH": f"{bin_dir}:{os.environ['PATH']}", "AUTOMATO_NETBOX_WAIT": "0"}  # fmt: skip


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


def test_netbox_aus_und_ohne_neustart_regel(tmp_path):
    """0.41.0: NetBox auf der VM nach einem Neustart aus - Automato lief, NetBox nicht."""
    out = _run(tmp_path, "pruefen", **_netbox(tmp_path))
    assert out.returncode == 1, out.stdout + out.stderr
    text = out.stdout
    assert "NetBox auf dieser VM" in text
    assert "FEHLER\x1b[0m  netbox: exited" in text
    assert "netbox-worker: created (Hintergrunddienst" in text
    assert "NetBox antwortet auf localhost:59999 nicht" in text
    assert "ohne Neustart-Regel: netbox netbox-worker" in text
    assert "deploy/aufbau.sh netbox-start" in text
    # auch die Automato-Container ohne Regel
    assert "ohne Neustart-Regel: orchestrator redis" in text


def test_netbox_start_setzt_neustart_regel_und_wartet(tmp_path):
    out = _run(tmp_path, "netbox-start", **_netbox(tmp_path))
    assert out.returncode == 1, out.stdout + out.stderr  # antwortet im Test nie
    text = out.stdout
    assert "Container gestartet" in text
    assert "Neustart-Regel unless-stopped gesetzt" in text
    assert "restart: unless-stopped je Dienst" in text  # Override noch ohne Regel
    assert "NetBox antwortet nicht" in text
    missing = _run(tmp_path, "netbox-start", AUTOMATO_NETBOX_DIR=str(tmp_path / "fehlt"))
    assert missing.returncode == 1 and "kein NetBox-Ordner" in missing.stdout
