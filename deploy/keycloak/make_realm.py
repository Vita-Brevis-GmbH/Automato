"""Realm-Datei fuer eine weitere Automato-Umgebung aus der Vorlage erzeugen.

Jede Automato-Umgebung (Prod, Dev, Kunde X) bekommt in Keycloak einen eigenen Realm:
Mandanten-IDs gehoeren zur Datenbank der jeweiligen Umgebung, Benutzer/OTP und
Redirect-Adressen bleiben getrennt. Die Vorlage ``realm-automato.json`` (Rollen, Clients,
tenant_ids-/Audience-Mapper, Haertung, geseedeter automato-admin) wird nur angepasst:
Realm-Name, Anzeigename und die erlaubten Web-UI-Adressen des Clients ``automato-web``.

Seit 1.4.0 (ADR 0051) dazu der Client ``automato-front`` des Anmelde-Tors im Front-Proxy: mit
``--front-kunde``/``--front-domain`` die Rückkehr-Adressen aller Werkzeuge des Kunden (Startseite,
-nb, -doc, -um, -cmdb), mit ``--front-url`` weitere (z. B. das Portal start.<interne Domain>).
Das Client-Secret erzeugt Keycloak beim Import; es gehört nur auf die Automato-VM (front.sh tor).

Beispiele:
    python3 deploy/keycloak/make_realm.py --realm automato-dev \\
        --ui-url https://10.0.12.12 --display-name "Automato Dev" --with-localhost \\
        > realm-automato-dev.json
    python3 deploy/keycloak/make_realm.py --realm kunde-a --display-name "Kunde A" \\
        --ui-url https://kunde-a-doc.mgmt.example.ch \\
        --front-kunde kunde-a --front-domain mgmt.example.ch > realm-kunde-a.json
"""

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

TEMPLATE = Path(__file__).with_name("realm-automato.json")
WEB_CLIENT = "automato-web"
FRONT_CLIENT = "automato-front"
FRONT_TOOLS = ("", "-nb", "-doc", "-um", "-cmdb")  # Werkzeuge eines Kunden (front.sh)
LOCAL_DEV_ORIGIN = "http://localhost:5173"
_REALM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ValueError(f"keine gueltige UI-Adresse: {url!r} (erwartet z. B. https://host)")
    return f"{parts.scheme}://{parts.netloc}"


def front_urls(kunde: str | None, domain: str | None, extra: list[str] | None = None) -> list[str]:
    """Adressen hinter dem Anmelde-Tor: alle Werkzeuge eines Kunden und weitere (Portal)."""
    urls = []
    if kunde or domain:
        if not (kunde and domain):
            raise ValueError("--front-kunde und --front-domain gehoeren zusammen")
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?", kunde):
            raise ValueError(f"ungueltiger Kurzname: {kunde!r}")
        if not re.fullmatch(r"[a-z0-9.-]+", domain):
            raise ValueError(f"ungueltige Domain: {domain!r}")
        urls += [f"https://{kunde}{w}.{domain}" for w in FRONT_TOOLS]
    for url in extra or []:
        origin = _origin(url)
        if not origin.startswith("https://"):
            raise ValueError(f"Anmelde-Tor nur mit https: {url!r}")
        urls.append(origin)
    return list(dict.fromkeys(urls))


def build_realm(
    template: dict,
    realm: str,
    ui_urls: list[str],
    display_name: str | None = None,
    with_localhost: bool = False,
    front: list[str] | None = None,
) -> dict:
    """Vorlage kopieren und auf die Umgebung zuschneiden (Vorlage bleibt unveraendert)."""
    if not _REALM_NAME.match(realm):
        raise ValueError(f"ungueltiger Realm-Name: {realm!r}")
    out = copy.deepcopy(template)
    out["realm"] = realm
    if display_name:
        out["displayName"] = display_name

    origins = [_origin(u) for u in ui_urls]
    if with_localhost:
        origins.append(LOCAL_DEV_ORIGIN)
    origins = list(dict.fromkeys(origins))  # Duplikate raus, Reihenfolge stabil

    clients = {c.get("clientId"): c for c in out.get("clients", [])}
    if WEB_CLIENT not in clients:
        raise ValueError(f"Client {WEB_CLIENT!r} fehlt in der Vorlage")
    web = clients[WEB_CLIENT]
    web["redirectUris"] = [f"{o}/*" for o in origins]
    web["webOrigins"] = origins
    web.setdefault("attributes", {})["post.logout.redirect.uris"] = "+"
    if FRONT_CLIENT in clients:  # Anmelde-Tor: nur die Rückkehr zu oauth2-proxy, kein Web-Origin
        clients[FRONT_CLIENT]["redirectUris"] = [f"{u}/oauth2/callback" for u in front or []]
        clients[FRONT_CLIENT].pop("secret", None)  # erzeugt Keycloak, nie aus einer Datei
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Keycloak-Realm fuer eine Automato-Umgebung aus realm-automato.json erzeugen."
    )
    parser.add_argument("--realm", required=True, help="Realm-Name, z. B. automato-dev")
    parser.add_argument(
        "--ui-url",
        dest="ui_urls",
        action="append",
        required=True,
        help="Adresse der Web-UI, z. B. https://10.0.12.12 (mehrfach moeglich)",
    )
    parser.add_argument("--display-name", help="Anzeigename auf der Login-Seite")
    parser.add_argument(
        "--with-localhost",
        action="store_true",
        help=f"zusaetzlich {LOCAL_DEV_ORIGIN} erlauben (Dev per SSH-Tunnel)",
    )
    parser.add_argument(
        "--front-kunde", help="Anmelde-Tor: Kurzname des Kunden (mit --front-domain)"
    )
    parser.add_argument("--front-domain", help="Anmelde-Tor: Domain der Kunden-Adressen")
    parser.add_argument(
        "--front-url",
        dest="front_urls",
        action="append",
        default=[],
        help="Anmelde-Tor: weitere Adresse, z. B. https://start.mgmt.int.example.ch (mehrfach)",
    )
    parser.add_argument("--template", type=Path, default=TEMPLATE, help="Vorlage (Default: Repo)")
    args = parser.parse_args(argv)

    template = json.loads(args.template.read_text(encoding="utf-8"))
    try:
        front = front_urls(args.front_kunde, args.front_domain, args.front_urls)
        realm = build_realm(
            template, args.realm, args.ui_urls, args.display_name, args.with_localhost, front
        )
    except ValueError as exc:
        parser.error(str(exc))
    json.dump(realm, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
