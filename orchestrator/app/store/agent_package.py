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
