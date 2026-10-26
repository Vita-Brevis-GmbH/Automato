#!/usr/bin/env python3
"""Plattform-Version an einer Stelle pflegen (Datei `VERSION`, SemVer).

Alle Pakete (automato, orchestrator, collectors, sot-gateway, Web-UI) tragen dieselbe
Version. Das Skript haelt `pyproject.toml`, `uv.lock` und `web-ui/frontend/package*.json`
deckungsgleich mit `VERSION`:

    python3 scripts/version.py            # aktuelle Version anzeigen
    python3 scripts/version.py check      # alle Stellen gleich? (CI, Exit 1 bei Abweichung)
    python3 scripts/version.py set 0.3.0  # neue Version setzen (VERSION, pyprojects, uv.lock)

Neue Version je Auslieferung: Patch fuer Korrekturen, Minor fuer neue Funktionen
(siehe CHANGELOG.md).
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = {
    "automato": ROOT / "pyproject.toml",
    "orchestrator": ROOT / "orchestrator" / "pyproject.toml",
    "collectors": ROOT / "collectors" / "pyproject.toml",
    "sot-gateway": ROOT / "sot-gateway" / "pyproject.toml",
}
FRONTEND = ROOT / "web-ui" / "frontend"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_PROJECT_VERSION = re.compile(r'(?m)^(\[project\][^\[]*?^version = ")([^"]+)(")', re.S)


def current() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _pyproject_version(path: Path) -> str | None:
    m = _PROJECT_VERSION.search(path.read_text(encoding="utf-8"))
    return m.group(2) if m else None


def _lock_versions() -> dict[str, str]:
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    out = {}
    for name in PACKAGES:
        m = re.search(rf'(?m)^name = "{re.escape(name)}"\nversion = "([^"]+)"', text)
        if m:
            out[name] = m.group(1)
    return out


def check() -> list[str]:
    want = current()
    problems = [] if SEMVER.match(want) else [f"VERSION '{want}' ist keine SemVer X.Y.Z"]
    for path in PACKAGES.values():
        have = _pyproject_version(path)
        if have != want:
            problems.append(f"{path.relative_to(ROOT)}: {have} != {want}")
    for name, have in _lock_versions().items():
        if have != want:
            problems.append(f"uv.lock {name}: {have} != {want}")
    for label, have in _frontend_versions().items():
        if have != want:
            problems.append(f"{label}: {have} != {want}")
    return problems


def _frontend_versions() -> dict[str, str | None]:
    pkg = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    return {
        "web-ui/frontend/package.json": pkg.get("version"),
        "web-ui/frontend/package-lock.json": lock.get("version"),
        "web-ui/frontend/package-lock.json (packages)": lock.get("packages", {})
        .get("", {})
        .get("version"),
    }


def _set_json_version(path: Path, new: str, lockfile: bool) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = new
    if lockfile and "" in data.get("packages", {}):
        data["packages"][""]["version"] = new
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def set_version(new: str) -> None:
    if not SEMVER.match(new):
        raise SystemExit(f"'{new}' ist keine SemVer X.Y.Z")
    (ROOT / "VERSION").write_text(new + "\n", encoding="utf-8")
    for path in PACKAGES.values():
        text = path.read_text(encoding="utf-8")
        path.write_text(_PROJECT_VERSION.sub(rf"\g<1>{new}\g<3>", text, count=1), encoding="utf-8")
    lock = ROOT / "uv.lock"
    text = lock.read_text(encoding="utf-8")
    for name in PACKAGES:
        text = re.sub(
            rf'(?m)^(name = "{re.escape(name)}"\nversion = ")[^"]+(")', rf"\g<1>{new}\g<2>", text
        )
    lock.write_text(text, encoding="utf-8")
    _set_json_version(FRONTEND / "package.json", new, lockfile=False)
    _set_json_version(FRONTEND / "package-lock.json", new, lockfile=True)


def main(argv: list[str]) -> int:
    if not argv:
        print(current())
        return 0
    if argv[0] == "check":
        problems = check()
        for p in problems:
            print(f"Version abweichend: {p}", file=sys.stderr)
        if not problems:
            print(f"Version {current()} ueberall gleich")
        return 1 if problems else 0
    if argv[0] == "set" and len(argv) == 2:
        set_version(argv[1])
        print(f"Version {argv[1]} gesetzt (VERSION, pyproject.toml, uv.lock, package.json)")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
