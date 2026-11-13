"""deploy/front/front.sh (1.2.0, ADR 0044 Nachtrag): Front-Proxy der Automato-VM – Startseiten
je Kunde, Portal, Weiterleitung je Werkzeug. Mit Ersatz für docker und curl; geprüft wird, was das
Skript schreibt (Caddy-Einträge, Seiten maskiert, Freigaben nie leer), dass Werkzeuge nur mit
Freischaltung erscheinen und dass Einstellungen nichts in den Caddyfile schleusen können."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "front" / "front.sh"

pytestmark = pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("openssl"), reason="bash/openssl fehlen"
)

# wie echtes Compose: der Proxy hängt an der Web-UI (Profil core) – nur mit beiden Profilen gültig
DOCKER = r"""
echo "docker $*" >> "$FRONT_LOG"
case "$*" in
  "compose --profile core --profile proxy "*) ;;
  "compose --profile core --profile collectors "*) ;;
  "compose -p automato-tor "*) ;;
  compose*) echo 'service "proxy" depends on undefined service "web-ui"' >&2; exit 1 ;;
esac
case "$*" in
  *"exec -T orchestrator python "*)
    [ "${FRONT_AUTOMATO_NB:-ok}" = leer ] || echo "${FRONT_AUTOMATO_NB:-ok}"; exit 0 ;;
  # Docker-Netze von Automato (1.10.0): nur mit FRONT_DOCKER_NETZE=1
  *" ps -q orchestrator"|*" ps -q collector"|*" ps -q proxy")
    [ "${FRONT_DOCKER_NETZE:-}" = 1 ] && echo "id-${!#}"; exit 0 ;;
  "inspect -f "*" id-"*)
    case "${!#}" in id-proxy) echo "netz-a netz-tor" ;; *) echo "netz-a" ;; esac; exit 0 ;;
  "network inspect -f "*)
    case "${!#}" in
      netz-a) echo "172.18.0.0/16 " ;;
      netz-tor) echo "172.19.0.0/16 fd00::/64 " ;;
    esac
    exit 0 ;;
  *"exec -T orchestrator getent hosts "*)
    [ -z "${FRONT_GETENT_ORCH:-}" ] || echo "$FRONT_GETENT_ORCH  ${!#}"; exit 0 ;;
  *"exec -T collector getent hosts "*)
    [ -z "${FRONT_GETENT_COLL:-}" ] || echo "$FRONT_GETENT_COLL  ${!#}"; exit 0 ;;
  *"exec -T proxy wget "*)
    [ -z "${FRONT_WGET_OUT:-}" ] || { echo "$FRONT_WGET_OUT" >&2; exit 1; }
    [ "${FRONT_WGET_FAIL:-}" != 1 ] || exit 1; exit 0 ;;
  "compose -p automato-tor "*"ps --status running --services"*)
    f=""; vor=""; for a in "$@"; do [ "$vor" = -f ] && f=$a; vor=$a; done
    sed -n 's/^  \(tor-[^:]*\):$/\1/p' "$f" ;;
  *"ps --status running --services"*) echo proxy ;;
esac
exit 0
"""

# DNS der VM (1.9.2): FRONT_DNS = Zeilen "<name> <ip>", "*.<domain> <ip>" für einen Wildcard
GETENT = r"""
[ "$1" = ahostsv4 ] || exit 2
while read -r name ip; do
  [ "$name" = "$2" ] && { echo "$ip       STREAM $2"; exit 0; }
done <<< "${FRONT_DNS:-}"
while read -r name ip; do
  case "$name" in
    "*."*) [[ "$2" == *".${name#\*.}" ]] && { echo "$ip       STREAM $2"; exit 0; } ;;
  esac
done <<< "${FRONT_DNS:-}"
exit 2
"""

IPTABLES = r"""
printf '%s\n' '-N DOCKER-USER' '-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN'
[ "${FRONT_DOCKER_USER_LEER:-}" = 1 ] ||
  printf '%s\n' '-A DOCKER-USER -i ens33 -m conntrack --ctstate NEW -j DROP'
"""

ENV = """AUTOMATO_FRONT_DOMAIN=mgmt.example.ch
AUTOMATO_FRONT_DOMAIN_INT=mgmt.int.example.ch
AUTOMATO_FRONT_WAF=10.0.40.1/32
AUTOMATO_FRONT_MGMT=10.0.10.0/24
AUTOMATO_FRONT_NETBOX=10.0.30.20
"""


def _stub(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)
    return path


def _zertifikat(pki: Path, name: str, sans: str) -> None:
    subprocess.run(  # noqa: S603 - Testzertifikat
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "400",  # noqa: S607
         "-subj", "/CN=test", "-addext", f"subjectAltName={sans}",
         "-keyout", str(pki / f"{name}.key"), "-out", str(pki / f"{name}.crt")],
        check=True, capture_output=True,
    )  # fmt: skip


@pytest.fixture
def front(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pki = tmp_path / "pki"
    pki.mkdir()
    env_file = tmp_path / "env"
    env_file.write_text(ENV)
    curl = _stub(  # 1.10.0: die übrigen Namen (Name, den es nicht gibt) mit eigenem Code
        bin_dir / "curl",
        'case "$*" in\n'
        '  *gibt-es-nicht-front-pruefen*) echo "${FRONT_CURL_UEBRIGE:-200}"; exit 0 ;;\n'
        "esac\n"
        # 1.10.0: NetBox je Kunde über /api/status/ mit ungültigem Token – NetBox antwortet JSON-403
        'case "$*" in\n'
        "  */api/status/*)\n"
        '    o=""; vor=""; for a in "$@"; do [ "$vor" = -o ] && o=$a; vor=$a; done\n'
        '    b=${FRONT_CURL_BODY-json}; [ "$b" != json ] || b=\'{"detail": "Invalid token"}\'\n'
        '    [ -z "$o" ] || printf \'%s\' "$b" > "$o"\n'
        '    echo "${FRONT_CURL_NB:-403}"; exit 0 ;;\n'
        "esac\n"
        'echo "${FRONT_CURL_CODE:-200}"\n',
    )
    env = {
        **os.environ,
        "AUTOMATO_ENV_FILE": str(env_file),
        "FRONT_DIR": str(tmp_path / "front"),
        "FRONT_PKI": str(pki),
        "FRONT_SKIP_ROOT": "1",
        "FRONT_LOG": str(tmp_path / "aufrufe.log"),
        "DOCKER": str(_stub(bin_dir / "docker", DOCKER)),
        "IPTABLES": str(_stub(bin_dir / "iptables", IPTABLES)),
        "CURL": str(curl),
        "GETENT": str(_stub(bin_dir / "getent", GETENT)),
        "FRONT_LOKALE_IPS": "10.0.20.5 172.17.0.1",
        # Host-Agent (1.5.0: pruefen fragt systemd) – Vorgabe: läuft
        "SYSTEMCTL": str(_stub(bin_dir / "systemctl", 'exit "${FRONT_AGENT_RC:-0}"\n')),
    }
    for k in [k for k in env if k.startswith("AUTOMATO_FRONT_")]:
        del env[k]  # nur die Test-.env zählt

    def run(*args: str, input: str | None = None, **extra: str) -> subprocess.CompletedProcess:  # noqa: A002
        return subprocess.run(  # noqa: S603 - Testskript des Repos
            [shutil.which("bash") or "bash", str(SCRIPT), *args], input=input,
            capture_output=True, text=True, env={**env, **extra}, timeout=60, check=False,
        )  # fmt: skip

    run.gen = tmp_path / "front" / "generated"  # type: ignore[attr-defined]
    run.dir = tmp_path / "front"  # type: ignore[attr-defined]
    run.env_file = env_file  # type: ignore[attr-defined]
    run.pki = pki  # type: ignore[attr-defined]
    run.log = tmp_path / "aufrufe.log"  # type: ignore[attr-defined]
    return run


def test_kunde_erzeugt_startseite_weiterleitung_und_portal(front):
    out = front("kunde", "kunde-a", "Kunde A & <Co>")
    assert out.returncode == 0, out.stdout + out.stderr
    g = front.gen
    snippets = (g / "snippets.caddy").read_text()
    # Kunden-Adressen: WAF + Management-Netz; intern nur das Management-Netz
    assert "@front_fremd not remote_ip 10.0.40.1/32 10.0.10.0/24" in snippets
    assert "@front_fremd not remote_ip 10.0.10.0/24\n" in snippets
    # interne Adressen nie über die WAF, auch wenn das Management-Netz sie einschliesst
    assert "@front_waf remote_ip 10.0.40.1/32\n}" in snippets
    assert 'respond @front_waf "Zugriff nur aus dem Management-Netz" 403' in snippets
    # 1.4.0: die Sperre läuft zuerst (in route), Kopfzeilen der Anmeldung nie vom Absender
    assert "(front-kunden-sperre) {\n\trespond @front_fremd" in snippets
    assert "request_header -X-Automato-User" in snippets
    assert "tls /etc/caddy/pki/mgmt.crt /etc/caddy/pki/mgmt.key" in snippets
    assert "Content-Security-Policy \"default-src 'none'" in snippets

    site = (g / "sites" / "kunde-a.caddy").read_text()
    assert "kunde-a.mgmt.example.ch {" in site and "root * /srv/front/kunde-a" in site
    assert "route {\n\t\timport front-kunden-sperre\n\t\troot * /srv/front/kunde-a" in site
    assert "front-tor" not in site and "front-gesperrt" not in site  # Tor aus: wie bis 1.3
    assert "kunde-a-nb.mgmt.example.ch {" in site
    assert "reverse_proxy https://10.0.30.20 {" in site
    assert "header_up Host {host}" in site and "tls_server_name {host}" in site
    assert "tls_trust_pool" not in site  # öffentliches Zertifikat: Systemvertrauen
    assert "-doc." not in site and "-um." not in site  # nur mit Freischaltung

    seite = (g / "www" / "kunde-a" / "index.html").read_text()
    assert "<title>Kunde A &amp; &lt;Co&gt;</title>" in seite
    assert 'href="https://kunde-a-nb.mgmt.example.ch"' in seite
    assert "Dokumentation" not in seite

    portal = (g / "www" / "_portal" / "index.html").read_text()
    assert 'href="https://kunde-a.mgmt.example.ch"' in portal
    intern = (g / "sites" / "_intern.caddy").read_text()
    assert "start.mgmt.int.example.ch {" in intern and "import front-intern" in intern
    # redir läuft sonst vor respond: in route erst die Sperre, dann die Weiterleitung
    assert ("route {\n\t\timport front-intern-sperre\n"
            "\t\tredir https://start.mgmt.int.example.ch{uri} 302") in intern  # fmt: skip
    unbekannt = (g / "sites" / "_unbekannt.caddy").read_text()
    assert "*.mgmt.example.ch {" in unbekannt and "*.mgmt.int.example.ch {" in unbekannt
    assert "exec -T proxy caddy reload" in front.log.read_text()
    assert "Proxy neu geladen" in out.stdout and "Proxy läuft nicht" not in out.stdout


def test_werkzeuge_nur_mit_freischaltung(front):
    out = front("kunde", "kunde-a", "Kunde A", "--doc", "--um")
    assert out.returncode == 0, out.stdout + out.stderr
    site = (front.gen / "sites" / "kunde-a.caddy").read_text()
    assert "kunde-a-doc.mgmt.example.ch {" in site and "respond @internal 404" in site
    assert "-um." not in site  # --um ohne Adresse von Magister bleibt aus
    assert "weder AUTOMATO_FRONT_MAGISTER noch _UEBRIGE" in out.stdout
    assert "Valid redirect URI https://kunde-a-doc.mgmt.example.ch/*" in out.stdout

    front.env_file.write_text(ENV + "AUTOMATO_FRONT_MAGISTER=https://magister.int.example.ch\n")
    assert front("erzeugen").returncode == 0
    site = (front.gen / "sites" / "kunde-a.caddy").read_text()
    assert "kunde-a-um.mgmt.example.ch {" in site
    um = site.split("kunde-a-um.mgmt.example.ch {")[1]
    # 1.12.0: Magister findet den Mandanten am Namen – Host und SNI des Aufrufs bleiben
    assert "reverse_proxy https://magister.int.example.ch {" in um
    assert "header_up Host {host}" in um and "tls_server_name {host}" in um
    seite = (front.gen / "www" / "kunde-a" / "index.html").read_text()
    assert "Magister" in seite and "https://kunde-a-um.mgmt.example.ch" in seite
    assert "Dokumentation" in seite

    # ohne AUTOMATO_FRONT_MAGISTER: wie die übrigen Namen (dort steht Magister), mit deren CA
    _zertifikat(front.pki, "magister-root", "DNS:magister.example.ch")
    (front.pki / "magister-root.crt").rename(front.pki / "magister-root.pem")
    front.env_file.write_text(
        ENV + "AUTOMATO_FRONT_UEBRIGE=10.0.20.10\nAUTOMATO_FRONT_UEBRIGE_CA=magister-root.pem\n"
    )
    assert front("erzeugen").returncode == 0
    um = (
        (front.gen / "sites" / "kunde-a.caddy").read_text().split("kunde-a-um.mgmt.example.ch {")[1]
    )
    assert "reverse_proxy https://10.0.20.10 {" in um and "header_up Host {host}" in um
    assert "tls_trust_pool file /etc/caddy/pki/magister-root.pem" in um


def test_eigene_ca_fuer_den_netbox_host(front):
    front.env_file.write_text(ENV + "AUTOMATO_FRONT_NETBOX_CA=intern-ca.crt\n")
    # 1.10.0: fehlt die Datei oder ist sie leer, ändert erzeugen nichts (Proxy lehnte sonst ab)
    fehlt = front("erzeugen")
    assert fehlt.returncode != 0 and "intern-ca.crt fehlt oder ist leer" in fehlt.stderr
    (front.pki / "intern-ca.crt").write_text("")
    assert "fehlt oder ist leer" in front("erzeugen").stderr
    (front.pki / "intern-ca.crt").write_text("kein Zertifikat\n")
    assert "ist kein Zertifikat im PEM-Format" in front("erzeugen").stderr
    _zertifikat(front.pki, "intern-ca", "DNS:ca.example.ch")
    assert front("kunde", "kunde-a", "Kunde A").returncode == 0
    site = (front.gen / "sites" / "kunde-a.caddy").read_text()
    assert "tls_trust_pool file /etc/caddy/pki/intern-ca.crt" in site


@pytest.mark.parametrize(
    "name",
    [
        "Kunde_A",
        "kundeA",
        "-a",
        "start",
        "konfig",
        "kunde-nb",
        "kunde-doc",
        "kunde-um",
        "kunde-cmdb",
    ],
)
def test_kurzname_wird_geprueft(front, name):
    out = front("kunde", name, "X")
    assert out.returncode != 0
    assert "ungültig" in out.stderr or "reserviert" in out.stderr


@pytest.mark.parametrize(
    "zeile",
    [
        "AUTOMATO_FRONT_MGMT=10.0.10.0/24 }\n",  # schlösse den Block im Caddyfile
        "AUTOMATO_FRONT_WAF=10.0.40.1;rm\n",
        "AUTOMATO_FRONT_NETBOX=nb host\n",
        "AUTOMATO_FRONT_MAGISTER=javascript:alert(1)\n",
        "AUTOMATO_FRONT_DOMAIN=mgmt.example.ch {\n",
    ],
)
def test_einstellungen_koennen_nichts_einschleusen(front, zeile):
    schluessel = zeile.split("=", 1)[0]
    rest = "".join(z + "\n" for z in ENV.splitlines() if not z.startswith(schluessel + "="))
    front.env_file.write_text(rest + zeile)
    out = front("erzeugen")
    assert out.returncode != 0 and "ungültig" in out.stderr
    assert not (front.gen / "snippets.caddy").exists()


def test_ohne_management_netz_kein_offener_proxy(front):
    front.env_file.write_text(ENV.replace("AUTOMATO_FRONT_MGMT=10.0.10.0/24\n", ""))
    out = front("erzeugen")
    assert out.returncode != 0 and "AUTOMATO_FRONT_MGMT fehlt" in out.stderr


def test_link_wird_maskiert(front):
    front("kunde", "kunde-a", "Kunde A")
    out = front(
        "link", "kunde-a", "<b>Lizenzen</b>", "https://lizenzen.example.ch", 'Geräte & "Verträge"'
    )
    assert out.returncode == 0, out.stdout + out.stderr
    seite = (front.gen / "www" / "kunde-a" / "index.html").read_text()
    assert "&lt;b&gt;Lizenzen&lt;/b&gt;" in seite and "<b>Lizenzen</b>" not in seite
    assert "Geräte &amp; &quot;Verträge&quot;" in seite
    boese = front("link", "kunde-a", "x", "javascript:alert(1)")
    assert boese.returncode != 0 and "https://" in boese.stderr


def test_entfernen_nur_mit_bestaetigung(front):
    front("kunde", "kunde-a", "Kunde A")
    front("kunde", "kunde-b", "Kunde B")
    ohne = front("entfernen", "kunde-a")
    assert ohne.returncode != 0 and "--ja" in ohne.stderr
    assert (front.gen / "sites" / "kunde-a.caddy").exists()
    assert front("entfernen", "kunde-a", "--ja").returncode == 0
    assert not (front.gen / "sites" / "kunde-a.caddy").exists()
    assert not (front.gen / "www" / "kunde-a").exists()
    portal = (front.gen / "www" / "_portal" / "index.html").read_text()
    assert "kunde-a.mgmt" not in portal and "kunde-b.mgmt" in portal
    liste = front("liste")
    assert "kunde-b" in liste.stdout and "kunde-a " not in liste.stdout


def test_pruefen_zertifikate_und_wege(front):
    front("kunde", "kunde-a", "Kunde A")
    fehlt = front("pruefen")
    assert fehlt.returncode == 1 and "Zertifikat fehlt" in fehlt.stderr

    _zertifikat(front.pki, "mgmt", "DNS:*.mgmt.example.ch")
    _zertifikat(front.pki, "mgmt-int", "DNS:mgmt.int.example.ch,DNS:*.mgmt.int.example.ch")
    out = front("pruefen")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "mgmt.crt gültig bis" in out.stdout and "mgmt-int.crt gültig bis" in out.stdout
    assert "kunde-a: Startseite antwortet" in out.stdout
    assert "kunde-a: NetBox vom Front-Proxy aus erreichbar" in out.stdout
    assert "Proxy läuft" in out.stdout and "DOCKER-USER sperrt" in out.stdout

    offen = front("pruefen", FRONT_DOCKER_USER_LEER="1")  # veröffentlichte Ports ohne Sperre
    assert "DOCKER-USER ohne Sperre" in offen.stdout + offen.stderr

    _zertifikat(front.pki, "mgmt-int", "DNS:mgmt.int.example.ch")  # ohne Wildcard
    out = front("pruefen", FRONT_CURL_BODY="Zugriff nur aus freigegebenen Netzen")
    assert "mgmt-int.crt deckt *.mgmt.int.example.ch nicht ab" in out.stderr
    assert "NetBox-Host lehnt diese VM ab (403)" in out.stderr

    # 1.10.0: Instanz fehlt auf dem NetBox-Host – Auffang 404 bzw. ältere Hosts mit leerer 200;
    # 1.11.0: die Meldung nur einmal (nicht noch einmal auf dem Weg aus dem Orchestrator)
    for code, body in [("404", "Keine NetBox für diesen Namen"), ("200", "")]:
        out = front("pruefen", FRONT_CURL_NB=code, FRONT_CURL_BODY=body, FRONT_AUTOMATO_NB="fehlt")
        assert "kunde-a: auf dem NetBox-Host gibt es keine NetBox für kunde-a" in out.stderr
        assert out.stderr.count("keine NetBox für kunde-a") == 1
        assert "netbox-host.sh neu kunde-a" in out.stderr
        assert "Weg Automato → NetBox nicht geprüft (erst die NetBox" in out.stdout
        assert "NetBox vom Front-Proxy aus erreichbar" not in out.stdout
    out = front("pruefen", FRONT_CURL_NB="502")
    assert "kunde-a: NetBox nicht erreichbar (HTTP 502" in out.stderr


def _soll(front, kunden: list[dict]) -> Path:
    state = front.gen.parent / "state"
    state.mkdir(parents=True, exist_ok=True)
    datei = state / "soll.json"
    datei.write_text(json.dumps({"version": 1, "kunden": kunden}), encoding="utf-8")
    return datei


def _status(front) -> dict:
    return json.loads((front.gen.parent / "state" / "status.json").read_text(encoding="utf-8"))


def test_sync_uebernimmt_kunden_aus_der_oberflaeche(front):
    front("kunde", "kunde-c", "Kunde C")  # von Hand angelegt - bleibt unberührt
    soll = _soll(front, [
        {"kurzname": "vb", "name": "Vita & <Co>", "doc": True, "um": False, "cmdb": False,
         "links": [{"title": "Handbuch", "url": "https://handbuch.example.ch", "text": "Doku"}]},
        {"kurzname": "kunde-b", "name": "Kunde B", "doc": False, "links": []},
    ])  # fmt: skip
    out = front("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    site = (front.gen / "sites" / "vb.caddy").read_text()
    assert "vb-doc.mgmt.example.ch {" in site
    seite = (front.gen / "www" / "vb" / "index.html").read_text()
    assert "Vita &amp; &lt;Co&gt;" in seite and 'href="https://handbuch.example.ch"' in seite
    status = _status(front)
    assert status["ok"] and set(status["kunden"]) >= {"vb", "kunde-b", "kunde-c"}
    assert status["soll_sha256"] == hashlib.sha256(soll.read_bytes()).hexdigest()

    # aus der Oberfläche gepflegt: nicht per Befehl änderbar
    nein = front("kunde", "vb", "Anders")
    assert nein.returncode != 0 and "Oberfläche" in nein.stderr
    # unverändert: nichts zu tun
    assert "unverändert" in front("sync").stdout

    # in der Oberfläche entfernt: weg; der von Hand angelegte bleibt
    _soll(front, [{"kurzname": "vb", "name": "Vita", "doc": True, "links": []}])
    assert front("sync").returncode == 0
    assert not (front.gen / "sites" / "kunde-b.caddy").exists()
    assert (front.gen / "sites" / "kunde-c.caddy").exists()


@pytest.mark.parametrize(
    ("kunde", "meldung"),
    [
        ({"kurzname": "start", "name": "X"}, "reserviert"),
        ({"kurzname": "a b", "name": "X"}, "ungültig"),
        ({"kurzname": "kunde-x", "name": "X",
          "links": [{"title": "T", "url": "javascript:alert(1)"}]}, "https://"),
    ],
)  # fmt: skip
def test_sync_prueft_den_soll_stand(front, kunde, meldung):
    _soll(front, [{"kurzname": "vb", "name": "Vita", "links": []}])
    assert front("sync").returncode == 0
    _soll(front, [kunde])
    out = front("sync")
    assert out.returncode != 0
    status = _status(front)
    assert status["ok"] is False and meldung in status["meldung"]
    assert (front.gen / "sites" / "vb.caddy").exists()  # alter Stand bleibt


def test_agent_richtet_systemd_ein(front, tmp_path):
    systemd = tmp_path / "systemd"
    log = tmp_path / "systemctl.log"
    stub = _stub(tmp_path / "bin" / "systemctl", f'echo "$*" >> "{log}"\n')
    out = front("agent", FRONT_SYSTEMD_DIR=str(systemd), SYSTEMCTL=str(stub))
    assert out.returncode == 0, out.stdout + out.stderr
    path = (systemd / "automato-front-sync.path").read_text()
    assert f"PathChanged={front.gen.parent / 'state' / 'soll.json'}" in path
    service = (systemd / "automato-front-sync.service").read_text()
    assert service.rstrip().endswith("front.sh sync")
    assert "OnUnitActiveSec=10min" in (systemd / "automato-front-sync.timer").read_text()
    assert "enable --now automato-front-sync.path automato-front-sync.timer" in log.read_text()


TOR_ENV = (
    ENV
    + """AUTOMATO_FRONT_TOR=an
AUTOMATO_OIDC_ISSUER=https://login.example.ch/realms/vb
AUTOMATO_OIDC_JWKS_URL=https://keycloak.int.example.ch/realms/vb/protocol/openid-connect/certs
AUTOMATO_FRONT_MAGISTER=https://magister.int.example.ch
"""
)


def test_tor_schuetzt_jede_adresse_und_sperrt_ohne_realm(front):
    """1.4.0 (ADR 0051): je Realm ein Tor, Kunden ohne Realm bzw. ohne Client-Secret gesperrt."""
    front.env_file.write_text(TOR_ENV)
    assert front("kunde", "vb", "Vita", "--doc", "--um", "--realm", "vb").returncode == 0
    out = front("kunde", "kunde-b", "Kunde B")
    assert "ohne --realm gesperrt" in out.stdout
    # ohne Client-Secret: gesperrt, die NetBox-API mit Token bleibt erreichbar
    site = (front.gen / "sites" / "vb.caddy").read_text()
    assert "import front-gesperrt" in site and "front-tor" not in site
    assert "handle @front_nb_token" in site
    out = front("tor", "vb", input="ein-client-secret-123\n")
    assert out.returncode == 0, out.stdout + out.stderr
    datei = front.dir / "tor" / "vb.env"
    assert datei.stat().st_mode & 0o777 == 0o600
    werte = datei.read_text()
    assert "OAUTH2_PROXY_CLIENT_SECRET=ein-client-secret-123" in werte
    assert re.search(r"^OAUTH2_PROXY_COOKIE_SECRET=[0-9a-f]{32}$", werte, re.M)
    assert "ein-client-secret-123" not in out.stdout + out.stderr

    site = (front.gen / "sites" / "vb.caddy").read_text()
    dienst = re.search(r"import front-tor (tor-vb-[0-9a-f]{8})", site).group(1)
    # Startseite, NetBox, Doku und Magister hinter dem Tor; Reihenfolge: Sperre, …, Tor, Ziel
    assert site.count(f"import front-tor {dienst}") == 4
    nb = site.split("vb-nb.mgmt.example.ch {")[1].split("\n}\n")[0]
    assert nb.index("front-kunden-sperre") < nb.index("handle @front_nb_token {") < nb.index(
        "redir /logout/ /oauth2/sign_out") < nb.index("import front-tor") < nb.index(
        "import front-identitaet vb")  # fmt: skip
    doc = site.split("vb-doc.mgmt.example.ch {")[1].split("\n}\n")[0]
    assert doc.index("respond @internal 404") < doc.index("handle @front_api") < doc.index(
        "import front-tor")  # fmt: skip
    assert "import front-identitaet vb" in site.split("vb-um.mgmt.example.ch {")[1]
    assert "Abmelden" in (front.gen / "www" / "vb" / "index.html").read_text()
    # 1.12.1: Abmelden über die Abmeldeseite von Keycloak, zurück auf die Startseite – sonst meldet
    # der nächste Aufruf still wieder an (Fund aus dem Dev-Test)
    abmelden = (
        "/oauth2/sign_out?rd=https%3A%2F%2Flogin.example.ch%2Frealms%2Fvb%2Fprotocol%2F"
        "openid-connect%2Flogout%3Fclient_id%3Dautomato-front%26post_logout_redirect_uri%3D"
        "https%253A%252F%252Fvb.mgmt.example.ch%252F"
    )
    assert f'href="{abmelden}"' in (front.gen / "www" / "vb" / "index.html").read_text()
    assert f"redir /logout/ {abmelden} 302" in nb
    gesperrt = (front.gen / "sites" / "kunde-b.caddy").read_text()
    assert gesperrt.count("import front-gesperrt") == 2 and "front-tor" not in gesperrt

    snippets = (front.gen / "snippets.caddy").read_text()
    assert '"{http.request.header.X-Auth-Request-Preferred-Username}@{args[0]}"' in snippets
    assert "copy_headers X-Auth-Request-User" in snippets

    # Portal nur für admin/operator des Realms von Automato, eigener Container
    intern = (front.gen / "sites" / "_intern.caddy").read_text()
    assert "import front-tor tor-intern" in intern
    compose = (front.gen / "tor" / "compose.yml").read_text()
    assert f"  {dienst}:" in compose and "  tor-intern:" in compose
    teil = compose.split("  tor-intern:")[1]
    assert '"--allowed-role=admin"' in teil and '"--allowed-role=operator"' in teil
    assert '"--allowed-role' not in compose.split("  tor-intern:")[0]
    assert '"--oidc-issuer-url=https://login.example.ch/realms/vb"' in compose
    assert ('"--redeem-url=https://keycloak.int.example.ch/realms/vb/protocol/openid-connect/token"'
            in compose)  # fmt: skip
    assert '"--code-challenge-method=S256"' in compose and '"--cookie-secure=true"' in compose
    assert f'env_file: ["{datei}"]' in compose and "ein-client-secret" not in compose
    assert "read_only: true" in compose and 'cap_drop: ["ALL"]' in compose
    assert "name: automato-front-tor\n    external: true" in compose
    assert '"--whitelist-domain=login.example.ch"' in compose  # Ziel nach dem Abmelden
    portal = (front.gen / "www" / "_portal" / "index.html").read_text()
    assert "post_logout_redirect_uri%3Dhttps%253A%252F%252Fstart.mgmt.int.example.ch%252F" in portal
    stand = front("tor")
    assert "Valid post logout redirect URIs:" in stand.stdout
    assert "https://vb.mgmt.example.ch/*" in stand.stdout
    assert "https://start.mgmt.int.example.ch/*" in stand.stdout
    log = front.log.read_text()
    assert "compose -p automato-tor -f" in log and "up -d --remove-orphans" in log


def test_tor_stand_und_pruefen(front):
    front.env_file.write_text(TOR_ENV)
    front("kunde", "vb", "Vita", "--realm", "vb")
    front("kunde", "kunde-a", "Kunde A", "--realm", "kunde-a")
    front("tor", "vb", input="ein-client-secret-123\n")
    out = front("tor")
    assert "Realm vb: Client-Secret hinterlegt" in out.stdout
    assert "Realm kunde-a: Client-Secret fehlt" in out.stdout
    assert "https://kunde-a-nb.mgmt.example.ch/oauth2/callback" in out.stdout
    assert "https://start.mgmt.int.example.ch/oauth2/callback" in out.stdout
    _zertifikat(front.pki, "mgmt", "DNS:*.mgmt.example.ch")
    _zertifikat(front.pki, "mgmt-int", "DNS:mgmt.int.example.ch,DNS:*.mgmt.int.example.ch")
    pr = front("pruefen", FRONT_CURL_CODE="302")
    assert "Realm kunde-a: Client-Secret fehlt" in pr.stderr
    assert "Anmelde-Tor läuft (2 Container)" in pr.stdout
    assert "leitet zur Anmeldung" in pr.stdout


@pytest.mark.parametrize("geheim", ["", "zu kurz", 'mit"anführung-1234'])
def test_tor_nimmt_nur_gueltige_client_secrets(front, geheim):
    front.env_file.write_text(TOR_ENV)
    out = front("tor", "vb", input=geheim + "\n")
    assert out.returncode != 0 and "nichts geändert" in out.stderr
    assert not (front.dir / "tor" / "vb.env").exists()


@pytest.mark.parametrize("realm", ["master", "a b", "-x", "x" * 65])
def test_realm_wird_geprueft(front, realm):
    front.env_file.write_text(TOR_ENV)
    out = front("kunde", "kunde-a", "Kunde A", "--realm", realm)
    assert out.returncode != 0 and "ungültig" in out.stderr


def test_tor_braucht_keycloak(front):
    front.env_file.write_text(ENV + "AUTOMATO_FRONT_TOR=an\n")
    out = front("erzeugen")
    assert out.returncode != 0 and "braucht Keycloak" in out.stderr


def test_tor_aus_haelt_container_an(front):
    front.env_file.write_text(TOR_ENV)
    front("kunde", "vb", "Vita", "--realm", "vb")
    front("tor", "vb", input="ein-client-secret-123\n")
    assert (front.gen / "tor" / "compose.yml").exists()
    front.env_file.write_text(ENV)  # Tor aus
    out = front("erzeugen")
    assert out.returncode == 0 and "nicht mehr benötigte Container angehalten" in out.stdout
    assert not (front.gen / "tor" / "compose.yml").exists()
    assert "down --remove-orphans" in front.log.read_text()
    site = (front.gen / "sites" / "vb.caddy").read_text()
    assert "front-tor" not in site and "front-gesperrt" not in site


def test_sync_uebernimmt_realm(front):
    front.env_file.write_text(TOR_ENV)
    _soll(front, [{"kurzname": "vb", "name": "Vita", "realm": "vb", "links": []},
                  {"kurzname": "kunde-b", "name": "B", "realm": "", "links": []}])  # fmt: skip
    assert front("sync").returncode == 0
    assert "REALM=vb" in (front.dir / "kunden" / "vb.conf").read_text()
    status = _status(front)
    assert status["tor"]["an"] and status["tor"]["realms_ohne_secret"] == ["vb"]
    assert set(status["tor"]["gesperrt"]) == {"vb", "kunde-b"}
    assert status["tor"]["haupt_realm"] == "vb"
    _soll(front, [{"kurzname": "vb", "name": "Vita", "realm": "master", "links": []}])
    out = front("sync")
    assert out.returncode != 0 and "ungültig" in _status(front)["meldung"]


# --- Zugriff je Kunde (1.5.0) --------------------------------------------------------------------


def test_zugriff_je_kunde_sperrt_alle_adressen_wie_unbekannt(front):
    assert front("kunde", "vb", "VB", "--doc", "--zugriff", "intern").returncode == 0
    nets = "203.0.113.0/24, 2001:db8::/48"
    assert front("kunde", "kunde-a", "A", "--zugriff", nets).returncode == 0
    assert front("kunde", "kunde-b", "B").returncode == 0

    vb = (front.gen / "sites" / "vb.caddy").read_text()
    # jede Adresse des Kunden: intern = Management-Netz, nie über die WAF
    for adresse in ("vb", "vb-nb", "vb-doc"):
        block = vb.split(f"{adresse}.mgmt.example.ch {{", 1)[1]
        assert block.startswith(
            "\n\timport front-kunden\n\t@front_kunde_fremd not remote_ip 10.0.10.0/24\n"
            "\t@front_kunde_waf remote_ip 10.0.40.1/32\n"
        )
    sperre = (
        "\t\timport front-kunden-sperre\n"
        '\t\trespond @front_kunde_fremd "Unbekannte Adresse" 404\n'
        '\t\trespond @front_kunde_waf "Unbekannte Adresse" 404\n'
    )
    assert vb.count(sperre) == 3
    # auch die NetBox-API mit Token erst nach der Sperre
    nb = vb.split("vb-nb.mgmt.example.ch {", 1)[1]
    assert nb.index("respond @front_kunde_fremd") < nb.index("handle @front_nb_token {")

    a = (front.gen / "sites" / "kunde-a.caddy").read_text()
    # Netze nach der Adresse des Benutzers (über die WAF aus X-Forwarded-For)
    assert "@front_kunde_fremd not client_ip 203.0.113.0/24 2001:db8::/48\n" in a
    assert "@front_kunde_waf" not in a
    assert "front_kunde" not in (front.gen / "sites" / "kunde-b.caddy").read_text()

    liste = front("liste").stdout
    assert re.search(r"^vb\s.*nur intern\s+Hand", liste, re.M)
    assert re.search(r"^kunde-b\s.*überall\s+Hand", liste, re.M)


@pytest.mark.parametrize(
    "zugriff",
    ["dead", "10.0.0.0/8; respond 200", "10.0.0.0/8 {", "vpn", "300.1.2"],
)
def test_zugriff_nimmt_nur_netze(front, zugriff):
    out = front("kunde", "kunde-a", "A", "--zugriff", zugriff)
    assert out.returncode != 0 and "ungültig" in out.stderr
    assert not (front.dir / "kunden" / "kunde-a.conf").exists()


def test_sync_uebernimmt_zugriff(front):
    _soll(front, [
        {"kurzname": "vb", "name": "VB", "realm": "vb", "zugriff": "intern", "netze": [],
         "links": []},
        {"kurzname": "kunde-a", "name": "A", "zugriff": "netze", "netze": ["203.0.113.0/24"],
         "links": []},
        {"kurzname": "kunde-c", "name": "C", "links": []},  # Oberfläche vor 1.5.0: überall
    ])  # fmt: skip
    assert front("sync").returncode == 0
    conf = (front.dir / "kunden" / "vb.conf").read_text()
    assert "ZUGRIFF=intern" in conf and "QUELLE=oberflaeche" in conf
    assert "not client_ip 203.0.113.0/24" in (front.gen / "sites" / "kunde-a.caddy").read_text()
    assert "ZUGRIFF=ueberall" in (front.dir / "kunden" / "kunde-c.conf").read_text()
    liste = front("liste").stdout
    assert re.search(r"^vb\s.*nur intern\s+Portal", liste, re.M)

    _soll(front, [{"kurzname": "vb", "name": "VB", "zugriff": "netze", "netze": ["10.0.0.0/8 {"],
                   "links": []}])  # fmt: skip
    out = front("sync")
    assert out.returncode != 0 and "ungültig" in _status(front)["meldung"]
    assert "ZUGRIFF=intern" in (front.dir / "kunden" / "vb.conf").read_text()  # alter Stand bleibt


def test_pruefen_host_agent_namen_und_keycloak(front):
    _zertifikat(front.pki, "mgmt", "DNS:*.mgmt.example.ch")
    _zertifikat(front.pki, "mgmt-int", "DNS:mgmt.int.example.ch,DNS:*.mgmt.int.example.ch")
    front.env_file.write_text(TOR_ENV + 'AUTOMATO_FRONT_KUNDEN_ALLOW="0.0.0.0/0 ::/0"\n')
    front("kunde", "vb", "VB", "--realm", "vb", "--zugriff", "intern")
    front("tor", "vb", input="ein-client-secret-123\n")
    out = front("pruefen", FRONT_CURL_CODE="404", FRONT_AGENT_RC="3", FRONT_WGET_FAIL="1",
                FRONT_GETENT_ORCH="10.0.30.20", FRONT_GETENT_COLL="203.0.113.9")  # fmt: skip
    text = out.stdout + out.stderr
    # FortiGate-VIP: Kunden-Adressen aus jedem Netz, dann muss die Oberfläche intern bleiben
    assert "AUTOMATO_UI_ALLOW leer" in text
    assert "Änderungen im Kunden-Portal kommen nicht an" in text
    assert "von Hand angelegt (nicht im Kunden-Portal): vb" in text
    kc = "Keycloak https://keycloak.int.example.ch (Realm vb) vom Proxy aus nicht erreichbar"
    assert kc in text
    assert "Orchestrator → 10.0.30.20, zentraler Worker (collector) → 203.0.113.9" in text
    gesperrt = "vb: Startseite antwortet (HTTP 404 – diese VM ausserhalb des Zugriffs: nur intern)"
    assert gesperrt in text

    front.env_file.write_text(
        TOR_ENV + 'AUTOMATO_FRONT_KUNDEN_ALLOW="0.0.0.0/0 ::/0"\nAUTOMATO_UI_ALLOW=10.0.10.0/24\n'
    )
    gut = front("pruefen", FRONT_GETENT_ORCH="10.0.30.20",
                FRONT_GETENT_COLL="10.0.30.20")  # fmt: skip
    text = gut.stdout + gut.stderr
    assert gut.returncode == 0, text
    assert "AUTOMATO_UI_ALLOW leer" not in text and "zentraler Worker" not in text
    assert "Keycloak für Realm vb vom Proxy aus erreichbar" in text
    assert "läuft (automato-front-sync.path)" in text


DEV_ENV = """AUTOMATO_FRONT_DOMAIN=mgmt.example.ch
AUTOMATO_FRONT_DOMAIN_INT=mgmt.int.example.ch
AUTOMATO_FRONT_MGMT=10.0.10.0/24
AUTOMATO_FRONT_NETBOX=10.0.20.10:8444
AUTOMATO_FRONT_MAGISTER=https://magister.int.example.ch
AUTOMATO_UI_HOST=automato.int.example.ch
AUTOMATO_UI_TLS="/etc/caddy/pki/ui.crt /etc/caddy/pki/ui.key"
"""


def test_pruefen_adressen_und_ziele(front):
    """1.9.2: jede Adresse im DNS auf diese VM, im Proxy und im Zertifikat – und ein Wildcard, der
    fremde Namen (z. B. Kundenseiten von Magister in derselben Domain) hierher holt, fällt auf."""
    front.env_file.write_text(DEV_ENV)
    _zertifikat(front.pki, "mgmt", "DNS:*.mgmt.example.ch")
    _zertifikat(front.pki, "mgmt-int", "DNS:mgmt.int.example.ch,DNS:*.mgmt.int.example.ch")
    _zertifikat(front.pki, "ui", "DNS:automato.int.example.ch")
    assert front("kunde", "kunde-a", "Kunde A", "--doc", "--um").returncode == 0

    # Wildcard der geteilten Domain zeigt auf diese VM (der Fehler aus dem Dev-Aufbau)
    dns = ("*.mgmt.example.ch 10.0.20.5\nautomato.int.example.ch 10.0.20.5\n"
           "start.mgmt.int.example.ch 10.0.20.5\n")  # fmt: skip
    out = front("pruefen", FRONT_DNS=dns)
    text = out.stdout + out.stderr
    assert out.returncode == 0, text
    assert "diese VM: 10.0.20.5 172.17.0.1" in text
    for name, zweck in [("automato.int.example.ch", "Oberfläche von Automato"),
                        ("start.mgmt.int.example.ch", "Portal"),
                        ("kunde-a.mgmt.example.ch", "kunde-a: Startseite"),
                        ("kunde-a-nb.mgmt.example.ch", "kunde-a: NetBox"),
                        ("kunde-a-doc.mgmt.example.ch", "kunde-a: Automato"),
                        ("kunde-a-um.mgmt.example.ch", "kunde-a: Magister")]:  # fmt: skip
        assert f"OK      {name} → 10.0.20.5 ({zweck})" in text
    assert "mgmt.int.example.ch (Verwaltung, leitet aufs Portal): nicht im DNS" in text
    assert "*.mgmt.example.ch zeigt auf diese VM: jeder andere Name unter mgmt.example.ch" in text
    assert "Kundenseiten von Magister" in text
    assert "kein Wildcard *.mgmt.int.example.ch im DNS" in text
    assert "Automato (Oberfläche, -doc) erreichbar (http://web-ui:5173/api/health)" in text
    assert "Magister (-um) erreichbar (https://magister.int.example.ch/)" in text
    assert "NetBox-Host 10.0.20.10:8444: je Kunde unter „Kunden“" in text

    # richtig: Wildcard bei der anderen Plattform, eigene Einträge – einer fehlt
    dns = ("*.mgmt.example.ch 10.0.20.10\nkunde-a.mgmt.example.ch 10.0.20.5\n"
           "kunde-a-nb.mgmt.example.ch 10.0.20.5\n")  # fmt: skip
    out = front("adressen", FRONT_DNS=dns)
    text = out.stdout + out.stderr
    assert out.returncode == 0, text
    assert "OK      kunde-a-nb.mgmt.example.ch → 10.0.20.5" in text
    assert "kunde-a-doc.mgmt.example.ch → 10.0.20.10, nicht diese VM – kunde-a: Automato" in text
    assert "*.mgmt.example.ch → 10.0.20.10 (nicht diese VM)" in text
    assert "zeigt auf diese VM" not in text and "Ziele" not in text  # nur die Adressen

    # mit WAF zeigen die Namen auf die WAF bzw. VIP: kein Fehler, nur Hinweis
    front.env_file.write_text(DEV_ENV + "AUTOMATO_FRONT_WAF=10.0.40.1/32\n")
    out = front("adressen", FRONT_DNS="*.mgmt.example.ch 203.0.113.7\n")
    waf = "kunde-a.mgmt.example.ch → 203.0.113.7 (kunde-a: Startseite; über WAF bzw. VIP)"
    assert waf in out.stdout
    front.env_file.write_text(DEV_ENV)

    # Zertifikat der Oberfläche passt nicht, ein Kunde fehlt im erzeugten Proxy
    _zertifikat(front.pki, "ui", "DNS:anderer.int.example.ch")
    (front.gen / "sites" / "kunde-a.caddy").unlink()
    out = front("adressen", FRONT_DNS="*.mgmt.example.ch 10.0.20.5\n")
    assert out.returncode == 1
    ui = "automato.int.example.ch (Oberfläche von Automato): Zertifikat ui.crt deckt"
    assert ui in out.stderr
    assert "kunde-a.mgmt.example.ch (kunde-a: Startseite): fehlt im Front-Proxy" in out.stderr

    # Ziele: Serverfehler, kein Weg, docker exec geht nicht
    front("erzeugen")
    for wget, erwartet in [
        ("wget: server returned error: HTTP/1.1 502 Bad Gateway", "antwortet mit HTTP 502"),
        ("wget: bad address 'magister.int.example.ch'", "Magister (-um) nicht erreichbar"),
        ("OCI runtime exec failed: exec failed: unable to start container process: procReady "
         "not received", "im Proxy-Container lässt sich nichts starten"),
    ]:  # fmt: skip
        out = front("pruefen", FRONT_DNS=dns, FRONT_WGET_OUT=wget)
        assert erwartet in out.stdout + out.stderr, wget
    verboten = "wget: server returned error: HTTP/1.1 403"
    out = front("pruefen", FRONT_DNS=dns, FRONT_WGET_OUT=verboten)
    assert "Magister (-um) erreichbar (https://magister.int.example.ch/, HTTP 403)" in out.stdout


def test_uebrige_namen_an_magister(front):
    """1.10.0 (ADR 0053): übrige Namen der Kunden-Domain gehen an die Plattform dahinter (Magister)
    statt „Unbekannte Adresse“ – Name und SNI bleiben, die Sperre nach Absender gilt."""
    uebrige = "AUTOMATO_FRONT_UEBRIGE=10.0.20.10\nAUTOMATO_FRONT_UEBRIGE_CA=magister-root.pem\n"
    front.env_file.write_text(DEV_ENV + uebrige)
    _zertifikat(front.pki, "mgmt", "DNS:*.mgmt.example.ch")
    _zertifikat(front.pki, "mgmt-int", "DNS:mgmt.int.example.ch,DNS:*.mgmt.int.example.ch")
    _zertifikat(front.pki, "ui", "DNS:automato.int.example.ch")
    shutil.copy(front.pki / "mgmt.crt", front.pki / "magister-root.pem")
    assert front("kunde", "kunde-a", "Kunde A").returncode == 0

    rest = (front.gen / "sites" / "_unbekannt.caddy").read_text()
    kunden, intern = rest.split("\n*.mgmt.int.example.ch {")
    kopf = "*.mgmt.example.ch {\n\timport front-uebrige\n\troute {\n\t\timport front-kunden-sperre"
    assert kopf in kunden
    assert "reverse_proxy https://10.0.20.10 {\n\t\t\theader_up Host {host}" in kunden
    assert "tls_server_name {host}" in kunden
    assert "tls_trust_pool file /etc/caddy/pki/magister-root.pem" in kunden
    assert 'respond "Unbekannte Adresse"' not in kunden
    assert 'respond "Unbekannte Adresse"' in intern
    snippets = (front.gen / "snippets.caddy").read_text()
    uebrig = snippets.split("(front-uebrige) {")[1].split("\n}\n")[0]
    # Sperre wie für Kunden-Adressen, aber keine eigenen Kopfzeilen über die der Plattform
    assert re.search(r"@front_fremd not remote_ip +10\.0\.10\.0/24", uebrig)
    assert "header" not in uebrig

    dns = "*.mgmt.example.ch 10.0.20.5\n"  # Wildcard auf den Front-Proxy: jetzt gewollt
    (front.pki / "magister-root.pem").write_text("")  # wie nach einem abgebrochenen `cat >`
    out = front("pruefen", FRONT_DNS=dns, FRONT_CURL_UEBRIGE="404")
    text = out.stdout + out.stderr
    assert "Übrige Namen unter mgmt.example.ch an: 10.0.20.10 (CA magister-root.pem)" in text
    assert "*.mgmt.example.ch zeigt auf diese VM – übrige Namen gehen an 10.0.20.10" in text
    # Konsole und Connector der Plattform dahinter gehen nicht über den Front-Proxy
    for n in ("konsole", "connect"):
        assert f"{n}.mgmt.example.ch zeigt auf diese VM (Wildcard)" in text
        assert "eigener A-Eintrag auf 10.0.20.10" in text
    assert "Übrige Namen: CA fehlt oder ist leer (deploy/proxy/pki/magister-root.pem)" in text
    assert "„Unbekannte Adresse“" not in text

    shutil.copy(front.pki / "mgmt.crt", front.pki / "magister-root.pem")
    out = front("pruefen", FRONT_DNS=dns, FRONT_CURL_UEBRIGE="404")
    text = out.stdout + out.stderr
    assert out.returncode == 0, text
    assert "Übrige Namen: 10.0.20.10 erreichbar, Zertifikat vertraut (HTTP 404)" in text
    direkt = front("adressen", FRONT_DNS=dns + "konsole.mgmt.example.ch 10.0.20.10\n")
    assert (
        "konsole.mgmt.example.ch → 10.0.20.10 (direkt, nicht über den Front-Proxy)" in direkt.stdout
    )
    assert "connect.mgmt.example.ch zeigt auf diese VM (Wildcard)" in direkt.stdout
    aufrufe = front.log.read_text() if front.log.exists() else ""
    assert "exec -T proxy wget" in aufrufe
    out = front("pruefen", FRONT_DNS=dns, FRONT_CURL_UEBRIGE="000")
    assert "Übrige Namen: 10.0.20.10 nicht erreichbar bzw. Zertifikat nicht vertraut" in out.stderr
    # Wildcard bei Magister: übrige Namen kommen gar nicht über den Front-Proxy
    out = front("adressen", FRONT_DNS="*.mgmt.example.ch 10.0.20.10\n")
    assert "AUTOMATO_FRONT_UEBRIGE ungenutzt" in out.stdout

    front.env_file.write_text(DEV_ENV + "AUTOMATO_FRONT_UEBRIGE=10.0.20.10 {\n")
    assert "AUTOMATO_FRONT_UEBRIGE ungültig" in front("erzeugen").stderr


def test_topologie_fuer_das_inventar(front):
    """1.10.0 (ADR 0053): erzeugen schreibt Adressen und Ziele für den Orchestrator (Inventar fürs
    Cockpit) – nur mit eingerichtetem Host-Agenten, nur Namen und Adressen."""
    front.env_file.write_text(DEV_ENV + "AUTOMATO_FRONT_UEBRIGE=10.0.20.10\n")
    assert front("kunde", "kunde-a", "Kunde A", "--doc").returncode == 0
    topo = front.dir / "state" / "topologie.json"
    assert not topo.exists()  # ohne Host-Agent kein Ordner, keine Datei

    (front.dir / "state").mkdir()
    out = front("kunde", "kunde-b", "Kunde B", FRONT_HOSTNAME="automato-vm.int.example.ch")
    assert out.returncode == 0, out.stdout + out.stderr
    data = json.loads(topo.read_text(encoding="utf-8"))
    assert oct(topo.stat().st_mode & 0o777) == "0o644"
    assert data["vm"] == {
        "name": "automato-vm.int.example.ch",
        "adressen": ["10.0.20.5", "172.17.0.1"],
    }
    assert data["oberflaeche"] == "https://automato.int.example.ch"
    assert data["portal"] == "https://start.mgmt.int.example.ch"
    assert (data["netbox_host"], data["uebrige"], data["tor"]) == (
        "10.0.20.10:8444",
        "10.0.20.10",
        False,
    )
    assert data["magister"] == "https://magister.int.example.ch" and data["cmdb"] is None
    kunden = {k["kurzname"]: k for k in data["kunden"]}
    assert kunden["kunde-a"] == {
        "kurzname": "kunde-a", "name": "Kunde A", "startseite": "https://kunde-a.mgmt.example.ch",
        "netbox": "https://kunde-a-nb.mgmt.example.ch", "automato": "https://kunde-a-doc.mgmt.example.ch",
        "magister": None, "cmdb": None,
        # 1.11.0 (ADR 0054): Herkunft und Auswahl fürs Cockpit
        "quelle": "befehl", "werkzeuge": ["doc", "nb"], "zugriff": "ueberall", "netze": [],
        "realm": None,
    }  # fmt: skip
    assert kunden["kunde-b"]["automato"] is None


def test_automato_selbst_an_seine_netbox(front):
    """1.10.0 (ADR 0053): Orchestrator und Worker kommen über den Hairpin mit einer Adresse ihrer
    Docker-Netze an den Front-Proxy – nur die API mit Token geht an die NetBox, vor der Sperre und
    ohne Kopfzeilen der Anmeldung; pruefen geht den Weg aus dem Orchestrator bis zur NetBox."""
    front.env_file.write_text(DEV_ENV)
    _zertifikat(front.pki, "mgmt", "DNS:*.mgmt.example.ch")
    _zertifikat(front.pki, "mgmt-int", "DNS:mgmt.int.example.ch,DNS:*.mgmt.int.example.ch")
    _zertifikat(front.pki, "ui", "DNS:automato.int.example.ch")
    out = front("kunde", "kunde-a", "Kunde A", "--zugriff", "intern", FRONT_DOCKER_NETZE="1")
    assert out.returncode == 0, out.stdout + out.stderr
    site = (front.gen / "sites" / "kunde-a.caddy").read_text()
    nb = site.split("kunde-a-nb.mgmt.example.ch {")[1]
    eigen = nb.split("@front_eigen {")[1].split("\n\t}")[0]
    assert "remote_ip 172.18.0.0/16 172.19.0.0/16 fd00::/64" in eigen
    assert "header_regexp Authorization ^(Token|Bearer)\\s" in eigen
    route = nb.split("\troute {\n")[1]
    vorn = route.split("\t\timport front-kunden-sperre")[0]
    assert vorn.startswith("\t\timport front-ohne-identitaet\n\t\thandle @front_eigen {")
    assert "reverse_proxy https://10.0.20.10:8444 {" in vorn and "tls_server_name {host}" in vorn
    assert "X-Automato-User" not in vorn
    # die Startseite und -doc bleiben wie bisher
    assert "@front_eigen" not in site.split("kunde-a-nb.mgmt.example.ch {")[0]

    ohne = front("erzeugen")  # Container laufen nicht: keine eigenen Netze, kein Eintrag
    assert ohne.returncode == 0
    assert "@front_eigen" not in (front.gen / "sites" / "kunde-a.caddy").read_text()

    for antwort, text in [
        ("ok", "kunde-a: Automato erreicht seine NetBox über den Front-Proxy"),
        ("tls", "vertraut dem Zertifikat von https://kunde-a-nb.mgmt.example.ch nicht – CA nach"),
        ("weg", "Automato erreicht https://kunde-a-nb.mgmt.example.ch nicht"),
        ("gesperrt", "Front-Proxy lässt Automato nicht zur NetBox – front.sh erzeugen"),
        ("fehlt", "keine NetBox für kunde-a – dort: netbox-host.sh neu kunde-a"),
        ("leer", "Weg Automato → NetBox nicht geprüft"),
    ]:  # fmt: skip
        out = front("pruefen", FRONT_AUTOMATO_NB=antwort)
        assert text in out.stdout + out.stderr, antwort
    log = front.log.read_text()
    assert "exec -T orchestrator python -c" in log and "https://kunde-a-nb.mgmt.example.ch" in log


def test_portal_unter_der_domain_der_kunden(front):
    """1.10.0 (Anwender 2026-11-13: Portal unter start.<domain>): interne Domain = Domain der
    Kunden – nur das Portal, keine Verwaltung auf der Domain selbst, nur ein Auffang-Eintrag."""
    geteilt = DEV_ENV.replace(
        "AUTOMATO_FRONT_DOMAIN_INT=mgmt.int.example.ch", "AUTOMATO_FRONT_DOMAIN_INT=mgmt.example.ch"
    )
    front.env_file.write_text(geteilt + "AUTOMATO_FRONT_UEBRIGE=10.0.20.10\n")
    _zertifikat(front.pki, "mgmt", "DNS:*.mgmt.example.ch")
    _zertifikat(front.pki, "mgmt-int", "DNS:*.mgmt.example.ch")
    _zertifikat(front.pki, "ui", "DNS:automato.int.example.ch")
    assert front("kunde", "kunde-a", "Kunde A").returncode == 0
    intern = (front.gen / "sites" / "_intern.caddy").read_text()
    assert "start.mgmt.example.ch {\n\timport front-intern" in intern
    assert "\nmgmt.example.ch {" not in intern and "Verwaltung" not in intern
    rest = (front.gen / "sites" / "_unbekannt.caddy").read_text()
    assert rest.count("*.mgmt.example.ch {") == 1 and "import front-uebrige" in rest

    out = front("pruefen", FRONT_DNS="*.mgmt.example.ch 10.0.20.5\n")
    text = out.stdout + out.stderr
    assert "Portal start.mgmt.example.ch (Domain der Kunden) nur aus: 10.0.10.0/24" in text
    assert "mgmt-int.crt gültig bis" in text and "deckt" not in text
    assert "OK      start.mgmt.example.ch → 10.0.20.5 (Portal)" in text
    assert "Verwaltung, leitet aufs Portal" not in text

    for name in ("start", "konsole", "connect"):
        assert f"Kurzname '{name}' ist reserviert" in front("kunde", name, "X").stderr


# --- NetBox als Werkzeug, Kunden aus dem Cockpit (1.11.0, ADR 0054) ------------------------------


def test_kunde_ohne_netbox(front):
    """NB=nein (--ohne-nb): keine -nb-Adresse, keine Kachel, keine Prüfung, kein Callback."""
    (front.dir / "state").mkdir(parents=True)
    out = front("kunde", "kunde-a", "Kunde A", "--doc", "--ohne-nb", "--realm", "kunde-a")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "NetBox: aus (--ohne-nb)" in out.stdout
    assert "NB=nein" in (front.dir / "kunden" / "kunde-a.conf").read_text()
    assert front("kunde", "kunde-b", "Kunde B", "--realm", "kunde-a").returncode == 0
    site = (front.gen / "sites" / "kunde-a.caddy").read_text()
    assert "kunde-a-nb." not in site and "kunde-a-doc.mgmt.example.ch {" in site
    seite = (front.gen / "www" / "kunde-a" / "index.html").read_text()
    assert "-nb." not in seite and "Dokumentation" in seite
    portal = (front.gen / "www" / "_portal" / "index.html").read_text()
    assert "<span>Doku</span>" in portal and "<span>NetBox</span>" in portal  # nur kunde-b
    assert "kunde-b-nb.mgmt.example.ch {" in (front.gen / "sites" / "kunde-b.caddy").read_text()

    liste = front("liste").stdout
    assert re.search(r"^kunde-a\s+Doku\s", liste, re.M)
    assert re.search(r"^kunde-b\s+NetBox\s", liste, re.M)
    tor = front("tor").stdout
    assert "https://kunde-a-doc.mgmt.example.ch/oauth2/callback" in tor
    assert "https://kunde-b-nb.mgmt.example.ch/oauth2/callback" in tor
    assert "kunde-a-nb" not in tor

    topo = json.loads((front.dir / "state" / "topologie.json").read_text(encoding="utf-8"))
    kunden = {k["kurzname"]: k for k in topo["kunden"]}
    assert kunden["kunde-a"]["netbox"] is None and kunden["kunde-a"]["werkzeuge"] == ["doc"]
    assert kunden["kunde-a"]["realm"] == "kunde-a" and kunden["kunde-b"]["werkzeuge"] == ["nb"]

    _zertifikat(front.pki, "mgmt", "DNS:*.mgmt.example.ch")
    _zertifikat(front.pki, "mgmt-int", "DNS:mgmt.int.example.ch,DNS:*.mgmt.int.example.ch")
    out = front("pruefen")
    text = out.stdout + out.stderr
    assert out.returncode == 0, text
    assert "kunde-a: ohne NetBox (NB=nein)" in text
    assert "kunde-a-nb" not in text and "kunde-b-nb.mgmt.example.ch" in text
    assert text.count("NetBox vom Front-Proxy aus erreichbar") == 1  # nur kunde-b


def test_alte_kunden_conf_ohne_nb_behaelt_netbox(front):
    """kunden/<k>.conf vor 1.11.0 ohne NB: NetBox wie bisher an."""
    kdir = front.dir / "kunden"
    kdir.mkdir(parents=True)
    (kdir / "kunde-a.conf").write_text(
        "NAME='Kunde A'\nDOC=nein\nUM=nein\nCMDB_AN=nein\nREALM=''\nZUGRIFF=ueberall\nNETZE=''\n"
    )
    assert front("erzeugen").returncode == 0
    assert "kunde-a-nb.mgmt.example.ch {" in (front.gen / "sites" / "kunde-a.caddy").read_text()
    seite = (front.gen / "www" / "kunde-a" / "index.html").read_text()
    assert 'href="https://kunde-a-nb.mgmt.example.ch"' in seite


def test_sync_uebernimmt_netbox_und_herkunft_cockpit(front):
    front("kunde", "kunde-c", "Kunde C")  # von Hand angelegt - bleibt unberührt
    _soll(front, [
        {"kurzname": "kunde-a", "name": "A", "doc": True, "nb": False, "quelle": "cockpit",
         "realm": "kunde-a", "zugriff": "intern", "netze": [], "links": []},
        {"kurzname": "kunde-b", "name": "B", "links": []},  # Oberfläche vor 1.11.0
        {"kurzname": "kunde-d", "name": "D", "nb": True, "quelle": "fremd", "links": []},
    ])  # fmt: skip
    out = front("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    conf = {
        k: (front.dir / "kunden" / f"{k}.conf").read_text()
        for k in ("kunde-a", "kunde-b", "kunde-d")
    }
    assert "NB=nein" in conf["kunde-a"] and "QUELLE=cockpit" in conf["kunde-a"]
    assert "NB=ja" in conf["kunde-b"] and "QUELLE=oberflaeche" in conf["kunde-b"]
    assert "QUELLE=oberflaeche" in conf["kunde-d"]  # nur cockpit oder oberflaeche
    assert "kunde-a-nb." not in (front.gen / "sites" / "kunde-a.caddy").read_text()
    assert "kunde-b-nb.mgmt.example.ch {" in (front.gen / "sites" / "kunde-b.caddy").read_text()

    # aus dem Cockpit: nicht per Befehl änderbar
    nein = front("kunde", "kunde-a", "Anders")
    assert nein.returncode != 0 and "im Cockpit" in nein.stderr
    liste = front("liste").stdout
    assert re.search(r"^kunde-a\s.*nur intern\s+Cockpit", liste, re.M)
    assert re.search(r"^kunde-b\s.*Portal", liste, re.M)
    assert re.search(r"^kunde-c\s.*Hand", liste, re.M)
    topo = json.loads((front.dir / "state" / "topologie.json").read_text(encoding="utf-8"))
    kunden = {k["kurzname"]: k for k in topo["kunden"]}
    assert (kunden["kunde-a"]["quelle"], kunden["kunde-a"]["zugriff"]) == ("cockpit", "intern")
    assert (kunden["kunde-b"]["quelle"], kunden["kunde-c"]["quelle"]) == ("oberflaeche", "befehl")

    # im Cockpit entfernt (nicht mehr im Soll-Stand): weg wie Kunden aus der Oberfläche
    _soll(front, [{"kurzname": "kunde-b", "name": "B", "links": []}])
    assert front("sync").returncode == 0
    assert not (front.dir / "kunden" / "kunde-a.conf").exists()
    assert not (front.gen / "sites" / "kunde-a.caddy").exists()
    assert (front.gen / "sites" / "kunde-c.caddy").exists()
