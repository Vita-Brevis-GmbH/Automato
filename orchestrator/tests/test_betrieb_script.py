"""deploy/betrieb.sh (1.15.0, ADR 0060, Vertrag betrieb/v1): Einstiegspunkt fuer den Host-Agenten
des Cockpits - Update aus dem signierten Manifest mit Images per Digest, Rueckfall, zurueck,
Sperre, eine Zeile JSON auf stdout. Echte git-Repos (Ursprung und Checkout), Ersatz fuer docker,
curl, Sicherung und Wiederherstellung; ein Ed25519-Testschluessel entsteht zur Laufzeit."""

import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DATEIEN = (
    "deploy/betrieb.sh",
    "deploy/lib-update.sh",
    "deploy/lib-instanz.sh",
    "deploy/lib-bundle.sh",
    "deploy/env.sh",
    "deploy/update.sh",
    "scripts/release_manifest.py",
    ".gitignore",
)
AUFTRAG = "3f1c2b6e-0000-4000-8000-000000000001"
VERSIONEN = ("1.15.0", "1.16.0", "1.17.0")
DEV_BRANCH = "claude/projekt-roadmap-planung-0mk5v0"  # Entwicklungs-Branch = Kanal dev


def zaehler(v: str) -> int:
    """n in <X.Y.Z>-<kanal>.<n>.<sha7> (1.18.1): Commits bis zur Version, je Version einer."""
    return VERSIONEN.index(v) + 1


GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.ch",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.ch",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
}  # fmt: skip


def _openssl3() -> bool:
    if not shutil.which("openssl"):
        return False
    out = subprocess.run(["openssl", "version"], capture_output=True, text=True, check=False)  # noqa: S603, S607
    m = re.match(r"OpenSSL (\d+)", out.stdout)
    return bool(m) and int(m.group(1)) >= 3


pytestmark = pytest.mark.skipif(
    not all(shutil.which(w) for w in ("bash", "git", "flock", "python3")) or not _openssl3(),
    reason="bash, git, flock, python3 bzw. OpenSSL >= 3 fehlen",
)

_spec = importlib.util.spec_from_file_location(
    "release_manifest", REPO / "scripts" / "release_manifest.py"
)
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)

DOCKER = r"""#!/usr/bin/env bash
echo "docker $*" >>"$STUB_LOG"
case "$1" in
  compose)
    shift
    while [ "${1:-}" = --profile ]; do shift 2; done
    case "$1" in
      config) [ "${2:-}" = --quiet ] || printf 'name: automato\nservices: {}\n' ;;
      up)
        gepinnt=$(env | grep '^AUTOMATO_IMAGE_' | sort | tr '\n' ' ')
        printf 'up-images %s\n' "$gepinnt" >>"$STUB_LOG"
        [ ! -f "$STUB_DIR/up-scheitert" ] || exit 1
        ;;
    esac
    ;;
  pull)
    ref=${!#}
    if grep -qxF "$ref" "$STUB_DIR/pull-scheitert" 2>/dev/null; then exit 1; fi
    echo "$ref" >>"$STUB_DIR/vorhanden"
    ;;
  image) [ "$2" != inspect ] || grep -qxF "$3" "$STUB_DIR/vorhanden" 2>/dev/null ;;
esac
"""
# Health: ok, ausser im Commit STUB_KAPUTT bzw. mit der Datei health-aus
CURL = r"""#!/usr/bin/env bash
[ ! -f "$STUB_DIR/health-aus" ] || exit 7
head=$(git -C "$STUB_CHECKOUT" rev-parse HEAD)
if [ -n "${STUB_KAPUTT:-}" ] && [ "$head" = "$STUB_KAPUTT" ]; then
  echo '{"status":"error"}'
  exit 0
fi
echo '{"status":"ok","version":"x"}'
"""
BACKUP = r"""#!/usr/bin/env bash
set -eu
[ ! -f "$STUB_DIR/backup-scheitert" ] || exit 1
mkdir -p "$1"
n=$(( $(cat "$1/.zaehler" 2>/dev/null || echo 0) + 1 ))
echo "$n" >"$1/.zaehler"
: >"$1/automato-$n.dump.age"
touch -d "@$((1763280000 + n * 60))" "$1/automato-$n.dump.age"
echo "backup automato-$n.dump.age" >>"$STUB_LOG"
"""
RESTORE = r"""#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/../.."
n=0
[ ! -f .automato-images.env ] || n=$(grep -c '^AUTOMATO_IMAGE_' .automato-images.env || true)
echo "restore $(basename "$1") head=$(git rev-parse --short=7 HEAD) images=$n" >>"$STUB_LOG"
[ ! -f "$STUB_DIR/restore-scheitert" ] || exit 1
"""


def _datei(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,  # noqa: S603, S607
                         env={**os.environ, **GIT_ENV}, check=True)  # fmt: skip
    return out.stdout.strip()


def _schluessel(ziel: Path) -> tuple[Path, Path]:
    privat, pub = ziel / "privat.pem", ziel / "herausgeber.pub"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(privat)],  # noqa: S603, S607
                   check=True, capture_output=True)  # fmt: skip
    subprocess.run(["openssl", "pkey", "-in", str(privat), "-pubout", "-out", str(pub)],  # noqa: S603, S607
                   check=True, capture_output=True)  # fmt: skip
    return privat, pub


@pytest.fixture(scope="module")
def vorlage(tmp_path_factory):
    """Ursprung mit 1.15.0, 1.16.0 und 1.17.0; Checkout bei 1.15.0 auf main (gebaut aus git)."""
    basis = tmp_path_factory.mktemp("betrieb")
    privat, pub = _schluessel(basis)
    origin = basis / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    for name in DATEIEN:
        ziel = origin / name
        ziel.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, ziel)
    _datei(origin / "deploy/backup/backup.sh", BACKUP, 0o755)
    _datei(origin / "deploy/backup/restore.sh", RESTORE, 0o755)
    _datei(origin / "deploy/aufbau.sh", "#!/usr/bin/env bash\nexit 0\n", 0o755)
    _datei(origin / "deploy/release/herausgeber.pub", pub.read_text())
    commits = {}
    for i, version in enumerate(VERSIONEN):
        (origin / "VERSION").write_text(version + "\n")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", f"Version {version}")
        commits[version] = _git(origin, "rev-parse", "HEAD")
        if i == 0:
            _git(basis, "clone", "-q", str(origin), "checkout")
    return basis, commits, privat


@dataclass
class Host:
    tmp: Path
    commits: dict
    privat: Path
    checkout: Path = field(init=False)
    stub: Path = field(init=False)
    log: Path = field(init=False)
    backups: Path = field(init=False)
    etc: Path = field(init=False)
    env: dict = field(init=False)

    def __post_init__(self):
        self.checkout = self.tmp / "checkout"
        self.stub = self.tmp / "stub"
        self.log = self.stub / "log"
        self.backups = self.tmp / "backups"
        self.etc = self.tmp / "etc" / "herausgeber.pub"
        _datei(self.stub / "bin" / "docker", DOCKER, 0o755)
        _datei(self.stub / "bin" / "curl", CURL, 0o755)
        self.log.write_text("")
        self.env = {
            "PATH": f"{self.stub / 'bin'}:{os.environ['PATH']}", "LANG": "C.UTF-8",
            "BETRIEB_AUFTRAG": AUFTRAG, "HOME": str(self.tmp), **GIT_ENV,
            "STUB_LOG": str(self.log), "STUB_DIR": str(self.stub),
            "STUB_CHECKOUT": str(self.checkout), "AUTOMATO_HERAUSGEBER_FEST": str(self.etc),
            "AUTOMATO_BACKUP_DIR": str(self.backups), "AUTOMATO_BACKUP_PLAIN": "1",
            "AUTOMATO_HEALTH_VERSUCHE": "2", "AUTOMATO_HEALTH_PAUSE": "0",
        }  # fmt: skip

    def run(self, *args: str, skript: str = "deploy/betrieb.sh", **extra: str):
        out = subprocess.run(["bash", skript, *args], cwd=self.checkout,  # noqa: S603, S607
                             env={**self.env, **extra}, capture_output=True, text=True,
                             timeout=120, check=False)  # fmt: skip
        return out

    def betrieb(self, *args: str, **extra: str) -> tuple[int, dict, str]:
        out = self.run(*args, **extra)
        zeilen = out.stdout.splitlines()
        assert len(zeilen) == 1, f"stdout: {out.stdout!r}\nstderr: {out.stderr}"
        return out.returncode, json.loads(zeilen[0]), out.stderr

    def head(self) -> str:
        return _git(self.checkout, "rev-parse", "HEAD")

    def version(self, v: str, kanal: str = "dev") -> str:
        c = self.commits[v]
        return v if kanal == "stable" else f"{v}-{kanal}.{zaehler(v)}.{c[:7]}"

    def images(self, v: str) -> dict:
        return {
            d: f"ghcr.io/kunde-a/automato-{d}@sha256:{hashlib.sha256((v + d).encode()).hexdigest()}"
            for d in ("migrate", "orchestrator", "web-ui")
        }

    def manifest(self, v: str, kanal: str = "dev", privat: Path | None = None,
                 version: str | None = None, commit: str | None = None,
                 ) -> tuple[str, Path, Path]:  # fmt: skip
        version = version or self.version(v, kanal)
        roh = rm.erstellen(kanal, version, commit or self.commits[v], self.images(v),
                           "2026-11-16T12:00:00Z")  # fmt: skip
        auftrag = self.tmp / "auftrag"
        auftrag.mkdir(exist_ok=True, mode=0o700)
        m, s = auftrag / "manifest.json", auftrag / "manifest.sig"
        m.write_bytes(roh)
        s.write_text(rm.signieren(str(privat or self.privat), roh) + "\n")
        return version, m, s

    def aktualisieren(self, v: str, kanal: str = "dev", **extra: str):
        version, m, s = self.manifest(v, kanal)
        return self.betrieb("aktualisieren", "--version", version, "--manifest", str(m),
                            "--signatur", str(s), **extra)  # fmt: skip

    def commit_im_ursprung(self, name: str, *, von: str | None = None, branch: str = "main") -> str:
        """Weiterer Commit im Ursprung (VERSION bleibt), auf main bzw. neuer Linie ab `von`."""
        origin = self.tmp / "origin"
        if von:
            _git(origin, "checkout", "-q", "-b", branch, von)
        else:
            _git(origin, "checkout", "-q", branch)
        (origin / "NOTIZ").write_text(name + "\n")
        _git(origin, "add", "NOTIZ")
        _git(origin, "commit", "-q", "-m", name)
        commit = _git(origin, "rev-parse", "HEAD")
        _git(origin, "checkout", "-q", "main")
        return commit

    def aktualisieren_auf(self, commit: str, version: str, kanal: str, *mehr: str, **extra: str):
        """Update auf einen beliebigen Commit (Images wie 1.17.0), z. B. gleicher Kern."""
        version, m, s = self.manifest("1.17.0", kanal, version=version, commit=commit)
        return self.betrieb("aktualisieren", "--version", version, "--manifest", str(m),
                            "--signatur", str(s), *mehr, **extra)  # fmt: skip

    def protokoll(self) -> list[str]:
        return self.log.read_text().splitlines()

    def leeren(self) -> None:
        self.log.write_text("")


@pytest.fixture
def host(vorlage, tmp_path) -> Host:
    basis, commits, privat = vorlage
    shutil.copytree(basis / "origin", tmp_path / "origin", symlinks=True)
    shutil.copytree(basis / "checkout", tmp_path / "checkout", symlinks=True)
    _git(tmp_path / "checkout", "remote", "set-url", "origin", str(tmp_path / "origin"))
    return Host(tmp_path, commits, privat)


STAND_FELDER = {"version", "commit", "quelle", "kanal", "gesund", "zurueck_moeglich", "vorher",
                "letzte_sicherung", "herkunft"}  # fmt: skip


def _format(antwort: dict, aktion: str) -> None:
    assert set(antwort) == {"format", "produkt", "aktion", "ok", "meldung", "stand"}
    assert antwort["format"] == "betrieb/v1" and antwort["produkt"] == "automato"
    assert antwort["aktion"] == aktion
    assert isinstance(antwort["ok"], bool) and isinstance(antwort["meldung"], str)
    assert 0 < len(antwort["meldung"]) <= 300
    assert set(antwort["stand"]) == STAND_FELDER


def test_stand_gebaut_aus_git(host):
    rc, antwort, stderr = host.betrieb("stand")
    assert rc == 0, stderr
    _format(antwort, "stand")
    assert antwort["ok"] is True
    assert antwort["meldung"] == "1.15.0 (git), Health ok"
    assert antwort["stand"] == {
        "version": "1.15.0", "commit": host.commits["1.15.0"], "quelle": "git", "kanal": None,
        "gesund": True, "zurueck_moeglich": False, "vorher": None, "letzte_sicherung": None,
        "herkunft": {"branch": "main", "losgeloest": False, "kanal": "latest", "aus": "branch"},
    }  # fmt: skip
    assert f"Auftrag {AUFTRAG}: stand" in stderr
    (host.stub / "health-aus").write_text("")
    rc, antwort, _ = host.betrieb("stand")
    assert rc == 0 and antwort["stand"]["gesund"] is False
    assert antwort["meldung"] == "1.15.0 (git), Health nicht ok"


def test_aktualisieren_mit_images_per_digest(host):
    rc, antwort, stderr = host.aktualisieren("1.16.0")
    version = host.version("1.16.0")
    assert rc == 0, stderr
    _format(antwort, "aktualisieren")
    assert antwort["ok"] is True and antwort["meldung"] == f"1.15.0 → {version}, Health ok"
    stand = antwort["stand"]
    assert stand["version"] == version and stand["commit"] == host.commits["1.16.0"]
    assert stand["quelle"] == "images" and stand["kanal"] == "dev" and stand["gesund"] is True
    assert stand["herkunft"] == {"branch": None, "losgeloest": True, "kanal": "dev",
                                 "aus": "manifest"}  # fmt: skip
    assert stand["zurueck_moeglich"] is True
    assert stand["vorher"]["version"] == "1.15.0"
    assert stand["vorher"]["commit"] == host.commits["1.15.0"]
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", stand["vorher"]["zeit"])
    assert stand["letzte_sicherung"] == "2025-11-16T08:01:00Z"
    # Commit des Manifests losgeloest, Images nur per Digest, nie gebaut
    assert host.head() == host.commits["1.16.0"]
    assert _git(host.checkout, "status", "--porcelain", "--untracked-files=no") == ""
    gepinnt = (host.checkout / ".automato-images.env").read_text()
    for dienst, ref in host.images("1.16.0").items():
        var = "AUTOMATO_IMAGE_" + dienst.upper().replace("-", "_")
        assert f"{var}={ref}\n" in gepinnt
    assert f"AUTOMATO_MANIFEST_VERSION={version}\n" in gepinnt
    log = host.protokoll()
    assert log.count("backup automato-1.dump.age") == 1
    assert sorted(z for z in log if z.startswith("docker pull")) == sorted(
        f"docker pull --quiet {ref}" for ref in host.images("1.16.0").values()
    )
    assert not [z for z in log if " build" in z]
    assert "docker compose --profile core up -d --no-build" in log
    up = next(z for z in log if z.startswith("up-images"))
    assert f"AUTOMATO_IMAGE_WEB_UI={host.images('1.16.0')['web-ui']}" in up
    # lokale Namen fuer ein `docker compose up -d` von Hand
    assert f"docker tag {host.images('1.16.0')['orchestrator']} automato-orchestrator" in log
    assert f"docker tag {host.images('1.16.0')['web-ui']} automato-web-ui" in log
    assert "Auftrag" in stderr and "1/5 Sicherung" in stderr

    # gleiche Version: nichts zu tun
    host.leeren()
    rc, antwort, _ = host.aktualisieren("1.16.0")
    assert rc == 0 and antwort["ok"] is True and antwort["meldung"] == f"schon auf {version}"
    assert host.protokoll() == []
    # aeltere Version: abgelehnt, nur ueber zurueck
    rc, antwort, _ = host.aktualisieren("1.15.0", kanal="stable")
    assert rc == 3 and antwort["ok"] is False
    assert (
        antwort["meldung"] == f"1.15.0 ist älter als {version} – zurück nur über die Aktion zurueck"
    )
    assert host.head() == host.commits["1.16.0"] and host.protokoll() == []

    # stand mit Images
    rc, antwort, _ = host.betrieb("stand")
    assert rc == 0 and antwort["meldung"] == f"{version} (images, dev), Health ok"
    assert antwort["stand"]["quelle"] == "images" and antwort["stand"]["zurueck_moeglich"] is True


def _unveraendert(host: Host) -> None:
    assert host.head() == host.commits["1.15.0"]
    assert host.protokoll() == []  # weder Sicherung noch docker
    assert not (host.checkout / ".automato-update").exists()
    assert not (host.checkout / ".automato-images.env").exists()


def test_signatur_fehlt_falsch_oder_manifest_veraendert(host):
    version, m, s = host.manifest("1.16.0")
    args = ("aktualisieren", "--version", version, "--manifest", str(m), "--signatur")
    leer = host.tmp / "leer.sig"
    leer.write_text("")
    rc, antwort, _ = host.betrieb(*args, str(leer))
    assert rc == 3 and antwort["meldung"] == "Signatur fehlt bzw. ist ungültig – nichts geändert"
    rc, antwort, _ = host.betrieb(*args, str(host.tmp / "gibt-es-nicht.sig"))
    assert rc == 3
    # falscher Schluessel
    anderer, _ = _schluessel(host.tmp)
    _, m2, s2 = host.manifest("1.16.0", privat=anderer)
    rc, antwort, _ = host.betrieb("aktualisieren", "--version", version, "--manifest", str(m2),
                                  "--signatur", str(s2))  # fmt: skip
    assert rc == 3
    assert antwort["meldung"] == "Signatur des Manifests fehlt bzw. ist ungültig – nichts geändert"
    # ein Byte veraendert
    _, m, s = host.manifest("1.16.0")
    m.write_bytes(m.read_bytes().replace(b"migrate", b"migrat3"))
    rc, antwort, _ = host.betrieb(*args, str(s))
    assert rc == 3 and "Signatur des Manifests" in antwort["meldung"]
    _unveraendert(host)


def test_version_produkt_und_eingaben_geprueft(host):
    version, m, s = host.manifest("1.16.0")
    rc, antwort, _ = host.betrieb("aktualisieren", "--version", "1.16.1", "--manifest", str(m),
                                  "--signatur", str(s))  # fmt: skip
    assert rc == 3 and "passt nicht zum Auftrag" in antwort["meldung"]
    for falsch in ("1.16", "v1.16.0", "1.16.0-" + "x" * 41, "1.16.0;ls"):
        rc, antwort, _ = host.betrieb("aktualisieren", "--version", falsch, "--manifest", str(m),
                                      "--signatur", str(s))  # fmt: skip
        assert rc == 3 and antwort["meldung"] == "--version fehlt bzw. ist ungültig"
    rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "main")
    assert rc == 3 and "kein --ref" in antwort["meldung"]
    rc, antwort, _ = host.betrieb("aktualisieren", "--version")
    assert rc == 3 and antwort["meldung"] == "Angabe ohne Wert"
    gross = host.tmp / "gross.json"
    gross.write_bytes(b" " * 65537)
    rc, antwort, _ = host.betrieb("aktualisieren", "--version", version, "--manifest", str(gross),
                                  "--signatur", str(s))  # fmt: skip
    assert rc == 3 and "64 KiB" in antwort["meldung"]
    # Produkt eines anderen Herausgebers (gleicher Schluessel)
    daten = json.loads(m.read_text())
    daten["produkt"] = "cockpit"
    fremd = (json.dumps(daten, indent=2) + "\n").encode()
    m.write_bytes(fremd)
    s.write_text(rm.signieren(str(host.privat), fremd))
    rc, antwort, _ = host.betrieb("aktualisieren", "--version", version, "--manifest", str(m),
                                  "--signatur", str(s))  # fmt: skip
    assert rc == 3 and "Produkt bzw. Version" in antwort["meldung"]
    rc, antwort, _ = host.betrieb("kaputt")
    assert rc == 3 and antwort["aktion"] == "kaputt" and "Unbekannte Aktion" in antwort["meldung"]
    rc, antwort, _ = host.betrieb()
    assert rc == 3 and antwort["aktion"] == "?"
    _unveraendert(host)


def test_herausgeber_schluessel_fest_vor_checkout(host):
    # ohne Schluessel: abgelehnt
    (host.checkout / "deploy/release/herausgeber.pub").unlink()
    rc, antwort, _ = host.aktualisieren("1.16.0")
    assert rc == 3 and antwort["meldung"].startswith("Herausgeber-Schlüssel fehlt")
    _unveraendert(host)
    _git(host.checkout, "checkout", "--", "deploy/release/herausgeber.pub")
    # /etc/automato/herausgeber.pub geht vor: ein anderer Schluessel dort lehnt ab
    host.etc.parent.mkdir(parents=True)
    _, anderer_pub = _schluessel(host.etc.parent)
    assert anderer_pub == host.etc
    rc, antwort, _ = host.aktualisieren("1.16.0")
    assert rc == 3 and "Signatur des Manifests" in antwort["meldung"]
    host.etc.write_text("kein Schluessel\n")
    rc, antwort, _ = host.aktualisieren("1.16.0")
    assert rc == 3 and antwort["meldung"].startswith("Herausgeber-Schlüssel ungültig")
    _unveraendert(host)
    # der richtige fest auf dem Host, keiner im Checkout: angenommen
    shutil.copy2(host.checkout / "deploy/release/herausgeber.pub", host.etc)
    (host.checkout / "deploy/release/herausgeber.pub").unlink()
    _git(host.checkout, "commit", "-q", "-am", "ohne Schluessel")  # Checkout ohne lokale Aenderung
    rc, antwort, stderr = host.aktualisieren("1.16.0")
    assert rc == 0, stderr
    assert f"Manifest pruefen ({host.etc})" in stderr


def test_lokale_aenderungen_und_ohne_sicherung_abgelehnt(host):
    (host.checkout / "VERSION").write_text("1.15.0-lokal\n")
    rc, antwort, _ = host.aktualisieren("1.16.0")
    assert rc == 3 and antwort["meldung"].startswith("Lokale Änderungen im Checkout")
    _git(host.checkout, "checkout", "--", "VERSION")
    host.leeren()
    rc, antwort, _ = host.aktualisieren("1.16.0", AUTOMATO_BACKUP_PLAIN="")
    assert rc == 3 and "age-Schlüssel fehlt" in antwort["meldung"]
    assert host.head() == host.commits["1.15.0"]
    assert not [z for z in host.protokoll() if z.startswith("backup")]


def test_images_nicht_geholt_nichts_umgestellt(host):
    ref = host.images("1.16.0")["web-ui"]
    (host.stub / "pull-scheitert").write_text(ref + "\n")
    rc, antwort, _ = host.aktualisieren("1.16.0")
    assert rc == 1 and antwort["ok"] is False
    assert (
        antwort["meldung"].startswith("Images nicht geholt")
        and "läuft auf 1.15.0" in antwort["meldung"]
    )
    assert host.head() == host.commits["1.15.0"]
    assert not (host.checkout / ".automato-images.env").exists()
    assert not list(host.checkout.glob(".automato-images.env*"))
    assert not (host.checkout / ".automato-update").exists()
    assert not [z for z in host.protokoll() if " up " in z or "restore" in z]
    assert antwort["stand"]["quelle"] == "git" and antwort["stand"]["zurueck_moeglich"] is False


def test_health_scheitert_rueckfall_auf_gebauten_stand(host):
    rc, antwort, stderr = host.aktualisieren("1.17.0", STUB_KAPUTT=host.commits["1.17.0"])
    version = host.version("1.17.0")
    assert rc == 1, stderr
    assert antwort["ok"] is False
    assert (
        antwort["meldung"]
        == f"{version} nicht gesund – zurück auf 1.15.0 (Code, Images, Datenbank)"
    )
    assert host.head() == host.commits["1.15.0"]
    assert not (host.checkout / ".automato-images.env").exists()
    log = host.protokoll()
    # vorher gebaut: wieder gebaut, Datenbank aus der eben gemachten Sicherung
    assert "restore automato-1.dump.age head=" + host.commits["1.15.0"][:7] + " images=0" in log
    assert "docker compose --profile core build" in log
    assert antwort["stand"]["quelle"] == "git" and antwort["stand"]["gesund"] is True
    # kein frueheres Update: nichts, wohin zurueck
    assert antwort["stand"]["zurueck_moeglich"] is False and antwort["stand"]["vorher"] is None
    assert not (host.checkout / ".automato-update").exists()


def test_health_scheitert_rueckfall_auf_vorherige_images(host):
    assert host.aktualisieren("1.16.0")[0] == 0
    host.leeren()
    rc, antwort, stderr = host.aktualisieren("1.17.0", STUB_KAPUTT=host.commits["1.17.0"])
    assert rc == 1, stderr
    assert host.head() == host.commits["1.16.0"]
    assert (host.checkout / ".automato-images.env").read_text().count(
        host.images("1.16.0")["orchestrator"]
    ) == 1
    log = host.protokoll()
    assert not [z for z in log if " build" in z]
    assert "restore automato-2.dump.age head=" + host.commits["1.16.0"][:7] + " images=3" in log
    ups = [z for z in log if z.startswith("up-images")]
    assert host.images("1.17.0")["orchestrator"] in ups[0]
    assert host.images("1.16.0")["orchestrator"] in ups[-1]
    # Stand fuer zurueck wie nach dem erfolgreichen Update (vorher 1.15.0)
    stand = antwort["stand"]
    assert stand["version"] == host.version("1.16.0") and stand["quelle"] == "images"
    assert stand["vorher"]["version"] == "1.15.0" and stand["zurueck_moeglich"] is True


def test_rueckfall_gescheitert_von_hand(host):
    (host.stub / "restore-scheitert").write_text("")
    rc, antwort, _ = host.aktualisieren("1.17.0", STUB_KAPUTT=host.commits["1.17.0"])
    assert rc == 2 and antwort["ok"] is False
    assert (
        "Rückfall gescheitert" in antwort["meldung"]
        and "update.sh --rollback" in antwort["meldung"]
    )
    # der Stand dieses Versuchs bleibt fuer den Rueckfall von Hand
    assert "prev_version=1.15.0" in (host.checkout / ".automato-update").read_text()


def test_zurueck_nur_einmal_mit_datenbank(host):
    rc, antwort, _ = host.betrieb("zurueck")
    assert rc == 3 and antwort["meldung"].startswith("Kein Stand für zurück")
    assert host.aktualisieren("1.16.0")[0] == 0
    host.leeren()
    rc, antwort, stderr = host.betrieb("zurueck")
    assert rc == 0, stderr
    assert (
        antwort["meldung"]
        == f"{host.version('1.16.0')} → 1.15.0 zurück (Code, Images, Datenbank), Health ok"
    )
    assert host.head() == host.commits["1.15.0"]
    assert not (host.checkout / ".automato-images.env").exists()
    log = host.protokoll()
    assert "docker compose --profile core build" in log  # 1.15.0 war gebaut
    assert "restore automato-1.dump.age head=" + host.commits["1.15.0"][:7] + " images=0" in log
    assert antwort["stand"]["zurueck_moeglich"] is False and antwort["stand"]["quelle"] == "git"
    rc, antwort, _ = host.betrieb("zurueck")
    assert rc == 3 and antwort["stand"]["zurueck_moeglich"] is False


def test_sperre_belegt(host):
    with open(host.checkout / ".betrieb.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc, antwort, _ = host.betrieb("stand")
        assert rc == 4 and antwort["ok"] is False
        assert antwort["meldung"] == "Belegt – auf diesem Checkout läuft schon eine Aktion"
        rc, antwort, _ = host.aktualisieren("1.16.0")
        assert rc == 4 and host.head() == host.commits["1.15.0"]
        out = host.run("main", skript="deploy/update.sh")
        assert out.returncode == 2 and "laeuft schon ein Update" in out.stderr
    assert host.betrieb("stand")[0] == 0


def test_sichern_und_neustart(host):
    rc, antwort, _ = host.betrieb("sichern")
    assert rc == 0 and antwort["meldung"] == "Sicherung fertig"
    assert antwort["stand"]["letzte_sicherung"] == "2025-11-16T08:01:00Z"
    rc, antwort, _ = host.betrieb("sichern", AUTOMATO_BACKUP_PLAIN="")
    assert rc == 3 and "age-Schlüssel fehlt" in antwort["meldung"]
    (host.stub / "backup-scheitert").write_text("")
    rc, antwort, _ = host.betrieb("sichern")
    assert rc == 2 and antwort["meldung"].startswith("Sicherung fehlgeschlagen")
    host.leeren()
    rc, antwort, _ = host.betrieb("neustart")
    assert rc == 0 and antwort["meldung"] == "Neu gestartet, Health ok"
    assert "docker compose --profile core restart" in host.protokoll()
    assert not [z for z in host.protokoll() if " build" in z or "pull" in z]
    (host.stub / "health-aus").write_text("")
    rc, antwort, _ = host.betrieb("neustart")
    assert rc == 2 and antwort["stand"]["gesund"] is False


def test_update_sh_baut_wieder_aus_git_und_rollback_stellt_images_her(host):
    """update.sh bleibt der Bau aus git: entfernt gepinnte Images; --rollback holt sie zurueck."""
    assert host.aktualisieren("1.16.0")[0] == 0
    host.leeren()
    out = host.run("main", skript="deploy/update.sh")
    assert out.returncode == 0, out.stdout + out.stderr
    assert host.head() == host.commits["1.17.0"]
    assert _git(host.checkout, "symbolic-ref", "--short", "HEAD") == "main"
    assert not (host.checkout / ".automato-images.env").exists()
    log = host.protokoll()
    assert "docker compose --profile core build --pull" in log
    assert "up-images " in log  # ohne gepinnte Images gestartet
    assert f"{host.version('1.16.0')} ({host.commits['1.16.0']}) -> 1.17.0" in out.stdout
    rc, antwort, _ = host.betrieb("stand")
    assert antwort["stand"]["quelle"] == "git" and antwort["stand"]["version"] == "1.17.0"
    assert antwort["stand"]["vorher"]["version"] == host.version("1.16.0")

    host.leeren()
    out = host.run("--rollback", skript="deploy/update.sh")
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"Rueckfall fertig: Version {host.version('1.16.0')}." in out.stdout
    assert host.head() == host.commits["1.16.0"]
    assert host.images("1.16.0")["web-ui"] in (host.checkout / ".automato-images.env").read_text()
    log = host.protokoll()
    assert not [z for z in log if " build" in z]
    assert "docker compose --profile core up -d --no-build" in log
    rc, antwort, _ = host.betrieb("stand")
    assert antwort["stand"]["quelle"] == "images" and antwort["stand"]["zurueck_moeglich"] is False


def test_unerwarteter_abbruch_gibt_trotzdem_eine_antwort(host):
    """Bricht die Aktion ohne Antwort ab, steht trotzdem genau eine Zeile JSON auf stdout."""
    (host.checkout / ".automato-update").write_text("exit 9\n")
    rc, antwort, _ = host.betrieb("stand")
    assert rc == 2
    _format(antwort, "stand")
    assert antwort["ok"] is False and antwort["meldung"].startswith("Unerwarteter Abbruch (Exit 9)")
    assert (
        antwort["stand"]["vorher"] is None and antwort["stand"]["commit"] == host.commits["1.15.0"]
    )


def test_stand_meldet_herkunft(host):
    """Branch bzw. losgelöst und Kanal (1.18.1) - das Cockpit wählt damit den Kanal ohne Zutun."""

    def herkunft() -> dict:
        rc, antwort, stderr = host.betrieb("stand")
        assert rc == 0, stderr
        _format(antwort, "stand")
        return antwort["stand"]["herkunft"]

    def erwartet(branch, kanal, aus) -> dict:
        return {"branch": branch, "losgeloest": branch is None, "kanal": kanal, "aus": aus}

    # aus git gebaut: Kanal nach Branch, losgelöst nur mit Tag v<VERSION>
    assert herkunft() == erwartet("main", "latest", "branch")
    _git(host.checkout, "checkout", "-q", "-b", DEV_BRANCH)
    assert herkunft() == erwartet(DEV_BRANCH, "dev", "branch")
    _git(host.checkout, "checkout", "-q", "-b", "andere-arbeit")
    assert herkunft() == erwartet("andere-arbeit", "unbekannt", None)
    _git(host.checkout, "checkout", "-q", "--detach")
    assert herkunft() == erwartet(None, "unbekannt", None)
    _git(host.checkout, "tag", "v1.15.0")
    assert herkunft() == erwartet(None, "stable", "tag")
    # aus dem Manifest: dessen Kanal; ohne Kanal-Zeile nach dem Suffix der Version
    _git(host.checkout, "checkout", "-q", "main")
    assert host.aktualisieren("1.16.0", kanal="stable")[0] == 0
    assert herkunft() == erwartet(None, "stable", "manifest")
    datei = host.checkout / ".automato-images.env"
    zeilen = [z for z in datei.read_text().splitlines() if "MANIFEST_KANAL=" not in z]
    neu = f"AUTOMATO_MANIFEST_VERSION=1.16.0-latest.2.{host.commits['1.16.0'][:7]}"
    datei.write_text("\n".join(neu if "MANIFEST_VERSION=" in z else z for z in zeilen) + "\n")
    assert herkunft() == erwartet(None, "latest", "version")


def test_gleicher_kern_nie_aelter(host):
    """Nie älter, auch bei gleichem Kern (1.18.1): im selben Kanal entscheidet n, sonst (anderer
    Kanal, stable gegen dev) die Abstammung im Checkout - Vorfahre des laufenden Stands = älter."""
    c4 = host.commit_im_ursprung("Doku 1")
    c5 = host.commit_im_ursprung("Doku 2")
    assert host.aktualisieren("1.17.0", kanal="stable")[0] == 0
    # Nachfolger mit gleichem Kern: angenommen
    dev5 = f"1.17.0-dev.5.{c5[:7]}"
    rc, antwort, stderr = host.aktualisieren_auf(c5, dev5, "dev")
    assert rc == 0, stderr
    assert host.head() == c5 and antwort["meldung"] == f"1.17.0 → {dev5}, Health ok"
    host.leeren()
    aelter = "ist älter als {} – zurück nur über die Aktion zurueck"
    # stable 1.17.0 ist Vorfahre des laufenden Stands (vergleichen allein sagte „neuer“)
    rc, antwort, _ = host.aktualisieren_auf(host.commits["1.17.0"], "1.17.0", "stable")
    assert rc == 3 and antwort["meldung"] == "1.17.0 " + aelter.format(dev5)
    # selber Kanal, kleineres n: älter schon nach der Version
    rc, antwort, _ = host.aktualisieren_auf(c4, f"1.17.0-dev.4.{c4[:7]}", "dev")
    assert rc == 3 and antwort["meldung"] == f"1.17.0-dev.4.{c4[:7]} " + aelter.format(dev5)
    # anderer Kanal, Vorfahre: älter
    rc, antwort, _ = host.aktualisieren_auf(c4, f"1.17.0-latest.4.{c4[:7]}", "latest")
    assert rc == 3 and antwort["meldung"] == f"1.17.0-latest.4.{c4[:7]} " + aelter.format(dev5)
    assert host.head() == c5 and host.protokoll() == []  # weder Sicherung noch docker
    assert antwort["stand"]["version"] == dev5


def test_gleicher_kern_getrennte_linie_abgelehnt(host):
    """Eine eigene Linie mit gleichem Kern (weder Vorfahre noch Nachfolger) wird abgelehnt - kennt
    der Checkout den Commit erst nach dem Holen, vor dem Umstellen."""
    c4 = host.commit_im_ursprung("Doku 1")
    dev4 = f"1.17.0-dev.4.{c4[:7]}"
    assert host.aktualisieren_auf(c4, dev4, "dev")[0] == 0
    seite = host.commit_im_ursprung("Seite", von=host.commits["1.17.0"], branch="seite")
    host.leeren()
    latest = f"1.17.0-latest.4.{seite[:7]}"
    rc, antwort, stderr = host.aktualisieren_auf(seite, latest, "latest")
    assert rc == 3, stderr
    assert antwort["meldung"] == (
        f"{latest} liegt auf einer anderen Linie als {dev4} (weder Vorfahre noch Nachfolger)"
        f" – nichts umgestellt, läuft auf {dev4}"
    )
    assert host.head() == c4 and antwort["stand"]["version"] == dev4
    assert not [z for z in host.protokoll() if "pull" in z or " up " in z or "restore" in z]
    assert dev4 in (host.checkout / ".automato-images.env").read_text()
    # jetzt im Checkout: abgelehnt, bevor etwas geschieht (auch gleiches n im selben Kanal)
    host.leeren()
    rc, antwort, _ = host.aktualisieren_auf(seite, f"1.17.0-dev.4.{seite[:7]}", "dev")
    assert rc == 3
    assert antwort["meldung"].endswith("(weder Vorfahre noch Nachfolger) – nichts geändert")
    assert host.head() == c4 and host.protokoll() == []


def test_abstammung_im_repo_auch_flach(vorlage, tmp_path):
    """lu_abstammung (deploy/lib-update.sh): nur mit Objekten im Repo; flach nie „getrennt“."""
    basis, commits, _ = vorlage
    origin = tmp_path / "origin"
    shutil.copytree(basis / "origin", origin, symlinks=True)
    c1, c2, c3 = (commits[v] for v in VERSIONEN)
    _git(origin, "checkout", "-q", "-b", "seite", c2)
    (origin / "NOTIZ").write_text("seite\n")
    _git(origin, "add", "NOTIZ")
    _git(origin, "commit", "-q", "-m", "seite")
    seite = _git(origin, "rev-parse", "HEAD")
    _git(origin, "checkout", "-q", "main")

    def abstammung(repo: Path, laufend: str, ziel: str) -> str:
        skript = '. "$0"; lu_abstammung "$1" "$2"'
        out = subprocess.run(["bash", "-c", skript, str(REPO / "deploy/lib-update.sh"),  # noqa: S603, S607
                              laufend, ziel], cwd=repo, capture_output=True, text=True,
                             env={**os.environ, **GIT_ENV}, check=True)  # fmt: skip
        return out.stdout.strip()

    assert abstammung(origin, c3, c1) == "aelter"
    assert abstammung(origin, c1, c3) == "neuer"
    assert abstammung(origin, c3, seite) == "getrennt"
    assert abstammung(origin, c3, c3) == "gleich"
    assert abstammung(origin, c3, "0" * 40) == "unbekannt"  # nicht im Repo
    assert abstammung(origin, c3, "HEAD") == "unbekannt"  # nur volle Commits
    flach = tmp_path / "flach"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--branch", "main", f"file://{origin}", "flach")
    _git(flach, "fetch", "-q", "--depth", "1", "origin", "seite")
    assert abstammung(flach, c3, seite) == "unbekannt"
    assert abstammung(flach, c3, c1) == "unbekannt"  # fehlt im flachen Checkout
