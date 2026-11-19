"""deploy/front/front.sh: eigene Automato-Instanz je Kunde (1.16.0, ADR 0061) - mit den
Ersatzprogrammen aus test_front_script.py und einem docker, das die laufenden Anschlüsse der
Instanzen im Netz automato-front-instanzen meldet.

`automato_instanz` im Soll-Stand lässt `<kunde>-doc` hinter dem Tor an den Anschluss der Instanz
gehen (automato-<n>-web-ui:5173) statt an die Hauptinstanz; läuft er nicht, antwortet die Adresse
503 - nie ein anderes Ziel. `sync` erzeugt neu, sobald eine Instanz kommt oder geht; der Ist-Stand
nennt die gefundenen Instanzen."""

import json
from pathlib import Path

from test_front_script import ENV, _soll, _status, _stub, front  # noqa: F401 - Fixture

TOR_ENV = (
    ENV
    + """AUTOMATO_FRONT_TOR=an
AUTOMATO_OIDC_ISSUER=https://login.example.ch/realms/automato
AUTOMATO_OIDC_JWKS_URL=https://keycloak.int.example.ch/realms/automato/protocol/openid-connect/certs
"""
)
KUNDE_A = {"kurzname": "kunde-a", "name": "Kunde A", "doc": True, "realm": "kunde-a",
           "quelle": "cockpit", "links": [], "automato_instanz": "kunde-a"}  # fmt: skip
KUNDE_B = {"kurzname": "kunde-b", "name": "Kunde B", "doc": True, "realm": "kunde-b",
           "quelle": "cockpit", "links": []}  # fmt: skip
INSTANZ_ZIEL = "reverse_proxy automato-kunde-a-web-ui:5173"
HAUPT_ZIEL = "reverse_proxy web-ui:5173"
NICHT_DA = 'respond "Automato-Instanz nicht erreichbar · instance not available" 503'


def _docker(front, tmp: Path) -> dict[str, str]:  # noqa: F811
    """docker wie im Test des Front-Proxys, dazu die Anschlüsse der Instanzen (Datei `da`: je
    Zeile ein Compose-Projekt) und das Netz der Instanzen (fehlt mit Datei `kein-netz`)."""
    orig = front.env_file.parent / "bin" / "docker"
    anschluss = "--filter label=com.docker.compose.service=anschluss"
    stub = _stub(
        tmp / "docker-instanz",
        f"""
case "$*" in
  "ps --filter network=automato-front-instanzen {anschluss} "*)
    echo "docker $*" >> "$FRONT_LOG"; cat "{tmp}/da" 2>/dev/null; exit 0 ;;
  "network inspect automato-front-instanzen")
    echo "docker $*" >> "$FRONT_LOG"; [ ! -f "{tmp}/kein-netz" ]; exit $? ;;
esac
exec "{orig}" "$@"
""",
    )
    return {"DOCKER": str(stub)}


def _da(tmp: Path, *projekte: str) -> None:
    (tmp / "da").write_text("".join(p + "\n" for p in projekte))


def _doc(front) -> str:  # noqa: F811
    site = (front.gen / "sites" / "kunde-a.caddy").read_text()
    return site.split("kunde-a-doc.mgmt.example.ch {")[1]


def test_doku_an_die_eigene_instanz(front, tmp_path):  # noqa: F811
    env = _docker(front, tmp_path)
    _da(tmp_path, "automato-kunde-a", "fremd-projekt", "automato-Kaputt")
    _soll(front, [KUNDE_A, KUNDE_B])
    out = front("sync", **env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "INSTANZ=kunde-a" in (front.dir / "kunden" / "kunde-a.conf").read_text()
    assert "INSTANZ=''" in (front.dir / "kunden" / "kunde-b.conf").read_text()
    site = (front.gen / "sites" / "kunde-a.caddy").read_text()
    assert "# Dokumentation: eigene Automato-Instanz automato-kunde-a (ADR 0061)" in site
    doc = _doc(front)
    # die Adresse geht an die Instanz - nie an die Hauptinstanz (ohne Tor ein Ziel für alles)
    assert doc.count(INSTANZ_ZIEL) == 1 and HAUPT_ZIEL not in doc
    assert "respond @internal 404" in doc
    # andere Kunden und die übrigen Adressen unverändert an die Hauptinstanz bzw. NetBox
    b = (front.gen / "sites" / "kunde-b.caddy").read_text()
    assert HAUPT_ZIEL in b and "automato-kunde-" not in b
    assert "reverse_proxy https://10.0.30.20" in site.split("kunde-a-doc.")[0]
    # Kachel „Dokumentation“ zeigt auf dieselbe Adresse (jetzt die Instanz)
    seite = (front.gen / "www" / "kunde-a" / "index.html").read_text()
    assert 'href="https://kunde-a-doc.mgmt.example.ch"' in seite
    # Inventar: Kunde mit Instanz, Ist-Stand mit den gefundenen (nur gültige Namen)
    topo = json.loads((front.gen.parent / "state" / "topologie.json").read_text())
    kunden = {k["kurzname"]: k for k in topo["kunden"]}
    assert kunden["kunde-a"]["automato_instanz"] == "kunde-a"
    assert "automato_instanz" not in kunden["kunde-b"]
    assert _status(front)["ist"]["instanzen"] == ["kunde-a"]
    assert "Doku (Instanz kunde-a)" in front("liste", **env).stdout


def test_instanz_laeuft_nicht_503_und_sync_holt_nach(front, tmp_path):  # noqa: F811
    env = _docker(front, tmp_path)
    _da(tmp_path)
    _soll(front, [KUNDE_A])
    assert front("sync", **env).returncode == 0
    doc = _doc(front)
    assert doc.count(NICHT_DA) == 1 and HAUPT_ZIEL not in doc and INSTANZ_ZIEL not in doc
    assert _status(front)["ist"]["instanzen"] == []
    pruefen = front("pruefen", **env)
    meldung = "Instanz automato-kunde-a läuft nicht im Netz automato-front-instanzen"
    assert meldung in pruefen.stderr
    # Soll-Stand unverändert, Instanz startet: der nächste sync (Timer) erzeugt neu
    _da(tmp_path, "automato-kunde-a")
    out = front("sync", **env)
    assert out.returncode == 0 and "Eigene Automato-Instanz gestartet bzw. weg" in out.stdout
    assert _doc(front).count(INSTANZ_ZIEL) == 1
    assert _status(front)["ist"]["instanzen"] == ["kunde-a"]
    assert "unverändert – nichts zu tun" in front("sync", **env).stdout
    # Instanz entfernt: wieder 503, nie die Hauptinstanz
    _da(tmp_path)
    assert front("sync", **env).returncode == 0
    assert _doc(front).count(NICHT_DA) == 1 and HAUPT_ZIEL not in _doc(front)


def test_instanz_hinter_tor_und_zugriff(front, tmp_path):  # noqa: F811
    front.env_file.write_text(TOR_ENV)
    env = _docker(front, tmp_path)
    _da(tmp_path, "automato-kunde-a")
    _soll(front, [{**KUNDE_A, "zugriff": "netze", "netze": ["203.0.113.0/24"]}])
    assert front("tor", "kunde-a", input="ein-client-secret-123\n", **env).returncode == 0
    assert front("sync", "--immer", **env).returncode == 0
    doc = _doc(front)
    # Sperre nach Absender, Zugriff, API ohne Tor (prüft das Token selbst), dann Tor, dann Instanz
    reihe = [doc.index("import front-kunden-sperre"), doc.index("@front_kunde_fremd \"Unbekannte"),
             doc.index("handle @front_api"), doc.index("import front-tor tor-kunde-a-"),
             doc.rindex(INSTANZ_ZIEL)]  # fmt: skip
    assert reihe == sorted(reihe), doc
    assert doc.count(INSTANZ_ZIEL) == 2 and HAUPT_ZIEL not in doc
    # gesperrt (Abbau): auch die Instanz nicht mehr erreichbar
    _soll(front, [{**KUNDE_A, "gesperrt": True}])
    assert front("sync", **env).returncode == 0
    doc = _doc(front)
    assert "import front-zugang-gesperrt" in doc and INSTANZ_ZIEL not in doc


def test_sync_prueft_instanzen(front, tmp_path):  # noqa: F811
    env = _docker(front, tmp_path)
    _soll(front, [KUNDE_B])
    assert front("sync", **env).returncode == 0
    for kunden, meldung in (
        ([{**KUNDE_A, "automato_instanz": "Kunde_A"}], "Automato-Instanz 'Kunde_A' ungültig"),
        ([{**KUNDE_A, "automato_instanz": "a;b"}], "ungültig"),
        ([KUNDE_A, {**KUNDE_B, "automato_instanz": "kunde-a"}], "doppelt"),
    ):
        _soll(front, kunden)
        out = front("sync", **env)
        assert out.returncode != 0
        status = _status(front)
        assert status["ok"] is False and meldung in status["meldung"], status
        assert HAUPT_ZIEL in (front.gen / "sites" / "kunde-b.caddy").read_text()  # alter Stand


def test_pruefen_netz_und_altlasten(front, tmp_path):  # noqa: F811
    env = _docker(front, tmp_path)
    _da(tmp_path, "automato-kunde-a", "automato-alt")
    _soll(front, [KUNDE_A])
    assert front("sync", **env).returncode == 0
    (tmp_path / "kein-netz").write_text("")
    out = front("pruefen", **env)
    text = out.stdout + out.stderr
    assert "Eigene Automato-Instanzen (ADR 0061)" in text
    assert "Netz automato-front-instanzen fehlt" in text
    assert "kunde-a: https://kunde-a-doc.mgmt.example.ch → Instanz automato-kunde-a" in text
    assert "Instanz automato-alt läuft, aber kein Kunde nutzt sie – bleibt" in text
    assert "http://automato-kunde-a-web-ui:5173/api/health" in text
    # ohne Instanzen: kein Abschnitt (nichts gefragt ausser den Anschlüssen)
    _da(tmp_path)
    _soll(front, [KUNDE_B])
    assert front("sync", **env).returncode == 0
    assert "Eigene Automato-Instanzen" not in front("pruefen", **env).stdout


def test_eigene_netze_ohne_das_netz_der_instanzen(front):  # noqa: F811
    """Der Anschluss einer Instanz ist nicht Automato selbst: das Netz der Instanzen zählt nicht zu
    den Absendern, die ohne Tor an die NetBox-API dürfen (1.10.0, ADR 0053)."""
    out = front("kunde", "kunde-x", "Kunde X", FRONT_DOCKER_NETZE="1")
    assert out.returncode == 0, out.stdout + out.stderr
    log = front.log.read_text()
    assert '{{if ne $n "automato-front-instanzen"}}{{$v.NetworkID}} {{end}}' in log
