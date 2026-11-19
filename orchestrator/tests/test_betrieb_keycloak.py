"""deploy/keycloak/betrieb.sh (1.17.0, ADR 0062, Vertrag betrieb/v1): Einstiegspunkt des
Host-Agenten des Cockpits auf der Keycloak-VM – Stack aus der Konfiguration, Version aus dem Image,
Sicherung der Datenbank nur verschlüsselt, Update mit Override (Tag mit Digest), nie älter, Rückfall
mit Datenbank, zurueck einmal, Sperre. Ersatz für docker (Stack mit Postgres, Keycloak und Caddy),
age und age-keygen – keine Registry. Dazu der Notfall der gemeinsamen Antwort
(deploy/lib-betrieb.sh)."""

import base64
import fcntl
import gzip
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
AUFTRAG = "3f1c2b6e-0000-4000-8000-000000000162"
EMPF = "age1" + "q" * 58  # Testwert im Format eines age-Empfängers
IMAGE = "quay.io/keycloak/keycloak"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(w) for w in ("bash", "flock", "python3", "base64", "gzip", "sha256sum")),
    reason="bash, flock, python3, base64, gzip bzw. sha256sum fehlen",
)

# docker: der Stack (postgres, keycloak, caddy) im Ordner des Stacks; welches Image läuft, steht in
# "laeuft" (gesetzt von `up -d keycloak` aus Override bzw. docker-compose.yml); Health scheitert
# für die Versionen in "kaputt"; die Datenbank-Schritte landen in "db"
DOCKER = r"""#!/usr/bin/env bash
echo "$*" >>"$STUB_LOG"
S=$STUB_DIR
konfig() {
  if [ -f docker-compose.override.yml ]; then sed -n 's/^    image: //p' docker-compose.override.yml
  else cat "$S/image"; fi
}
tag() { local r=${1%%@*}; echo "${r##*:}"; }
case "$1" in
  compose)
    shift
    case "$*" in
      "config --format json")
        printf '{"name":"kc","services":{"keycloak":{"image":"%s"}}}\n' "$(konfig)" ;;
      "config --services") printf 'postgres\nkeycloak\ncaddy\n' ;;
      "ps --status running --services")
        printf 'postgres\ncaddy\n'; [ -f "$S/kc-aus" ] || echo keycloak ;;
      "ps -q keycloak") [ -f "$S/kc-aus" ] || echo kc-id ;;
      "up -d keycloak")
        [ ! -f "$S/up-scheitert" ] || exit 1; konfig >"$S/laeuft"; rm -f "$S/kc-aus" ;;
      "stop keycloak") : >"$S/kc-aus" ;;
      *pg_dump*) echo "KC-SQL-DUMP $(tag "$(cat "$S/laeuft")")" ;;
      *"DROP SCHEMA"*) echo "leeren" >>"$S/db" ;;
      *psql*) echo "einspielen $(cat)" >>"$S/db" ;;
      restart) [ ! -f "$S/restart-aus" ] ;;
    esac ;;
  exec)
    v=$(tag "$(cat "$S/laeuft")")
    case "$3" in
      cat) echo "Keycloak - Version $v" ;;
      bash) ! grep -qxF "$v" "$S/kaputt" 2>/dev/null ;;
    esac ;;
  pull) [ ! -f "$S/pull-scheitert" ] || exit 1 ;;
  run) v=$(tag "${*: -2:1}"); [ ! -f "$S/image-falsch" ] || v=9.9.9; echo "Keycloak - Version $v" ;;
  image) r=${*: -1}; echo "${r%%:*}@sha256:$(printf %s "$r" | sha256sum | cut -c1-64)" ;;
esac
"""
AGE = r"""#!/usr/bin/env bash
case "$1" in
  -r) out="${*: -1}"; { echo "age-stub"; base64 -w0; echo; } >"$out" ;;
  -d) [ ! -f "$STUB_DIR/age-aus" ] || exit 1; tail -n +2 "${*: -1}" | base64 -d ;;
  *) exit 1 ;;
esac
"""
KEYGEN = r"""#!/usr/bin/env bash
[ "$1" = -y ] && [ -r "$2" ] && echo "$STUB_EMPF"
"""


def _datei(path: Path, text: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


@dataclass
class Vm:
    tmp: Path
    checkout: Path = field(init=False)
    kc: Path = field(init=False)
    stub: Path = field(init=False)
    log: Path = field(init=False)
    backups: Path = field(init=False)
    konf: Path = field(init=False)
    identitaet: Path = field(init=False)
    env: dict = field(init=False)

    def __post_init__(self):
        self.checkout = self.tmp / "automato"
        for name in ("deploy/lib-betrieb.sh", "deploy/keycloak/betrieb.sh"):
            (self.checkout / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, self.checkout / name)
        self.kc = self.tmp / "automato-keycloak"
        _datei(
            self.kc / "docker-compose.yml", f"services:\n  keycloak:\n    image: {IMAGE}:26.0.7\n"
        )
        _datei(self.kc / ".env", "# Testinhalt ohne Geheimnis\n", 0o600)
        self.stub = self.tmp / "stub"
        self.log = self.stub / "log"
        _datei(self.log, "")
        _datei(self.stub / "image", f"{IMAGE}:26.0.7\n")
        _datei(self.stub / "laeuft", f"{IMAGE}:26.0.7\n")
        self.backups = self.tmp / "backups"
        self.identitaet = _datei(self.tmp / "kc-backup.agekey", "AGE-SECRET-KEY-TEST\n", 0o600)
        self.konf = _datei(self.tmp / "etc" / "keycloak-betrieb.conf", (
            "# Test\n"
            f"KC_DIR={self.kc}\n"
            f'AGE_RECIPIENT="{EMPF}"\n'
            f"AGE_IDENTITY={self.identitaet}\n"
            f"BACKUP_DIR={self.backups}\n"
        ), 0o600)  # fmt: skip
        self.env = {
            "PATH": os.environ["PATH"], "LANG": "C.UTF-8", "BETRIEB_AUFTRAG": AUFTRAG,
            "HOME": str(self.tmp), "STUB_LOG": str(self.log), "STUB_DIR": str(self.stub),
            "STUB_EMPF": EMPF, "KC_BETRIEB_CONF": str(self.konf), "KC_HEALTH_SEK": "0",
            "KC_HEALTH_PAUSE": "0",
            "DOCKER": str(_datei(self.stub / "bin/docker", DOCKER, 0o755)),
            "AGE": str(_datei(self.stub / "bin/age", AGE, 0o755)),
            "AGE_KEYGEN": str(_datei(self.stub / "bin/age-keygen", KEYGEN, 0o755)),
        }  # fmt: skip

    def betrieb(self, *args: str, **extra: str) -> tuple[int, dict, str]:
        out = subprocess.run(["bash", "deploy/keycloak/betrieb.sh", *args],  # noqa: S603, S607
                             cwd=self.checkout, env={**self.env, **extra}, capture_output=True,
                             text=True, timeout=60, check=False)  # fmt: skip
        zeilen = out.stdout.splitlines()
        assert len(zeilen) == 1, f"stdout: {out.stdout!r}\nstderr: {out.stderr}"
        return out.returncode, json.loads(zeilen[0]), out.stderr

    def laeuft(self) -> str:
        return (self.stub / "laeuft").read_text().strip()

    def db(self) -> list[str]:
        f = self.stub / "db"
        return f.read_text().splitlines() if f.exists() else []

    def sicherungen(self) -> list[Path]:
        return sorted(self.backups.glob("keycloak-*.sql.gz.age"))

    def protokoll(self) -> list[str]:
        return self.log.read_text().splitlines()

    @property
    def override(self) -> Path:
        return self.kc / "docker-compose.override.yml"


@pytest.fixture
def vm(tmp_path) -> Vm:
    return Vm(tmp_path)


STAND_FELDER = {"version", "commit", "quelle", "kanal", "gesund", "zurueck_moeglich", "vorher",
                "letzte_sicherung", "herkunft"}  # fmt: skip


def _format(antwort: dict, aktion: str) -> None:
    assert set(antwort) == {"format", "produkt", "aktion", "ok", "meldung", "stand"}
    assert antwort["format"] == "betrieb/v1" and antwort["produkt"] == "keycloak"
    assert antwort["aktion"] == aktion
    assert isinstance(antwort["ok"], bool) and 0 < len(antwort["meldung"]) <= 300
    assert set(antwort["stand"]) == STAND_FELDER
    stand = antwort["stand"]
    assert stand["commit"] is None and stand["quelle"] == "images" and stand["kanal"] is None
    assert set(stand["herkunft"]) == {"branch", "losgeloest"}


def _unveraendert(vm: Vm) -> None:
    assert vm.laeuft() == f"{IMAGE}:26.0.7"
    assert not vm.override.exists() and not vm.sicherungen() and vm.db() == []
    assert not (vm.kc / ".betrieb").exists()


def test_ohne_konfiguration_bzw_stack_abgelehnt(vm):
    vm.konf.write_text(f"KC_DIR={vm.tmp / 'gibt-es-nicht'}\n")
    for aktion in ("stand", "sichern", "zurueck", "neustart"):
        rc, antwort, _ = vm.betrieb(aktion)
        assert rc == 3, antwort
        _format(antwort, aktion)
        assert antwort["meldung"].startswith(f"Kein Keycloak-Stack in {vm.tmp / 'gibt-es-nicht'}")
        assert antwort["stand"]["version"] is None
    rc, antwort, _ = vm.betrieb("aktualisieren", "--version", "26.4.2")
    assert rc == 3 and "Kein Keycloak-Stack" in antwort["meldung"]
    # ohne Konfiguration: Vorgabe /opt/automato-keycloak
    rc, antwort, _ = vm.betrieb("stand", KC_BETRIEB_CONF=str(vm.tmp / "fehlt.conf"))
    if not Path("/opt/automato-keycloak/docker-compose.yml").exists():
        assert rc == 3 and "/opt/automato-keycloak" in antwort["meldung"]
    assert vm.protokoll() == []


def test_stand(vm):
    rc, antwort, stderr = vm.betrieb("stand")
    assert rc == 0, stderr
    _format(antwort, "stand")
    assert antwort["ok"] is True and antwort["meldung"] == "Keycloak 26.0.7, Health ok"
    assert antwort["stand"] == {
        "version": "26.0.7", "commit": None, "quelle": "images", "kanal": None, "gesund": True,
        "zurueck_moeglich": False, "vorher": None, "letzte_sicherung": None,
        "herkunft": {"branch": None, "losgeloest": True},  # kein git-Checkout
    }  # fmt: skip
    assert f"Auftrag {AUFTRAG}: keycloak stand" in stderr
    (vm.stub / "kc-aus").write_text("")
    rc, antwort, _ = vm.betrieb("stand")
    assert rc == 0 and antwort["stand"]["gesund"] is False
    # Version dann aus dem Image-Tag der Konfiguration
    assert antwort["meldung"] == "Keycloak 26.0.7, Health nicht ok: keycloak läuft nicht"
    assert not [z for z in vm.protokoll() if "pg_dump" in z or "restart" in z or "pull" in z]


def test_sichern_nur_verschluesselt(vm):
    rc, antwort, stderr = vm.betrieb("sichern")
    assert rc == 0, stderr
    _format(antwort, "sichern")
    assert antwort["meldung"] == "Sicherung der Keycloak-Datenbank fertig (verschlüsselt)"
    (datei,) = vm.sicherungen()
    assert vm.backups.stat().st_mode & 0o777 == 0o700
    assert datei.stat().st_mode & 0o777 == 0o600
    roh = datei.read_bytes()
    assert b"KC-SQL-DUMP" not in roh and roh.startswith(b"age-stub\n")
    klar = base64.b64decode(roh.split(b"\n", 1)[1])
    assert gzip.decompress(klar).decode().strip() == "KC-SQL-DUMP 26.0.7"
    assert not list(vm.backups.glob("*.tmp"))
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", antwort["stand"]["letzte_sicherung"])
    # ohne Empfänger und ohne Identität: abgelehnt
    vm.konf.write_text(f"KC_DIR={vm.kc}\nBACKUP_DIR={vm.backups}\nAGE_IDENTITY={vm.tmp}/fehlt\n")
    rc, antwort, _ = vm.betrieb("sichern")
    assert rc == 3 and antwort["meldung"].startswith("age-Empfänger fehlt")
    # Empfänger aus der vorhandenen Identität
    vm.konf.write_text(f"KC_DIR={vm.kc}\nBACKUP_DIR={vm.backups}\nAGE_IDENTITY={vm.identitaet}\n")
    assert vm.betrieb("sichern")[0] == 0
    rc, antwort, _ = vm.betrieb("sichern", AGE="false")  # age scheitert: keine halbe Datei
    assert rc == 2 and antwort["meldung"].startswith("Sicherung gescheitert")
    assert not list(vm.backups.glob("*.tmp"))


def test_aktualisieren_mit_digest_und_sicherung(vm):
    rc, antwort, stderr = vm.betrieb("aktualisieren", "--version", "26.4.2")
    assert rc == 0, stderr
    _format(antwort, "aktualisieren")
    assert antwort["meldung"] == "26.0.7 → 26.4.2, Health ok"
    stand = antwort["stand"]
    assert stand["version"] == "26.4.2" and stand["gesund"] is True
    assert stand["zurueck_moeglich"] is True
    assert stand["vorher"]["version"] == "26.0.7" and stand["vorher"]["commit"] is None
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", stand["vorher"]["zeit"])
    # Override mit Tag und Digest; docker-compose.yml (Ansible) unverändert
    over = vm.override.read_text().splitlines()
    assert over[0] == "# Keycloak-Version aus dem Cockpit (deploy/keycloak/betrieb.sh, ADR 0062)"
    assert re.fullmatch(rf"    image: {re.escape(IMAGE)}:26\.4\.2@sha256:[0-9a-f]{{64}}", over[-1])
    assert "26.0.7" in (vm.kc / "docker-compose.yml").read_text()
    assert vm.laeuft().startswith(f"{IMAGE}:26.4.2@sha256:")
    log = vm.protokoll()
    assert f"pull --quiet {IMAGE}:26.4.2" in log
    assert "compose up -d keycloak" in log
    # Reihenfolge: holen und prüfen -> Sicherung -> umstellen
    assert (
        log.index(f"pull --quiet {IMAGE}:26.4.2")
        < next(i for i, z in enumerate(log) if "pg_dump" in z)
        < log.index("compose up -d keycloak")
    )
    assert len(vm.sicherungen()) == 1
    zustand = (vm.kc / ".betrieb" / "stand").read_text()
    assert "vorher_version=26.0.7" in zustand and "vorher_override=nein" in zustand
    assert (vm.kc / ".betrieb").stat().st_mode & 0o777 == 0o700
    assert "Testinhalt" not in zustand

    # gleich: nichts zu tun; älter: nur über zurueck; ungültig: abgelehnt
    vm.log.write_text("")
    rc, antwort, _ = vm.betrieb("aktualisieren", "--version", "26.4.2")
    assert rc == 0 and antwort["meldung"] == "schon auf 26.4.2"
    rc, antwort, _ = vm.betrieb("aktualisieren", "--version", "26.1.0")
    assert rc == 3 and antwort["meldung"] == (
        "26.1.0 ist älter als 26.4.2 – zurück nur über die Aktion zurueck; nichts geändert"
    )
    assert not [z for z in vm.protokoll() if "pull" in z or "pg_dump" in z or " up " in z]


@pytest.mark.parametrize(
    "args",
    [(), ("--version",), ("--version", "26.4"), ("--version", "v26.4.2"),
     ("--version", "26.4.2;ls"), ("--ref", "main"), ("--version", "26.4.2", "--manifest", "x")],
)  # fmt: skip
def test_eingaben_abgelehnt(vm, args):
    rc, antwort, _ = vm.betrieb("aktualisieren", *args)
    assert rc == 3 and antwort["ok"] is False
    _format(antwort, "aktualisieren")
    _unveraendert(vm)


def test_health_scheitert_rueckfall_mit_datenbank(vm):
    (vm.stub / "kaputt").write_text("26.4.2\n")
    rc, antwort, stderr = vm.betrieb("aktualisieren", "--version", "26.4.2")
    assert rc == 1, stderr
    _format(antwort, "aktualisieren")
    assert antwort["meldung"] == (
        "26.4.2 gescheitert (Health nicht ok: Keycloak nicht bereit (/health/ready)) – "
        "zurück auf 26.0.7 (Image, Datenbank), Health ok"
    )
    assert vm.laeuft() == f"{IMAGE}:26.0.7" and not vm.override.exists()
    # zuerst geleert, dann die eben gemachte Sicherung (Stand 26.0.7)
    assert vm.db() == ["leeren", "einspielen KC-SQL-DUMP 26.0.7"]
    log = vm.protokoll()
    assert log.index("compose stop keycloak") < log.index("compose up -d postgres")
    stand = antwort["stand"]
    assert stand["version"] == "26.0.7" and stand["gesund"] is True
    assert stand["zurueck_moeglich"] is False and stand["vorher"] is None
    assert not (vm.kc / ".betrieb").exists()


def test_rueckfall_auf_vorheriges_override_und_zustand(vm):
    assert vm.betrieb("aktualisieren", "--version", "26.4.2")[0] == 0
    vorher = vm.override.read_text()
    (vm.stub / "kaputt").write_text("26.5.0\n")
    rc, antwort, _ = vm.betrieb("aktualisieren", "--version", "26.5.0")
    assert rc == 1 and "zurück auf 26.4.2" in antwort["meldung"]
    assert vm.override.read_text() == vorher
    assert vm.db()[-1] == "einspielen KC-SQL-DUMP 26.4.2"
    # der Stand für zurück ist wieder der des erfolgreichen Updates (vor 26.4.2: 26.0.7)
    assert antwort["stand"]["vorher"]["version"] == "26.0.7"
    assert antwort["stand"]["zurueck_moeglich"] is True


def test_rueckfall_ohne_lesbare_sicherung_von_hand(vm):
    (vm.stub / "kaputt").write_text("26.4.2\n")
    (vm.stub / "age-aus").write_text("")
    rc, antwort, _ = vm.betrieb("aktualisieren", "--version", "26.4.2")
    assert rc == 2 and "Rückfall gescheitert" in antwort["meldung"]
    assert "Datenbank unverändert" in antwort["meldung"]
    assert vm.db() == []  # nie geleert ohne lesbare Sicherung
    assert vm.laeuft().startswith(f"{IMAGE}:26.4.2")  # läuft weiter mit dem passenden Image


def test_zurueck_nur_einmal(vm):
    rc, antwort, _ = vm.betrieb("zurueck")
    assert rc == 3 and antwort["meldung"].startswith("Kein Stand für zurück")
    assert vm.betrieb("aktualisieren", "--version", "26.4.2")[0] == 0
    rc, antwort, stderr = vm.betrieb("zurueck")
    assert rc == 0, stderr
    _format(antwort, "zurueck")
    assert antwort["meldung"] == "26.4.2 → 26.0.7 zurück (Image, Datenbank), Health ok"
    assert vm.laeuft() == f"{IMAGE}:26.0.7" and not vm.override.exists()
    assert vm.db() == ["leeren", "einspielen KC-SQL-DUMP 26.0.7"]
    assert antwort["stand"]["zurueck_moeglich"] is False
    assert antwort["stand"]["vorher"]["version"] == "26.0.7"
    rc, antwort, _ = vm.betrieb("zurueck")
    assert rc == 3


def test_image_nicht_geholt_bzw_falsch(vm):
    (vm.stub / "pull-scheitert").write_text("")
    rc, antwort, _ = vm.betrieb("aktualisieren", "--version", "26.4.2")
    assert rc == 1 and antwort["meldung"] == (
        f"Image {IMAGE}:26.4.2 nicht geholt (Registry erreichbar?) – nichts umgestellt, "
        "läuft auf 26.0.7"
    )
    _unveraendert(vm)
    (vm.stub / "pull-scheitert").unlink()
    (vm.stub / "image-falsch").write_text("")
    rc, antwort, _ = vm.betrieb("aktualisieren", "--version", "26.4.2")
    assert rc == 3 and "meldet Keycloak 9.9.9 statt 26.4.2" in antwort["meldung"]
    _unveraendert(vm)


def test_vorbedingungen(vm):
    vm.override.write_text("services:\n  keycloak:\n    environment: {}\n")
    rc, antwort, _ = vm.betrieb("aktualisieren", "--version", "26.4.2")
    assert rc == 3 and "von Hand angelegt" in antwort["meldung"]
    vm.override.unlink()
    vm.identitaet.unlink()
    rc, antwort, _ = vm.betrieb("aktualisieren", "--version", "26.4.2")
    assert rc == 3 and antwort["meldung"].startswith("Ohne age-Identität")
    _unveraendert(vm)


def test_sperre_und_neustart(vm):
    with open(vm.checkout / ".betrieb.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc, antwort, _ = vm.betrieb("aktualisieren", "--version", "26.4.2")
        assert (
            rc == 4 and antwort["meldung"] == "Belegt – auf diesem Checkout läuft schon eine Aktion"
        )
    _unveraendert(vm)
    rc, antwort, _ = vm.betrieb("neustart")
    assert rc == 0 and antwort["meldung"] == "Neu gestartet, Health ok"
    assert "compose restart" in vm.protokoll()
    assert not [z for z in vm.protokoll() if "pull" in z]
    (vm.stub / "kaputt").write_text("26.0.7\n")
    rc, antwort, _ = vm.betrieb("neustart")
    assert rc == 2 and antwort["stand"]["gesund"] is False


def test_unerwarteter_abbruch_gibt_trotzdem_eine_antwort(tmp_path):
    """deploy/lib-betrieb.sh: bricht die Aktion ohne Antwort ab, steht trotzdem genau eine Zeile
    JSON auf stdout (Exit 2); Ausgaben der Aktion gehen nur nach stderr."""
    skript = _datei(tmp_path / "deploy" / "probe.sh", (
        "#!/usr/bin/env bash\n"
        "CHECKOUT=$(cd \"$(dirname \"$0\")/..\" && pwd)\nPRODUKT=probe\n"
        f". {REPO / 'deploy' / 'lib-betrieb.sh'}\n"
        'stand_json() { printf \'{"version":null,"notfall":%s}\' "$NOTFALL"; }\n'
        'main() { echo "nur nach stderr"; [ "$1" != stand ] || exit 9; ende 0 "gut"; }\n'
        'betrieb_einstieg "$@"; exit $?\n'
    ), 0o755)  # fmt: skip
    UMGEBUNG = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8"}  # noqa: N806
    out = subprocess.run(["bash", str(skript), "stand"], capture_output=True, text=True,  # noqa: S603, S607
                         env=UMGEBUNG, check=False)  # fmt: skip
    assert out.returncode == 2
    (zeile,) = out.stdout.splitlines()
    antwort = json.loads(zeile)
    assert antwort["produkt"] == "probe" and antwort["aktion"] == "stand"
    assert antwort["meldung"].startswith("Unerwarteter Abbruch (Exit 9)")
    assert antwort["stand"] == {"version": None, "notfall": 1}
    assert "nur nach stderr" in out.stderr
    out = subprocess.run(["bash", str(skript), "sichern"], capture_output=True, text=True,  # noqa: S603, S607
                         env=UMGEBUNG, check=False)  # fmt: skip
    assert out.returncode == 0 and json.loads(out.stdout)["stand"]["notfall"] == 0


def test_vorlagen_der_konfiguration_passen_zum_skript(vm):
    """Vorlage für die Hand und Vorlage des Playbooks nennen genau die Schlüssel, die betrieb.sh
    liest; die Vorlage für die Hand ergibt ohne Änderung die Vorgaben (KC_DIR /opt/…)."""
    skript = (REPO / "deploy/keycloak/betrieb.sh").read_text()
    gelesen = set(re.findall(r"(?:konf_wert|pfad_wert) ([A-Z_]+)", skript))
    assert gelesen == {"KC_DIR", "AGE_RECIPIENT", "AGE_IDENTITY", "BACKUP_DIR", "BACKUP_KEEP_DAYS"}
    for vorlage in ("deploy/keycloak/keycloak-betrieb.conf.example",
                    "deploy/keycloak/ansible/templates/keycloak-betrieb.conf.j2"):  # fmt: skip
        text = (REPO / vorlage).read_text()
        assert set(re.findall(r"^([A-Z_]+)=", text, re.M)) == gelesen, vorlage
    shutil.copy2(REPO / "deploy/keycloak/keycloak-betrieb.conf.example", vm.konf)
    rc, antwort, _ = vm.betrieb("stand")
    if not Path("/opt/automato-keycloak/docker-compose.yml").exists():
        assert rc == 3 and "/opt/automato-keycloak" in antwort["meldung"]


def test_schon_vorher_krank_kein_update(vm):
    (vm.stub / "kaputt").write_text("26.0.7\n")
    rc, antwort, _ = vm.betrieb("aktualisieren", "--version", "26.4.2")
    assert rc == 3 and antwort["stand"]["gesund"] is False
    assert antwort["meldung"].startswith(
        "Health schon vor dem Update nicht ok (Keycloak nicht bereit"
    )
    _unveraendert(vm)
    assert not [z for z in vm.protokoll() if "pull" in z]


@pytest.mark.skipif(not shutil.which("git"), reason="git fehlt")
def test_stand_meldet_die_herkunft_des_checkouts(vm):
    """1.18.2: `herkunft` nennt den Branch des Automato-Checkouts, der das Skript liefert (bzw.
    losgelöst) - wie deploy/betrieb.sh, ohne Kanal."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.ch",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.ch",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}  # fmt: skip

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=vm.checkout, env=env, check=True,  # noqa: S603, S607
                       capture_output=True)  # fmt: skip

    git("init", "-q", "-b", "claude/zweig")
    git("add", "-A")
    git("commit", "-q", "-m", "Werkzeug")
    rc, antwort, _ = vm.betrieb("stand")
    assert rc == 0
    _format(antwort, "stand")
    assert antwort["stand"]["herkunft"] == {"branch": "claude/zweig", "losgeloest": False}
    git("checkout", "-q", "--detach")
    rc, antwort, _ = vm.betrieb("stand")
    assert rc == 0 and antwort["stand"]["herkunft"] == {"branch": None, "losgeloest": True}
