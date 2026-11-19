"""Zusaetzliche CAs fuer Orchestrator und Worker (1.10.0, ADR 0053).

Quellen wie die NetBox eines Kunden hinter dem Front-Proxy tragen oft ein Zertifikat der internen
CA. Bisher hiess das: `docker-compose.override.yml` mit `SSL_CERT_FILE` von Hand (INSTALLATION-PROD
6.3). Jetzt genuegt eine Datei in `deploy/trust/` (im Container `/etc/automato/trust`, nur lesend):
beim Start baut jeder Prozess daraus ein Buendel (die bisherigen CAs plus diese) in `/tmp` und
zeigt `SSL_CERT_FILE` darauf - httpx und `ssl` lesen es von dort. Ohne Dateien aendert sich
nichts. Die Pruefung bleibt immer an; eine unlesbare Datei wird uebergangen.

TLS zwischen den Containern (interne CA, ADR 0023) nimmt seine CA ausdruecklich und ist davon nicht
betroffen.
"""

from __future__ import annotations

import logging
import os
import ssl
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

ORDNER = "/etc/automato/trust"
BUENDEL = "automato-ca-bundle.pem"
_PEM = "-----BEGIN CERTIFICATE-----"


def _grundlage() -> str:
    """Die CAs, die ohne diesen Schritt gelten: ein schon gesetztes Buendel bzw. certifi."""
    gesetzt = os.environ.get("SSL_CERT_FILE", "")
    if gesetzt and Path(gesetzt).name != BUENDEL and Path(gesetzt).is_file():
        return Path(gesetzt).read_text(encoding="utf-8", errors="replace")
    try:
        import certifi  # noqa: PLC0415 - nur hier gebraucht (Abhaengigkeit von httpx)

        return Path(certifi.where()).read_text(encoding="utf-8")
    except (ImportError, OSError):
        return ""


def _als_pem(pfad: Path) -> str | None:
    try:
        roh = pfad.read_bytes()
    except OSError:
        return None
    if _PEM.encode() in roh:
        text = roh.decode("ascii", errors="ignore")
        try:
            ssl.create_default_context(cadata=text)  # nur pruefen, ob es ein Zertifikat ist
        except (ssl.SSLError, ValueError):
            return None
        return text
    try:
        return ssl.DER_cert_to_PEM_cert(roh) if ssl.create_default_context(cadata=roh) else None
    except (ssl.SSLError, ValueError):
        return None


def zusaetzliche_cas(ordner: str | None = None) -> str | None:
    """CAs aus dem Ordner zusaetzlich vertrauen; Rueckgabe = Pfad des Buendels bzw. None."""
    quelle = Path(ordner or os.environ.get("AUTOMATO_TRUST_DIR", ORDNER))
    if not quelle.is_dir():
        return None
    teile: list[str] = []
    for datei in sorted(quelle.iterdir()):
        if datei.suffix.lower() not in {".crt", ".pem", ".cer"} or not datei.is_file():
            continue
        pem = _als_pem(datei)
        if pem is None:
            log.warning("CA-Datei %s ist unlesbar und wird uebergangen", datei)
            continue
        teile.append(pem.strip())
    if not teile:
        return None
    # fester Name, atomar ersetzt: jeder Prozess (Dienst, CLI) schreibt dasselbe Buendel
    ziel = Path(tempfile.gettempdir()) / BUENDEL
    inhalt = _grundlage().rstrip() + "\n" + "\n".join(teile) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".automato-ca-", suffix=".pem", dir=ziel.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(inhalt)
        os.replace(tmp, ziel)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        log.warning("Buendel der zusaetzlichen CAs nicht geschrieben (%s)", ziel)
        return None
    os.environ["SSL_CERT_FILE"] = str(ziel)
    log.info("Zusaetzliche CAs aus %s: %d Datei(en)", quelle, len(teile))
    return str(ziel)
