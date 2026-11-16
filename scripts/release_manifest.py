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
        `neuer`, `gleich` oder `aelter` (nur der Kern X.Y.Z zaehlt; gleicher Kern mit anderem
        Commit gilt als neuer - Vertrag betrieb/v1)

Nur Standardbibliothek; Ed25519 ueber `openssl` (>= 3.0, `pkeyutl -rawin`), so prueft auch der
Host (deploy/betrieb.sh) ohne weitere Werkzeuge. Exit-Codes von `pruefen`: 0 gueltig, 3 Inhalt
ungueltig, 4 Signatur fehlt bzw. ungueltig, 5 Schluessel ungueltig bzw. openssl fehlt,
6 Produkt bzw. Version passt nicht, 2 Aufruf.
"""

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


def vergleichen(neu: str, neu_commit: str, laufend: str, laufend_commit: str) -> str:
    """`neuer`, `gleich` oder `aelter` - Vertrag betrieb/v1."""
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
    # Kanal und Version passen zusammen (Vertrag: stable X.Y.Z, sonst <VERSION>-<kanal>.<sha7>)
    if kanal == "stable":
        if "-" in version:
            raise Abgelehnt(INHALT, "stable nur mit X.Y.Z")
    elif not version.endswith(f"-{kanal}.{commit[:7]}"):
        raise Abgelehnt(INHALT, f"Version fuer {kanal}: <VERSION>-{kanal}.<sha7 des Commits>")
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
