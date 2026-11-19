#!/usr/bin/env python3
"""Manifest der Automato-Images je Kanal (1.15.0, ADR 0060, Format `vitabrevis-manifest/v1`).

    release_manifest.py dienste --compose <config.json> [--wurzel <repo>]
        eigene Dienste (mit `build:`) aus `docker compose config --format json`, je Zeile
        Dienst, Kontext, Dockerfile und Ziel (`-` ohne) mit Tab getrennt, Pfade relativ zum Repo
    release_manifest.py erstellen --kanal <k> --version <v> --commit <sha> --image <dienst>=<ref> …
        manifest.json auf stdout - deterministisch (feste Reihenfolge, Images sortiert)
    release_manifest.py signieren --schluessel <privat.pem> --manifest manifest.json
        Ed25519-Signatur ueber die exakten Bytes, Base64 in einer Zeile (manifest.sig)
    release_manifest.py pruefen --manifest <m> (--signatur <s> --schluessel <pub.pem> |
                                --ohne-signatur) [--produkt automato] [--version <v>]
        Format, Inhalt, Produkt, Version und Signatur; gibt `name=wert` je Zeile aus
    release_manifest.py vergleichen <neu> <neu-commit> <laufend> <laufend-commit>
        `neuer`, `gleich` oder `aelter`: der Kern X.Y.Z zaehlt, bei gleichem Kern im selben Kanal
        die Zahl n aus `-dev.<n>.<sha7>` bzw. `-latest.<n>.<sha7>` (1.18.1); sonst gilt gleicher
        Kern mit anderem Commit als neuer (Vertrag betrieb/v1) - deploy/betrieb.sh prueft dann die
        Abstammung im Checkout
    release_manifest.py freigabe --datei deploy/release/stable [--repo <git> --stand <commit>]
                                 [--bisher <manifest.json>]
        Kanal stable ohne Tag (1.18.1, ADR 0060 Nachtrag): Freigabe-Datei lesen (eine Zeile
        `X.Y.Z <commit>`, Kommentare mit #); mit --repo/--stand: Commit im Repo, Vorfahre des
        Stands, VERSION dort = X.Y.Z; mit --bisher: das veroeffentlichte Manifest von kanal-stable.
        Gibt `version=`, `commit=`, `aktion=` (veroeffentlichen | vorhanden | behalten) und
        `grund=` aus; nie eine aeltere Version als die veroeffentlichte

Nur Standardbibliothek; Ed25519 ueber `openssl` (>= 3.0, `pkeyutl -rawin`), so prueft auch der
Host (deploy/betrieb.sh) ohne weitere Werkzeuge. Exit-Codes von `pruefen`: 0 gueltig, 3 Inhalt
ungueltig, 4 Signatur fehlt bzw. ungueltig, 5 Schluessel ungueltig bzw. openssl fehlt,
6 Produkt bzw. Version passt nicht, 2 Aufruf. `freigabe`: 0 ok, 3 Datei ungueltig, 6 passt nicht
zum Repo (Commit fehlt, kein Vorfahre, VERSION anders).
"""

from __future__ import annotations  # auch mit aelterem python3 auf dem Host

import argparse
import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

FORMAT = "vitabrevis-manifest/v1"
PRODUKT = "automato"
KANAELE = ("stable", "latest", "dev")
MAX_BYTES = 64 * 1024
FELDER = ("format", "produkt", "kanal", "version", "commit", "erstellt", "images")

VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]{1,40})?$")
KERN_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
# dev bzw. latest (1.18.1): <X.Y.Z>-<kanal>.<n>.<sha7>, n = `git rev-list --count <commit>` -
# waechst mit jedem Commit derselben Linie (bis 1.18.0 ohne n: <X.Y.Z>-<kanal>.<sha7>)
ZAEHLER_RE = re.compile(r"^(dev|latest)\.([0-9]{1,9})\.([0-9a-f]{7})$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ERSTELLT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
DIENST_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
# Registry mit Punkt im Namen (nie implizit Docker Hub), Pfad, immer per Digest - nie nur ein Tag
REF_RE = re.compile(
    r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::[0-9]{1,5})?"
    r"(?:/[a-z0-9]+(?:[._-]+[a-z0-9]+)*)+@sha256:[0-9a-f]{64}$"
)

OK, AUFRUF, INHALT, SIGNATUR, SCHLUESSEL, PASST_NICHT = 0, 2, 3, 4, 5, 6


class Abgelehnt(Exception):
    def __init__(self, code: int, grund: str) -> None:
        super().__init__(grund)
        self.code = code
        self.grund = grund


def _kern(version: str) -> tuple[int, int, int]:
    teile = version.split("-", 1)[0].split(".")
    return int(teile[0]), int(teile[1]), int(teile[2])


def _zaehler(version: str) -> tuple[str, int] | None:
    """Kanal und n aus `<X.Y.Z>-<kanal>.<n>.<sha7>`, sonst None (stable, bis 1.18.0, gebaut)."""
    m = ZAEHLER_RE.match(version.split("-", 1)[1]) if "-" in version else None
    return (m.group(1), int(m.group(2))) if m else None


def vergleichen(neu: str, neu_commit: str, laufend: str, laufend_commit: str) -> str:
    """`neuer`, `gleich` oder `aelter` - Vertrag betrieb/v1.

    Gleicher Kern: gleicher Commit -> gleich; im selben Kanal mit n entscheidet n (1.18.1);
    sonst `neuer` wie bisher - die Abstammung prueft deploy/betrieb.sh im Checkout."""
    if not VERSION_RE.match(neu):
        raise Abgelehnt(INHALT, "neue Version ungueltig")
    if not VERSION_RE.match(laufend):
        return "neuer"  # laufende Version unbekannt (z. B. ohne VERSION) - nie aelter
    if _kern(neu) != _kern(laufend):
        return "neuer" if _kern(neu) > _kern(laufend) else "aelter"
    if neu_commit and neu_commit == laufend_commit:
        return "gleich"
    if neu == laufend and not laufend_commit:
        return "gleich"
    z_neu, z_laufend = _zaehler(neu), _zaehler(laufend)
    if z_neu and z_laufend and z_neu[0] == z_laufend[0] and z_neu[1] != z_laufend[1]:
        return "neuer" if z_neu[1] > z_laufend[1] else "aelter"
    return "neuer"


def pruefe_inhalt(daten: object) -> dict:
    """Aufbau und Werte des Manifests, sonst Abgelehnt(INHALT)."""
    if not isinstance(daten, dict):
        raise Abgelehnt(INHALT, "kein JSON-Objekt")
    fremd = sorted(set(daten) - set(FELDER))
    if fremd:
        raise Abgelehnt(INHALT, f"unbekannte Felder: {', '.join(fremd)}")
    fehlt = [f for f in FELDER if f not in daten]
    if fehlt:
        raise Abgelehnt(INHALT, f"Felder fehlen: {', '.join(fehlt)}")
    if daten["format"] != FORMAT:
        raise Abgelehnt(INHALT, "unbekanntes Format")
    for feld in ("produkt", "kanal", "version", "commit", "erstellt"):
        if not isinstance(daten[feld], str):
            raise Abgelehnt(INHALT, f"{feld} ist kein Text")
    if not DIENST_RE.match(daten["produkt"]):
        raise Abgelehnt(INHALT, "produkt ungueltig")
    if daten["kanal"] not in KANAELE:
        raise Abgelehnt(INHALT, "kanal ungueltig")
    if not VERSION_RE.match(daten["version"]):
        raise Abgelehnt(INHALT, "version ungueltig")
    if not COMMIT_RE.match(daten["commit"]):
        raise Abgelehnt(INHALT, "commit ungueltig (40 Hex-Zeichen)")
    if not ERSTELLT_RE.match(daten["erstellt"]):
        raise Abgelehnt(INHALT, "erstellt ungueltig (JJJJ-MM-TTThh:mm:ssZ)")
    images = daten["images"]
    if not isinstance(images, dict) or not images:
        raise Abgelehnt(INHALT, "images fehlen")
    for dienst, ref in images.items():
        if not DIENST_RE.match(dienst):
            raise Abgelehnt(INHALT, "Dienstname ungueltig")
        if not isinstance(ref, str) or len(ref) > 300 or not REF_RE.match(ref):
            raise Abgelehnt(INHALT, f"Image von {dienst} nicht per Digest (@sha256:)")
    return daten


def _ohne_doppelte(paare: list) -> dict:
    schluessel = [k for k, _ in paare]
    if len(schluessel) != len(set(schluessel)):
        raise Abgelehnt(INHALT, "doppelte Felder")
    return dict(paare)


def lesen(roh: bytes) -> dict:
    if len(roh) > MAX_BYTES:
        raise Abgelehnt(INHALT, "groesser als 64 KiB")
    try:
        daten = json.loads(roh.decode("utf-8"), object_pairs_hook=_ohne_doppelte)
    except ValueError:  # auch UnicodeDecodeError; doppelte Felder kommen als Abgelehnt durch
        raise Abgelehnt(INHALT, "kein gueltiges JSON (UTF-8)") from None
    return pruefe_inhalt(daten)


def erstellen(
    kanal: str, version: str, commit: str, images: dict[str, str], erstellt: str | None = None
) -> bytes:
    """Manifest-Bytes: feste Reihenfolge der Felder, Images nach Dienst sortiert, LF am Ende."""
    daten = {
        "format": FORMAT,
        "produkt": PRODUKT,
        "kanal": kanal,
        "version": version,
        "commit": commit,
        "erstellt": erstellt or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "images": {k: images[k] for k in sorted(images)},
    }
    pruefe_inhalt(daten)
    # Kanal und Version passen zusammen (Vertrag: stable X.Y.Z, sonst <VERSION>-<kanal>.<n>.<sha7>,
    # n = Anzahl Commits bis hier - monoton auf derselben Linie, 1.18.1)
    if kanal == "stable":
        if "-" in version:
            raise Abgelehnt(INHALT, "stable nur mit X.Y.Z")
    else:
        zaehler = _zaehler(version)
        if not zaehler or zaehler[0] != kanal or not version.endswith(f".{commit[:7]}"):
            raise Abgelehnt(
                INHALT, f"Version fuer {kanal}: <VERSION>-{kanal}.<n>.<sha7 des Commits>"
            )
    roh = (json.dumps(daten, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(roh) > MAX_BYTES:
        raise Abgelehnt(INHALT, "groesser als 64 KiB")
    return roh


def _openssl() -> str:
    pfad = shutil.which("openssl")
    if not pfad:
        raise Abgelehnt(SCHLUESSEL, "openssl fehlt")
    return pfad


def _schluesselart(pfad: str, oeffentlich: bool) -> None:
    """Nur Ed25519 - sonst prueft `pkeyutl -rawin` anders oder gar nicht."""
    args = [_openssl(), "pkey", "-in", pfad, "-noout", "-text"]
    if oeffentlich:
        args.insert(2, "-pubin")
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603 - fester Argumentvektor
    except (OSError, subprocess.SubprocessError):
        raise Abgelehnt(SCHLUESSEL, "Schluessel nicht lesbar") from None
    erste = out.stdout.splitlines()[0] if out.stdout else ""
    if out.returncode != 0 or not erste.startswith("ED25519 "):
        raise Abgelehnt(SCHLUESSEL, "kein Ed25519-Schluessel")


def signieren(schluessel: str, roh: bytes) -> str:
    _schluesselart(schluessel, oeffentlich=False)
    with tempfile.TemporaryDirectory() as tmp:
        nachricht = os.path.join(tmp, "manifest.json")
        with open(nachricht, "wb") as fh:
            fh.write(roh)
        out = subprocess.run(  # noqa: S603 - fester Argumentvektor
            [_openssl(), "pkeyutl", "-sign", "-inkey", schluessel, "-rawin", "-in", nachricht],
            capture_output=True, timeout=30, check=False,
        )  # fmt: skip
    if out.returncode != 0 or len(out.stdout) != 64:
        raise Abgelehnt(SCHLUESSEL, "Signieren fehlgeschlagen")
    return base64.b64encode(out.stdout).decode("ascii")


def signatur_pruefen(roh: bytes, signatur: bytes, schluessel: str) -> None:
    """Ed25519 ueber genau diese Bytes mit `openssl pkeyutl -verify -rawin`."""
    if len(signatur) > 1024:
        raise Abgelehnt(SIGNATUR, "Signatur zu gross")
    try:
        sig = base64.b64decode(signatur.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise Abgelehnt(SIGNATUR, "Signatur ist kein Base64") from None
    if len(sig) != 64:
        raise Abgelehnt(SIGNATUR, "Signatur fehlt bzw. hat nicht 64 Bytes")
    _schluesselart(schluessel, oeffentlich=True)
    with tempfile.TemporaryDirectory() as tmp:
        nachricht, sigdatei = os.path.join(tmp, "manifest.json"), os.path.join(tmp, "sig.bin")
        with open(nachricht, "wb") as fh:
            fh.write(roh)
        with open(sigdatei, "wb") as fh:
            fh.write(sig)
        out = subprocess.run(  # noqa: S603 - fester Argumentvektor
            [_openssl(), "pkeyutl", "-verify", "-pubin", "-inkey", schluessel, "-rawin",
             "-in", nachricht, "-sigfile", sigdatei],
            capture_output=True, text=True, timeout=30, check=False,
        )  # fmt: skip
    if out.returncode != 0 or "Signature Verified Successfully" not in out.stdout:
        raise Abgelehnt(SIGNATUR, "Signatur ungueltig")


def pruefen(
    roh: bytes,
    signatur: bytes | None,
    schluessel: str | None,
    produkt: str | None = PRODUKT,
    version: str | None = None,
) -> dict:
    daten = lesen(roh)
    if produkt and daten["produkt"] != produkt:
        raise Abgelehnt(PASST_NICHT, f"Manifest fuer {daten['produkt']}, nicht {produkt}")
    if version and daten["version"] != version:
        raise Abgelehnt(PASST_NICHT, "Version des Manifests passt nicht zum Auftrag")
    if signatur is not None:
        if not schluessel:
            raise Abgelehnt(SCHLUESSEL, "Herausgeber-Schluessel fehlt")
        signatur_pruefen(roh, signatur, schluessel)
    return daten


def dienste(config: dict, wurzel: str) -> list[tuple[str, str, str, str]]:
    """Eigene Dienste (mit build:) aus `docker compose config --format json`."""
    wurzel = os.path.realpath(wurzel)
    zeilen = []
    for name, dienst in sorted((config.get("services") or {}).items()):
        build = dienst.get("build") if isinstance(dienst, dict) else None
        if not build:
            continue
        if not DIENST_RE.match(name):
            raise Abgelehnt(INHALT, f"Dienstname ungueltig: {name}")
        if isinstance(build, str):
            build = {"context": build}
        kontext = os.path.realpath(os.path.join(wurzel, build.get("context") or "."))
        dockerfile = os.path.realpath(
            os.path.join(kontext, build.get("dockerfile") or "Dockerfile")
        )
        for pfad in (kontext, dockerfile):
            if os.path.commonpath([wurzel, pfad]) != wurzel:
                raise Abgelehnt(INHALT, f"{name}: Pfad ausserhalb des Repos")
        zeilen.append((
            name,
            os.path.relpath(kontext, wurzel),
            os.path.relpath(dockerfile, wurzel),
            build.get("target") or "-",
        ))  # fmt: skip
    if not zeilen:
        raise Abgelehnt(INHALT, "keine Dienste mit build:")
    return zeilen


def freigabe_lesen(roh: bytes) -> tuple[str, str]:
    """deploy/release/stable (1.18.1): genau eine Zeile `X.Y.Z <commit>`, Kommentarzeilen mit #."""
    try:
        text = roh.decode("utf-8")
    except UnicodeDecodeError:
        raise Abgelehnt(INHALT, "Freigabe-Datei ist kein UTF-8") from None
    zeilen = [z.strip() for z in text.splitlines()]
    eintraege = [z for z in zeilen if z and not z.startswith("#")]
    if len(eintraege) != 1:
        raise Abgelehnt(INHALT, "Freigabe-Datei: genau eine Zeile `X.Y.Z <commit>` erwartet")
    teile = eintraege[0].split()
    if len(teile) != 2 or not KERN_RE.match(teile[0]) or not COMMIT_RE.match(teile[1]):
        raise Abgelehnt(INHALT, "Freigabe-Datei: `X.Y.Z <commit>` (ohne Zusatz, 40 Hex-Zeichen)")
    return teile[0], teile[1]


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    git = shutil.which("git")
    if not git:
        raise Abgelehnt(PASST_NICHT, "git fehlt")
    try:
        return subprocess.run(  # noqa: S603 - fester Argumentvektor
            [git, "-C", repo, *args], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError):
        raise Abgelehnt(PASST_NICHT, "git nicht ausfuehrbar") from None


def freigabe_im_repo(repo: str, stand: str, version: str, commit: str) -> None:
    """Commit im Repo, Vorfahre des gepushten Stands und dort VERSION = version."""
    if _git(repo, "cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        raise Abgelehnt(PASST_NICHT, f"Commit {commit[:12]} nicht im Repo")
    abstammung = _git(repo, "merge-base", "--is-ancestor", commit, stand).returncode
    if abstammung == 1:
        raise Abgelehnt(PASST_NICHT, f"Commit {commit[:12]} ist kein Vorfahre von {stand[:12]}")
    if abstammung != 0:
        raise Abgelehnt(PASST_NICHT, f"Abstammung von {commit[:12]} nicht pruefbar")
    datei = _git(repo, "show", f"{commit}:VERSION")
    gefunden = datei.stdout.strip() if datei.returncode == 0 else ""
    if gefunden != version:
        raise Abgelehnt(
            PASST_NICHT, f"VERSION am Commit {commit[:12]} ist {gefunden or '?'}, nicht {version}"
        )


def freigabe_entscheiden(version: str, commit: str, bisher: bytes | None) -> tuple[str, str]:
    """Aktion fuer kanal-stable: `veroeffentlichen` (fehlt bzw. aelter), `vorhanden` (diese
    Version mit diesem Commit) oder `behalten` (neuer bzw. gleiche Version mit anderem Commit)."""
    if bisher is None:
        return "veroeffentlichen", "kanal-stable hat noch kein Manifest"
    try:
        daten = lesen(bisher)
    except Abgelehnt:
        return "veroeffentlichen", "Manifest in kanal-stable ungueltig - wird ersetzt"
    b_version, b_commit = daten["version"], daten["commit"]
    if _kern(version) > _kern(b_version):
        return "veroeffentlichen", f"kanal-stable hat {b_version}"
    if _kern(version) < _kern(b_version):
        return "behalten", f"kanal-stable hat schon die neuere Version {b_version} - nie aelter"
    if b_version != version:  # gleicher Kern mit Zusatz: X.Y.Z ist neuer als X.Y.Z-<zusatz>
        return "veroeffentlichen", f"kanal-stable hat {b_version}"
    if b_commit == commit:
        return "vorhanden", f"kanal-stable hat schon {version}"
    return "behalten", f"kanal-stable hat {version} schon mit {b_commit[:12]} - nicht ersetzt"


def _images_aus_args(werte: list[str]) -> dict[str, str]:
    images: dict[str, str] = {}
    for wert in werte:
        dienst, _, ref = wert.partition("=")
        if not ref or dienst in images:
            raise Abgelehnt(AUFRUF, f"--image erwartet <dienst>=<ref> (einmal je Dienst): {wert}")
        images[dienst] = ref
    return images


def _lies(pfad: str, grenze: int) -> bytes:
    try:
        with open(pfad, "rb") as fh:
            roh = fh.read(grenze + 1)
    except OSError:
        raise Abgelehnt(INHALT, f"nicht lesbar: {os.path.basename(pfad)}") from None
    if len(roh) > grenze:
        raise Abgelehnt(INHALT, f"groesser als {grenze // 1024} KiB: {os.path.basename(pfad)}")
    return roh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="befehl", required=True)
    p = sub.add_parser("dienste")
    p.add_argument("--compose", required=True)
    p.add_argument("--wurzel", default=".")
    p = sub.add_parser("erstellen")
    p.add_argument("--kanal", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--erstellt")
    p.add_argument("--image", action="append", default=[])
    p = sub.add_parser("signieren")
    p.add_argument("--schluessel", required=True)
    p.add_argument("--manifest", required=True)
    p = sub.add_parser("pruefen")
    p.add_argument("--manifest", required=True)
    p.add_argument("--signatur")
    p.add_argument("--schluessel")
    p.add_argument("--ohne-signatur", action="store_true")
    p.add_argument("--produkt", default=PRODUKT)
    p.add_argument("--version")
    p = sub.add_parser("vergleichen")
    for name in ("neu", "neu_commit", "laufend", "laufend_commit"):
        p.add_argument(name)
    p = sub.add_parser("freigabe")
    p.add_argument("--datei", required=True)
    p.add_argument("--repo")
    p.add_argument("--stand")
    p.add_argument("--bisher")
    args = parser.parse_args(argv)

    try:
        if args.befehl == "dienste":
            with open(args.compose, encoding="utf-8") as fh:
                config = json.load(fh)
            for zeile in dienste(config, args.wurzel):
                print("\t".join(zeile))
        elif args.befehl == "erstellen":
            roh = erstellen(args.kanal, args.version, args.commit,
                            _images_aus_args(args.image), args.erstellt)  # fmt: skip
            sys.stdout.buffer.write(roh)
        elif args.befehl == "signieren":
            print(signieren(args.schluessel, _lies(args.manifest, MAX_BYTES)))
        elif args.befehl == "pruefen":
            if args.ohne_signatur == (args.signatur is not None):
                raise Abgelehnt(AUFRUF, "--signatur/--schluessel oder --ohne-signatur angeben")
            signatur = None if args.ohne_signatur else _lies(args.signatur, 1024)
            daten = pruefen(_lies(args.manifest, MAX_BYTES), signatur, args.schluessel,
                            args.produkt, args.version)  # fmt: skip
            for feld in ("produkt", "kanal", "version", "commit", "erstellt"):
                print(f"{feld}={daten[feld]}")
            for dienst, ref in sorted(daten["images"].items()):
                print(f"image.{dienst}={ref}")
        elif args.befehl == "freigabe":
            if (args.repo is None) != (args.stand is None):
                raise Abgelehnt(AUFRUF, "--repo und --stand nur zusammen")
            version, commit = freigabe_lesen(_lies(args.datei, 4096))
            if args.repo is not None:
                freigabe_im_repo(args.repo, args.stand, version, commit)
            bisher = None
            if args.bisher is not None:
                with open(args.bisher, "rb") as fh:
                    bisher = fh.read(MAX_BYTES + 1)
            aktion, grund = freigabe_entscheiden(version, commit, bisher)
            print(f"version={version}\ncommit={commit}\naktion={aktion}\ngrund={grund}")
        else:
            print(vergleichen(args.neu, args.neu_commit, args.laufend, args.laufend_commit))
    except Abgelehnt as exc:
        print(f"abgelehnt: {exc.grund}", file=sys.stderr)
        return exc.code
    except (OSError, ValueError) as exc:
        print(f"Fehler: {exc.__class__.__name__}", file=sys.stderr)
        return AUFRUF
    return OK


if __name__ == "__main__":
    sys.exit(main())
