#!/usr/bin/env python3
"""Agent-Paket pruefen: enthalten die Wheels ihre Module? (ADR 0011)

Ein Wheel, das aus einem unvollstaendigen Quellbaum gebaut wird (z. B. nur `pyproject.toml`
im Docker-Build), ist gueltig, enthaelt aber nur `dist-info` - pip installiert es ohne
Fehler, der Agent startet danach nicht (`No module named ...`). Darum bricht der Bau ab:

    python3 scripts/check_agent_package.py /agent-package
"""

import sys
import zipfile
from pathlib import Path

REQUIRED = {
    "collectors-*.whl": "collectors/run.py",
    "sot_gateway-*.whl": "sot_gateway/readonly.py",
}


def check(package_dir: Path) -> list[str]:
    problems = []
    for pattern, module in REQUIRED.items():
        wheels = sorted(package_dir.glob(pattern))
        if not wheels:
            problems.append(f"{pattern} fehlt in {package_dir}")
        elif module not in zipfile.ZipFile(wheels[-1]).namelist():
            problems.append(f"{wheels[-1].name} enthaelt {module} nicht (leeres Wheel)")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    problems = check(Path(argv[0]))
    for p in problems:
        print(f"Agent-Paket unvollstaendig: {p}", file=sys.stderr)
    if not problems:
        print("Agent-Paket vollstaendig")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
