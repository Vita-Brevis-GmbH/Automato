"""deploy/betrieb.sh mit `--bundle` und deploy/update.sh ohne Zugang zu origin (1.18.0, ADR 0060
Nachtrag „Alles privat“, Vertrag betrieb/v1 Erweiterung): die Repos sind privat, nur das Cockpit
hat einen GitHub-Zugang und gibt den Code als git-Bundle mit. Echte git-Repos und Bundles; origin
zeigt auf eine ungültige Adresse mit einer Falle als upload-pack – jeder Versuch, origin zu
kontaktieren, hinterlässt eine Marke. Ersatz für docker, curl, Sicherung wie in
test_betrieb_script.py (dort auch der Ablauf ohne Bundle)."""

import shutil
from pathlib import Path

import pytest
from test_betrieb_script import (
    DOCKER,
    Host,
    _datei,
    _format,
    _git,
    _unveraendert,
    pytestmark,  # noqa: F401 - gleiche Voraussetzungen
    vorlage,  # noqa: F401 - Fixture
)

GEHEIMER_ORT = "docker-zugang-nur-fuer-diesen-auftrag"
UNGUELTIG = "Bundle ungültig bzw. passt nicht zu diesem Checkout (git bundle verify)"


@pytest.fixture
def host(vorlage, tmp_path) -> Host:  # noqa: F811 - wie in test_betrieb_script.py
    basis, commits, privat = vorlage
    shutil.copytree(basis / "origin", tmp_path / "origin", symlinks=True)
    shutil.copytree(basis / "checkout", tmp_path / "checkout", symlinks=True)
    _git(tmp_path / "checkout", "remote", "set-url", "origin", str(tmp_path / "origin"))
    return Host(tmp_path, commits, privat)


def _origin_unerreichbar(host: Host) -> Path:
    """origin auf eine ungültige Adresse; die Falle meldet „privat“ und hinterlässt eine Marke."""
    marke = host.tmp / "origin-kontaktiert"
    falle = host.stub / "falle-upload-pack"
    _datei(falle, f"#!/bin/sh\necho kontakt >>'{marke}'\n"
                  "echo 'ERROR: Repository not found.' >&2\nexit 128\n", 0o755)  # fmt: skip
    _git(host.checkout, "remote", "set-url", "origin", "/nicht/vorhanden/automato.git")
    _git(host.checkout, "config", "remote.origin.uploadpack", str(falle))
    return marke


def _bundle(host: Host, *refs: str, name: str = "code.bundle") -> Path:
    """Bundle aus dem Ursprung, wie es das Cockpit über den Host-Kanal gibt."""
    ziel = host.tmp / "agent" / name
    ziel.parent.mkdir(exist_ok=True, mode=0o700)
    _git(host.tmp / "origin", "bundle", "create", str(ziel), *refs)
    return ziel


def _docker_mit_zugang(host: Host) -> Path:
    """docker-Ersatz, der festhält, welches DOCKER_CONFIG er erbt."""
    ort = host.tmp / GEHEIMER_ORT
    ort.mkdir(mode=0o700)
    zeile = 'echo "docker $*" >>"$STUB_LOG"\n'
    merken = 'echo "${DOCKER_CONFIG-ohne}" >>"$STUB_DIR/docker-config"\n'
    text = DOCKER.replace(zeile, zeile + merken)
    assert text != DOCKER
    _datei(host.stub / "bin" / "docker", text, 0o755)
    return ort


def _bundle_aktualisieren(host: Host, v: str, bundle: Path, **extra: str):
    version, m, s = host.manifest(v)
    return host.betrieb("aktualisieren", "--version", version, "--manifest", str(m),
                        "--signatur", str(s), "--bundle", str(bundle), **extra)  # fmt: skip


def test_aktualisieren_aus_dem_bundle_ohne_origin(host):
    marke = _origin_unerreichbar(host)
    # nur, was dem Checkout fehlt: Voraussetzung ist sein Stand (1.15.0)
    bundle = _bundle(host, f"{host.commits['1.15.0']}..main")
    zugang = _docker_mit_zugang(host)
    rc, antwort, stderr = _bundle_aktualisieren(host, "1.16.0", bundle, DOCKER_CONFIG=str(zugang))
    version = host.version("1.16.0")
    assert rc == 0, stderr
    _format(antwort, "aktualisieren")
    assert antwort["meldung"] == f"1.15.0 → {version}, Health ok"
    assert antwort["stand"]["commit"] == host.commits["1.16.0"]
    assert antwort["stand"]["quelle"] == "images" and antwort["stand"]["zurueck_moeglich"] is True
    assert host.head() == host.commits["1.16.0"]  # Commit des Manifests, losgelöst
    assert _git(host.checkout, "status", "--porcelain", "--untracked-files=no") == ""
    assert not marke.exists(), "origin kontaktiert"
    assert "Code aus dem Bundle des Cockpits pruefen" in stderr
    log = host.protokoll()
    assert log.count("backup automato-1.dump.age") == 1
    assert "docker compose --profile core up -d --no-build" in log
    # Pull-Zugang: docker erbt DOCKER_CONFIG, das Skript nennt es nie
    geerbt = (host.stub / "docker-config").read_text().split()
    assert geerbt and set(geerbt) == {str(zugang)}
    assert GEHEIMER_ORT not in stderr and GEHEIMER_ORT not in str(antwort)

    # stand und zurueck brauchen origin nicht
    rc, antwort, _ = host.betrieb("stand")
    assert rc == 0 and antwort["stand"]["version"] == version
    rc, antwort, stderr = host.betrieb("zurueck")
    assert rc == 0, stderr
    assert host.head() == host.commits["1.15.0"]
    assert not marke.exists(), "origin kontaktiert"


def _abgelehntes_bundle(host: Host, fall: str) -> Path:
    if fall == "ohne-commit":  # Bundle bis 1.16.0, Auftrag 1.17.0
        _git(host.tmp / "origin", "branch", "bis-1.16", host.commits["1.16.0"])
        return _bundle(host, "bis-1.16")
    if fall == "kaputt":
        kaputt = host.tmp / "agent" / "kaputt.bundle"
        _datei(kaputt, "kein Bundle\n")
        return kaputt
    if fall == "abgeschnitten":  # Kopf und Voraussetzungen lesbar, Objekte unvollständig
        voll = _bundle(host, "main")
        voll.write_bytes(voll.read_bytes()[: voll.stat().st_size // 2])
        return voll
    if fall == "voraussetzung-fehlt":  # dünnes Bundle ab 1.16.0, der Checkout hat nur 1.15.0
        return _bundle(host, f"{host.commits['1.16.0']}..main")
    return host.tmp / "agent" / "gibt-es-nicht.bundle"


@pytest.mark.parametrize(
    ("fall", "meldung"),
    [
        ("ohne-commit", "Commit {c} fehlt im Bundle bzw. Bundle beschädigt"),
        ("kaputt", UNGUELTIG),
        ("abgeschnitten", "Commit {c} fehlt im Bundle bzw. Bundle beschädigt"),
        ("voraussetzung-fehlt", UNGUELTIG),
        ("fehlt", "Bundle fehlt bzw. ist leer"),
    ],
)
def test_bundle_ohne_commit_defekt_bzw_unpassend_abgelehnt(host, fall, meldung):
    marke = _origin_unerreichbar(host)
    (host.tmp / "agent").mkdir(mode=0o700)
    bundle = _abgelehntes_bundle(host, fall)
    rc, antwort, stderr = _bundle_aktualisieren(host, "1.17.0", bundle)
    assert rc == 3, stderr
    _format(antwort, "aktualisieren")
    c = host.commits["1.17.0"][:12]
    assert antwort["meldung"] == meldung.format(c=c) + " – nichts geändert"
    _unveraendert(host)
    assert not marke.exists(), "origin kontaktiert"


def test_bundle_ohne_wert_abgelehnt(host):
    rc, antwort, _ = host.betrieb("aktualisieren", "--version", "1.16.0", "--bundle")
    assert rc == 3 and antwort["meldung"] == "Angabe ohne Wert"
    _unveraendert(host)


def test_commit_lokal_aber_nicht_im_bundle_abgelehnt(host):
    """Der Commit muss zum Stand des Bundles gehören – auch wenn das Repo ihn schon kennt."""
    _git(host.checkout, "fetch", "-q", "origin")  # alle Stände schon lokal
    marke = _origin_unerreichbar(host)
    origin = host.tmp / "origin"
    _git(origin, "checkout", "-q", "-b", "anders", host.commits["1.15.0"])
    (origin / "ANDERS").write_text("x\n")
    _git(origin, "add", "ANDERS")
    _git(origin, "commit", "-q", "-m", "anders")
    rc, antwort, _ = _bundle_aktualisieren(host, "1.16.0", _bundle(host, "anders"))
    assert rc == 3
    assert antwort["meldung"] == (
        f"Commit {host.commits['1.16.0'][:12]} gehört nicht zum Stand des Bundles – nichts geändert"
    )
    _unveraendert(host)
    assert not marke.exists()


def test_checkout_aus_dem_bundle_ohne_remote(host):
    """Wie der Agent beim Installieren: git clone <bundle>, losgelöst auf den Commit, origin weg –
    stand, aktualisieren mit Bundle und zurueck gehen ohne Remote."""
    bundle = _bundle(host, "main")
    shutil.rmtree(host.checkout)
    _git(host.tmp, "clone", "-q", str(bundle), str(host.checkout))
    _git(host.checkout, "checkout", "-q", "--detach", host.commits["1.15.0"])
    _git(host.checkout, "remote", "remove", "origin")
    assert _git(host.checkout, "remote") == ""
    rc, antwort, stderr = host.betrieb("stand")
    assert rc == 0, stderr
    assert antwort["stand"]["commit"] == host.commits["1.15.0"]
    rc, antwort, stderr = _bundle_aktualisieren(host, "1.17.0", bundle)
    assert rc == 0, stderr
    assert host.head() == host.commits["1.17.0"]
    rc, antwort, stderr = host.betrieb("zurueck")
    assert rc == 0, stderr
    assert host.head() == host.commits["1.15.0"]
    # ohne Bundle: kein origin - nichts umgestellt, klare Meldung
    rc, antwort, _ = host.aktualisieren("1.16.0")
    assert rc == 1 and antwort["meldung"].startswith("Code nicht geholt (git fetch)")
    assert host.head() == host.commits["1.15.0"]


@pytest.mark.parametrize(
    ("vorbereiten", "text"),
    [
        ("falle", "kein Zugang zu origin (/nicht/vorhanden/automato.git) - das Repo ist privat"),
        ("ungueltig", "origin nicht erreichbar (/nicht/vorhanden/automato.git)"),
        ("ohne", "kein Remote origin"),
    ],
)
def test_update_sh_ohne_zugang_klare_meldung_nichts_geaendert(host, vorbereiten, text):
    if vorbereiten == "ohne":
        _git(host.checkout, "remote", "remove", "origin")
    else:
        _origin_unerreichbar(host)
        if vorbereiten == "ungueltig":
            _git(host.checkout, "config", "--unset", "remote.origin.uploadpack")
    out = host.run("main", skript="deploy/update.sh")
    assert out.returncode == 2, out.stdout + out.stderr
    assert text in out.stderr
    assert "Nichts geaendert" in out.stderr and "Update aus dem Cockpit" in out.stderr
    assert "Lese-Zugang" in out.stderr
    if vorbereiten == "falle":
        assert "git meldet: ERROR: Repository not found." in out.stderr
    # nichts geändert: weder Sicherung noch Stand für den Rückfall noch Umschalten
    assert host.head() == host.commits["1.15.0"]
    assert _git(host.checkout, "symbolic-ref", "--short", "HEAD") == "main"
    assert not [z for z in host.protokoll() if z.startswith("backup") or " build" in z]
    assert not (host.checkout / ".automato-update").exists()


def test_bundle_gleicher_kern_aelter_bzw_eigene_linie_abgelehnt(host):
    """Nie älter (1.18.1) auch mit --bundle: die Abstammung mit den Objekten aus dem Bundle, vor
    der Sicherung, ohne origin."""
    c4 = host.commit_im_ursprung("Doku 1")
    dev4 = f"1.17.0-dev.4.{c4[:7]}"
    assert host.aktualisieren_auf(c4, dev4, "dev")[0] == 0
    seite = host.commit_im_ursprung("Seite", von=host.commits["1.17.0"], branch="seite")
    marke = _origin_unerreichbar(host)
    host.leeren()
    # eigene Linie, nur aus dem Bundle bekannt (Voraussetzung 1.17.0 hat der Checkout)
    bundle = _bundle(host, f"{host.commits['1.17.0']}..seite")
    latest = f"1.17.0-latest.4.{seite[:7]}"
    rc, antwort, stderr = host.aktualisieren_auf(seite, latest, "latest", "--bundle", str(bundle))
    assert rc == 3, stderr
    _format(antwort, "aktualisieren")
    assert antwort["meldung"] == (
        f"{latest} liegt auf einer anderen Linie als {dev4} (weder Vorfahre noch Nachfolger)"
        " – nichts geändert"
    )
    # Vorfahre des laufenden Stands: älter, nur über zurueck
    voll = _bundle(host, "main", name="main.bundle")
    rc, antwort, _ = host.aktualisieren_auf(host.commits["1.17.0"], "1.17.0", "stable",
                                            "--bundle", str(voll))  # fmt: skip
    assert rc == 3
    assert antwort["meldung"] == f"1.17.0 ist älter als {dev4} – zurück nur über die Aktion zurueck"
    assert host.head() == c4 and host.protokoll() == []  # weder Sicherung noch docker
    assert not marke.exists(), "origin kontaktiert"
