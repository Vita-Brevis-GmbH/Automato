"""Agent-Paket fuer das Selbst-Update (ADR 0011).

Das Orchestrator-Image enthaelt das Agent-Paket (`agent_package_dir`, Dockerfile-Stage
`agent-package`). Fuer das Update zaehlen die Dateien, die der Agent installiert: das
collectors- und das sot-gateway-Wheel sowie `run-agent.ps1`.

Version = `<Release>+<Build>`: Release ist die Plattform-Version (Datei `VERSION`, steht im
Wheel-Namen, z. B. `0.2.0`), Build ein Fingerabdruck ueber Namen und SHA-256 der Dateien
(8 Hex-Zeichen). So erkennt der Agent auch eine Aenderung ohne neue Release-Nummer.
"""

import hashlib
import re
from functools import lru_cache
from pathlib import Path

UPDATE_PATTERNS = ("collectors-*.whl", "sot_gateway-*.whl", "run-agent.ps1")


def update_files(base_dir: str) -> list[Path]:
    base = Path(base_dir)
    if not base.is_dir():
        return []
    out: list[Path] = []
    for pattern in UPDATE_PATTERNS:
        found = sorted(p for p in base.glob(pattern) if p.is_file())
        out += found[-1:]  # jeweils die neueste Datei
    return out


def manifest(base_dir: str) -> dict | None:
    """{"version", "files": [{"name", "sha256", "size"}]} - None ohne Agent-Paket."""
    files = update_files(base_dir)
    if not any(p.name.startswith("collectors-") for p in files):
        return None
    key = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    return _manifest(key)


@lru_cache(maxsize=4)
def _manifest(key: tuple) -> dict:
    entries = []
    for path, _mtime, size in key:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        entries.append({"name": Path(path).name, "sha256": digest, "size": size})
    build = hashlib.sha256(
        "\n".join(f"{e['name']}:{e['sha256']}" for e in entries).encode()
    ).hexdigest()[:8]
    release = next((m.group(1) for e in entries if (m := _WHEEL_VERSION.match(e["name"]))), "0.0.0")
    return {"version": f"{release}+{build}", "release": release, "build": build, "files": entries}


_WHEEL_VERSION = re.compile(r"^collectors-([^-]+)-")


def file_path(base_dir: str, name: str) -> Path | None:
    """Pfad einer Update-Datei - nur Namen aus dem Manifest (kein Pfad-Trick moeglich)."""
    for path in update_files(base_dir):
        if path.name == name:
            return path
    return None


# --- Signierte Updates (Ed25519, Produktions-Gate) -------------------------------------------
# Signiert wird offline (scripts/agent_signing.py) die Nachricht aus Version und Dateien; der
# Orchestrator liefert die Signatur nur mit aus, der Agent prueft sie gegen seinen hinterlegten
# oeffentlichen Schluessel. Dieselbe Nachricht bildet collectors.common.selfupdate.

SIGNING_CONTEXT = "automato-agent-update/v1"


def signed_message(info: dict) -> bytes:
    """Zu signierende Nachricht: Kontext, Version, je Datei `name:sha256` (sortiert)."""
    files = sorted((str(f["name"]), str(f["sha256"])) for f in info.get("files", []))
    lines = [SIGNING_CONTEXT, str(info["version"]), *(f"{n}:{h}" for n, h in files)]
    return ("\n".join(lines) + "\n").encode()


def load_signature(path: str, info: dict | None) -> str | None:
    """Eingespielte Signatur (JSON {version, signature}) - nur, wenn sie zum Paket passt."""
    import json

    if info is None:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != info["version"]:
        return None
    sig = data.get("signature")
    return sig if isinstance(sig, str) and sig else None


def verify(pubkey_b64: str, info: dict, signature_b64: str) -> bool:
    import base64
    import binascii

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64, validate=True))
        key.verify(base64.b64decode(signature_b64, validate=True), signed_message(info))
    except (InvalidSignature, ValueError, binascii.Error):
        return False
    return True


def signature_status(package_dir: str, signature_file: str, pubkey_b64: str) -> dict:
    """Zustand fuer UI/System-Seite: no_package | unsigned | signed | invalid | unchecked."""
    info = manifest(package_dir)
    if info is None:
        return {"state": "no_package", "version": None}
    sig = load_signature(signature_file, info)
    if sig is None:
        state = "unsigned"
    elif not pubkey_b64:
        state = "unchecked"  # Signatur da, aber kein Schluessel zum Pruefen hinterlegt
    else:
        state = "signed" if verify(pubkey_b64, info, sig) else "invalid"
    return {"state": state, "version": info["version"]}
