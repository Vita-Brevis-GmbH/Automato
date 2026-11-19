"""scripts/release_manifest.py (1.15.0, ADR 0060, Format vitabrevis-manifest/v1): Manifest der
Images je Kanal erstellen (deterministisch, nur per Digest), signieren und pruefen (Ed25519 ueber
openssl, Testschluessel zur Laufzeit), Versionen vergleichen (1.18.1 mit n), eigene Dienste aus
Compose, Kanal stable aus der Freigabe-Datei (1.18.1, echtes git-Repo im Temp-Ordner)."""

import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release_manifest.py"
_spec = importlib.util.spec_from_file_location("release_manifest", SCRIPT)
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)

COMMIT = "0123456789abcdef0123456789abcdef01234567"
DEV = f"1.15.0-dev.42.{COMMIT[:7]}"  # <VERSION>-<kanal>.<n>.<sha7> (1.18.1)
DIGEST = "sha256:" + "ab" * 32
IMAGES = {
    "web-ui": f"ghcr.io/kunde-a/automato-web-ui@{DIGEST}",
    "orchestrator": f"ghcr.io/kunde-a/automato-orchestrator@{DIGEST}",
}
ERSTELLT = "2026-11-16T12:00:00Z"


def _openssl3() -> bool:
    if not shutil.which("openssl"):
        return False
    out = subprocess.run(["openssl", "version"], capture_output=True, text=True, check=False)  # noqa: S603, S607
    m = re.match(r"OpenSSL (\d+)", out.stdout)
    return bool(m) and int(m.group(1)) >= 3


openssl3 = pytest.mark.skipif(not _openssl3(), reason="OpenSSL >= 3 fehlt")


def _manifest(**aenderung) -> bytes:
    args = {"kanal": "dev", "version": DEV, "commit": COMMIT,
            "images": IMAGES, "erstellt": ERSTELLT, **aenderung}  # fmt: skip
    return rm.erstellen(**args)


def _schluessel(tmp: Path, name: str = "a", *algo: str) -> tuple[str, str]:
    privat, pub = tmp / f"{name}.pem", tmp / f"{name}.pub"
    algo = algo or ("-algorithm", "ed25519")
    subprocess.run(["openssl", "genpkey", *algo, "-out", str(privat)],  # noqa: S603, S607
                   check=True, capture_output=True)  # fmt: skip
    subprocess.run(["openssl", "pkey", "-in", str(privat), "-pubout", "-out", str(pub)],  # noqa: S603, S607
                   check=True, capture_output=True)  # fmt: skip
    return str(privat), str(pub)


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True,  # noqa: S603
                          text=True, check=False)  # fmt: skip


def test_erstellen_deterministisch_und_nur_per_digest():
    roh = _manifest()
    assert roh == _manifest(images=dict(reversed(list(IMAGES.items()))))
    assert roh.endswith(b"}\n") and roh.decode().count("\n") == 12
    daten = json.loads(roh)
    assert list(daten) == ["format", "produkt", "kanal", "version", "commit", "erstellt", "images"]
    assert list(daten["images"]) == ["orchestrator", "web-ui"]
    assert daten["format"] == "vitabrevis-manifest/v1" and daten["produkt"] == "automato"
    assert re.match(
        r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", json.loads(_manifest(erstellt=None))["erstellt"]
    )
    for kaputt in (
        {"images": {"web-ui": "ghcr.io/kunde-a/automato-web-ui:1.15.0"}},  # nur Tag
        {"images": {"web-ui": f"automato-web-ui@{DIGEST}"}},  # ohne Registry
        {"images": {"Web UI": IMAGES["web-ui"]}},
        {"images": {}},
        {"kanal": "nightly"},
        {"commit": COMMIT[:7]},
        {"version": "1.15"},
        {"erstellt": "16.11.2026"},
        {"kanal": "stable", "version": "1.15.0-dev.x"},  # stable nur X.Y.Z
        {"version": "1.15.0-dev.42.fffffff"},  # sha7 passt nicht zum Commit
        {"version": f"1.15.0-dev.{COMMIT[:7]}"},  # ohne n (bis 1.18.0) nicht mehr erstellt
        {"version": f"1.15.0-dev.x2.{COMMIT[:7]}"},  # n keine Zahl
        {"version": f"1.15.0-latest.42.{COMMIT[:7]}"},  # Kanal der Version passt nicht
        {"kanal": "latest"},  # Version mit -dev. im Kanal latest
    ):
        with pytest.raises(rm.Abgelehnt):
            _manifest(**kaputt)
    assert json.loads(_manifest(kanal="stable", version="1.15.0"))["version"] == "1.15.0"
    latest = f"1.15.0-latest.7.{COMMIT[:7]}"
    assert json.loads(_manifest(kanal="latest", version=latest))["version"] == latest


def test_lesen_lehnt_ab():
    roh = _manifest()
    assert rm.lesen(roh)["commit"] == COMMIT
    daten = json.loads(roh)
    faelle = [
        b"[]", b"kein json", "\u00e4".encode("latin-1"), b" " * (64 * 1024 + 1),
        json.dumps({**daten, "zusatz": 1}).encode(),
        json.dumps({k: v for k, v in daten.items() if k != "erstellt"}).encode(),
        json.dumps({**daten, "format": "vitabrevis-manifest/v2"}).encode(),
        json.dumps({**daten, "version": 115}).encode(),
        roh.replace(b'"produkt": "automato",', b'"produkt": "automato", "produkt": "automato",'),
    ]  # fmt: skip
    for kaputt in faelle:
        with pytest.raises(rm.Abgelehnt) as exc:
            rm.lesen(kaputt)
        assert exc.value.code == rm.INHALT


@openssl3
def test_signieren_und_pruefen(tmp_path):
    privat, pub = _schluessel(tmp_path)
    roh = _manifest()
    sig = rm.signieren(privat, roh)
    assert len(base64.b64decode(sig)) == 64 and "\n" not in sig
    assert rm.pruefen(roh, sig.encode(), pub, "automato", DEV)["commit"] == COMMIT
    # wie auf dem Host: openssl pkeyutl -verify -rawin ueber die exakten Bytes (Vertrag)
    (tmp_path / "m.json").write_bytes(roh)
    (tmp_path / "s.bin").write_bytes(base64.b64decode(sig))
    out = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", pub, "-rawin",  # noqa: S603, S607
                          "-in", str(tmp_path / "m.json"), "-sigfile", str(tmp_path / "s.bin")],
                         capture_output=True, text=True, check=False)  # fmt: skip
    assert out.returncode == 0 and "Signature Verified Successfully" in out.stdout

    def code(*args, **kwargs) -> int:
        with pytest.raises(rm.Abgelehnt) as exc:
            rm.pruefen(*args, **kwargs)
        return exc.value.code

    # Manipulation: ein Byte, nur Leerraum, andere Images
    assert code(roh.replace(b"web-ui@", b"web-uj@"), sig.encode(), pub) == rm.SIGNATUR
    assert code(roh.replace(b"\n", b"\r\n"), sig.encode(), pub) == rm.SIGNATUR
    assert code(json.dumps(json.loads(roh)).encode(), sig.encode(), pub) == rm.SIGNATUR
    # falscher Schluessel, kaputte bzw. fehlende Signatur
    _, anderer = _schluessel(tmp_path, "b")
    assert code(roh, sig.encode(), anderer) == rm.SIGNATUR
    assert code(roh, b"", pub) == rm.SIGNATUR
    assert code(roh, b"kein base64 !", pub) == rm.SIGNATUR
    assert code(roh, base64.b64encode(b"x" * 63), pub) == rm.SIGNATUR
    # nur Ed25519 als Herausgeber-Schluessel; Schluessel fehlt
    _, ec = _schluessel(tmp_path, "ec", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256")
    assert code(roh, sig.encode(), ec) == rm.SCHLUESSEL
    (tmp_path / "kaputt.pub").write_text("kein Schluessel")
    assert code(roh, sig.encode(), str(tmp_path / "kaputt.pub")) == rm.SCHLUESSEL
    assert code(roh, sig.encode(), None) == rm.SCHLUESSEL
    # Produkt bzw. Version passen nicht
    assert code(roh, sig.encode(), pub, "cockpit") == rm.PASST_NICHT
    assert code(roh, sig.encode(), pub, "automato", "1.15.1") == rm.PASST_NICHT
    with pytest.raises(rm.Abgelehnt):
        rm.signieren(ec.replace(".pub", ".pem"), roh)


@openssl3
def test_cli_wie_im_workflow_und_auf_dem_host(tmp_path):
    privat, pub = _schluessel(tmp_path)
    version = DEV
    out = _cli("erstellen", "--kanal", "dev", "--version", version, "--commit", COMMIT,
               "--erstellt", ERSTELLT, "--image", f"web-ui={IMAGES['web-ui']}",
               "--image", f"orchestrator={IMAGES['orchestrator']}")  # fmt: skip
    assert out.returncode == 0, out.stderr
    assert out.stdout.encode() == _manifest()
    m, s = tmp_path / "manifest.json", tmp_path / "manifest.sig"
    m.write_text(out.stdout)
    out = _cli("signieren", "--schluessel", privat, "--manifest", str(m))
    assert out.returncode == 0
    s.write_text(out.stdout)
    out = _cli("pruefen", "--manifest", str(m), "--signatur", str(s), "--schluessel", pub,
               "--version", version)  # fmt: skip
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == [
        "produkt=automato", "kanal=dev", f"version={version}", f"commit={COMMIT}",
        f"erstellt={ERSTELLT}", f"image.orchestrator={IMAGES['orchestrator']}",
        f"image.web-ui={IMAGES['web-ui']}",
    ]  # fmt: skip
    # Exit-Codes fuer deploy/betrieb.sh, Grund nur auf stderr
    assert _cli("pruefen", "--manifest", str(m), "--ohne-signatur").returncode == 0
    assert _cli("pruefen", "--manifest", str(m)).returncode == rm.AUFRUF
    m.write_text(out.stdout.replace("dev", "dew"))
    falsch = _cli("pruefen", "--manifest", str(m), "--signatur", str(s), "--schluessel", pub)
    assert falsch.returncode == rm.INHALT and falsch.stdout == ""
    assert falsch.stderr.startswith("abgelehnt: ")
    assert _cli("erstellen", "--kanal", "dev", "--version", version, "--commit", COMMIT,
                "--image", "web-ui").returncode == rm.AUFRUF  # fmt: skip


@pytest.mark.parametrize(
    ("neu", "neu_commit", "laufend", "laufend_commit", "ergebnis"),
    [
        ("1.15.0", "a", "1.14.0", "b", "neuer"),
        ("1.10.0", "a", "1.9.9", "b", "neuer"),  # Zahlen, nicht Text
        ("1.14.0", "a", "1.15.0", "b", "aelter"),
        ("1.14.9-dev.ab12cd3", "a", "1.15.0", "b", "aelter"),
        ("1.15.0", "a", "1.15.0", "a", "gleich"),
        ("1.15.0-dev.ab12cd3", "a", "1.15.0-dev.ab12cd3", "a", "gleich"),
        (
            "1.15.0-dev.ef45ab6",
            "b",
            "1.15.0-dev.ab12cd3",
            "a",
            "neuer",
        ),  # gleicher Kern, anderer Commit
        ("1.15.0", "b", "1.15.0-dev.ab12cd3", "a", "neuer"),
        ("1.15.0-latest.ab12cd3", "a", "1.15.0", "a", "gleich"),  # gleicher Commit, gebaut
        ("1.15.0", "a", "?", "", "neuer"),  # laufende Version unbekannt
        # 1.18.1: monoton mit n im selben Kanal (Zahl, nicht Text)
        ("1.18.1-dev.120.aaaaaaa", "a", "1.18.1-dev.119.bbbbbbb", "b", "neuer"),
        ("1.18.1-dev.119.aaaaaaa", "a", "1.18.1-dev.120.bbbbbbb", "b", "aelter"),
        ("1.18.1-dev.9.aaaaaaa", "a", "1.18.1-dev.10.bbbbbbb", "b", "aelter"),
        ("1.18.1-latest.9.aaaaaaa", "a", "1.18.1-latest.10.bbbbbbb", "b", "aelter"),
        ("1.18.1-dev.120.aaaaaaa", "a", "1.18.1-dev.120.aaaaaaa", "a", "gleich"),
        ("1.19.0-dev.3.aaaaaaa", "a", "1.18.1-dev.120.bbbbbbb", "b", "neuer"),  # Kern zuerst
        # ohne vergleichbares n: neuer wie bisher - die Abstammung prueft deploy/betrieb.sh
        ("1.18.1-dev.120.aaaaaaa", "a", "1.18.1-dev.120.bbbbbbb", "b", "neuer"),  # gleiches n
        ("1.18.1-latest.9.aaaaaaa", "a", "1.18.1-dev.10.bbbbbbb", "b", "neuer"),  # anderer Kanal
        ("1.18.1", "a", "1.18.1-dev.10.bbbbbbb", "b", "neuer"),  # stable gegen dev
        ("1.18.1-dev.9.aaaaaaa", "a", "1.18.1-dev.bbbbbbb", "b", "neuer"),  # bis 1.18.0 ohne n
    ],
)
def test_vergleichen(neu, neu_commit, laufend, laufend_commit, ergebnis):
    assert rm.vergleichen(neu, neu_commit, laufend, laufend_commit) == ergebnis
    assert _cli("vergleichen", neu, neu_commit, laufend, laufend_commit).stdout.strip() == ergebnis


def test_dienste_aus_compose(tmp_path):
    (tmp_path / "web").mkdir()
    config = {"services": {
        "postgres": {"image": "postgres:16-alpine"},
        "web-ui": {"build": {"context": str(tmp_path / "web"), "dockerfile": "Dockerfile",
                             "target": "prod"}},
        "orchestrator": {"build": {"context": str(tmp_path),
                                   "dockerfile": "orchestrator/Dockerfile"}},
        "migrate": {"image": "x", "build": {"context": str(tmp_path),
                                            "dockerfile": "orchestrator/Dockerfile"}},
    }}  # fmt: skip
    assert rm.dienste(config, str(tmp_path)) == [
        ("migrate", ".", "orchestrator/Dockerfile", "-"),
        ("orchestrator", ".", "orchestrator/Dockerfile", "-"),
        ("web-ui", "web", "web/Dockerfile", "prod"),
    ]
    with pytest.raises(rm.Abgelehnt):  # Kontext ausserhalb des Repos
        rm.dienste({"services": {"x": {"build": {"context": "/"}}}}, str(tmp_path))
    with pytest.raises(rm.Abgelehnt):
        rm.dienste({"services": {"postgres": {"image": "postgres"}}}, str(tmp_path))
    datei = tmp_path / "compose.json"
    datei.write_text(json.dumps(config))
    out = _cli("dienste", "--compose", str(datei), "--wurzel", str(tmp_path))
    assert (
        out.returncode == 0 and out.stdout.splitlines()[-1] == "web-ui\tweb\tweb/Dockerfile\tprod"
    )


@pytest.mark.skipif(not shutil.which("docker"), reason="docker (Compose) fehlt")
def test_dienste_des_repos_wie_im_workflow(tmp_path):
    """Die eigenen Dienste stehen in docker-compose.yml (build:), nicht fest im Workflow."""
    profile = subprocess.run(["docker", "compose", "config", "--profiles"], cwd=REPO,  # noqa: S603, S607
                             capture_output=True, text=True, check=False)  # fmt: skip
    if profile.returncode != 0:
        pytest.skip("docker compose nicht nutzbar")
    args = [a for p in profile.stdout.split() for a in ("--profile", p)]
    out = subprocess.run(["docker", "compose", *args, "config", "--format", "json"], cwd=REPO,  # noqa: S603, S607
                         capture_output=True, text=True, check=True)  # fmt: skip
    zeilen = rm.dienste(json.loads(out.stdout), str(REPO))
    assert [z[0] for z in zeilen] == ["collector", "migrate", "orchestrator", "web-ui"]
    assert ("web-ui", "web-ui/frontend", "web-ui/frontend/Dockerfile", "prod") in zeilen
    # Compose nimmt gepinnte Images per Variable, sonst den lokalen Namen wie bisher
    images = {n: s.get("image") for n, s in json.loads(out.stdout)["services"].items()}
    projekt = json.loads(out.stdout)["name"]
    assert images["web-ui"] == f"{projekt}-web-ui"


def test_eigene_dienste_mit_image_variable():
    """Jeder Dienst mit build: nimmt gepinnte Images (betrieb.sh), sonst den lokalen Namen wie
    bisher - ein neuer eigener Dienst ohne diese Zeile wuerde nach einem Update aus dem Cockpit
    still der alte gebaute Stand bleiben."""
    yaml = pytest.importorskip("yaml")
    dienste = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]
    eigene = {name: d for name, d in dienste.items() if "build" in d}
    assert set(eigene) == {"migrate", "orchestrator", "collector", "web-ui"}
    for name, dienst in eigene.items():
        var = "AUTOMATO_IMAGE_" + name.upper().replace("-", "_")
        lokal = "${COMPOSE_PROJECT_NAME:-automato}-" + name
        assert dienst.get("image") == "${" + var + ":-" + lokal + "}", name


# --- Kanal stable ohne Tag: Freigabe-Datei (1.18.1, ADR 0060 Nachtrag) -------------------------

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.ch",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.ch",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
}  # fmt: skip


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,  # noqa: S603, S607
                         env={**os.environ, **GIT_ENV}, check=True)  # fmt: skip
    return out.stdout.strip()


def test_freigabe_datei_lesen():
    text = "# abgenommen 2026-11-17\n\n  1.17.0   " + COMMIT + "  \n# Ende\n"
    assert rm.freigabe_lesen(text.encode()) == ("1.17.0", COMMIT)
    for kaputt in (
        "",  # keine Zeile
        "# nur Kommentar\n",
        f"1.17.0 {COMMIT}\n1.16.0 {COMMIT}\n",  # zwei Zeilen
        f"v1.17.0 {COMMIT}\n",
        f"1.17.0-dev.4.{COMMIT[:7]} {COMMIT}\n",  # stable nur X.Y.Z
        f"1.17 {COMMIT}\n",
        f"1.17.0 {COMMIT[:7]}\n",  # nur voller Commit
        f"1.17.0 {COMMIT.upper()}\n",
        f"1.17.0 {COMMIT} # Kommentar am Ende\n",
        "1.17.0\n",
    ):
        with pytest.raises(rm.Abgelehnt) as exc:
            rm.freigabe_lesen(kaputt.encode())
        assert exc.value.code == rm.INHALT, kaputt
    with pytest.raises(rm.Abgelehnt):
        rm.freigabe_lesen(b"\xff\xfe")


def test_freigabe_datei_im_repo():
    """deploy/release/stable im Repo: gueltig, Kanal stable ohne Tag (Startinhalt 1.17.0)."""
    version, commit = rm.freigabe_lesen((REPO / "deploy/release/stable").read_bytes())
    assert rm.KERN_RE.match(version) and rm.COMMIT_RE.match(commit)
    assert rm._kern(version) >= (1, 17, 0)


@pytest.fixture
def git_repo(tmp_path):
    """1.16.0 -> 1.17.0 -> Doku (VERSION 1.17.0) auf main, dazu eine eigene Linie ab 1.16.0."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    commits = {}
    for name, version in (("a", "1.16.0"), ("b", "1.17.0"), ("doku", "1.17.0")):
        (repo / "VERSION").write_text(version + "\n")
        (repo / "NOTIZ").write_text(name + "\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", name)
        commits[name] = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "seite", commits["a"])
    (repo / "NOTIZ").write_text("seite\n")
    _git(repo, "commit", "-q", "-am", "seite")
    commits["seite"] = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    return repo, commits


@pytest.mark.skipif(not shutil.which("git"), reason="git fehlt")
def test_freigabe_im_repo_geprueft(git_repo):
    repo, c = git_repo
    rm.freigabe_im_repo(str(repo), c["doku"], "1.17.0", c["b"])  # Vorfahre, VERSION passt
    rm.freigabe_im_repo(str(repo), "HEAD", "1.17.0", c["doku"])  # auch der Stand selbst

    def grund(*args: str) -> str:
        with pytest.raises(rm.Abgelehnt) as exc:
            rm.freigabe_im_repo(str(repo), *args)
        assert exc.value.code == rm.PASST_NICHT
        return exc.value.grund

    assert "kein Vorfahre" in grund(c["doku"], "1.16.0", c["seite"])  # andere Linie
    assert "kein Vorfahre" in grund(c["a"], "1.17.0", c["b"])  # nach dem Stand
    falsch = grund(c["doku"], "1.16.0", c["b"])
    assert falsch == f"VERSION am Commit {c['b'][:12]} ist 1.17.0, nicht 1.16.0"
    assert "nicht im Repo" in grund(c["doku"], "1.17.0", "f" * 40)


@pytest.mark.parametrize(
    ("bisher", "aktion"),
    [
        (None, "veroeffentlichen"),  # noch kein Manifest
        (("1.15.0", COMMIT), "veroeffentlichen"),  # aelter
        (("1.17.0", COMMIT), "vorhanden"),
        (("1.17.0", "f" * 40), "behalten"),  # gleiche Version, anderer Commit
        (("1.17.1", "f" * 40), "behalten"),  # schon neuer - nie aelter
        (("1.18.0", "f" * 40), "behalten"),
        (("1.17.0-rc.1", "f" * 40), "veroeffentlichen"),  # X.Y.Z neuer als X.Y.Z-<zusatz>
        ("kaputt", "veroeffentlichen"),  # ungueltig - wird ersetzt
    ],
)
def test_freigabe_entscheiden(bisher, aktion):
    if bisher is None or bisher == "kaputt":
        roh = None if bisher is None else b"kein json"
    else:
        daten = json.loads(_manifest(kanal="stable", version="1.15.0"))
        daten["version"], daten["commit"] = bisher
        roh = json.dumps(daten).encode()
    assert rm.freigabe_entscheiden("1.17.0", COMMIT, roh)[0] == aktion


@pytest.mark.skipif(not shutil.which("git"), reason="git fehlt")
def test_freigabe_cli_wie_im_workflow(git_repo, tmp_path):
    repo, c = git_repo
    datei = tmp_path / "stable"
    datei.write_text(f"# Kanal stable\n1.17.0 {c['b']}\n")
    bisher = tmp_path / "bisher.json"
    out = _cli("freigabe", "--datei", str(datei), "--repo", str(repo), "--stand", c["doku"])
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == [
        "version=1.17.0", f"commit={c['b']}", "aktion=veroeffentlichen",
        "grund=kanal-stable hat noch kein Manifest",
    ]  # fmt: skip
    for version, aktion in (("1.16.0", "veroeffentlichen"), ("1.17.0", "vorhanden"),
                            ("1.18.0", "behalten")):  # fmt: skip
        daten = json.loads(_manifest(kanal="stable", version=version))
        daten["commit"] = c["b"]
        bisher.write_text(json.dumps(daten))
        out = _cli("freigabe", "--datei", str(datei), "--bisher", str(bisher))
        assert out.returncode == 0 and f"aktion={aktion}" in out.stdout.splitlines()
    # passt nicht zum Repo bzw. zum Stand: Exit 6, Datei ungueltig: Exit 3, Aufruf: Exit 2
    falsch = _cli("freigabe", "--datei", str(datei), "--repo", str(repo), "--stand", c["seite"])
    assert falsch.returncode == rm.PASST_NICHT and falsch.stdout == ""
    assert falsch.stderr.startswith("abgelehnt: ") and "kein Vorfahre" in falsch.stderr
    datei.write_text("1.17.0\n")
    assert _cli("freigabe", "--datei", str(datei)).returncode == rm.INHALT
    assert _cli("freigabe", "--datei", str(tmp_path / "fehlt")).returncode == rm.INHALT
    assert _cli("freigabe", "--datei", str(datei), "--repo", str(repo)).returncode == rm.AUFRUF
