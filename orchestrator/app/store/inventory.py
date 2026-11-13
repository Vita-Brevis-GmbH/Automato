"""Inventar fuers Cockpit (1.10.0, ADR 0053): was diese Automato-Installation betreibt.

Der Front-Proxy (`front.sh erzeugen`, auch ueber den Host-Agenten) schreibt seine Adressen und
Ziele nach `topologie.json` in den Ordner des Host-Agenten; hier kommen Version, Umgebung und
Keycloak dazu. Das Cockpit holt das mit einem eigenen Token (`GET /inventory`) und fuehrt daraus
sein Inventar nach (Hosts, Instanzen je Kunde). Keine Geheimnisse und keine Fachdaten: nur
Namen und Adressen, jeder Wert geprueft und gekuerzt - die Datei schreibt root auf dem Host,
gelesen wird sie trotzdem wie fremde Eingabe.
"""

import json
import re
from pathlib import Path

from app import __version__
from app.config import Settings

TOPOLOGIE = "topologie.json"
#: Fassung der Antwort (Vertrag im Cockpit: docs/vertraege/inventar.md)
VERTRAG = 1
_KUNDE = ("kurzname", "name", "startseite", "netbox", "automato", "magister", "cmdb")
_ADRESSE = re.compile(r"^[0-9A-Fa-f.:]{2,45}$")


def _topologie(state_dir: str) -> dict:
    if not state_dir:
        return {}
    try:
        data = json.loads((Path(state_dir) / TOPOLOGIE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _text(value: object, laenge: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and len(value) <= laenge and value.isprintable() else None


def _front(topo: dict) -> dict | None:
    if not topo:
        return None
    vm = topo.get("vm") if isinstance(topo.get("vm"), dict) else {}
    adressen = vm.get("adressen") if isinstance(vm.get("adressen"), list) else []
    return {
        "stand": _text(topo.get("zeit"), 40),
        "vm": {
            "name": _text(vm.get("name"), 253),
            "adressen": [a for a in adressen if isinstance(a, str) and _ADRESSE.match(a)][:16],
        },
        "portal": _text(topo.get("portal")),
        "domain": _text(topo.get("domain"), 253),
        "netbox_host": _text(topo.get("netbox_host"), 260),
        "uebrige": _text(topo.get("uebrige"), 260),
        "magister": _text(topo.get("magister")),
        "cmdb": _text(topo.get("cmdb")),
        "tor": topo.get("tor") is True,
    }


def _kunden(topo: dict) -> list[dict]:
    kunden = topo.get("kunden") if isinstance(topo.get("kunden"), list) else []
    out = []
    for k in kunden[:1000]:
        if isinstance(k, dict) and _text(k.get("kurzname"), 63):
            out.append({feld: _text(k.get(feld)) for feld in _KUNDE})
    return out


def inventory(settings: Settings) -> dict:
    """Antwort von `GET /inventory` - Vertrag v1 (`VERTRAG`)."""
    topo = _topologie(settings.front_state_dir)
    issuer = settings.oidc_issuer if settings.auth_mode == "oidc" else ""
    return {
        "vertrag": VERTRAG,
        "automato": {
            "version": __version__,
            "umgebung": settings.environment,
            "oberflaeche": _text(topo.get("oberflaeche")),
        },
        "keycloak": {"issuer": _text(issuer)},
        "front": _front(topo),
        "kunden": _kunden(topo),
    }
