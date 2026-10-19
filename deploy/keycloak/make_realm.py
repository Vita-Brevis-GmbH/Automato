"""Realm-Datei fuer eine weitere Automato-Umgebung aus der Vorlage erzeugen.

Jede Automato-Umgebung (Prod, Dev, Kunde X) bekommt in Keycloak einen eigenen Realm:
Mandanten-IDs gehoeren zur Datenbank der jeweiligen Umgebung, Benutzer/OTP und
Redirect-Adressen bleiben getrennt. Die Vorlage ``realm-automato.json`` (Rollen, Clients,
tenant_ids-/Audience-Mapper, Haertung, geseedeter automato-admin) wird nur angepasst:
Realm-Name, Anzeigename und die erlaubten Web-UI-Adressen des Clients ``automato-web``.

Beispiel:
    python3 deploy/keycloak/make_realm.py --realm automato-dev \\
        --ui-url https://172.25.12.12 --display-name "Automato Dev" --with-localhost \\
        > realm-automato-dev.json
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
LOCAL_DEV_ORIGIN = "http://localhost:5173"
_REALM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ValueError(f"keine gueltige UI-Adresse: {url!r} (erwartet z. B. https://host)")
    return f"{parts.scheme}://{parts.netloc}"


def build_realm(
    template: dict,
    realm: str,
    ui_urls: list[str],
    display_name: str | None = None,
    with_localhost: bool = False,
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

    for client in out.get("clients", []):
        if client.get("clientId") == WEB_CLIENT:
            client["redirectUris"] = [f"{o}/*" for o in origins]
            client["webOrigins"] = origins
            client.setdefault("attributes", {})["post.logout.redirect.uris"] = "+"
            return out
    raise ValueError(f"Client {WEB_CLIENT!r} fehlt in der Vorlage")


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
        help="Adresse der Web-UI, z. B. https://172.25.12.12 (mehrfach moeglich)",
    )
    parser.add_argument("--display-name", help="Anzeigename auf der Login-Seite")
    parser.add_argument(
        "--with-localhost",
        action="store_true",
        help=f"zusaetzlich {LOCAL_DEV_ORIGIN} erlauben (Dev per SSH-Tunnel)",
    )
    parser.add_argument("--template", type=Path, default=TEMPLATE, help="Vorlage (Default: Repo)")
    args = parser.parse_args(argv)

    template = json.loads(args.template.read_text(encoding="utf-8"))
    try:
        realm = build_realm(
            template, args.realm, args.ui_urls, args.display_name, args.with_localhost
        )
    except ValueError as exc:
        parser.error(str(exc))
    json.dump(realm, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
