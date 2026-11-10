"""deploy/front/front.sh (1.2.0, ADR 0044 Nachtrag): Front-Proxy der Automato-VM – Startseiten
je Kunde, Portal, Weiterleitung je Werkzeug. Mit Ersatz für docker und curl; geprüft wird, was das
Skript schreibt (Caddy-Einträge, Seiten maskiert, Freigaben nie leer), dass Werkzeuge nur mit
Freischaltung erscheinen und dass Einstellungen nichts in den Caddyfile schleusen können."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "front" / "front.sh"

pytestmark = pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("openssl"), reason="bash/openssl fehlen"
)

DOCKER = r"""
echo "docker $*" >> "$FRONT_LOG"
case "$*" in
  *"ps --status running --services"*) echo proxy ;;
esac
exit 0
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
        "CURL": str(curl),
    }
    for k in [k for k in env if k.startswith("AUTOMATO_FRONT_")]:
        del env[k]  # nur die Test-.env zählt

    def run(*args: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603 - Testskript des Repos
            [shutil.which("bash") or "bash", str(SCRIPT), *args],
            capture_output=True, text=True, env={**env, **extra}, timeout=60, check=False,
        )  # fmt: skip

    run.gen = tmp_path / "front" / "generated"  # type: ignore[attr-defined]
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
    assert "@front_waf remote_ip 10.0.40.1/32\n\trespond @front_waf" in snippets
    assert "tls /etc/caddy/pki/mgmt.crt /etc/caddy/pki/mgmt.key" in snippets
    assert "Content-Security-Policy \"default-src 'none'" in snippets

    site = (g / "sites" / "kunde-a.caddy").read_text()
    assert "kunde-a.mgmt.example.ch {" in site and "root * /srv/front/kunde-a" in site
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
    # redir läuft vor respond: nur für erlaubte Adressen, nie über die WAF
    assert "remote_ip 10.0.10.0/24\n\t\tnot remote_ip 10.0.40.1/32\n" in intern
    assert "redir @front_portal https://start.mgmt.int.example.ch{uri} 302" in intern
    unbekannt = (g / "sites" / "_unbekannt.caddy").read_text()
    assert "*.mgmt.example.ch {" in unbekannt and "*.mgmt.int.example.ch {" in unbekannt
    assert "exec -T proxy caddy reload" in front.log.read_text()


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

    _zertifikat(front.pki, "mgmt-int", "DNS:mgmt.int.example.ch")  # ohne Wildcard
    out = front("pruefen", FRONT_CURL_CODE="403")
    assert "mgmt-int.crt deckt *.mgmt.int.example.ch nicht ab" in out.stderr
    assert "NetBox-Host lehnt diese VM ab (403)" in out.stderr
