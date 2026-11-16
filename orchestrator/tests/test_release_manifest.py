"""scripts/release_manifest.py (1.15.0, ADR 0060, Format vitabrevis-manifest/v1): Manifest der
Images je Kanal erstellen (deterministisch, nur per Digest), signieren und pruefen (Ed25519 ueber
openssl, Testschluessel zur Laufzeit), Versionen vergleichen, eigene Dienste aus Compose."""

import base64
import importlib.util
import json
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
    args = {"kanal": "dev", "version": f"1.15.0-dev.{COMMIT[:7]}", "commit": COMMIT,
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
        {"version": "1.15.0-dev.fffffff"},  # sha7 passt nicht zum Commit
        {"kanal": "latest"},  # Version mit -dev. im Kanal latest
    ):
        with pytest.raises(rm.Abgelehnt):
            _manifest(**kaputt)
    assert json.loads(_manifest(kanal="stable", version="1.15.0"))["version"] == "1.15.0"


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
    assert (
        rm.pruefen(roh, sig.encode(), pub, "automato", f"1.15.0-dev.{COMMIT[:7]}")["commit"]
        == COMMIT
    )
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
    version = f"1.15.0-dev.{COMMIT[:7]}"
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
