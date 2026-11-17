"""deploy/betrieb.sh installieren und entfernen (1.16.0, ADR 0061, Vertrag betrieb/v1, P4
„Ausrollen“): eine eigene Automato-Instanz je Kunde im frischen Checkout <basis>/<instanz> -
.env mit neuen Geheimnissen auf dem Host, Projekt automato-<instanz> ohne Ports, Images nur per
Digest, Health im Container, Einmal-Code der Kopplung nur als Hash; entfernen nur mit Sicherung,
nur in einer Instanz, Ordner ins Archiv. Echte git-Repos, Ersatz fuer docker (mit Container- und
Volume-Stand je Projekt), age-keygen, Sicherung; kein Zugriff auf eine Registry."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from test_betrieb_script import (  # noqa: F401 - Fixture vorlage
    AUFTRAG,
    GIT_ENV,
    STAND_FELDER,
    _datei,
    _git,
    rm,
    vorlage,
    zaehler,
)

pytestmark = pytest.mark.skipif(
    not all(shutil.which(w) for w in ("bash", "git", "flock", "python3", "openssl", "comm")),
    reason="bash, git, flock, python3 bzw. openssl fehlen",
)

INSTANZ = "kunde-a"
PROJEKT = f"automato-{INSTANZ}"
ISSUER = "https://login.example.ch/realms/kunde-a"
CODE = "ABCD-EFGH-JKMN"  # Einmal-Code in Testform (Crockford-Base32)
DIENSTE = ("postgres", "redis", "migrate", "orchestrator", "web-ui", "anschluss", "collector")
DIGEST_DIENSTE = ("migrate", "orchestrator", "web-ui", "collector")  # eigene Images im Manifest

# docker mit Stand: Container und Volumes je Compose-Projekt (Projekt aus der .env im Arbeitsordner)
DOCKER = r"""#!/usr/bin/env bash
echo "docker $*" >>"$STUB_LOG"
projekt=automato
if [ -f .env ]; then p=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env); projekt=${p:-automato}; fi
touch "$STUB_DIR/container" "$STUB_DIR/volumes"
label() {
  local a
  for a in "$@"; do
    case $a in label=com.docker.compose.project=*) echo "${a#*project=}" ;; esac
  done
}
weg() { grep -v "^$1 " "$2" >"$2.neu" || true; mv "$2.neu" "$2"; }
case "$1" in
  compose)
    shift
    while [ "${1:-}" = --profile ]; do shift 2; done
    case "$1" in
      config) [ "${2:-}" = --quiet ] || printf 'name: %s\nservices: {}\n' "$projekt" ;;
      up)
        printf 'up %s images=%s\n' "$projekt" "$(env | grep -c '^AUTOMATO_IMAGE_')" >>"$STUB_LOG"
        [ ! -f "$STUB_DIR/up-scheitert" ] || exit 1
        weg "$projekt" "$STUB_DIR/container"
        for d in postgres redis migrate orchestrator web-ui anschluss collector; do
          echo "$projekt $projekt-$d-1" >>"$STUB_DIR/container"
        done
        if ! grep -q "^$projekt " "$STUB_DIR/volumes"; then
          for v in postgres-data orchestrator-data; do
            echo "$projekt ${projekt}_$v" >>"$STUB_DIR/volumes"
          done
        fi
        ;;
      down)
        [ ! -f "$STUB_DIR/down-scheitert" ] || exit 1
        weg "$projekt" "$STUB_DIR/container"
        case " $* " in *" -v "*) weg "$projekt" "$STUB_DIR/volumes" ;; esac
        ;;
      exec)
        case "$*" in
          *"orchestrator python -m app.serve --check"*) [ ! -f "$STUB_DIR/health-aus" ] || exit 1 ;;
          *"cockpit-kopplung --hinterlegen"*)
            read -r code
            echo "kopplung $code" >>"$STUB_LOG"
            [ ! -f "$STUB_DIR/kopplung-scheitert" ] || exit 1
            echo "OK     Einmal-Code des Cockpits hinterlegt"
            ;;
        esac
        ;;
    esac
    ;;
  network)
    case "$2" in
      inspect) [ ! -f "$STUB_DIR/netz-fehlt" ] || exit 1 ;;
      ls) ;;
    esac
    ;;
  ps) p=$(label "$@"); [ -z "$p" ] || awk -v p="$p" '$1 == p {print $2}' "$STUB_DIR/container" ;;
  volume)
    case "$2" in
      ls) p=$(label "$@"); [ -z "$p" ] || awk -v p="$p" '$1 == p {print $2}' "$STUB_DIR/volumes" ;;
      rm)
        [ ! -f "$STUB_DIR/rm-scheitert" ] || exit 1
        grep -v " $3$" "$STUB_DIR/volumes" >"$STUB_DIR/v.neu" || true
        mv "$STUB_DIR/v.neu" "$STUB_DIR/volumes"
        ;;
    esac
    ;;
  rm)
    [ ! -f "$STUB_DIR/rm-scheitert" ] || exit 1
    grep -v " ${!#}$" "$STUB_DIR/container" >"$STUB_DIR/c.neu" || true
    mv "$STUB_DIR/c.neu" "$STUB_DIR/container"
    ;;
  pull) echo "${!#}" >>"$STUB_DIR/vorhanden" ;;
  image)
    if [ "$2" = inspect ]; then grep -qxF "$3" "$STUB_DIR/vorhanden" 2>/dev/null || exit 1; fi
    ;;
esac
exit 0
"""
# age-keygen ohne echte Schluessel: -o legt eine Testdatei an, -y gibt einen Testempfaenger aus
AGE = r"""#!/usr/bin/env bash
case "$1" in
  -o) echo "# Testidentitaet (keine echte)" >"$2" ;;
  -y) [ -f "$2" ] && echo "age1testempfaenger$(printf 'q%.0s' $(seq 1 40))" ;;
  *) exit 2 ;;
esac
"""


@dataclass
class Instanz:
    tmp: Path
    commits: dict
    privat: Path
    basis: Path = field(init=False)
    ziel: Path = field(init=False)
    stub: Path = field(init=False)
    log: Path = field(init=False)
    backups: Path = field(init=False)
    env: dict = field(init=False)

    def __post_init__(self):
        self.basis = self.tmp / "instanzen"
        self.ziel = self.basis / INSTANZ
        self.stub = self.tmp / "stub"
        self.log = self.stub / "log"
        self.backups = self.tmp / "backups"
        _datei(self.stub / "bin" / "docker", DOCKER, 0o755)
        _datei(self.stub / "bin" / "age-keygen", AGE, 0o755)
        self.log.write_text("")
        # wie der Host-Agent: nur PATH, LANG und BETRIEB_AUFTRAG (dazu HOME und Testwerte)
        self.env = {
            "PATH": f"{self.stub / 'bin'}:{os.environ['PATH']}", "LANG": "C.UTF-8",
            "BETRIEB_AUFTRAG": AUFTRAG, "HOME": str(self.tmp), **GIT_ENV,
            "STUB_LOG": str(self.log), "STUB_DIR": str(self.stub),
            "AUTOMATO_HERAUSGEBER_FEST": str(self.tmp / "etc" / "herausgeber.pub"),
            "AUTOMATO_HEALTH_VERSUCHE": "2", "AUTOMATO_HEALTH_PAUSE": "0",
        }  # fmt: skip

    def klonen(self, version: str = "1.16.0") -> None:
        """Wie der Host-Agent: git clone der Quelle, losgeloest auf den Commit des Manifests."""
        self.basis.mkdir(exist_ok=True)
        _git(self.basis, "clone", "-q", str(self.tmp / "origin"), INSTANZ)
        _git(self.ziel, "checkout", "-q", "--detach", self.commits[version])

    def run(self, *args: str, cwd: Path | None = None, **extra: str):
        return subprocess.run(["bash", "deploy/betrieb.sh", *args], cwd=cwd or self.ziel,  # noqa: S603, S607
                              env={**self.env, **extra}, capture_output=True, text=True,
                              timeout=120, check=False)  # fmt: skip

    def betrieb(self, *args: str, cwd: Path | None = None, **extra: str) -> tuple[int, dict, str]:
        out = self.run(*args, cwd=cwd, **extra)
        zeilen = out.stdout.splitlines()
        assert len(zeilen) == 1, f"stdout: {out.stdout!r}\nstderr: {out.stderr}"
        return out.returncode, json.loads(zeilen[0]), out.stderr

    def manifest(self, v: str = "1.16.0") -> tuple[str, Path, Path]:
        version = f"{v}-dev.{zaehler(v)}.{self.commits[v][:7]}"
        digest = {d: hashlib.sha256((v + d).encode()).hexdigest() for d in DIGEST_DIENSTE}
        images = {d: f"ghcr.io/kunde-a/automato-{d}@sha256:{h}" for d, h in digest.items()}
        roh = rm.erstellen("dev", version, self.commits[v], images, "2026-11-16T12:00:00Z")
        auftrag = self.tmp / "auftrag"
        auftrag.mkdir(exist_ok=True, mode=0o700)
        m, s = auftrag / "manifest.json", auftrag / "manifest.sig"
        m.write_bytes(roh)
        s.write_text(rm.signieren(str(self.privat), roh) + "\n")
        return version, m, s

    def installieren(self, *mehr: str, v: str = "1.16.0", **extra: str):
        version, m, s = self.manifest(v)
        args = ["installieren", "--instanz", INSTANZ, "--version", version, "--manifest", str(m),
                "--signatur", str(s), "--kunde", "kunde-a", "--domain", "mgmt.example.ch",
                "--issuer", ISSUER, *mehr]  # fmt: skip
        return self.betrieb(*args, **{"AUTOMATO_BACKUP_DIR": str(self.backups), **extra})

    def protokoll(self) -> list[str]:
        return self.log.read_text().splitlines()

    def container(self) -> list[str]:
        datei = self.stub / "container"
        return datei.read_text().split() if datei.exists() else []

    def volumes(self) -> list[str]:
        datei = self.stub / "volumes"
        return datei.read_text().split() if datei.exists() else []


@pytest.fixture
def host(vorlage, tmp_path) -> Instanz:  # noqa: F811
    basis, commits, privat = vorlage
    shutil.copytree(basis / "origin", tmp_path / "origin", symlinks=True)
    h = Instanz(tmp_path, commits, privat)
    h.klonen()
    return h


def _env(host: Instanz) -> dict[str, str]:
    werte = {}
    for zeile in (host.ziel / ".env").read_text().splitlines():
        if zeile and not zeile.startswith("#"):
            k, _, v = zeile.partition("=")
            werte[k] = v
    return werte


def test_installieren_legt_instanz_an(host):
    rc, antwort, stderr = host.installieren("--kopplung", CODE.lower())
    version = f"1.16.0-dev.{zaehler('1.16.0')}.{host.commits['1.16.0'][:7]}"
    assert rc == 0, stderr
    assert set(antwort) == {"format", "produkt", "aktion", "ok", "meldung", "stand", "instanz"}
    assert antwort["aktion"] == "installieren" and antwort["ok"] is True
    assert set(antwort["stand"]) == STAND_FELDER
    assert antwort["meldung"] == (
        f"Instanz {INSTANZ} installiert: {version}, Health ok, Einmal-Code der Kopplung hinterlegt"
    )
    assert antwort["instanz"] == {
        "name": INSTANZ, "projekt": PROJEKT, "web_ui": f"{PROJEKT}-web-ui:5173",
        "kunde": "kunde-a", "domain": "mgmt.example.ch",
        "adresse": "https://kunde-a-doc.mgmt.example.ch",
    }  # fmt: skip
    stand = antwort["stand"]
    assert (stand["version"], stand["commit"], stand["quelle"], stand["kanal"]) == (
        version, host.commits["1.16.0"], "images", "dev")  # fmt: skip
    assert stand["gesund"] is True and stand["zurueck_moeglich"] is False
    # .env nur fuer root, neue Geheimnisse, Projekt und Profil der Instanz, OIDC auf den Realm
    datei = host.ziel / ".env"
    assert datei.stat().st_mode & 0o777 == 0o600
    env = _env(host)
    assert env["COMPOSE_PROJECT_NAME"] == PROJEKT and env["AUTOMATO_INSTANZ"] == INSTANZ
    assert env["COMPOSE_FILE"] == "docker-compose.yml:deploy/instanz/compose.yml"
    assert env["COMPOSE_PROFILES"] == "core,collectors"
    assert env["AUTOMATO_ENVIRONMENT"] == "prod" and env["AUTOMATO_AUTH_MODE"] == "oidc"
    assert env["AUTOMATO_OIDC_ISSUER"] == ISSUER
    assert env["AUTOMATO_OIDC_JWKS_URL"] == ISSUER + "/protocol/openid-connect/certs"
    assert env["AUTOMATO_OIDC_WEB_CLIENT_ID"] == "automato-web"
    assert env["AUTOMATO_CORS_ORIGINS"] == '["https://kunde-a-doc.mgmt.example.ch"]'
    for k in ("POSTGRES_PASSWORD", "AUTOMATO_INTERNAL_API_TOKEN", "AUTOMATO_AUDIT_ENCRYPTION_KEY"):
        assert re.fullmatch(r"[0-9a-f]{64}", env[k]), k
    assert re.fullmatch(r"[0-9a-f]{48}", env["AUTOMATO_DB_PASSWORD"])
    assert len({env["POSTGRES_PASSWORD"], env["AUTOMATO_INTERNAL_API_TOKEN"],
                env["AUTOMATO_AUDIT_ENCRYPTION_KEY"]}) == 3  # fmt: skip
    assert env["AUTOMATO_BACKUP_AGE_RECIPIENT"].startswith("age1")
    assert env["AUTOMATO_BACKUP_AGE_IDENTITY"] == str(host.tmp / "automato-backup.agekey")
    assert env["AUTOMATO_BACKUP_DIR"] == str(host.backups / "instanzen" / INSTANZ)
    # kein Geheimnis in Antwort oder Fortschritt
    for k in ("POSTGRES_PASSWORD", "AUTOMATO_DB_PASSWORD", "AUTOMATO_INTERNAL_API_TOKEN"):
        assert env[k] not in json.dumps(antwort) and env[k] not in stderr
    assert CODE not in json.dumps(antwort) and CODE not in stderr
    # Images nur per Digest, nie gebaut, Projekt der Instanz, Code von stdin (nie als Argument)
    log = host.protokoll()
    refs = json.loads(host.manifest()[1].read_text())["images"]
    assert sorted(z for z in log if z.startswith("docker pull ")) == sorted(
        f"docker pull --quiet {ref}" for ref in refs.values()
    )
    assert not [z for z in log if " build" in z]
    assert f"up {PROJEKT} images=4" in log
    assert "docker compose exec -T orchestrator automato cockpit-kopplung --hinterlegen" in log
    assert f"kopplung {CODE}" in log and not [z for z in log if CODE in z and "docker" in z]
    assert "docker compose exec -T orchestrator python -m app.serve --check" in log
    assert sorted(host.container()[1::2]) == sorted(f"{PROJEKT}-{d}-1" for d in DIENSTE)
    gepinnt = (host.ziel / ".automato-images.env").read_text()
    assert f"AUTOMATO_MANIFEST_VERSION={version}" in gepinnt
    assert f"docker tag {refs['web-ui']} {PROJEKT}-web-ui" in log

    # danach gehen stand, sichern und neustart in der Instanz - mit `instanz` in der Antwort
    rc, antwort, _ = host.betrieb("stand")
    assert rc == 0 and antwort["meldung"] == f"{version} (images, dev), Health ok"
    assert antwort["instanz"]["name"] == INSTANZ
    rc, antwort, _ = host.betrieb("sichern")
    assert rc == 0 and antwort["meldung"] == "Sicherung fertig"
    assert (host.backups / "instanzen" / INSTANZ / "automato-1.dump.age").exists()
    (host.stub / "health-aus").write_text("")
    rc, antwort, _ = host.betrieb("stand")
    assert rc == 0 and antwort["stand"]["gesund"] is False


def test_installieren_ohne_kopplung_und_zweites_mal_abgelehnt(host):
    rc, antwort, stderr = host.installieren()
    assert rc == 0, stderr
    assert antwort["meldung"].endswith("Health ok")
    assert not [z for z in host.protokoll() if "kopplung" in z]
    rc, antwort, _ = host.installieren()
    assert rc == 3 and antwort["meldung"].startswith("Checkout ist schon eingerichtet")


@pytest.mark.parametrize(
    ("aendern", "meldung"),
    [
        ({"--instanz": "Kunde_A"}, "--instanz fehlt bzw. ist ungültig"),
        ({"--instanz": "a"}, "--instanz fehlt bzw. ist ungültig"),
        ({"--instanz": "1abc"}, "--instanz fehlt bzw. ist ungültig"),
        ({"--instanz": "kunde-b"}, "Checkout liegt nicht unter <basisordner>/kunde-b"),
        ({"--kunde": "kunde a"}, "--kunde fehlt bzw. ist ungültig"),
        ({"--domain": "mgmt.example.ch;ls"}, "--domain fehlt bzw. ist ungültig"),
        ({"--issuer": "http://login.example.ch/realms/kunde-a"}, "--issuer fehlt"),
        ({"--issuer": "https://login.example.ch/realms/"}, "--issuer fehlt bzw. ist ungültig"),
        ({"--kopplung": "ABCD-EFGH-JKMU"}, "--kopplung ist kein Einmal-Code"),
        ({"--version": "1.16"}, "--version fehlt bzw. ist ungültig"),
        ({"--unbekannt": "x"}, "Unbekannte Angabe"),
    ],
)
def test_installieren_prueft_eingaben(host, aendern, meldung):
    version, m, s = host.manifest()
    werte = {"--instanz": INSTANZ, "--version": version, "--manifest": str(m),
             "--signatur": str(s), "--kunde": "kunde-a", "--domain": "mgmt.example.ch",
             "--issuer": ISSUER, "--kopplung": CODE}  # fmt: skip
    werte.update(aendern)
    args = [x for k, v in werte.items() for x in (k, v)]
    rc, antwort, _ = host.betrieb("installieren", *args)
    assert rc == 3 and antwort["ok"] is False and antwort["meldung"].startswith(meldung), antwort
    assert not (host.ziel / ".env").exists() and host.container() == []


def test_installieren_lehnt_ab_ohne_aenderung(host):
    # Herausgeber-Schluessel fehlt bzw. Signatur falsch
    version, m, s = host.manifest()
    s.write_text(rm.signieren(str(host.privat), m.read_bytes() + b" ") + "\n")
    rc, antwort, _ = host.betrieb("installieren", "--instanz", INSTANZ, "--version", version,
                                  "--manifest", str(m), "--signatur", str(s), "--kunde", "kunde-a",
                                  "--domain", "mgmt.example.ch", "--issuer", ISSUER)  # fmt: skip
    assert rc == 3 and "Signatur des Manifests" in antwort["meldung"]
    # Checkout nicht auf dem Commit des Manifests
    rc, antwort, _ = host.installieren(v="1.17.0")
    assert rc == 3 and antwort["meldung"].startswith("Checkout steht nicht auf dem Commit")
    # Netz des Front-Proxys fehlt (anderer Host)
    (host.stub / "netz-fehlt").write_text("")
    rc, antwort, _ = host.installieren()
    assert rc == 3 and "Netz automato-front-instanzen fehlt" in antwort["meldung"]
    (host.stub / "netz-fehlt").unlink()
    # Reste einer früheren Instanz gleichen Namens: nur melden, nie still loeschen
    (host.stub / "volumes").write_text(f"{PROJEKT} {PROJEKT}_postgres-data\n")
    rc, antwort, _ = host.installieren()
    assert rc == 3 and antwort["meldung"].startswith(f"Reste einer früheren Instanz {PROJEKT}")
    assert host.volumes() == [PROJEKT, f"{PROJEKT}_postgres-data"]
    (host.stub / "volumes").write_text("")
    assert not (host.ziel / ".env").exists()
    assert not [z for z in host.protokoll() if z.startswith(("docker pull", "up "))]
    assert "instanz" in antwort and antwort["instanz"]["name"] == INSTANZ


def test_installieren_in_der_hauptinstanz_abgelehnt(host):
    """Ein eingerichteter Checkout (.env) - z. B. die Hauptinstanz - wird nie zur Instanz."""
    (host.ziel / ".env").write_text("COMPOSE_PROFILES=core\n")
    rc, antwort, _ = host.installieren()
    assert rc == 3 and antwort["meldung"].startswith("Checkout ist schon eingerichtet")
    assert host.protokoll() == [] or not [z for z in host.protokoll() if "up " in z]


@pytest.mark.parametrize(
    ("datei", "meldung"),
    [
        ("health-aus", "Instanz nicht gesund"),
        ("up-scheitert", "Instanz startet nicht"),
        ("kopplung-scheitert", "Einmal-Code der Kopplung nicht hinterlegt"),
    ],
)
def test_installieren_scheitert_und_raeumt_auf(host, datei, meldung):
    (host.stub / datei).write_text("")
    rc, antwort, _ = host.installieren("--kopplung", CODE)
    assert rc == 1 and antwort["ok"] is False
    assert antwort["meldung"].startswith(meldung)
    assert antwort["meldung"].endswith("– Instanz wieder entfernt, nichts installiert")
    assert host.container() == [] and host.volumes() == []
    assert not (host.ziel / ".env").exists() and not (host.ziel / ".automato-images.env").exists()
    assert "docker compose down -v --remove-orphans" in host.protokoll()


def test_installieren_aufraeumen_unvollstaendig(host):
    for datei in ("health-aus", "down-scheitert", "rm-scheitert"):
        (host.stub / datei).write_text("")
    rc, antwort, _ = host.installieren()
    assert rc == 2 and antwort["ok"] is False
    assert "Aufräumen unvollständig" in antwort["meldung"]
    assert f"label=com.docker.compose.project={PROJEKT}" in antwort["meldung"]


def test_entfernen_sichert_loescht_und_archiviert(host):
    assert host.installieren()[0] == 0
    host.log.write_text("")
    # ohne --ja bzw. mit mehr: abgelehnt
    for args in ((), ("--ja", "--x"), ("ja",)):
        rc, antwort, _ = host.betrieb("entfernen", *args)
        assert rc == 3 and antwort["meldung"].startswith("entfernen nur mit --ja")
    # Sicherung scheitert: nichts geloescht
    (host.stub / "backup-scheitert").write_text("")
    rc, antwort, _ = host.betrieb("entfernen", "--ja")
    assert rc == 2
    assert antwort["meldung"].startswith("Sicherung fehlgeschlagen – nichts gelöscht")
    assert len(host.container()) == 2 * len(DIENSTE) and host.ziel.exists()
    (host.stub / "backup-scheitert").unlink()

    rc, antwort, stderr = host.betrieb("entfernen", "--ja")
    assert rc == 0, stderr
    m = re.fullmatch(
        rf"Instanz {INSTANZ} entfernt: Sicherung automato-1\.dump\.age, Container und Volumes "
        rf"gelöscht, Ordner unter archiv/{INSTANZ}-(\d{{8}}T\d{{6}}Z)",
        antwort["meldung"],
    )
    assert m, antwort["meldung"]
    assert antwort["instanz"]["name"] == INSTANZ and antwort["stand"]["gesund"] is None
    assert host.container() == [] and host.volumes() == []
    assert not host.ziel.exists()
    archiv = host.basis / "archiv" / f"{INSTANZ}-{m.group(1)}"
    assert (archiv / ".env").exists() and (archiv / "deploy" / "betrieb.sh").exists()
    assert (host.basis / "archiv").stat().st_mode & 0o777 == 0o700
    # die Sicherung bleibt (Ort der Instanz)
    assert (host.backups / "instanzen" / INSTANZ / "automato-1.dump.age").exists()
    log = host.protokoll()
    assert log.index("backup automato-1.dump.age") < log.index(
        "docker compose down -v --remove-orphans"
    )
    assert f"docker image rm {PROJEKT}-orchestrator" in log


def test_entfernen_nie_in_der_hauptinstanz(host, tmp_path):
    """Ohne Instanz (Hauptinstanz) bzw. mit falschem Projekt lehnt entfernen ab - nichts weg."""
    rc, antwort, _ = host.betrieb("entfernen", "--ja")  # frischer Checkout ohne .env
    assert rc == 3 and "die Hauptinstanz wird nie entfernt" in antwort["meldung"]
    assert host.installieren()[0] == 0
    # .env der Instanz, aber ein anderes Projekt (z. B. von Hand geaendert)
    env = (host.ziel / ".env").read_text()
    (host.ziel / ".env").write_text(env.replace(f"COMPOSE_PROJECT_NAME={PROJEKT}",
                                                "COMPOSE_PROJECT_NAME=automato"))  # fmt: skip
    rc, antwort, _ = host.betrieb("entfernen", "--ja")
    assert rc == 3
    assert antwort["meldung"] == f"Compose-Projekt ist nicht {PROJEKT} – nichts geändert"
    assert len(host.container()) == 2 * len(DIENSTE) and host.ziel.exists()
    # Ordner heisst nicht wie die Instanz
    (host.ziel / ".env").write_text(env)
    anders = host.basis / "anders"
    host.ziel.rename(anders)
    rc, antwort, _ = host.betrieb("entfernen", "--ja", cwd=anders)
    assert rc == 3 and "Checkout liegt nicht unter" in antwort["meldung"]


def test_aktualisieren_in_der_instanz(host):
    """Eine Instanz aktualisiert wie die Hauptinstanz (Images per Digest, Health im Container)."""
    assert host.installieren()[0] == 0
    host.log.write_text("")
    _git(host.ziel, "fetch", "-q", "origin")
    version, m, s = host.manifest("1.17.0")
    rc, antwort, stderr = host.betrieb("aktualisieren", "--version", version, "--manifest", str(m),
                                       "--signatur", str(s))  # fmt: skip
    assert rc == 0, stderr
    assert antwort["meldung"].endswith(f"→ {version}, Health ok")
    assert antwort["instanz"]["projekt"] == PROJEKT
    assert _git(host.ziel, "rev-parse", "HEAD") == host.commits["1.17.0"]
    assert (host.backups / "instanzen" / INSTANZ / "automato-1.dump.age").exists()
    assert f"up {PROJEKT} images=4" in host.protokoll()
