"""deploy/netbox-host/betrieb.sh `aktualisieren --ref <ref> --bundle <datei> --commit <sha>`
(1.18.0, ADR 0062 Nachtrag „Alles privat“, Vertrag betrieb/v1 Erweiterung): der Stand kommt aus dem
git-Bundle des Cockpits, origin wird nie kontaktiert (origin zeigt auf eine ungültige Adresse mit
einer Falle als upload-pack). Branch → lokaler Branch ohne Upstream, sonst losgelöst; Bundle defekt
bzw. ohne den Commit → Exit 3, nichts geändert. Ersatz für docker, git von netbox-docker und age wie
in test_betrieb_netbox_host.py (dort der Ablauf ohne Bundle)."""

import shutil
import subprocess
from pathlib import Path

import pytest
from test_betrieb_netbox_host import (
    ALT,
    NEU,
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
AUFRUF = "Der NetBox-Host wird aus git aktualisiert"


@pytest.fixture
def host(vorlage, tmp_path) -> Host:  # noqa: F811 - wie in test_betrieb_netbox_host.py
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


def _origin_unerreichbar(host: Host) -> Path:
    marke = host.tmp / "origin-kontaktiert"
    falle = _datei(host.stub / "falle-upload-pack",
                   f"#!/bin/sh\necho kontakt >>'{marke}'\n"
                   "echo 'ERROR: Repository not found.' >&2\nexit 128\n", 0o755)  # fmt: skip
    _git(host.checkout, "remote", "set-url", "origin", "/nicht/vorhanden/automato.git")
    _git(host.checkout, "config", "remote.origin.uploadpack", str(falle))
    return marke


def _neuer_stand(host: Host, branch: str = "main", tag: str = "") -> str:
    """1.18.2 im Ursprung (nur das Werkzeug neu) – der Checkout kennt ihn noch nicht."""
    origin = host.tmp / "origin"
    _git(origin, "checkout", "-q", "-B", branch, host.commits["1.18.1"])
    (origin / "VERSION").write_text("1.18.2\n")
    _git(origin, "commit", "-qam", "Version 1.18.2")
    if tag:
        _git(origin, "tag", tag)
    return _git(origin, "rev-parse", "HEAD")


def _bundle(host: Host, *refs: str, name: str = "code.bundle") -> Path:
    ziel = host.tmp / "agent" / name
    ziel.parent.mkdir(exist_ok=True, mode=0o700)
    _git(host.tmp / "origin", "bundle", "create", str(ziel), *refs)
    return ziel


def _upstream(host: Host, branch: str) -> str:
    out = subprocess.run(["git", "config", "--get", f"branch.{branch}.remote"],  # noqa: S603, S607
                         cwd=host.checkout, capture_output=True, text=True,
                         check=False)  # fmt: skip
    return out.stdout.strip()


def test_aktualisieren_aus_dem_bundle_branch_ohne_upstream(host):
    commit = _neuer_stand(host)
    marke = _origin_unerreichbar(host)
    assert _upstream(host, "main") == "origin"
    # docker erbt den Pull-Zugang (DOCKER_CONFIG), das Skript nennt ihn nie
    zugang = host.tmp / GEHEIMER_ORT
    zugang.mkdir(mode=0o700)
    docker = Path(host.env["DOCKER"])
    zeile = 'echo "$PWD|$*" >>"$STUB_LOG"\n'
    text = docker.read_text()
    assert zeile in text
    merken = 'echo "${DOCKER_CONFIG-ohne}" >>"$STUB_DIR/dc"\n'
    docker.write_text(text.replace(zeile, zeile + merken))

    bundle = _bundle(host, "main")
    rc, antwort, stderr = host.betrieb("aktualisieren", "--ref", "main", "--bundle", str(bundle),
                                       "--commit", commit, DOCKER_CONFIG=str(zugang))  # fmt: skip
    assert rc == 0, stderr
    _format(antwort, "aktualisieren")
    assert antwort["meldung"] == f"1.17.0 → 1.18.2, NetBox {ALT} → {NEU} (2 Instanzen), Health ok"
    assert antwort["stand"]["commit"] == commit and antwort["stand"]["zurueck_moeglich"] is True
    assert host.head() == commit and host.branch() == "main"
    assert _upstream(host, "main") == ""  # Branch ohne Upstream
    assert host.version("kunde-a") == NEU and host.dumps("kunde-a")
    assert not marke.exists(), "origin kontaktiert"
    assert "Neuen Stand aus dem Bundle des Cockpits holen" in stderr
    geerbt = (host.stub / "dc").read_text().split()
    assert geerbt and set(geerbt) == {str(zugang)}
    assert GEHEIMER_ORT not in stderr and GEHEIMER_ORT not in str(antwort)

    rc, antwort, _ = host.betrieb("stand")
    assert rc == 0 and antwort["stand"]["version"] == "1.18.2"
    rc, antwort, stderr = host.betrieb("zurueck")
    assert rc == 0, stderr
    assert host.head() == host.commits["1.17.0"] and host.branch() == "main"
    assert host.version("kunde-a") == ALT
    assert not marke.exists(), "origin kontaktiert"


@pytest.mark.parametrize(
    ("ref", "refs", "branch"),
    [
        ("v1.18.2", ("v1.18.2",), ""),  # Tag im Bundle → losgelöst
        ("{kurz}", ("weiter",), ""),  # Commit → losgelöst
        ("unbekannt", ("weiter",), ""),  # weder im Bundle noch lokal bekannt → losgelöst
        ("weiter", ("weiter",), "weiter"),  # Branch im Bundle → lokaler Branch ohne Upstream
        ("weiter", ("{alt}..weiter",), "weiter"),  # dünnes Bundle: nur, was fehlt
    ],
)
def test_bundle_branch_tag_oder_commit(host, ref, refs, branch):
    commit = _neuer_stand(host, branch="weiter", tag="v1.18.2")
    marke = _origin_unerreichbar(host)
    bundle = _bundle(host, *(r.format(alt=host.commits["1.18.1"]) for r in refs))
    rc, antwort, stderr = host.betrieb("aktualisieren", "--ref", ref.format(kurz=commit[:12]),
                                       "--bundle", str(bundle), "--commit", commit)  # fmt: skip
    assert rc == 0, stderr
    assert host.head() == commit and host.branch() == branch
    if branch:
        assert _upstream(host, branch) == ""
    assert not marke.exists(), "origin kontaktiert"


def _abgelehntes_bundle(host: Host, fall: str) -> Path:
    if fall == "kaputt":
        kaputt = host.tmp / "agent" / "kaputt.bundle"
        _datei(kaputt, "kein Bundle\n")
        return kaputt
    if fall == "fehlt":
        return host.tmp / "agent" / "gibt-es-nicht.bundle"
    return _bundle(host, "v1.18.0")  # ohne den neuen Stand


@pytest.mark.parametrize(
    ("fall", "meldung"),
    [
        ("ohne-commit", "Commit {c} fehlt im Bundle bzw. Bundle beschädigt – nichts geändert"),
        ("kaputt", UNGUELTIG + " – nichts geändert"),
        ("fehlt", "Bundle fehlt bzw. ist leer – nichts geändert"),
    ],
)
def test_bundle_defekt_bzw_ohne_commit_abgelehnt(host, fall, meldung):
    commit = _neuer_stand(host)
    marke = _origin_unerreichbar(host)
    (host.tmp / "agent").mkdir(mode=0o700)
    bundle = _abgelehntes_bundle(host, fall)
    rc, antwort, stderr = host.betrieb("aktualisieren", "--ref", "main", "--bundle", str(bundle),
                                       "--commit", commit)  # fmt: skip
    assert rc == 3, stderr
    _format(antwort, "aktualisieren")
    assert antwort["meldung"] == meldung.format(c=commit[:12])
    _unveraendert(host)
    assert not marke.exists(), "origin kontaktiert"


@pytest.mark.parametrize(
    ("args", "meldung"),
    [
        (("--ref", "main", "--bundle", "{b}"), "--bundle und --commit nur zusammen"),
        (("--ref", "main", "--commit", "{c}"), "--bundle und --commit nur zusammen"),
        (("--ref", "main", "--bundle", "{b}", "--commit", "{c39}"), "--commit ist kein Commit"),
        (("--ref", "main", "--bundle", "{b}", "--commit", "HEAD"), "--commit ist kein Commit"),
        (("--ref", "main", "--bundle", "{b}", "--bundle", "{b}", "--commit", "{c}"),
         AUFRUF),
        (("--bundle", "{b}", "--commit", "{c}"), AUFRUF),
        (("--ref", "a..b", "--bundle", "{b}", "--commit", "{c}"), "Ungültige Ref"),
        (("--ref", "main", "--bundle", "{b}", "--commit"), AUFRUF),
    ],
)  # fmt: skip
def test_bundle_angaben_geprueft(host, args, meldung):
    commit = _neuer_stand(host)
    marke = _origin_unerreichbar(host)
    bundle = _bundle(host, "main")
    ersatz = {"{b}": str(bundle), "{c}": commit, "{c39}": commit[:-1]}
    werte = [ersatz.get(a, a) for a in args]
    rc, antwort, _ = host.betrieb("aktualisieren", *werte)
    assert rc == 3 and antwort["meldung"].startswith(meldung)
    _unveraendert(host)
    assert not marke.exists()


def test_lokaler_branch_mit_eigenen_commits_abgelehnt(host):
    commit = _neuer_stand(host)
    marke = _origin_unerreichbar(host)
    (host.checkout / "LOKAL").write_text("x\n")
    _git(host.checkout, "add", "LOKAL")
    _git(host.checkout, "commit", "-q", "-m", "lokal")
    lokal = host.head()
    rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "main", "--bundle",
                                  str(_bundle(host, "main")), "--commit", commit)  # fmt: skip
    assert rc == 3
    assert antwort["meldung"] == (
        "Der lokale Branch main hat Commits, die der Stand aus dem Cockpit nicht hat – "
        "nichts geändert"
    )
    assert host.head() == lokal and not host.dumps("kunde-a")
    assert not marke.exists()


def test_ohne_bundle_origin_privat_klare_meldung(host):
    """Ohne Bundle wie bisher von origin – ist das Repo privat, sagt die Meldung, was hilft."""
    marke = _origin_unerreichbar(host)
    rc, antwort, _ = host.betrieb("aktualisieren", "--ref", "main")
    assert rc == 3
    assert antwort["meldung"].startswith("git fetch von origin gescheitert")
    assert "Repo privat: Code als Bundle aus dem Cockpit" in antwort["meldung"]
    _unveraendert(host)
    assert marke.exists()  # ohne Bundle fragt es origin
