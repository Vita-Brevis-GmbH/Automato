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


def _age(tmp_path) -> dict:
    """Ersatz fuer age-keygen: -o schreibt eine Identitaet, -y gibt den Empfaenger."""
    bin_dir = tmp_path / "agebin"
    bin_dir.mkdir()
    _stub(bin_dir / "age-keygen",
          'case "$1" in -o) echo AGE-SECRET-KEY-1TEST > "$2" ;;\n'
          "  -y) echo age1testempfaenger ;; esac\n")  # fmt: skip
    _stub(bin_dir / "age", "exit 0\n")
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path),
            "AUTOMATO_ENV_FILE": str(tmp_path / "env"),
            "AUTOMATO_BACKUP_DIR": str(tmp_path / "bk")}  # fmt: skip


def test_backup_schluessel_einmal_einrichten_und_aus_env_lesen(tmp_path):
    """0.42.0: deploy/update.sh brach ohne AUTOMATO_BACKUP_AGE_RECIPIENT ab - einmal mit
    backup-schluessel in .env eintragen, danach liest jedes Skript ihn von dort."""
    env = _age(tmp_path)
    (tmp_path / "env").write_text("AUTOMATO_NETBOX_TOKEN=bleibt\n")
    out = _run(tmp_path, "pruefen", **env)
    assert "kein age-Empfänger" in out.stdout and "backup-schluessel" in out.stdout

    out = _run(tmp_path, "backup-schluessel", **env)
    assert out.returncode == 0, out.stdout + out.stderr
    ident = tmp_path / "automato-backup.agekey"
    assert ident.is_file() and oct(ident.stat().st_mode & 0o777) == "0o600"
    lines = (tmp_path / "env").read_text().splitlines()
    assert lines == ["AUTOMATO_NETBOX_TOKEN=bleibt",
                     "AUTOMATO_BACKUP_AGE_RECIPIENT=age1testempfaenger",
                     f"AUTOMATO_BACKUP_AGE_IDENTITY={ident}"]  # fmt: skip
    # zweiter Aufruf: dieselbe Identitaet, keine doppelten Zeilen
    again = _run(tmp_path, "backup-schluessel", **env)
    assert "vorhandene Identität" in again.stdout
    assert (tmp_path / "env").read_text().splitlines() == lines

    out = _run(tmp_path, "pruefen", **env)
    assert "verschlüsselt für age1testempf…" in out.stdout
    assert "Identität für den Rückfall lesbar" in out.stdout
    assert "noch keine Sicherung" in out.stdout
    # die Umgebung geht vor
    out = _run(tmp_path, "pruefen", **env, AUTOMATO_BACKUP_AGE_RECIPIENT="age1ausderumgebung")
    assert "verschlüsselt für age1ausderum…" in out.stdout


def test_ungueltige_compose_konfiguration_mit_hinweis_auf_override(tmp_path):
    """0.42.1: NetBox-Override im Automato-Ordner - `config` scheitert, pruefen nennt die Datei."""
    stub = _stub(
        tmp_path / "bad-compose",
        'case "$*" in\n'
        '  *config*) echo \'service "netbox" has neither an image nor a build context'
        " specified' >&2; exit 1 ;;\n"
        "esac\n",
    )
    out = _run(tmp_path, "pruefen", COMPOSE=str(stub))
    assert out.returncode == 1
    assert 'Automato-Projekt ungültig: service "netbox" has neither an image' in out.stdout


def _apt(tmp_path) -> tuple[dict, Path]:
    """Ersatz fuer apt-cache, apt-get und docker (0.43.0): docker-ce mit neuerer Version in der
    Paketliste, containerd.io aktuell, docker.io nicht installiert. apt-get schreibt seine
    Aufrufe ins Log; `install` hebt die Engine-Version an."""
    bin_dir = tmp_path / "aptbin"
    bin_dir.mkdir()
    log, engine = tmp_path / "apt.log", tmp_path / "engine"
    engine.write_text("27.3.1\n")
    _stub(bin_dir / "apt-cache", "cat <<'X'\n"
          "docker-ce:\n  Installed: 5:27.3.1-1~ubuntu.24.04~noble\n"
          "  Candidate: 5:27.4.0-1~ubuntu.24.04~noble\n  Version table:\n"
          "containerd.io:\n  Installed: 1.7.22-1\n  Candidate: 1.7.22-1\n"
          "docker.io:\n  Installed: (none)\n  Candidate: 24.0.7-0ubuntu4\nX\n")  # fmt: skip
    _stub(bin_dir / "apt-get", f'echo "$*" >> {log}\n'
          f'case "$*" in *install*) echo 27.4.0 > {engine} ;; esac\n')  # fmt: skip
    _stub(bin_dir / "docker", f'case "$1" in version) cat {engine} ;;\n'
          "  inspect) echo unless-stopped ;; *) exit 0 ;; esac\n")  # fmt: skip
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "APT_CACHE": str(bin_dir / "apt-cache"),
           "APT_GET": str(bin_dir / "apt-get"), "SUDO": ""}  # fmt: skip
    return env, log


def test_docker_stand_und_docker_update(tmp_path):
    """Anwender: Docker gleich mit aktualisieren, damit er immer aktuell ist - pruefen zeigt,
    was apt neuer kennt, docker-update hebt nur die installierten Docker-Pakete an."""
    env, log = _apt(tmp_path)
    out = _run(tmp_path, "pruefen", **env)
    text = out.stdout
    assert "Docker aktuell?" in text
    assert "docker-ce 5:27.3.1-1~ubuntu.24.04~noble – verfügbar: 5:27.4.0-1" in text
    assert "OK\x1b[0m      containerd.io 1.7.22-1" in text
    assert "docker.io" not in text  # nicht installiert
    assert "sudo deploy/aufbau.sh docker-update" in text and "AUTOMATO_DOCKER_UPDATE=1" in text

    out = _run(tmp_path, "docker-update", **env)
    assert out.returncode == 0, out.stdout + out.stderr
    calls = log.read_text().splitlines()
    assert calls == [
        "update -qq",
        "install -y -qq --only-upgrade -o Dpkg::Options::=--force-confold docker-ce",
    ]
    assert "Docker 27.3.1 → 27.4.0" in out.stdout
    assert "Automato-Container laufen" in out.stdout


def test_container_ausserhalb_der_update_profile(tmp_path):
    """0.42.2: der Collector lief, war aber nicht in AUTOMATO_COMPOSE_PROFILES - update.sh
    baute und startete ihn nie neu (alter Stand, ohne Neustart-Regel)."""
    stub = _stub(
        tmp_path / "prof-compose",
        'case "$*" in\n'
        "  *'config --services'*) printf 'orchestrator\\nredis\\n' ;;\n"
        "  *' ps '*) printf 'orchestrator|running|starting|o\\ncollector|running||c\\n' ;;\n"
        "esac\n",
    )
    out = _run(tmp_path, "status", COMPOSE=str(stub))
    assert "orchestrator (startet gerade)" in out.stdout
    assert "nicht in den Profilen von deploy/update.sh: collector" in out.stdout
    assert "--profile collectors" in out.stdout


def test_migrate_dienst_einmalig(tmp_path):
    """0.48.0: der Dienst migrate laeuft je Start einmal (Migration + DB-Rollen) und endet -
    beendet mit Exit 0 ist in Ordnung, ohne Neustart-Regel auch."""

    def status(lines: str) -> str:
        stub = _stub(
            tmp_path / "mig-compose", f"case \"$*\" in *' ps '*) printf '{lines}' ;; esac\n"
        )
        return _run(tmp_path, "status", COMPOSE=str(stub)).stdout

    text = status("migrate|exited||m|0\\norchestrator|running|healthy|o|0\\n")
    assert "migrate (Migration fertig)" in text and "migrate:" not in text
    assert "ohne Neustart-Regel: migrate" not in text
    text = status("migrate|exited||m|1\\n")
    assert "migrate: exited (Exit 1)" in text and "logs --tail 50 migrate" in text


def test_db_rollen_einrichten(tmp_path):
    """0.48.0 (R14): getrennte DB-Rollen - App-Rolle mit Zufalls-Passwort in .env, danach
    Container neu starten; pruefen warnt vorher und meldet die Rolle danach."""
    env = {"AUTOMATO_ENV_FILE": str(tmp_path / "env")}
    (tmp_path / "env").write_text("POSTGRES_PASSWORD=bleibt\n")
    before = _run(tmp_path, "pruefen", **env).stdout
    assert "arbeitet als Eigentümer der Tabellen" in before and "db-rollen" in before

    out = _run(tmp_path, "db-rollen", **env)
    assert out.returncode == 0, out.stdout + out.stderr
    lines = (tmp_path / "env").read_text().splitlines()
    assert lines[:2] == ["POSTGRES_PASSWORD=bleibt", "AUTOMATO_DB_USER=automato_app"]
    password = lines[2].removeprefix("AUTOMATO_DB_PASSWORD=")
    assert len(password) == 48 and password not in out.stdout
    assert "Container neu starten" in out.stdout

    again = _run(tmp_path, "db-rollen", **env)
    assert "ist in" in again.stdout and (tmp_path / "env").read_text().splitlines() == lines
    rotated = _run(tmp_path, "db-rollen", "--neu", **env)
    assert rotated.returncode == 0
    assert (tmp_path / "env").read_text().splitlines()[2] != lines[2]

    after = _run(tmp_path, "pruefen", **env).stdout
    assert "Orchestrator als automato_app" in after


def test_tls_intern_einrichten_und_pruefen(tmp_path):
    """0.49.0 (ADR 0023): tls-intern stellt die Zertifikate im Dienst migrate als root aus,
    traegt den Schalter in .env ein und startet neu; pruefen meldet Zustand und Ablauf."""
    log = tmp_path / "calls.log"
    stub = _stub(tmp_path / "tls-compose", f'echo "$*" >> {log}\n')
    pki = tmp_path / "pki"
    env = {"AUTOMATO_ENV_FILE": str(tmp_path / "env"), "AUTOMATO_TLS_PKI": str(pki),
           "COMPOSE": str(stub), "AUTOMATO_UI_HOST": "ui.example.ch",
           "AUTOMATO_TLS_WAIT": "0"}  # fmt: skip
    (tmp_path / "env").write_text("POSTGRES_PASSWORD=bleibt\n")
    before = _run(tmp_path, "pruefen", **env).stdout
    assert "Verbindungen zwischen den Containern unverschlüsselt" in before
    assert "deploy/aufbau.sh tls-intern" in before

    out = _run(tmp_path, "tls-intern", **env)
    assert out.returncode == 0, out.stdout + out.stderr
    calls = log.read_text().splitlines()
    (init,) = (c for c in calls if " run " in f" {c} ")
    assert (
        f"run --rm --no-deps --user root -v {pki}:/pki migrate automato tls-init --dir /pki" in init
    )
    assert "--name ui.example.ch" in init and "--renew" not in init
    assert any(c.endswith("up -d --force-recreate") for c in calls)
    assert (tmp_path / "env").read_text().splitlines() == [
        "POSTGRES_PASSWORD=bleibt",
        "AUTOMATO_INTERNAL_TLS=on",
    ]
    assert "AUTOMATO_ORCHESTRATOR_CA_BUNDLE" in out.stdout  # Hinweis fuer Agenten direkt auf :8000

    renew = _run(tmp_path, "tls-intern", "--erneuern", **env)
    assert renew.returncode == 0 and "--renew" in log.read_text().splitlines()[-2]

    # pruefen: eingeschaltet, Zertifikat noch lange gueltig
    from app import tls

    tls.issue(pki, [])
    after = _run(tmp_path, "pruefen", **env).stdout
    assert "eingeschaltet, Zertifikate noch" in after
