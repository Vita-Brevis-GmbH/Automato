"""Inventar fuers Cockpit (1.10.0, ADR 0053): was diese Automato-Installation betreibt.

Der Front-Proxy (`front.sh erzeugen`, auch ueber den Host-Agenten) schreibt seine Adressen und
Ziele nach `topologie.json` in den Ordner des Host-Agenten; hier kommen Version, Umgebung und
Keycloak dazu. Das Cockpit holt das mit einem eigenen Token (`GET /inventory`) und fuehrt daraus
sein Inventar nach (Hosts, Instanzen je Kunde). Keine Geheimnisse und keine Fachdaten: nur
Namen und Adressen, jeder Wert geprueft und gekuerzt - die Datei schreibt root auf dem Host,
gelesen wird sie trotzdem wie fremde Eingabe.

Seit 1.11.0 (ADR 0054, Gegenrichtung der Freigaben) je Kunde auch Herkunft (`quelle`),
Werkzeuge, Zugriff, Netze und Realm - so uebernimmt das Cockpit Kunden, die es noch nicht kennt,
ohne Realm oder Zugriff zu verlieren. Vertrag bleibt v1 (nur neue optionale Felder).
"""

import ipaddress
import json
import re
from pathlib import Path

from app import __version__
from app.config import Settings
from app.store import portal

TOPOLOGIE = "topologie.json"
#: Fassung der Antwort (Vertrag im Cockpit: docs/vertraege/inventar.md)
VERTRAG = 1
_KUNDE = ("kurzname", "name", "startseite", "netbox", "automato", "magister", "cmdb")
_ADRESSE = re.compile(r"^[0-9A-Fa-f.:]{2,45}$")
#: Herkunft je Kunde: Cockpit (Freigaben), Oberflaeche (Kunden-Portal), Befehl (front.sh kunde)
_QUELLEN = ("cockpit", "oberflaeche", "befehl")
_WERKZEUGE = ("doc", "nb", "um", "cmdb")


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


def _netze(value: object) -> list[str]:
    out: list[str] = []
    for raw in (value if isinstance(value, list) else [])[: portal.MAX_NETS]:
        if not isinstance(raw, str):
            continue
        try:
            net = str(ipaddress.ip_network(raw.strip(), strict=False))
        except ValueError:
            continue
        if net not in out:
            out.append(net)
    return out


def _herkunft(k: dict) -> dict:
    """Herkunft, Werkzeuge, Zugriff, Netze und Realm (1.11.0) - nur bekannte bzw. gueltige
    Werte, sonst null/leer (aeltere topologie.json ohne die Felder: null)."""
    werkzeuge = k.get("werkzeuge")
    zugriff = k.get("zugriff") if k.get("zugriff") in portal.ACCESS else None
    realm = k.get("realm")
    realm_ok = isinstance(realm, str) and portal.REALM.match(realm) and realm != "master"
    return {
        "quelle": k.get("quelle") if k.get("quelle") in _QUELLEN else None,
        "werkzeuge": [w for w in _WERKZEUGE if w in werkzeuge]
        if isinstance(werkzeuge, list)
        else None,
        "zugriff": zugriff,
        "netze": _netze(k.get("netze")) if zugriff == "netze" else [],
        "realm": realm if realm_ok else None,
    }


def _kunden(topo: dict) -> list[dict]:
    kunden = topo.get("kunden") if isinstance(topo.get("kunden"), list) else []
    out = []
    for k in kunden[:1000]:
        if isinstance(k, dict) and _text(k.get("kurzname"), 63):
            out.append({**{feld: _text(k.get(feld)) for feld in _KUNDE}, **_herkunft(k)})
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
