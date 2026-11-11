#!/usr/bin/env python3
"""Die .env der Automato-VM pruefen und ordnen (1.6.0) - zeigt nie Werte, nur Namen.

    python3 deploy/env_check.py [--datei .env]            pruefen (Exit 1 bei Fehlern)
    python3 deploy/env_check.py --ordnen [--datei .env]   ordnen, vorher Sicherung
                                                          .env.vorher-<Zeit>

Aufruf ueber `sudo deploy/aufbau.sh env` bzw. `... env ordnen`.

Bekannt ist, was der Code liest: Schluessel aus .env.example, die Einstellungen von Orchestrator
und Collector (Settings, Praefix AUTOMATO_), die Variablen in docker-compose.yml und die
AUTOMATO_-Namen der Skripte unter deploy/. Ordnen schreibt die Schluessel in den Abschnitten und
der Reihenfolge von .env.example, weitere bekannte danach; nicht mehr gelesene fallen weg,
unbekannte bleiben auskommentiert stehen (Tippfehler?). Werte bleiben unveraendert.
"""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# seit 1.6.0 nicht mehr gelesen - mit Grund (ordnen entfernt sie)
_GERAETE = "Geraete und Referenzquellen nur noch im Web-UI (Mandanten -> Quellsysteme)"
OBSOLETE = {
    "AUTOMATO_COMPOSE_PROFILES": "ersetzt durch COMPOSE_PROFILES (ordnen stellt um)",
    **{
        f"AUTOMATO_{k}": _GERAETE
        for k in (
            "FORTIGATE_URL", "FORTIGATE_TOKEN", "FORTIGATE_VERIFY_TLS",
            "ACI_URL", "ACI_USERNAME", "ACI_PASSWORD", "ACI_VERIFY_TLS", "ACI_TENANTS",
            "NETBOX_URL", "NETBOX_TOKEN", "NETBOX_VERIFY_TLS",
            "INFOBLOX_URL", "INFOBLOX_USERNAME", "INFOBLOX_PASSWORD", "INFOBLOX_VERIFY_TLS",
        )
    },
}  # fmt: skip
UNKNOWN_TITLE = "Unbekannt (auskommentiert, bitte pruefen)"
COMPOSE_BUILTIN = {"COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME", "COMPOSE_FILE"}
DEV_TOKEN = "dev-only-token"  # noqa: S105 - Vorgabe der Entwicklung, kein Geheimnis

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_EXAMPLE_KEY = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")
_SECTION = re.compile(r"^# --- (.+?) -*$")
_FIELD = re.compile(r"^    ([a-z_][a-z0-9_]*)\s*:")


def ok(text: str) -> None:
    print(f"  \033[32mOK\033[0m      {text}")


def warn(text: str) -> None:
    print(f"  \033[33mWARNUNG\033[0m {text}")


def error(text: str) -> None:
    print(f"  \033[31mFEHLER\033[0m  {text}")


def info(text: str) -> None:
    print(f"          {text}")


def example_sections(root: Path) -> list[tuple[str, list[str]]]:
    """Abschnitte von .env.example mit ihren Schluesseln (Reihenfolge wie dort)."""
    sections: list[tuple[str, list[str]]] = []
    for line in (root / ".env.example").read_text().splitlines():
        if m := _SECTION.match(line):
            sections.append((m.group(1).strip(), []))
        elif (m := _EXAMPLE_KEY.match(line)) and sections:
            if m.group(1) not in sections[-1][1]:
                sections[-1][1].append(m.group(1))
    return sections


def _settings_fields(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    fields, inside = set(), False
    for line in path.read_text().splitlines():
        if line.startswith("class Settings"):
            inside = True
        elif inside and line and not line.startswith((" ", "#")):
            inside = False
        elif inside and (m := _FIELD.match(line)) and m.group(1) != "model_config":
            fields.add("AUTOMATO_" + m.group(1).upper())
    return fields


def known_keys(root: Path) -> set[str]:
    keys = set(COMPOSE_BUILTIN)
    for _, section in example_sections(root):
        keys.update(section)
    for cfg in ("orchestrator/app/config.py", "collectors/src/collectors/config.py"):
        keys |= _settings_fields(root / cfg)
    compose = root / "docker-compose.yml"
    if compose.is_file():
        keys.update(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", compose.read_text()))
    for script in [*root.glob("deploy/*.sh"), *root.glob("deploy/*/*.sh")]:
        keys.update(k for k in re.findall(r"AUTOMATO_[A-Z0-9_]+", script.read_text())
                    if not k.endswith("_"))  # fmt: skip
    return keys - set(OBSOLETE)


def parse(path: Path) -> tuple[list[tuple[int, str, str]], list[tuple[int, str]]]:
    """(Eintraege (Zeile, Schluessel, roher Wert), Zeilen ohne NAME=Wert)."""
    entries, bad = [], []
    for nr, line in enumerate(path.read_text().splitlines(), 1):
        line = line.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if m := _LINE.match(line):
            entries.append((nr, m.group(1), m.group(2)))
        else:
            bad.append((nr, line))
    return entries, bad


def _plain(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _profiles_from_old(raw: str) -> str:
    """'--profile core --profile proxy' -> 'core,proxy'"""
    words = _plain(raw).replace("=", " ").split()
    return ",".join(w for i, w in enumerate(words) if i and words[i - 1] == "--profile")


def _kept_unknown(path: Path) -> list[str]:
    """Schon auskommentierte unbekannte Zeilen (frueheres ordnen) - bleiben stehen."""
    keep, inside = [], False
    for line in path.read_text().splitlines():
        if line == f"# --- {UNKNOWN_TITLE} ---":
            inside = True
        elif line.startswith("# --- "):
            inside = False
        elif inside and line.startswith("# "):
            keep.append(line)
    return keep


def check(path: Path, root: Path = ROOT) -> int:
    print(f"\n\033[1m==> .env pruefen ({path})\033[0m")
    if not path.is_file():
        error(f"{path} fehlt - Vorlage: cp .env.example {path}")
        return 1
    entries, bad = parse(path)
    known = known_keys(root)
    values: dict[str, str] = {}
    lines: dict[str, list[int]] = {}
    for nr, key, raw in entries:
        values[key] = _plain(raw)  # wie Compose: die letzte gilt
        lines.setdefault(key, []).append(nr)
    failed = False
    for nr, _ in bad:
        warn(f"Zeile {nr}: kein NAME=Wert (wird ignoriert; ordnen kommentiert sie aus)")
    for key, nrs in lines.items():
        if len(nrs) > 1:
            warn(f"doppelt: {key} (Zeilen {', '.join(map(str, nrs))}) - Compose nimmt die letzte, "
                 "die Skripte die erste; ordnen behaelt die letzte")  # fmt: skip
    for key in lines:
        if key in OBSOLETE:
            warn(f"nicht mehr gelesen: {key} - {OBSOLETE[key]}")
        elif key not in known:
            warn(f"unbekannt: {key} - Tippfehler? (ordnen kommentiert ihn aus)")

    profiles = [p.strip() for p in values.get("COMPOSE_PROFILES", "").split(",") if p.strip()]
    if not profiles:
        if "AUTOMATO_COMPOSE_PROFILES" in values:
            error("COMPOSE_PROFILES fehlt - AUTOMATO_COMPOSE_PROFILES wird nicht mehr gelesen: "
                  "sudo deploy/aufbau.sh env ordnen")  # fmt: skip
            failed = True
        else:
            warn("COMPOSE_PROFILES fehlt - Compose und die Skripte starten nur das Profil core")
    environment = values.get("AUTOMATO_ENVIRONMENT", "dev")
    if environment != "dev" and values.get("AUTOMATO_INTERNAL_API_TOKEN", DEV_TOKEN) in (
        "",
        DEV_TOKEN,
    ):
        error(f"AUTOMATO_INTERNAL_API_TOKEN fehlt bzw. ist der Entwicklungs-Wert ({environment}) - "
              "openssl rand -hex 32")  # fmt: skip
        failed = True
    mtls = values.get("AUTOMATO_INTERNAL_MTLS_MODE", "off")
    if "agent-gateway" in profiles or mtls in ("require", "bind"):
        if not values.get("AUTOMATO_AGENT_GATEWAY_HOST"):
            error("AUTOMATO_AGENT_GATEWAY_HOST fehlt (Name im Zertifikat des Agent-Gateways)")
            failed = True
        if len(values.get("AUTOMATO_AGENT_GATEWAY_SECRET", "")) < 16:
            error("AUTOMATO_AGENT_GATEWAY_SECRET fehlt bzw. kuerzer als 16 Zeichen")
            failed = True
    if "proxy" in profiles and not values.get("AUTOMATO_UI_HOST"):
        warn("AUTOMATO_UI_HOST fehlt (Name der Oberflaeche, Profil proxy)")
    if "POSTGRES_PASSWORD" not in values:
        warn("POSTGRES_PASSWORD fehlt - Vorgabe der Entwicklung")
    if not failed:
        ok(f"{len(lines)} Eintraege geprueft (Werte werden nicht angezeigt)")
    return 1 if failed else 0


def order(path: Path, root: Path = ROOT, now: datetime | None = None) -> int:
    print(f"\n\033[1m==> .env ordnen ({path})\033[0m")
    if not path.is_file():
        error(f"{path} fehlt")
        return 1
    entries, bad = parse(path)
    known = known_keys(root)
    values: dict[str, str] = {}
    for _, key, raw in entries:
        values.pop(key, None)  # die letzte gilt, an ihrer Stelle
        values[key] = raw
    switched = False
    if "COMPOSE_PROFILES" not in values and "AUTOMATO_COMPOSE_PROFILES" in values:
        if derived := _profiles_from_old(values["AUTOMATO_COMPOSE_PROFILES"]):
            values["COMPOSE_PROFILES"] = derived
            switched = True
    removed = [k for k in values if k in OBSOLETE]
    unknown = [k for k in values if k not in OBSOLETE and k not in known]

    out = [
        "# Automato - Einstellungen der VM (geordnet mit: sudo deploy/aufbau.sh env ordnen).",
        "# Bedeutung der Werte: .env.example. Nie committen.",
    ]
    placed: set[str] = set()
    for title, keys in example_sections(root):
        block = [f"{k}={values[k]}" for k in keys if k in values and k not in OBSOLETE]
        placed.update(k for k in keys if k in values)
        if block:
            out += ["", f"# --- {title} ---", *block]
    rest = sorted(k for k in values if k not in placed and k in known)
    if rest:
        out += ["", "# --- Weitere (bekannt, nicht in .env.example) ---",
                *(f"{k}={values[k]}" for k in rest)]  # fmt: skip
    commented = list(dict.fromkeys([*_kept_unknown(path), *(f"# {k}={values[k]}" for k in unknown),
                                    *(f"# {line}" for _, line in bad)]))  # fmt: skip
    if commented:
        out += ["", f"# --- {UNKNOWN_TITLE} ---", *commented]
    text = "\n".join(out) + "\n"
    if text == path.read_text():
        ok("bereits geordnet - nichts geaendert")
        return 0
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.vorher-{stamp}")
    shutil.copy2(path, backup)
    st = path.stat()
    tmp = path.with_name(f"{path.name}.neu")
    tmp.write_text(text)
    os.chmod(tmp, st.st_mode & 0o777)
    if os.geteuid() == 0:
        os.chown(tmp, st.st_uid, st.st_gid)
    tmp.replace(path)
    ok(f"geordnet: {sum(1 for line in out if '=' in line and not line.startswith('#'))} Eintraege")
    if switched:
        ok("umgestellt: AUTOMATO_COMPOSE_PROFILES -> COMPOSE_PROFILES")
    if removed:
        ok(f"entfernt (nicht mehr gelesen): {', '.join(removed)}")
    if unknown or bad:
        warn(f"auskommentiert (unbekannt): {', '.join(unknown) or '-'}"
             + (f"; {len(bad)} Zeile(n) ohne NAME=Wert" if bad else ""))  # fmt: skip
    info(f"Sicherung: {backup} (Werte unveraendert, Kommentare durch Abschnitte ersetzt)")
    info("Danach: sudo docker compose up -d")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ordnen", action="store_true")
    ap.add_argument("--datei", default=os.environ.get("AUTOMATO_ENV_FILE", ".env"))
    args = ap.parse_args(argv)
    path = Path(args.datei)
    if not path.is_absolute():
        path = ROOT / path
    if args.ordnen:
        rc = order(path)
        return rc or check(path)
    return check(path)


if __name__ == "__main__":
    sys.exit(main())
