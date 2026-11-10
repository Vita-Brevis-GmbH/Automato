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
  *"exec -T orchestrator getent hosts "*)
    [ -z "${FRONT_GETENT_ORCH:-}" ] || echo "$FRONT_GETENT_ORCH  ${!#}"; exit 0 ;;
  *"exec -T collector getent hosts "*)
    [ -z "${FRONT_GETENT_COLL:-}" ] || echo "$FRONT_GETENT_COLL  ${!#}"; exit 0 ;;
  *"exec -T proxy wget "*) [ "${FRONT_WGET_FAIL:-}" != 1 ] || exit 1; exit 0 ;;
  "compose -p automato-tor "*"ps --status running --services"*)
    f=""; vor=""; for a in "$@"; do [ "$vor" = -f ] && f=$a; vor=$a; done
    sed -n 's/^  \(tor-[^:]*\):$/\1/p' "$f" ;;
  *"ps --status running --services"*) echo proxy ;;
esac
exit 0
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
    curl = _stub(bin_dir / "curl", 'echo "${FRONT_CURL_CODE:-200}"\n')
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
    assert "AUTOMATO_FRONT_MAGISTER leer" in out.stdout
    assert "Valid redirect URI https://kunde-a-doc.mgmt.example.ch/*" in out.stdout

    front.env_file.write_text(ENV + "AUTOMATO_FRONT_MAGISTER=https://magister.int.example.ch\n")
    assert front("erzeugen").returncode == 0
    site = (front.gen / "sites" / "kunde-a.caddy").read_text()
    assert "kunde-a-um.mgmt.example.ch {" in site
    assert "reverse_proxy https://magister.int.example.ch" in site
    seite = (front.gen / "www" / "kunde-a" / "index.html").read_text()
    assert "Benutzerverwaltung" in seite and "Dokumentation" in seite


def test_eigene_ca_fuer_den_netbox_host(front):
    front.env_file.write_text(ENV + "AUTOMATO_FRONT_NETBOX_CA=intern-ca.crt\n")
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
    out = front("pruefen", FRONT_CURL_CODE="403")
    assert "mgmt-int.crt deckt *.mgmt.int.example.ch nicht ab" in out.stderr
    assert "NetBox-Host lehnt diese VM ab (403)" in out.stderr


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
