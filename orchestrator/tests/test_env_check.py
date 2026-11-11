""".env pruefen und ordnen (1.6.0, Anwender 2026-11-11: „Können wir das env aufräumen, sauber
sortieren und löschen, was wir nicht mehr brauchen?“): deploy/env_check.py zeigt nur Namen, nie
Werte; ordnen sortiert nach den Abschnitten von .env.example, entfernt nicht mehr gelesene
Schlüssel, stellt AUTOMATO_COMPOSE_PROFILES auf COMPOSE_PROFILES um und sichert vorher.
Dazu die Profile der Skripte (deploy/env.sh) nur noch aus COMPOSE_PROFILES."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "env_check.py"
GEHEIM = "s3cr3t-wert-0123456789abcdef"  # Testwert - darf nie in der Ausgabe stehen

ALT = f"""# meine alte .env
AUTOMATO_COMPOSE_PROFILES="--profile core --profile collectors --profile proxy --profile \
agent-gateway"
AUTOMATO_FORTIGATE_VERIFY_TLS=true
AUTOMATO_NETBOX_VERIFY_TLS=true
AUTOMATO_AGENT_GATEWAY_SECRET={GEHEIM}
AUTOMATO_UI_HOST=automato.int.example.ch
AUTOMATO_ENVIRONMENT=prod
AUTOMATO_INTERNAL_API_TOKEN={GEHEIM}
POSTGRES_PASSWORD={GEHEIM}
AUTOMATO_TIPPFEHLR=1
AUTOMATO_UI_ALLOW="10.0.10.0/24 10.8.0.0/16"
AUTOMATO_UI_HOST=automato.example.ch
AUTOMATO_RUN_REDISPATCH_MINUTES=5
kaputte zeile
"""


def _run(env_file: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "AUTOMATO_ENV_FILE": str(env_file)}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,  # noqa: S603
                          env=env, check=False)  # fmt: skip


def test_pruefen_nennt_namen_nie_werte(tmp_path):
    f = tmp_path / ".env"
    f.write_text(ALT)
    out = _run(f)
    text = out.stdout
    assert GEHEIM not in text and "10.0.10.0" not in text
    assert out.returncode == 1  # Fehler: Profile und Gateway-Name fehlen
    assert "COMPOSE_PROFILES fehlt - AUTOMATO_COMPOSE_PROFILES wird nicht mehr gelesen" in text
    assert "nicht mehr gelesen: AUTOMATO_FORTIGATE_VERIFY_TLS - Geraete" in text
    assert "nicht mehr gelesen: AUTOMATO_NETBOX_VERIFY_TLS" in text
    assert "unbekannt: AUTOMATO_TIPPFEHLR" in text
    assert "doppelt: AUTOMATO_UI_HOST (Zeilen 6, 12)" in text
    assert "Zeile 14: kein NAME=Wert" in text
    # bekannt, obwohl nicht in .env.example: Einstellung des Orchestrators
    assert "AUTOMATO_RUN_REDISPATCH_MINUTES" not in text
    # Gateway im Profil (aus der alten Angabe noch nicht übernommen) bzw. mTLS - Name fehlt
    (tmp_path / ".env").write_text(ALT + "COMPOSE_PROFILES=core,agent-gateway\n")
    text = _run(f).stdout
    assert "AUTOMATO_AGENT_GATEWAY_HOST fehlt" in text and "SECRET" not in text.split("FEHLER")[-1]


def test_ordnen_sortiert_entfernt_stellt_um_und_sichert(tmp_path):
    f = tmp_path / ".env"
    f.write_text(ALT)
    f.chmod(0o600)
    out = _run(f, "--ordnen")
    assert GEHEIM not in out.stdout
    assert "umgestellt: AUTOMATO_COMPOSE_PROFILES -> COMPOSE_PROFILES" in out.stdout
    assert ("entfernt (nicht mehr gelesen): AUTOMATO_COMPOSE_PROFILES, "
            "AUTOMATO_FORTIGATE_VERIFY_TLS, AUTOMATO_NETBOX_VERIFY_TLS") in out.stdout  # fmt: skip
    neu = f.read_text()
    sicherung = list(tmp_path.glob(".env.vorher-*"))
    assert len(sicherung) == 1 and sicherung[0].read_text() == ALT
    assert (f.stat().st_mode & 0o777) == 0o600  # Rechte bleiben
    assert (sicherung[0].stat().st_mode & 0o777) == 0o600  # Sicherung wie die .env (Secrets)
    assert not (tmp_path / ".env.neu").exists()
    zeilen = neu.splitlines()
    # Abschnitte und Reihenfolge wie .env.example, Werte unverändert (auch Anführungszeichen)
    assert (
        zeilen.index("# --- Betrieb (Docker Compose, Skripte) ---")
        < zeilen.index("COMPOSE_PROFILES=core,collectors,proxy,agent-gateway")
        < zeilen.index("AUTOMATO_ENVIRONMENT=prod")
        < zeilen.index(f"POSTGRES_PASSWORD={GEHEIM}")
    )
    assert zeilen.index("# --- Orchestrator ---") < zeilen.index(
        f"AUTOMATO_INTERNAL_API_TOKEN={GEHEIM}"
    )
    assert zeilen.index("# --- Oberflaeche ---") < zeilen.index(
        "AUTOMATO_UI_HOST=automato.example.ch"
    )
    assert 'AUTOMATO_UI_ALLOW="10.0.10.0/24 10.8.0.0/16"' in zeilen
    assert neu.count("AUTOMATO_UI_HOST=") == 1  # doppelt: die letzte gilt
    assert "# --- Weitere (bekannt, nicht in .env.example) ---" in zeilen
    assert "AUTOMATO_RUN_REDISPATCH_MINUTES=5" in zeilen
    assert "# AUTOMATO_TIPPFEHLR=1" in zeilen and "# kaputte zeile" in zeilen
    for weg in ("AUTOMATO_COMPOSE_PROFILES", "FORTIGATE", "NETBOX_VERIFY", "# meine alte .env"):
        assert weg not in neu
    # ein zweites Mal ändert nichts (keine weitere Sicherung)
    out = _run(f, "--ordnen")
    assert "bereits geordnet" in out.stdout and len(list(tmp_path.glob(".env.vorher-*"))) == 1


def test_ordnen_ohne_alte_profile_und_fehlende_datei(tmp_path):
    f = tmp_path / ".env"
    f.write_text("COMPOSE_PROFILES=core\nAUTOMATO_ENVIRONMENT=dev\n")
    assert _run(f, "--ordnen").returncode == 0
    assert f.read_text().splitlines()[-2:] == ["COMPOSE_PROFILES=core", "AUTOMATO_ENVIRONMENT=dev"]
    out = _run(tmp_path / "fehlt")
    assert out.returncode == 1 and "fehlt" in out.stdout


@pytest.mark.skipif(not shutil.which("bash"), reason="bash fehlt")
@pytest.mark.parametrize(
    ("inhalt", "rc", "profile"),
    [
        ("COMPOSE_PROFILES=core,proxy\n", 0, ""),  # Compose liest sie selbst: kein --profile
        ("", 0, "--profile core"),
        ('AUTOMATO_COMPOSE_PROFILES="--profile core --profile proxy"\n', 1, ""),
    ],
)
def test_profile_der_skripte(tmp_path, inhalt, rc, profile):
    (tmp_path / ".env").write_text(inhalt)
    shutil.copy(REPO / "deploy" / "env.sh", tmp_path / "env.sh")
    skript = (
        ". ./env.sh; compose_profile; r=$?; "
        'echo "rc=$r profile=${PROFILES[*]} env=${COMPOSE_PROFILES:-}"'
    )
    env = {k: v for k, v in os.environ.items() if "PROFILES" not in k}
    out = subprocess.run(["bash", "-c", skript], cwd=tmp_path, capture_output=True, text=True,  # noqa: S603, S607
                         env=env, check=False)  # fmt: skip
    assert f"rc={rc} profile={profile} " in out.stdout, out.stdout + out.stderr
    if rc:
        assert "aufbau.sh env ordnen" in out.stderr
    if "COMPOSE_PROFILES=core" in inhalt:
        assert "env=core,proxy" in out.stdout  # exportiert für docker compose
