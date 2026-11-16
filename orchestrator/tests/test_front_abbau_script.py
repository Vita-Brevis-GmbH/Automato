"""deploy/front/front.sh: Sperre, Tor-Geheimnisse auf Auftrag und Ist-Stand (ADR 0059) - mit den
Ersatzprogrammen aus test_front_script.py.

`gesperrt` im Soll-Stand sperrt alle Adressen eines Kunden („Zugang gesperrt“, nach der Sperre
nach Absender und dem Zugriff), Daten bleiben. `tor_entfernen` entfernt nur das Tor-Geheimnis eines
Realms ohne Kunden - nie den Realm von Automato, je Auftrag nur einmal; verwaiste Dateien ohne
Auftrag werden nur gemeldet."""

import hashlib
import json
from pathlib import Path

from test_front_script import ENV, _soll, _status, _stub, front  # noqa: F401 - Fixture

A1, A2 = "1" * 32, "2" * 32
# Anmelde-Tor an, Realm von Automato selbst: `automato`
TOR_ENV = (
    ENV
    + """AUTOMATO_FRONT_TOR=an
AUTOMATO_OIDC_ISSUER=https://login.example.ch/realms/automato
AUTOMATO_OIDC_JWKS_URL=https://keycloak.int.example.ch/realms/automato/protocol/openid-connect/certs
"""
)
COCKPIT_A = {"kurzname": "kunde-a", "name": "Kunde A", "doc": True, "realm": "kunde-a",
             "quelle": "cockpit", "links": []}  # fmt: skip
COCKPIT_B = {"kurzname": "kunde-b", "name": "Kunde B", "realm": "kunde-b", "quelle": "cockpit",
             "links": []}  # fmt: skip


def _soll_mit(front, kunden: list[dict], **extra) -> Path:  # noqa: F811
    datei = _soll(front, kunden)
    data = json.loads(datei.read_text(encoding="utf-8"))
    datei.write_text(json.dumps({**data, **extra}), encoding="utf-8")
    return datei


def _dienst(realm: str) -> str:
    """Name des Containers wie tor_dienst in front.sh."""
    slug = realm.lower().replace(".", "-").replace("_", "-")[:20]
    return f"tor-{slug}-{hashlib.sha256(realm.encode()).hexdigest()[:8]}"


def test_gesperrt_sperrt_alle_adressen(front):  # noqa: F811
    front.env_file.write_text(TOR_ENV)
    gesperrt = {**COCKPIT_A, "gesperrt": True, "zugriff": "netze", "netze": ["203.0.113.0/24"]}
    _soll(front, [gesperrt, COCKPIT_B])
    assert front("tor", "kunde-a", input="ein-client-secret-123\n").returncode == 0
    out = front("sync", "--immer")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "SPERRE=ja" in (front.dir / "kunden" / "kunde-a.conf").read_text()
    assert "SPERRE=nein" in (front.dir / "kunden" / "kunde-b.conf").read_text()
    snippets = (front.gen / "snippets.caddy").read_text()
    assert '(front-zugang-gesperrt) {\n\trespond "Zugang gesperrt · access blocked" 403' in snippets
    site = (front.gen / "sites" / "kunde-a.caddy").read_text()
    # jede Adresse: erst Sperre nach Absender, dann Zugriff (fremde Netze: unbekannt), dann gesperrt
    for name in ("kunde-a", "kunde-a-nb", "kunde-a-doc"):
        assert f"{name}.mgmt.example.ch {{" in site
    assert site.count("\t\timport front-zugang-gesperrt\n\t}") == 3
    assert site.count('\t\timport front-kunden-sperre\n\t\trespond @front_kunde_fremd "Unbekannte '
                      'Adresse" 404\n\t\timport front-zugang-gesperrt') == 3  # fmt: skip
    # nichts mehr dahinter: kein Tor, keine Weiterleitung, auch nicht die NetBox-API mit Token
    assert "front-tor" not in site and "reverse_proxy" not in site and "handle @" not in site
    # der andere Kunde wie bisher
    assert "front-zugang-gesperrt" not in (front.gen / "sites" / "kunde-b.caddy").read_text()
    # Portal und Inventar zeigen es
    portal = (front.gen / "www" / "_portal" / "index.html").read_text()
    assert "Zugang gesperrt (zum Entfernen vorgemerkt)" in portal
    topo = json.loads((front.dir / "state" / "topologie.json").read_text())
    assert {k["kurzname"]: k["gesperrt"] for k in topo["kunden"]} == {"kunde-a": True,
                                                                      "kunde-b": False}  # fmt: skip
    assert _status(front)["ist"]["gesperrt"] == ["kunde-a"]
    assert "Zugang gesperrt (zum Entfernen vorgemerkt, ADR 0059): kunde-a" in front("liste").stdout
    pruefen = front("pruefen")
    assert (
        "kunde-a: Zugang gesperrt – zum Entfernen vorgemerkt (Cockpit), Daten bleiben"
        in pruefen.stdout
    )

    # rueckgaengig (Frist nicht abgelaufen): wieder offen, Client-Secret und Daten unveraendert
    _soll(front, [COCKPIT_A, COCKPIT_B])
    assert front("sync").returncode == 0
    site = (front.gen / "sites" / "kunde-a.caddy").read_text()
    assert "front-zugang-gesperrt" not in site and "import front-tor tor-kunde-a-" in site
    assert (front.dir / "tor" / "kunde-a.env").exists()


def test_tor_geheimnis_nur_auf_auftrag_und_nur_einmal(front):  # noqa: F811
    front.env_file.write_text(TOR_ENV)
    _soll(front, [COCKPIT_A, COCKPIT_B])
    assert front("sync").returncode == 0
    tor = front.dir / "tor"
    for realm in ("kunde-a", "kunde-b", "automato", "alt-r"):  # alt-r: Altlast ohne Kunden
        assert front("tor", realm, input=f"secret-{realm}-0123\n").returncode == 0
    # kunde-a im Cockpit abgebaut: aus dem Soll-Stand, Auftrag fuers Tor-Geheimnis
    _soll_mit(front, [COCKPIT_B], tor_entfernen=[
        {"realm": "kunde-a", "auftrag": A1},
        {"realm": "kunde-b", "auftrag": A1},  # noch genutzt: nie
        {"realm": "automato", "auftrag": A1},  # Realm von Automato: nie
        {"realm": "../x", "auftrag": A1},  # ungueltig: uebergangen
        {"realm": "alt-r", "auftrag": "kurz"},
    ])  # fmt: skip
    out = front("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Realm kunde-a: Tor-Geheimnis entfernt (Abbau aus dem Cockpit" in out.stdout
    assert not (tor / "kunde-a.env").exists()
    assert (tor / "kunde-a.entfernt").read_text().strip() == A1
    assert (tor / "kunde-a.entfernt").stat().st_mode & 0o777 == 0o600
    for realm in ("kunde-b", "automato", "alt-r"):
        assert (tor / f"{realm}.env").exists()
    compose = (front.gen / "tor" / "compose.yml").read_text()
    assert _dienst("kunde-a") not in compose and _dienst("kunde-b") in compose
    ist = _status(front)["ist"]
    assert ist["tor_entfernt"] == ["kunde-a"]
    assert ist["tor_entfernen_abgelehnt"] == [
        {"realm": "kunde-b", "grund": "in_verwendung"},
        {"realm": "automato", "grund": "haupt_realm"},
    ]
    assert ist["tor_realms_dateien"] == ["alt-r", "automato", "kunde-b"]
    assert ist["tor_verwaist"] == ["alt-r"]  # nur gemeldet, nie still geloescht
    pruefen = front("pruefen")
    assert "Realm alt-r: Tor-Geheimnis ohne Kunden" in pruefen.stdout
    assert "Tor-Geheimnis nicht entfernt: Realm kunde-b (in_verwendung)" in pruefen.stdout

    # danach von Hand wieder hinterlegt: derselbe Auftrag entfernt es nie noch einmal
    assert front("tor", "kunde-a", input="secret-neu-0123456\n").returncode == 0
    assert front("sync", "--immer").returncode == 0
    assert (tor / "kunde-a.env").exists()
    assert "kunde-a" in _status(front)["ist"]["tor_verwaist"]
    # erneuter Erzeugen-Lauf behaelt, was der letzte sync entfernt hat
    assert front("erzeugen").returncode == 0
    assert _status(front)["ist"]["tor_entfernt"] == []  # letzter sync entfernte nichts
    # ein neuer Auftrag (anderer Abbau) entfernt wieder
    _soll_mit(front, [COCKPIT_B], tor_entfernen=[{"realm": "kunde-a", "auftrag": A2}])
    assert front("sync").returncode == 0
    assert not (tor / "kunde-a.env").exists()
    assert (tor / "kunde-a.entfernt").read_text().strip() == A2
    assert front("erzeugen").returncode == 0
    assert _status(front)["ist"]["tor_entfernt"] == ["kunde-a"]


def test_tor_geheimnis_auch_mit_tor_aus(front):  # noqa: F811
    """Reste von frueher (Tor inzwischen aus) entfernt der Auftrag ebenfalls."""
    front.env_file.write_text(TOR_ENV)
    _soll(front, [COCKPIT_A])
    assert front("sync").returncode == 0
    assert front("tor", "kunde-a", input="secret-kunde-a-0123\n").returncode == 0
    front.env_file.write_text(ENV)
    _soll_mit(front, [], tor_entfernen=[{"realm": "kunde-a", "auftrag": A1}])
    assert front("sync").returncode == 0
    assert not (front.dir / "tor" / "kunde-a.env").exists()


def test_ist_nennt_tor_container(front, tmp_path):  # noqa: F811
    front.env_file.write_text(TOR_ENV)
    _soll(front, [COCKPIT_A, COCKPIT_B])
    assert front("sync").returncode == 0
    assert front("tor", "kunde-a", input="secret-kunde-a-0123\n").returncode == 0
    # Ersatz fuer docker ps: Container des Projekts automato-tor
    original = tmp_path / "bin" / "docker"
    docker = _stub(tmp_path / "bin" / "docker-ps", (
        'case "$*" in "ps -a --filter label=com.docker.compose.project=automato-tor"*)\n'
        f'  printf "%s\\n" {_dienst("kunde-a")} tor-intern tor-weg-12345678; exit 0 ;;\nesac\n'
        f'exec {original} "$@"\n'))  # fmt: skip
    assert front("erzeugen", DOCKER=str(docker)).returncode == 0
    ist = _status(front)["ist"]
    assert ist["kunden"] == ["kunde-a", "kunde-b"]
    assert ist["tor_dienste"] == ["kunde-a"] and ist["tor_dienste_fremd"] == ["tor-weg-12345678"]
    assert ist["tor_realms_dateien"] == ["kunde-a"] and ist["tor_verwaist"] == []
    pruefen = front("pruefen", DOCKER=str(docker))
    assert "Container tor-weg-12345678 des Anmelde-Tors gehört zu keinem Realm" in pruefen.stdout
