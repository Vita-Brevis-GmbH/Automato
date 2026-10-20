"""Agent-Paket fuer das Selbst-Update (ADR 0011).

Das Orchestrator-Image enthaelt das Agent-Paket (`agent_package_dir`, Dockerfile-Stage
`agent-package`). Fuer das Update zaehlen die Dateien, die der Agent installiert: das
collectors- und das sot-gateway-Wheel sowie `run-agent.ps1`. Die **Version** ist ein
Fingerabdruck ueber Namen und SHA-256 dieser Dateien - so erkennt der Agent jede Aenderung,
auch wenn die Paketnummer (0.1.0) gleich bleibt.
"""

import hashlib
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
    fingerprint = hashlib.sha256(
        "\n".join(f"{e['name']}:{e['sha256']}" for e in entries).encode()
    ).hexdigest()
    return {"version": fingerprint[:16], "files": entries}


def file_path(base_dir: str, name: str) -> Path | None:
    """Pfad einer Update-Datei - nur Namen aus dem Manifest (kein Pfad-Trick moeglich)."""
    for path in update_files(base_dir):
        if path.name == name:
            return path
    return None
