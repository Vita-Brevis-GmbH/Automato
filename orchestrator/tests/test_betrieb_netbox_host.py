"""deploy/netbox-host/betrieb.sh (1.17.0, ADR 0062, Vertrag betrieb/v1): Einstiegspunkt des
Host-Agenten des Cockpits auf dem NetBox-Host. Echte git-Repos (Ursprung und Checkout mit drei
Ständen: 1.17.0 und 1.18.0 mit verschiedener NetBox, 1.18.1 nur Werkzeug), echtes
netbox-host.sh; Ersatz für docker, git von netbox-docker, age und age-keygen (keine Registry, kein
GitHub). Geprüft: genau eine Zeile JSON, Update mit Sicherung und neuer NetBox je Instanz, nie
älter, Rückfall mit Datenbank aus der Sicherung, zurueck einmal, Sperren, Eingaben."""

import fcntl
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
    "deploy/lib-betrieb.sh",
    "deploy/netbox-host/betrieb.sh",
    "deploy/netbox-host/netbox-host.sh",
    "deploy/netbox-host/host.conf.example",
    "deploy/netbox-host/proxy/docker-compose.yml",
    "deploy/netbox-host/proxy/Caddyfile",
    "source-of-truth/netbox/sync-user.sh",
    ".gitignore",
)
# Version des Werkzeugs -> netbox-docker-Tag und NetBox-Image
STAENDE = {
    "1.17.0": ("5.0.2", "v4.6.10-5.0.2"),
    "1.18.0": ("5.1.0", "v4.7.0-5.1.0"),
    "1.18.1": ("5.1.0", "v4.7.0-5.1.0"),
}
ALT, NEU = "v4.6.10-5.0.2", "v4.7.0-5.1.0"
AUFTRAG = "3f1c2b6e-0000-4000-8000-000000000062"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.ch",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.ch",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
}  # fmt: skip

pytestmark = pytest.mark.skipif(
    not all(shutil.which(w) for w in ("bash", "git", "flock", "openssl", "base64", "gzip")),
    reason="bash, git, flock, openssl, base64 bzw. gzip fehlen",
)

# docker: Aufrufe mit Ordner protokolliert; Health je Instanz aus ihrer .env (VERSION in
# "kaputt" bzw. Datei "aus-<kunde>" = antwortet nicht); Datenbank: Dump nennt Kunde und Version,
# Einspielen und Leeren landen in "db"
DOCKER = r"""#!/usr/bin/env bash
echo "$PWD|$*" >>"$STUB_LOG"
if [ "$1" = network ]; then [ "$2" = inspect ] && exit 1; exit 0; fi
[ "$1" = compose ] || exit 0
shift
k=$(basename "$(dirname "$PWD")")
v=$(sed -n 's/^VERSION=//p' .env 2>/dev/null)
case "$*" in
  "config --services") printf 'netbox\nnetbox-worker\npostgres\nredis\nredis-cache\n' ;;
  "ps --status running --services") [ -f "$STUB_DIR/proxy-aus" ] || echo proxy ;;
  ps*) exit 0 ;;
  *"exec -T netbox curl"*)
    [ ! -f "$STUB_DIR/aus-$k" ] || exit 7
    ! grep -qxF "$v" "$STUB_DIR/kaputt" 2>/dev/null || exit 7 ;;
  *pg_dump*) echo "SQL-DUMP $k $v" ;;
  *"DROP SCHEMA"*) echo "leeren $k $v" >>"$STUB_DIR/db" ;;
  *psql*) echo "einspielen $k $(cat)" >>"$STUB_DIR/db" ;;
  *tar*-cf*) echo "MEDIEN" ;;
  *-xf* | *manage.py*) cat >/dev/null ;;
  restart*) [ ! -f "$STUB_DIR/restart-aus" ] ;;
  *) exit 0 ;;
esac
"""
# git von netbox-docker: Stand je Instanz in .stub-head (Tag); "fetch-aus-<kunde>" = kein Netz
NBGIT = r"""#!/usr/bin/env bash
echo "$PWD|git $*" >>"$STUB_LOG"
case "$1" in
  clone)
    dir="${*: -1}" tag=""
    while [ $# -gt 0 ]; do [ "$1" = -b ] && tag=$2; shift; done
    mkdir -p "$dir/env" "$dir/configuration"
    printf 'services:\n  netbox:\n    image: x\n' >"$dir/docker-compose.yml"
    echo "$tag" >"$dir/.stub-head" ;;
  fetch) [ ! -f "$STUB_DIR/fetch-aus-$(basename "$(dirname "$PWD")")" ] ;;
  rev-parse) cat .stub-head ;;
  -c) shift 2; [ "$1" != checkout ] || echo "${*: -1}" >.stub-head ;;
  checkout) echo "${*: -1}" >.stub-head ;;
esac
"""
# age: verschlüsselt "zum Schein" (Base64 hinter einer Kopfzeile) – Klartext steht nie in der Datei
AGE = r"""#!/usr/bin/env bash
case "$1" in
  -r) out="${*: -1}"; { echo "age-stub"; base64 -w0; echo; } >"$out" ;;
  -d) [ ! -f "$STUB_DIR/age-aus" ] || exit 1; tail -n +2 "${*: -1}" | base64 -d ;;
  *) exit 1 ;;
esac
"""
KEYGEN = r"""#!/usr/bin/env bash
if [ "$1" = -o ]; then echo "AGE-SECRET-KEY-TEST" >"$2"; exit 0; fi
if [ "$1" = -y ]; then [ -r "$2" ] && echo "age1testempfaenger"; exit 0; fi
exit 1
"""


def _datei(path: Path, text: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,  # noqa: S603, S607
                         env={**os.environ, **GIT_ENV}, check=True)  # fmt: skip
    return out.stdout.strip()


@pytest.fixture(scope="module")
def vorlage(tmp_path_factory):
    """Ursprung mit 1.17.0 (Tag v1.17.0), 1.18.0 (v1.18.0) und 1.18.1 (main)."""
    basis = tmp_path_factory.mktemp("nbh-betrieb")
    origin = basis / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    for name in DATEIEN:
        ziel = origin / name
        ziel.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, ziel)
    commits = {}
    for version, (tag, image) in STAENDE.items():
        (origin / "VERSION").write_text(version + "\n")
        _datei(origin / "deploy/netbox-host/netbox-version.conf",
               f"# Test\nNETBOX_DOCKER_TAG={tag}\nNETBOX_VERSION={image}\n")  # fmt: skip
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", f"Version {version}")
        commits[version] = _git(origin, "rev-parse", "HEAD")
        if version != "1.18.1":
            _git(origin, "tag", f"v{version}")
    _git(basis, "clone", "-q", str(origin), "checkout")
    _git(basis / "checkout", "checkout", "-q", "-B", "main", commits["1.17.0"])
    return basis, commits


@dataclass
class Host:
    tmp: Path
    commits: dict
    checkout: Path = field(init=False)
    nbh: Path = field(init=False)
    stub: Path = field(init=False)
    log: Path = field(init=False)
    backups: Path = field(init=False)
    env: dict = field(init=False)

    def __post_init__(self):
        self.checkout = self.tmp / "checkout"
        self.nbh = self.tmp / "nbh"
        self.stub = self.tmp / "stub"
        self.log = self.stub / "log"
        self.backups = self.tmp / "backups"
        self.stub.mkdir()
        self.log.write_text("")
        self.env = {
            "PATH": os.environ["PATH"], "LANG": "C.UTF-8", "BETRIEB_AUFTRAG": AUFTRAG,
            "HOME": str(self.tmp), **GIT_ENV, "STUB_LOG": str(self.log),
            "STUB_DIR": str(self.stub), "NBH_DIR": str(self.nbh), "NBH_SKIP_ROOT": "1",
            "NBH_WAIT": "0", "NBH_HEALTH_SEK": "0", "NBH_HEALTH_PAUSE": "0",
            "NBH_SYNC_WARTEN": "0", "AGE_IDENTITY": str(self.tmp / "backup.agekey"),
            "DOCKER": str(_datei(self.stub / "bin/docker", DOCKER, 0o755)),
            "GIT": str(_datei(self.stub / "bin/nb-git", NBGIT, 0o755)),
            "AGE": str(_datei(self.stub / "bin/age", AGE, 0o755)),
            "AGE_KEYGEN": str(_datei(self.stub / "bin/age-keygen", KEYGEN, 0o755)),
        }  # fmt: skip

    def nbh_sh(self, *args: str) -> subprocess.CompletedProcess:
        out = subprocess.run(["bash", "deploy/netbox-host/netbox-host.sh", *args],  # noqa: S603, S607
                             cwd=self.checkout, env=self.env, capture_output=True, text=True,
                             timeout=120, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
        assert out.returncode == 0, out.stdout + out.stderr
        return out

    def betrieb(self, *args: str, **extra: str) -> tuple[int, dict, str]:
        out = subprocess.run(["bash", "deploy/netbox-host/betrieb.sh", *args],  # noqa: S603, S607
                             cwd=self.checkout, env={**self.env, **extra}, capture_output=True,
                             text=True, timeout=120, check=False)  # fmt: skip
        zeilen = out.stdout.splitlines()
        assert len(zeilen) == 1, f"stdout: {out.stdout!r}\nstderr: {out.stderr}"
        return out.returncode, json.loads(zeilen[0]), out.stderr

    def head(self) -> str:
        return _git(self.checkout, "rev-parse", "HEAD")

    def branch(self) -> str:
        out = subprocess.run(["git", "symbolic-ref", "-q", "--short", "HEAD"],  # noqa: S603, S607
                             cwd=self.checkout, capture_output=True, text=True,
                             check=False)  # fmt: skip
        return out.stdout.strip()

    def nb(self, kunde: str) -> Path:
        return self.nbh / "kunden" / kunde / "netbox"

    def version(self, kunde: str) -> str:
        return re.search(r"^VERSION=(.*)$", (self.nb(kunde) / ".env").read_text(), re.M).group(1)

    def db(self) -> list[str]:
        f = self.stub / "db"
        return f.read_text().splitlines() if f.exists() else []

    def dumps(self, kunde: str) -> list[Path]:
        return sorted((self.backups / kunde).glob(f"{kunde}-*.sql.gz.age"))

    def protokoll(self) -> list[str]:
        return self.log.read_text().splitlines()

    def conf(self, text: str) -> None:
        with (self.nbh / "host.conf").open("a") as f:
            f.write(text)


@pytest.fixture
def host(vorlage, tmp_path) -> Host:
    basis, commits = vorlage
    shutil.copytree(basis / "origin", tmp_path / "origin", symlinks=True)
    shutil.copytree(basis / "checkout", tmp_path / "checkout", symlinks=True)
    _git(tmp_path / "checkout", "remote", "set-url", "origin", str(tmp_path / "origin"))
    h = Host(tmp_path, commits)
    h.nbh_sh("init")
    h.conf(f"BACKUP_DIR={h.backups}\n")
    h.nbh_sh("neu", "kunde-a", "Kunde A")
    h.nbh_sh("neu", "kunde-b", "Kunde B")
    h.log.write_text("")
    return h


STAND_FELDER = {"version", "commit", "quelle", "kanal", "gesund", "zurueck_moeglich", "vorher",
                "letzte_sicherung"}  # fmt: skip


def _format(antwort: dict, aktion: str) -> None:
    assert set(antwort) == {"format", "produkt", "aktion", "ok", "meldung", "stand"}
    assert antwort["format"] == "betrieb/v1" and antwort["produkt"] == "netbox-host"
    assert antwort["aktion"] == aktion
    assert isinstance(antwort["ok"], bool) and 0 < len(antwort["meldung"]) <= 300
    assert set(antwort["stand"]) == STAND_FELDER
    assert antwort["stand"]["quelle"] == "git" and antwort["stand"]["kanal"] is None


def _unveraendert(host: Host) -> None:
    assert host.head() == host.commits["1.17.0"] and host.branch() == "main"
    assert host.version("kunde-a") == ALT and host.version("kunde-b") == ALT
    assert not host.dumps("kunde-a") and host.db() == []
    assert not (host.nbh / "betrieb").exists()


def test_stand(host):
    rc, antwort, stderr = host.betrieb("stand")
    assert rc == 0, stderr
    _format(antwort, "stand")
    assert antwort["ok"] is True
    assert antwort["meldung"] == f"1.17.0 (main), NetBox {ALT}, 2 Instanzen gesund"
    assert antwort["stand"] == {
        "version": "1.17.0", "commit": host.commits["1.17.0"], "quelle": "git", "kanal": None,
        "gesund": True, "zurueck_moeglich": False, "vorher": None, "letzte_sicherung": None,
    }  # fmt: skip
    assert f"Auftrag {AUFTRAG}: netbox-host stand" in stderr
    (host.stub / "aus-kunde-b").write_text("")
    (host.stub / "proxy-aus").write_text("")
    rc, antwort, _ = host.betrieb("stand")
    assert rc == 0 and antwort["stand"]["gesund"] is False
    assert antwort["meldung"] == (
        f"1.17.0 (main), NetBox {ALT}, Proxy läuft nicht, 1 von 2 Instanzen nicht gesund: kunde-b"
    )
    # nur lesen: nichts gesichert, nichts neu gestartet
    assert not [z for z in host.protokoll() if "restart" in z or "pg_dump" in z]


def test_aktualisieren_neue_netbox_je_instanz(host):
    rc, antwort, stderr = host.betrieb("aktualisieren", "--ref", "v1.18.0")
    assert rc == 0, stderr
    _format(antwort, "aktualisieren")
    assert antwort["meldung"] == f"1.17.0 → 1.18.0, NetBox {ALT} → {NEU} (2 Instanzen), Health ok"
    stand = antwort["stand"]
    assert stand["version"] == "1.18.0" and stand["commit"] == host.commits["1.18.0"]
    assert stand["gesund"] is True and stand["zurueck_moeglich"] is True
    assert stand["vorher"]["version"] == "1.17.0"
    assert stand["vorher"]["commit"] == host.commits["1.17.0"]
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", stand["vorher"]["zeit"])
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", stand["letzte_sicherung"])
    # Tag: losgelöst; jede Instanz auf der neuen NetBox, vorher gesichert (alle + je update)
    assert host.head() == host.commits["1.18.0"] and host.branch() == ""
    for k in ("kunde-a", "kunde-b"):
        assert host.version(k) == NEU
        assert (host.nb(k) / ".stub-head").read_text().strip() == "5.1.0"
        assert host.dumps(k), "Sicherung fehlt"
    log = host.protokoll()
    assert sum("fetch -q --depth 1 origin tag 5.1.0" in z for z in log) == 2
    assert sum("pg_dump" in z for z in log) >= 4  # sichern alle + Sicherung vor jedem update
    assert list((host.backups / "_konfig").glob("konfig-*.tar.gz.age"))
    for d in host.dumps("kunde-a"):
        assert b"SQL-DUMP" not in d.read_bytes()  # nie Klartext
    assert "1/4 Sicherung aller Instanzen" in stderr and "Health" in stderr
    # Zustand für zurück: keine Geheimnisse, Override je Instanz
    zustand = host.nbh / "betrieb" / "zustand"
    assert (zustand / "stand").stat().st_mode & 0o777 == 0o600
    assert "vorher_version=1.17.0" in (zustand / "stand").read_text()
    assert f"version={ALT}" in (zustand / "instanzen" / "kunde-a").read_text()
    assert "NBH_SECRET_KEY:" not in (zustand / "instanzen" / "kunde-a.override.yml").read_text()
    assert "NBH_SECRET_KEY=" not in "".join(
        p.read_text() for p in zustand.rglob("*") if p.is_file()
    )

    # gleicher Stand: nichts zu tun; älter: nur über zurueck
    host.log.write_text("")
    rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "v1.18.0")
    assert rc == 0 and antwort["meldung"] == "schon auf 1.18.0"
    rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "v1.17.0")
    assert rc == 3
    assert antwort["meldung"] == (
        "v1.17.0 (1.17.0) ist älter als 1.18.0 – zurück nur über die Aktion zurueck; "
        "nichts geändert"
    )
    assert not [z for z in host.protokoll() if "pg_dump" in z or "compose up" in z]

    # nur das Werkzeug neu (gleiche NetBox): Branch wieder angehängt, keine Instanz angefasst
    rc, antwort, stderr = host.betrieb("aktualisieren", "--ref", "main")
    assert rc == 0, stderr
    assert antwort["meldung"] == f"1.18.0 → 1.18.1, NetBox {NEU} unverändert, Health ok"
    assert host.head() == host.commits["1.18.1"] and host.branch() == "main"
    assert not [z for z in host.protokoll() if "origin tag" in z]
    assert antwort["stand"]["vorher"]["version"] == "1.18.0"


def test_health_scheitert_rueckfall_mit_datenbank(host):
    (host.stub / "kaputt").write_text(NEU + "\n")
    rc, antwort, stderr = host.betrieb("aktualisieren", "--ref", "v1.18.0")
    assert rc == 1, stderr
    _format(antwort, "aktualisieren")
    assert antwort["ok"] is False
    assert antwort["meldung"] == (
        "1.18.0 gescheitert (Health nicht ok: 2 von 2 Instanzen nicht gesund: kunde-a kunde-b) – "
        "zurück auf 1.17.0, 2 Instanzen aus der Sicherung, Health ok"
    )
    # Checkout und Branch zurück, je Instanz netbox-docker, VERSION und Datenbank
    assert host.head() == host.commits["1.17.0"] and host.branch() == "main"
    for k in ("kunde-a", "kunde-b"):
        assert host.version(k) == ALT
        assert (host.nb(k) / ".stub-head").read_text().strip() == "5.0.2"
        # zuerst geleert, dann die Sicherung, die update vor dem Umstellen gemacht hat
        assert host.db().index(f"leeren {k} {ALT}") < host.db().index(
            f"einspielen {k} SQL-DUMP {k} {ALT}"
        )
    stand = antwort["stand"]
    assert stand["gesund"] is True and stand["version"] == "1.17.0"
    assert stand["zurueck_moeglich"] is False and stand["vorher"] is None
    assert not (host.nbh / "betrieb" / "zustand").exists()


def test_update_einer_instanz_scheitert(host):
    (host.stub / "fetch-aus-kunde-b").write_text("")
    rc, antwort, stderr = host.betrieb("aktualisieren", "--ref", "v1.18.0")
    assert rc == 1, stderr
    assert antwort["meldung"] == (
        "1.18.0 gescheitert (Update der NetBox kunde-b gescheitert) – zurück auf 1.17.0, "
        "1 Instanzen aus der Sicherung, Health ok"
    )
    assert host.version("kunde-a") == ALT and host.version("kunde-b") == ALT
    # kunde-b blieb unverändert: nicht geleert, nicht eingespielt
    assert [z for z in host.db() if "kunde-b" in z] == []
    assert any(z.startswith("einspielen kunde-a") for z in host.db())


def test_rueckfall_gescheitert_von_hand(host):
    (host.stub / "kaputt").write_text(NEU + "\n")
    (host.stub / "age-aus").write_text("")  # Sicherung nicht lesbar
    rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "v1.18.0")
    assert rc == 2 and antwort["ok"] is False
    assert "Rückfall gescheitert (NetBox kunde-a, NetBox kunde-b)" in antwort["meldung"]
    assert "von Hand" in antwort["meldung"]
    # Datenbank nie geleert, wenn die Sicherung nicht lesbar ist
    assert not [z for z in host.db() if z.startswith("leeren")]
    # der Stand dieses Versuchs bleibt für zurück von Hand
    assert "vorher_version=1.17.0" in (host.nbh / "betrieb/zustand/stand").read_text()


def test_zurueck_nur_einmal(host):
    rc, antwort, _ = host.betrieb("zurueck")
    assert rc == 3 and antwort["meldung"].startswith("Kein Stand für zurück")
    assert host.betrieb("aktualisieren", "--ref", "v1.18.0")[0] == 0
    neueste = host.dumps("kunde-a")[-1].name
    rc, antwort, stderr = host.betrieb("zurueck")
    assert rc == 0, stderr
    _format(antwort, "zurueck")
    assert antwort["meldung"] == (
        f"1.18.0 → 1.17.0 zurück (NetBox {ALT}, 2 Instanzen aus der Sicherung), Health ok"
    )
    assert host.head() == host.commits["1.17.0"] and host.branch() == "main"
    assert host.version("kunde-a") == ALT and host.version("kunde-b") == ALT
    assert neueste in stderr  # die Sicherung, die update vor dem Umstellen gemacht hat
    assert antwort["stand"]["zurueck_moeglich"] is False
    assert antwort["stand"]["vorher"]["version"] == "1.17.0"
    rc, antwort, _ = host.betrieb("zurueck")
    assert rc == 3


@pytest.mark.parametrize(
    "args",
    [(), ("--ref",), ("--version", "1.18.0"), ("--ref", "../v1.18.0"), ("--ref", "-x"),
     ("--ref", "a..b"), ("--ref", "main@{1}"), ("--ref", "HEAD"), ("--ref", "gibt-es-nicht"),
     ("--ref", "main", "--ref", "main")],
)  # fmt: skip
def test_eingaben_abgelehnt(host, args):
    rc, antwort, _ = host.betrieb("aktualisieren", *args)
    assert rc == 3 and antwort["ok"] is False
    _format(antwort, "aktualisieren")
    _unveraendert(host)


def test_aktion_und_angaben(host):
    rc, antwort, _ = host.betrieb("kaputt")
    assert rc == 3 and antwort["aktion"] == "kaputt" and "Unbekannte Aktion" in antwort["meldung"]
    rc, antwort, _ = host.betrieb()
    assert rc == 3 and antwort["aktion"] == "?"
    rc, antwort, _ = host.betrieb("stand", "--alles")
    assert rc == 3 and "keine Angaben" in antwort["meldung"]
    _unveraendert(host)


def test_lokale_aenderungen_und_ohne_identitaet_abgelehnt(host):
    (host.checkout / "VERSION").write_text("1.17.0-lokal\n")
    rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "v1.18.0")
    assert rc == 3 and antwort["meldung"].startswith("Lokale Änderungen im Checkout")
    _git(host.checkout, "checkout", "--", "VERSION")
    (host.tmp / "backup.agekey").unlink()
    rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "v1.18.0")
    assert rc == 3 and antwort["meldung"].startswith("Ohne age-Identität")
    _unveraendert(host)


def test_netbox_fest_in_host_conf(host):
    host.conf(f"NETBOX_DOCKER_TAG=5.0.2\nNETBOX_VERSION={ALT}\n")
    rc, antwort, _ = host.betrieb("stand")
    assert (
        antwort["meldung"] == f"1.17.0 (main), NetBox {ALT} (fest in host.conf), 2 Instanzen gesund"
    )
    rc, antwort, stderr = host.betrieb("aktualisieren", "--ref", "v1.18.0")
    assert rc == 0, stderr
    assert antwort["meldung"] == f"1.17.0 → 1.18.0, NetBox {ALT} unverändert, Health ok"
    assert host.version("kunde-a") == ALT and host.head() == host.commits["1.18.0"]


def test_nie_aeltere_netbox_als_eine_instanz(host):
    env = host.nb("kunde-a") / ".env"
    env.write_text(env.read_text().replace(f"VERSION={ALT}", "VERSION=v4.8.0-6.0.0"))
    rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "v1.18.0")
    assert rc == 3
    assert antwort["meldung"] == (
        f"NetBox {NEU} wäre älter als v4.8.0-6.0.0 der Instanz kunde-a – nichts geändert"
    )
    assert host.head() == host.commits["1.17.0"] and not host.dumps("kunde-a")


def test_sperren(host):
    with open(host.checkout / ".betrieb.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc, antwort, _ = host.betrieb("stand")
        assert (
            rc == 4 and antwort["meldung"] == "Belegt – auf diesem Checkout läuft schon eine Aktion"
        )
    # der Abgleich des Agenten läuft: kein Update daneben
    with open(host.nbh / ".sync.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "v1.18.0")
        assert rc == 4 and "Abgleich des Agenten" in antwort["meldung"]
    _unveraendert(host)
    # danach wieder frei
    assert host.betrieb("stand")[0] == 0


def test_sichern_und_neustart(host):
    rc, antwort, _ = host.betrieb("sichern")
    assert (
        rc == 0 and antwort["meldung"] == "Sicherung von 2 Instanzen und der Konfiguration fertig"
    )
    assert host.dumps("kunde-a") and host.dumps("kunde-b")
    assert antwort["stand"]["letzte_sicherung"] is not None
    conf = host.nbh / "host.conf"
    conf.write_text(re.sub(r"^AGE_RECIPIENT=.*$", "", conf.read_text(), flags=re.M))
    rc, antwort, _ = host.betrieb("sichern")
    assert rc == 3 and antwort["meldung"].startswith("AGE_RECIPIENT fehlt")
    host.log.write_text("")
    rc, antwort, _ = host.betrieb("neustart")
    assert rc == 0 and antwort["meldung"] == "Proxy und 2 Instanzen neu gestartet, Health ok"
    log = host.protokoll()
    assert f"{host.nbh / 'proxy'}|compose restart" in log
    assert f"{host.nb('kunde-a')}|compose restart" in log
    assert not [z for z in log if "pull" in z or " up " in z]
    (host.stub / "aus-kunde-a").write_text("")
    rc, antwort, _ = host.betrieb("neustart")
    assert rc == 2 and antwort["stand"]["gesund"] is False
    assert "1 von 2 Instanzen nicht gesund: kunde-a" in antwort["meldung"]


def test_nicht_eingerichtet(host):
    (host.nbh / "host.conf").unlink()
    for aktion in ("stand", "sichern", "zurueck", "neustart"):
        rc, antwort, _ = host.betrieb(aktion)
        assert rc == 3 and antwort["meldung"].startswith("NetBox-Host nicht eingerichtet")
        assert antwort["stand"]["version"] == "1.17.0"
    rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "v1.18.0")
    assert rc == 3 and host.head() == host.commits["1.17.0"]


def test_netbox_version_aus_dem_checkout(host):
    """netbox-host.sh nimmt die Vorgabe des Checkouts, solange host.conf keine festhält."""
    assert "NETBOX_VERSION=" not in re.sub(
        r"^#.*$", "", (host.nbh / "host.conf").read_text(), flags=re.M
    )
    host.nbh_sh("update", "kunde-a")
    assert host.version("kunde-a") == ALT
    _git(host.checkout, "checkout", "-q", host.commits["1.18.0"])
    host.nbh_sh("update", "kunde-a")
    assert host.version("kunde-a") == NEU and host.version("kunde-b") == ALT


def test_schon_vorher_krank_kein_update(host):
    """Antwortet eine Instanz schon vor dem Update nicht, könnte das Update nur scheitern und
    Datenbanken zurückholen – abgelehnt, nichts geändert."""
    (host.stub / "aus-kunde-b").write_text("")
    rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "v1.18.0")
    assert rc == 3 and antwort["stand"]["gesund"] is False
    assert antwort["meldung"].startswith(
        "Health schon vor dem Update nicht ok (1 von 2 Instanzen nicht gesund: kunde-b)"
    )
    _unveraendert(host)
