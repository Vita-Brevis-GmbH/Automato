"""deploy/netbox-host/netbox-host.sh (0.121.0, ADR 0044): NetBox je Kunde hinter einem Proxy –
mit Ersatz für docker, git, age und curl. Geprüft wird, was das Skript schreibt (Secrets nur in
der .env der Instanz, eigene Namen im Netz des Proxys) und dass Löschen und Update nie ohne
Sicherung laufen. Seit 1.1.0 (ADR 0044 Nachtrag) ein Backend hinter dem Front-Proxy der
Automato-VM: NetBox unter <kunde>-nb, keine Startseiten hier, Proxy gehärtet, Konfiguration in
der Sicherung."""

import ast
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "netbox-host" / "netbox-host.sh"
OPENSSL = shutil.which("openssl") or "openssl"

pytestmark = pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("openssl"), reason="bash/openssl fehlen"
)

# 1.13.0: Tokens für Automato kommen nur über die Umgebung (-e NAME ohne Wert) – der Ersatz hält
# fest, was ankam, damit die Tests sie mit der Anfrage an Automato vergleichen können
DOCKER = r"""
echo "$PWD|$*" >> "$NBH_LOG"
[ -z "${NBH_LESE_TOKEN:-}" ] || echo "$NBH_LESE_TOKEN" >> "$NBH_LOG.lese"
[ -z "${SYNC_TOKEN:-}" ] || echo "$SYNC_TOKEN" >> "$NBH_LOG.sync"
case "$*" in *NBH_LESE_TOKEN*) [ -f "$NBH_LOG.netbox-aus" ] && { cat >/dev/null; exit 1; } ;; esac
if [ "$1" = network ]; then [ "$2" = inspect ] && exit 1; exit 0; fi
[ "$1" = compose ] || exit 0
shift
case "$*" in
  "config --services") printf 'netbox\nnetbox-worker\npostgres\nredis\nredis-cache\n' ;;
  ps*restarting*) [ -f "$NBH_LOG.restarting" ] && echo proxy; exit 0 ;;
  ps*--services*) echo proxy ;;
  ps*) echo "netbox running" ;;
  restart*) [ -f "$NBH_LOG.restart-aus" ] && exit 1; exit 0 ;;
  *pg_dump*) echo "SQL-DUMP" ;;
  *tar*-cf*) echo "MEDIEN" ;;
  *manage.py*|*psql*|*-xf*) cat >/dev/null ;;
  *) exit 0 ;;
esac
"""

GIT = r"""
echo "$PWD|git $*" >> "$NBH_LOG"
if [ "$1" = clone ]; then
  dir="${@: -1}"
  mkdir -p "$dir/env" "$dir/configuration"
  printf 'services:\n  netbox:\n    image: x\n' > "$dir/docker-compose.yml"
  printf 'DB_PASSWORD=J5brHrAXFLQSif0K\n' > "$dir/env/netbox.env"
fi
exit 0
"""

AGE = r"""
if [ "$1" = -r ]; then out="$4"; cat > "$out"; exit 0; fi
if [ "$1" = -d ]; then cat "$4"; exit 0; fi
exit 1
"""

# 1.11.0: Abruf der Kunden aus Automato (Agent) – Antwort aus "$NBH_LOG.antwort", Aufrufe und die
# Kopfzeilen-Datei protokolliert (das Token darf nie in der Befehlszeile stehen)
CURL = r"""
echo "curl $*" >> "$NBH_LOG.curl"
case "$*" in
  */api/netbox-host/zugang*)
    # 1.13.0: Zugang für Automato – Inhalt der Anfrage (stdin) festhalten, Antwort und HTTP-Code
    # aus "$NBH_LOG.zugang" bzw. "$NBH_LOG.zugang-code" (Vorgabe: übernommen, 200)
    [ -f "$NBH_LOG.automato-aus" ] && { echo 000; exit 7; }
    o=""; vor=""
    for a in "$@"; do
      [ "$vor" = -o ] && o=$a
      [ "$vor" = -H ] && case "$a" in @*) cat "${a#@}" >> "$NBH_LOG.kopf" ;; esac
      vor=$a
    done
    cat >> "$NBH_LOG.put"; echo >> "$NBH_LOG.put"
    [ -f "$NBH_LOG.zugang" ] || echo '{"status": "uebernommen"}' > "$NBH_LOG.zugang"
    cp "$NBH_LOG.zugang" "$o"
    cat "$NBH_LOG.zugang-code" 2>/dev/null || printf 200
    exit 0 ;;
  */api/netbox-host/stand*)
    # ADR 0059: Ist-Stand an Automato – Inhalt (stdin) festhalten, HTTP-Code aus
    # "$NBH_LOG.stand-code" (Vorgabe 200)
    o=""; vor=""
    for a in "$@"; do [ "$vor" = -o ] && o=$a; vor=$a; done
    cat >> "$NBH_LOG.stand"; echo >> "$NBH_LOG.stand"
    echo '{"vertrag": 1, "status": "gespeichert"}' > "$o"
    cat "$NBH_LOG.stand-code" 2>/dev/null || printf 200
    exit 0 ;;
  */api/netbox-host*)
    [ -f "$NBH_LOG.automato-aus" ] && exit 7
    o=""; vor=""
    for a in "$@"; do
      [ "$vor" = -o ] && o=$a
      [ "$vor" = -H ] && case "$a" in @*) cat "${a#@}" >> "$NBH_LOG.kopf" ;; esac
      vor=$a
    done
    cp "$NBH_LOG.antwort" "$o"; exit 0 ;;
esac
echo 200
"""

SYSTEMCTL = r"""
echo "systemctl $*" >> "$NBH_LOG"
case "$*" in is-active*) [ -f "$NBH_LOG.timer-aus" ] && exit 3 ;; esac
exit 0
"""

KEYGEN = r"""
if [ "$1" = -o ]; then echo "AGE-SECRET-KEY-TEST" > "$2"; exit 0; fi
if [ "$1" = -y ]; then echo "age1testempfaenger"; exit 0; fi
exit 1
"""


def _stub(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def host(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = {
        **os.environ,
        "NBH_DIR": str(tmp_path / "nbh"),
        "NBH_LOG": str(tmp_path / "aufrufe.log"),
        "NBH_SKIP_ROOT": "1",
        "NBH_WAIT": "0",
        "DOCKER": str(_stub(bin_dir / "docker", DOCKER)),
        "GIT": str(_stub(bin_dir / "git", GIT)),
        "AGE": str(_stub(bin_dir / "age", AGE)),
        "AGE_KEYGEN": str(_stub(bin_dir / "age-keygen", KEYGEN)),
        "CURL": str(_stub(bin_dir / "curl", CURL)),
        "SYSTEMCTL": str(_stub(bin_dir / "systemctl", SYSTEMCTL)),
        "NBH_SYSTEMD_DIR": str(tmp_path / "systemd"),
        "AGE_IDENTITY": str(tmp_path / "backup.agekey"),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",  # sync-user.sh ruft docker direkt
    }

    def run(*args: str, **mehr: str) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603 - Testskript des Repos
            [shutil.which("bash") or "bash", str(SCRIPT), *args],
            capture_output=True, text=True, env={**env, **mehr}, timeout=60, check=False,
            stdin=subprocess.DEVNULL,
        )  # fmt: skip

    run.dir = tmp_path / "nbh"  # type: ignore[attr-defined]
    run.log = tmp_path / "aufrufe.log"  # type: ignore[attr-defined]
    conf = run("init")
    assert conf.returncode == 0, conf.stdout + conf.stderr
    # Sicherung nach tmp statt /var/backups
    with (run.dir / "host.conf").open("a") as f:  # type: ignore[attr-defined]
        f.write(f"BACKUP_DIR={tmp_path / 'backups'}\n")
    return run


def test_init_legt_grundgeruest_und_schluessel_an(host):
    d = host.dir
    assert (d / "host.conf").stat().st_mode & 0o777 == 0o600
    conf = (d / "host.conf").read_text()
    assert "AGE_RECIPIENT=age1testempfaenger" in conf
    caddy = (d / "proxy" / "Caddyfile").read_text()
    assert "import /etc/caddy/sites/*.caddy" in caddy
    assert "trusted_proxies static {$FRONT" in caddy
    env = (d / "proxy" / ".env").read_text()
    assert 'ALLOW="10.0.30.5/32 10.0.10.0/24"' in env and 'FRONT="10.0.30.5/32"' in env
    # Startseiten und Übersicht liegen seit 1.1.0 auf dem Front-Proxy
    assert not (d / "proxy" / "www").exists()
    # nur der Auffang für Namen ohne Instanz (1.10.0): 404 statt einer leeren Seite von Caddy
    assert [p.name for p in (d / "proxy" / "sites").glob("*.caddy")] == ["_unbekannt.caddy"]
    assert "network create netbox-front" in host.log.read_text()


def test_proxy_ist_gehaertet(host):
    compose = (host.dir / "proxy" / "docker-compose.yml").read_text()
    assert 'security_opt: ["no-new-privileges:true"]' in compose
    assert "cap_drop: [ALL]" in compose and "cap_add: [NET_BIND_SERVICE]" in compose
    assert "read_only: true" in compose and "tmpfs: [/tmp]" in compose
    assert compose.count('"${NBH_PORT:-443}:443"') == 1  # einziger veröffentlichter Port
    assert "NBH_PORT=443" in (host.dir / "proxy" / ".env").read_text()  # Vorgabe 443


def test_port_aus_host_conf(host):
    """1.9.1: auf einer VM mit einem anderen Dienst auf 443 (Dev auf der Magister-VM)."""
    with (host.dir / "host.conf").open("a") as f:
        f.write("PORT=8444\n")
    r = host("init")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NBH_PORT=8444" in (host.dir / "proxy" / ".env").read_text()
    with (host.dir / "host.conf").open("a") as f:
        f.write("PORT=443x\n")
    r = host("init")
    assert r.returncode != 0
    assert "PORT in host.conf" in r.stdout + r.stderr


def _zertifikat(pki: Path, name: str) -> None:
    """Selbst signiertes Zertifikat mit Schlüssel (EC, schnell) nach pki/wildcard.*."""
    subprocess.run(  # noqa: S603 - feste Argumente
        [OPENSSL, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
         "-nodes", "-keyout", str(pki / "wildcard.key"), "-out", str(pki / "wildcard.crt"),
         "-days", "2", "-subj", "/CN=test", "-addext", f"subjectAltName=DNS:{name}"],
        check=True, capture_output=True,
    )  # fmt: skip


def test_zertifikat_passt_zum_schluessel(host):
    _zertifikat(host.dir / "proxy" / "pki", "*.mgmt.example.ch")
    r = host("init")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Zertifikat für *.mgmt.example.ch, passt zum Schlüssel" in r.stdout
    assert "Zertifikat für *.mgmt.example.ch, passt zum Schlüssel" in host("pruefen").stdout


def test_init_startet_den_proxy_nicht_mit_fremdem_zertifikat(host):
    """1.9.1, Dev: in wildcard.crt stand nur die CA – Caddy startete endlos neu."""
    pki = host.dir / "proxy" / "pki"
    _zertifikat(pki, "*.mgmt.example.ch")
    subprocess.run(  # noqa: S603 - feste Argumente
        [OPENSSL, "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256",
         "-out", str(pki / "wildcard.key")],
        check=True, capture_output=True,
    )  # fmt: skip
    host.log.write_text("")
    r = host("init")
    assert r.returncode != 0
    assert "passt nicht zu pki/wildcard.key" in r.stderr
    assert "compose up" not in host.log.read_text(), "Proxy darf nicht starten"
    p = host("pruefen")
    assert "passt nicht zu pki/wildcard.key" in p.stderr


def test_init_holt_den_proxy_aus_der_neustart_schleife(host):
    """Nach dem Richten des Zertifikats: init startet den hängenden Proxy neu."""
    _zertifikat(host.dir / "proxy" / "pki", "*.mgmt.example.ch")
    Path(f"{host.log}.restarting").write_text("")
    host.log.write_text("")
    r = host("init")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "compose restart proxy" in host.log.read_text()
    assert "Proxy aus der Neustart-Schleife geholt" in r.stdout


def test_pruefen_fragt_den_proxy_auf_seinem_port(host):
    with (host.dir / "host.conf").open("a") as f:
        f.write("PORT=8444\n")
    assert host("init").returncode == 0
    assert host("neu", "kunde-a", "Kunde A").returncode == 0
    r = host("pruefen")
    assert "Proxy läuft (Port 8444)" in r.stdout
    assert "über den Proxy erreichbar (https://kunde-a-nb.mgmt.example.ch:8444)" in r.stdout


def test_neu_schreibt_instanz_mit_eigenen_secrets(host):
    out = host("neu", "kunde-a", "Kunde A")
    assert out.returncode == 0, out.stdout + out.stderr
    nb = host.dir / "kunden" / "kunde-a" / "netbox"
    env = (nb / ".env").read_text()
    assert (nb / ".env").stat().st_mode & 0o777 == 0o600
    assert "COMPOSE_PROJECT_NAME=nb-kunde-a" in env
    assert "VERSION=v4.6.10-5.0.2" in env
    assert "NBH_HOST=kunde-a-nb.mgmt.example.ch" in env
    werte = re.findall(r"^NBH_(?:SECRET_KEY|API_TOKEN_PEPPER_1|DB_PASSWORD|REDIS_PASSWORD|"
                       r"REDIS_CACHE_PASSWORD)=([0-9a-f]{64})$", env, re.M)  # fmt: skip
    assert len(werte) == 5 and len(set(werte)) == 5

    over = (nb / "docker-compose.override.yml").read_text()
    assert 'aliases: ["nb-kunde-a"]' in over
    assert "SECRET_KEY: ${NBH_SECRET_KEY}" in over
    assert "POSTGRES_PASSWORD: ${NBH_DB_PASSWORD}" in over
    assert "REDIS_PASSWORD: ${NBH_REDIS_CACHE_PASSWORD}" in over
    assert "CSRF_TRUSTED_ORIGINS: https://${NBH_HOST}" in over
    assert over.count("restart: unless-stopped") == 5
    assert "name: netbox-front" in over and "external: true" in over
    assert "J5brHrAXFLQSif0K" not in env + over  # Vorgabe-Passwort von netbox-docker nie benutzt
    assert "ports:" not in over  # nur der Proxy veröffentlicht einen Port

    sites = sorted(p.name for p in (host.dir / "proxy" / "sites").glob("*.caddy"))
    assert sites == ["_unbekannt.caddy", "kunde-a-nb.caddy"]  # keine Startseite hier (Front-Proxy)
    site = (host.dir / "proxy" / "sites" / "kunde-a-nb.caddy").read_text()
    assert "kunde-a-nb.mgmt.example.ch {" in site and "reverse_proxy nb-kunde-a:8080" in site
    assert "NAME=Kunde\\ A" in (host.dir / "kunden" / "kunde-a" / "kunde.conf").read_text()
    assert "Admin 'admin' angelegt" in out.stdout
    # 1.4.0: Anmeldung über das Anmelde-Tor – Benutzer und Rollen aus Kopfzeilen des Front-Proxys
    sso = (nb / "configuration" / "zz_automato_sso.py").read_text()
    assert 'REMOTE_AUTH_HEADER = "HTTP_X_AUTOMATO_USER"' in sso
    assert 'REMOTE_AUTH_SUPERUSER_GROUPS = ["role:admin"]' in sso
    assert "REMOTE_AUTH_AUTO_CREATE_GROUPS = False" in sso
    assert "Anmeldung über den Realm" in out.stdout
    assert "git clone -q -c advice.detachedHead=false --depth 1 -b 5.0.2" in host.log.read_text()

    doppelt = host("neu", "kunde-a")
    assert doppelt.returncode != 0 and "gibt es schon" in doppelt.stderr


@pytest.mark.parametrize(
    "name",
    ["Kunde_A", "kundeA", "-a", "a-", "start", "alle", "konfig", "x" * 43,
     # endet wie ein Werkzeug eines Kunden: "kunde-nb" fiele mit der NetBox von "kunde" zusammen
     "kunde-nb", "kunde-doc", "kunde-um", "kunde-cmdb"],
)  # fmt: skip
def test_kurzname_wird_geprueft(host, name):
    out = host("neu", name)
    assert out.returncode != 0
    assert "ungültig" in out.stderr or "reserviert" in out.stderr
    assert not (host.dir / "kunden" / name).exists()


def test_sichern_verschluesselt_und_raeumt_auf(host, tmp_path):
    host("neu", "kunde-a")
    ziel = tmp_path / "backups" / "kunde-a"
    ziel.mkdir(parents=True)
    alt = ziel / "kunde-a-20200101-000000.sql.gz.age"
    alt.write_text("alt")
    vor_langem = time.time() - 40 * 86400
    os.utime(alt, (vor_langem, vor_langem))
    out = host("sichern", "alle")
    assert out.returncode == 0, out.stdout + out.stderr
    dumps = sorted(ziel.glob("kunde-a-*.sql.gz.age"))
    assert len(dumps) == 1 and not alt.exists()
    import gzip

    assert gzip.decompress(dumps[0].read_bytes()).decode().strip() == "SQL-DUMP"
    assert list(ziel.glob("kunde-a-*.media.tar.gz.age"))
    assert "pg_dump -c --if-exists" in host.log.read_text()

    # "alle" sichert auch die Konfiguration: host.conf, Proxy und Secrets je Kunde
    (konf,) = (tmp_path / "backups" / "_konfig").glob("konfig-*.tar.gz.age")
    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(konf.read_bytes()), mode="r:gz") as tar:
        namen = set(tar.getnames())
    assert {"./host.conf", "./proxy/.env", "./kunden/kunde-a/netbox/.env",
            "./kunden/kunde-a/netbox/docker-compose.override.yml"} <= namen  # fmt: skip


def test_pruefen_meldet_sicherung_der_konfiguration(host):
    host("neu", "kunde-a")
    vorher = host("pruefen")
    assert "keine Sicherung der Konfiguration" in vorher.stdout
    assert host("sichern", "konfig").returncode == 0
    nachher = host("pruefen")
    assert "Konfiguration gesichert" in nachher.stdout
    assert "Front-Proxy: 10.0.30.5/32" in nachher.stdout


def test_loeschen_nur_mit_bestaetigung_und_sicherung(host, tmp_path):
    host("neu", "kunde-a")
    ohne = host("loeschen", "kunde-a")
    assert ohne.returncode != 0 and "--ja" in ohne.stderr
    assert (host.dir / "kunden" / "kunde-a").exists()
    out = host("loeschen", "kunde-a", "--ja")
    assert out.returncode == 0, out.stdout + out.stderr
    assert not (host.dir / "kunden" / "kunde-a").exists()
    assert not (host.dir / "proxy" / "sites" / "kunde-a-nb.caddy").exists()
    assert list((tmp_path / "backups" / "kunde-a").glob("*.sql.gz.age"))
    assert "down -v --remove-orphans" in host.log.read_text()


def test_token_legt_benutzer_fuer_automato_an(host):
    host("neu", "kunde-a")
    out = host("token", "kunde-a")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "https://kunde-a-nb.mgmt.example.ch" in out.stdout
    lese = re.search(r"Lese-Token:\s+([0-9a-f]{40})", out.stdout)
    sync = re.search(r"Schreib-Token:\s+([0-9a-f]{40})", out.stdout)
    assert lese and sync
    shells = [z for z in host.log.read_text().splitlines() if "manage.py shell" in z]
    # Admin und Gruppen der Anmeldung bei "neu", Lese-Benutzer, Sync-Benutzer (sync-user.sh)
    assert len(shells) == 4
    # 1.13.0: die Tokens nur über die Umgebung an docker, nie in der Befehlszeile (ps)
    assert Path(f"{host.log}.lese").read_text().split() == [lese.group(1)]
    assert Path(f"{host.log}.sync").read_text().split() == [sync.group(1)]
    assert lese.group(1) not in host.log.read_text() and sync.group(1) not in host.log.read_text()


def test_update_sichert_vorher_und_setzt_version(host, tmp_path):
    host("neu", "kunde-a")
    conf = host.dir / "host.conf"
    conf.write_text(conf.read_text() + "NETBOX_DOCKER_TAG=5.1.1\nNETBOX_VERSION=v4.7.2-5.1.1\n")
    out = host("update", "kunde-a")
    assert out.returncode == 0, out.stdout + out.stderr
    env = (host.dir / "kunden" / "kunde-a" / "netbox" / ".env").read_text()
    assert "VERSION=v4.7.2-5.1.1" in env
    log = host.log.read_text()
    assert "git fetch -q --depth 1 origin tag 5.1.1" in log
    assert log.index("pg_dump") < log.index("git fetch")  # erst sichern, dann holen
    assert list((tmp_path / "backups" / "kunde-a").glob("*.sql.gz.age"))


def test_pruefen_meldet_fehlendes_zertifikat(host):
    host("neu", "kunde-a")
    out = host("pruefen")
    assert out.returncode == 1
    assert "Wildcard-Zertifikat fehlt" in out.stderr
    assert "kunde-a: NetBox läuft" in out.stdout
    assert "kunde-a: über den Proxy erreichbar" in out.stdout
    assert "keine Sicherung der letzten 26 h" in out.stdout


def test_wiederherstellen_stoppt_netbox_und_spielt_ein(host, tmp_path):
    host("neu", "kunde-a")
    assert host("sichern", "kunde-a").returncode == 0
    dump = next((tmp_path / "backups" / "kunde-a").glob("*.sql.gz.age"))
    out = host("wiederherstellen", "kunde-a", str(dump))
    assert out.returncode == 0, out.stdout + out.stderr
    log = host.log.read_text()
    assert "stop netbox netbox-worker" in log
    assert "psql -q -v ON_ERROR_STOP=1" in log
    assert log.index("stop netbox netbox-worker") < log.index("psql")
    assert "Medien eingespielt" in out.stdout


def test_proxy_nimmt_anmelde_kopfzeilen_nur_vom_front_proxy(host):
    caddy = (host.dir / "proxy" / "Caddyfile").read_text()
    assert "@nicht_front not remote_ip {$FRONT:255.255.255.255/32}" in caddy
    for kopf in ("X-Automato-User", "X-Automato-Realm", "X-Automato-Groups"):
        assert f"request_header @nicht_front -{kopf}" in caddy


def test_sso_richtet_ein_und_entfernt(host):
    host("neu", "kunde-a")
    datei = host.dir / "kunden" / "kunde-a" / "netbox" / "configuration" / "zz_automato_sso.py"
    conf = host.dir / "host.conf"
    conf.write_text(conf.read_text() + "SSO=aus\n")
    out = host("sso", "kunde-a")
    assert out.returncode == 0, out.stdout + out.stderr
    assert not datei.exists()  # aus: nur noch lokale Konten
    conf.write_text(conf.read_text().replace("SSO=aus\n", "SSO=an\n"))
    out = host("sso", "alle")
    assert out.returncode == 0, out.stdout + out.stderr
    assert datei.exists() and "Anmeldung über den Realm" in out.stdout
    log = host.log.read_text()
    assert "restart netbox netbox-worker" in log  # neue Einstellungen erst nach dem Neustart
    falsch = conf.read_text().replace("SSO=an\n", "SSO=vielleicht\n")
    conf.write_text(falsch)
    assert "SSO in host.conf" in host("sso", "alle").stderr


def test_namen_ohne_instanz_antworten_mit_404(host):
    """1.10.0: Caddy beantwortet einen Namen ohne Site mit einer leeren 200 – der Front-Proxy hielt
    so eine fehlende NetBox für erreichbar (Dev-Test 2026-11-13). Der Auffang sagt es deutlich."""
    auffang = (host.dir / "proxy" / "sites" / "_unbekannt.caddy").read_text()
    domain = re.search(r"^DOMAIN=\"?([^\"\n]+)", (host.dir / "host.conf").read_text(), re.M)
    assert domain and f"*.{domain.group(1)} {{" in auffang
    assert "import common" in auffang  # Zertifikat, ALLOW und Kopfzeilen wie bei jeder NetBox
    assert 'respond "Keine NetBox für diesen Namen' in auffang and '" 404' in auffang
    (host.dir / "proxy" / "sites" / "_unbekannt.caddy").unlink()
    out = host("sso", "alle")  # sso schreibt den Proxy neu – auch den Auffang
    assert out.returncode == 0, out.stdout + out.stderr
    assert (host.dir / "proxy" / "sites" / "_unbekannt.caddy").exists()
    # init lädt einen laufenden Proxy neu – sonst bliebe der Auffang bis zum Neustart unwirksam
    _zertifikat(host.dir / "proxy" / "pki", "*.mgmt.example.ch")
    host.log.write_text("")
    out = host("init")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "caddy reload --config /etc/caddy/Caddyfile" in host.log.read_text()


TOKEN = "t0ken-fuer-den-netbox-host-0123456789"


def _antwort(host, kunden, domain="mgmt.example.ch"):
    Path(f"{host.log}.antwort").write_text(
        json.dumps({"vertrag": 1, "domain": domain, "kunden": kunden})
    )


def test_agent_richtet_den_abgleich_ein(host):
    """1.11.0 (ADR 0054): Token nur in einer Datei für root, Adresse in host.conf, systemd-Timer."""
    ohne = host("agent", "https://automato.int.example.ch")  # kein Terminal, kein NBH_TOKEN
    assert ohne.returncode == 1 and "Token fehlt" in ohne.stderr
    falsch = host("agent", "http://automato.int.example.ch", NBH_TOKEN=TOKEN)
    assert falsch.returncode == 1 and "Verwendung: agent https://" in falsch.stderr
    out = host("agent", "https://automato.int.example.ch/", NBH_TOKEN=TOKEN)
    assert out.returncode == 0, out.stdout + out.stderr
    datei = host.dir / "automato.token"
    assert datei.read_text().strip() == TOKEN and datei.stat().st_mode & 0o777 == 0o600
    conf = (host.dir / "host.conf").read_text()
    assert "AUTOMATO_URL=https://automato.int.example.ch\n" in conf and TOKEN not in conf
    systemd = host.dir.parent / "systemd"
    assert "netbox-host.sh sync --takt" in (systemd / "netbox-host-sync.service").read_text()
    assert "OnUnitActiveSec=1min" in (systemd / "netbox-host-sync.timer").read_text()
    assert "systemctl enable --now netbox-host-sync.timer" in host.log.read_text()
    # erneut: Adresse ersetzt, nicht doppelt
    host("agent", "https://automato2.int.example.ch", NBH_TOKEN=TOKEN)
    conf = (host.dir / "host.conf").read_text()
    assert re.findall(r"^AUTOMATO_URL=.*$", conf, re.M) == [
        "AUTOMATO_URL=https://automato2.int.example.ch"
    ]


def test_sync_legt_fehlende_netbox_an_und_entfernt_nie(host):
    assert host("neu", "kunde-a").returncode == 0
    assert host("neu", "kunde-x").returncode == 0  # in Automato nicht (mehr) freigegeben
    host("agent", "https://automato.int.example.ch", NBH_TOKEN=TOKEN)
    _antwort(host, [
        {"kurzname": "kunde-a", "name": "Kunde A"},
        {"kurzname": "kunde-b", "name": "Kunde B"},
        {"kurzname": "Kunde C", "name": "ungültig"},
        "kein Objekt",
    ])  # fmt: skip
    out = host("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "kunde-a: NetBox vorhanden" in out.stdout
    assert "kunde-b: angelegt" in out.stdout
    assert (host.dir / "kunden" / "kunde-b" / "netbox").is_dir()
    assert "NAME=Kunde\\ B" in (host.dir / "kunden" / "kunde-b" / "kunde.conf").read_text()
    pw = host.dir / "kunden" / "kunde-b" / "admin-passwort"
    assert pw.stat().st_mode & 0o777 == 0o600 and len(pw.read_text().strip()) >= 12
    assert pw.read_text().strip() not in out.stdout  # nicht ins Protokoll des Agenten
    assert "kunde-x: in Automato ohne NetBox freigegeben – bleibt" in out.stdout + out.stderr
    assert (host.dir / "kunden" / "kunde-x" / "netbox").is_dir()  # nie selbst entfernt
    assert not (host.dir / "kunden" / "kunde-c").exists()
    stand = json.loads((host.dir / "sync-status.json").read_text())
    assert (stand["ok"], stand["kunden"], stand["angelegt"], stand["ohne_freigabe"]) == (
        True, 2, ["kunde-b"], ["kunde-x"],
    )  # fmt: skip
    # das Token nur als Kopfzeile aus einer Datei, nie in der Befehlszeile
    assert TOKEN not in Path(f"{host.log}.curl").read_text()
    assert f"Authorization: Bearer {TOKEN}" in Path(f"{host.log}.kopf").read_text()
    assert not list(host.dir.glob(".kopf-*")) and not list(host.dir.glob(".antwort-*"))

    out = host("pruefen")
    assert "letzter Abgleich" in out.stdout and "2 Kunden mit NetBox" in out.stdout
    assert "ohne Freigabe in Automato: kunde-x (bleiben" in out.stdout


def test_sync_fuehrt_rechte_des_sync_benutzers_nach_ohne_neuen_token(host):
    """1.19.1: neue Objekttypen des Syncs (ASN, ADR 0066) kommen beim Abgleich in bestehende
    Instanzen - nur die Rechte, der Schreib-Token bleibt; danach nur bei geänderter Liste."""
    assert host("neu", "kunde-a").returncode == 0
    host("agent", "https://automato.int.example.ch", NBH_TOKEN=TOKEN)
    _antwort(host, [{"kurzname": "kunde-a", "name": "Kunde A", "zugang": "vorhanden"}])
    sync_log = Path(f"{host.log}.sync")
    vorher = sync_log.read_text() if sync_log.exists() else ""
    out = host("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "kunde-a: Rechte des Sync-Benutzers nachgeführt (Token unverändert)" in out.stdout
    log = host.log.read_text()
    assert "-e NUR_RECHTE" in log and "manage.py shell" in log
    # kein neuer Token: sync-user.sh hat keinen SYNC_TOKEN an docker gegeben
    assert (sync_log.read_text() if sync_log.exists() else "") == vorher
    assert (host.dir / "kunden" / "kunde-a" / ".sync-rechte").is_file()
    host.log.write_text("")
    out = host("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Rechte des Sync-Benutzers nachgeführt" not in out.stdout
    assert "NUR_RECHTE" not in host.log.read_text()


def test_sync_meldet_fehler_und_andere_domain(host):
    host("agent", "https://automato.int.example.ch", NBH_TOKEN=TOKEN)
    _antwort(host, [], domain="andere.example.ch")
    out = host("sync")
    assert out.returncode == 0
    assert "Automato nennt die Domain andere.example.ch, dieser Host mgmt.example.ch" in (
        out.stdout + out.stderr
    )
    Path(f"{host.log}.automato-aus").write_text("")
    out = host("sync")
    assert out.returncode == 1 and "Automato antwortet nicht" in out.stderr
    assert json.loads((host.dir / "sync-status.json").read_text())["ok"] is False
    Path(f"{host.log}.timer-aus").write_text("")
    out = host("pruefen")
    assert "Timer läuft nicht" in out.stderr and "fehlgeschlagen" in out.stderr


def test_ohne_agent_wie_bisher(host):
    out = host("pruefen")
    assert "nicht eingerichtet – NetBox je Kunde von Hand" in out.stdout
    assert host("sync").returncode == 1


# --- 1.13.0 (ADR 0056): SSO und Zugang für Automato ohne Handgriffe -----------------------------


def _alle_dateien(wurzel: Path) -> str:
    """Inhalt aller Dateien unter NBH_DIR und der Protokolle – dort darf kein Token stehen
    (ausser im Inhalt der Anfrage an Automato, den der Ersatz für curl festhält)."""
    teile = []
    for p in wurzel.rglob("*"):
        if p.is_file() and not p.name.endswith(".put"):
            teile.append(p.read_text(errors="replace"))
    return "\n".join(teile)


def _anfragen(host) -> list[dict]:
    datei = Path(f"{host.log}.put")
    if not datei.exists():
        return []
    return [json.loads(z) for z in datei.read_text().splitlines() if z.strip()]


def test_sync_uebergibt_den_zugang_nur_wenn_automato_ihn_annimmt(host, tmp_path):
    assert host("neu", "kunde-a").returncode == 0
    assert host("neu", "kunde-c").returncode == 0
    host("agent", "https://automato.int.example.ch", NBH_TOKEN=TOKEN)
    _antwort(host, [
        {"kurzname": "kunde-a", "name": "Kunde A", "zugang": "fehlt"},  # vorhandene NetBox
        {"kurzname": "kunde-b", "name": "Kunde B", "zugang": "fehlt"},  # neue NetBox
        {"kurzname": "kunde-c", "name": "Kunde C", "zugang": "vorhanden"},
        {"kurzname": "kunde-d", "name": "Kunde D", "zugang": "kein_mandant"},
        {"kurzname": "kunde-e", "name": "Kunde E", "zugang": "andere_adresse"},
    ])  # fmt: skip
    out = host("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    anfragen = _anfragen(host)
    assert [a["kurzname"] for a in anfragen] == ["kunde-a", "kunde-b"]  # nur „fehlt“
    lese = Path(f"{host.log}.lese").read_text().split()
    sync = Path(f"{host.log}.sync").read_text().split()
    # an Automato gehen genau die Tokens, die die NetBox eben bekommen hat
    assert [a["lesen"] for a in anfragen] == lese and [a["sync"] for a in anfragen] == sync
    tokens = lese + sync
    assert len(set(tokens)) == 4 and all(re.fullmatch(r"[0-9a-f]{40}", t) for t in tokens)
    assert "kunde-a: Zugang für Automato übergeben" in out.stdout
    assert "kunde-c: Zugang für Automato vorhanden" in out.stdout
    assert "kunde-d: in Automato noch ohne Mandant" in out.stdout
    assert "kunde-e: der Mandant in Automato hat ein Quellsystem NetBox mit" in out.stdout
    # Klartext nirgends: nicht in der Ausgabe, nicht in Befehlszeilen (docker, curl), nicht in
    # Status- oder sonstigen Dateien des NetBox-Hosts
    curl = Path(f"{host.log}.curl").read_text()
    protokolle = out.stdout + out.stderr + host.log.read_text() + curl
    for t in tokens:
        assert t not in protokolle
        assert t not in _alle_dateien(host.dir)
    assert "-X PUT" in curl and "--data-binary @-" in curl  # Inhalt über stdin
    assert Path(f"{host.log}.kopf").read_text().count(f"Authorization: Bearer {TOKEN}") == 3
    assert not list(host.dir.glob(".kopf-*")) and not list(host.dir.glob(".antwort-*"))
    stand = json.loads((host.dir / "sync-status.json").read_text())
    assert stand["ok"] is True
    assert stand["zugang_uebergeben"] == ["kunde-a", "kunde-b"]
    assert (stand["zugang_offen"], stand["zugang_klaeren"]) == (["kunde-d"], ["kunde-e"])
    p = host("pruefen")
    assert "Zugang an Automato übergeben: kunde-a kunde-b" in p.stdout
    assert "in Automato noch ohne Mandant (Zugang folgt): kunde-d" in p.stdout
    assert "Zugang für Automato von Hand klären: kunde-e" in p.stdout

    # nächster Abgleich: Automato meldet „vorhanden“ – keine neuen Tokens
    Path(f"{host.log}.put").unlink()
    _antwort(host, [{"kurzname": k, "name": k, "zugang": "vorhanden"}
                    for k in ("kunde-a", "kunde-b")])  # fmt: skip
    assert host("sync").returncode == 0
    assert _anfragen(host) == []
    assert len(Path(f"{host.log}.lese").read_text().split()) == 2


def test_sync_zugang_abgelehnt_oder_fehler(host):
    assert host("neu", "kunde-a").returncode == 0
    host("agent", "https://automato.int.example.ch", NBH_TOKEN=TOKEN)
    _antwort(host, [{"kurzname": "kunde-a", "name": "Kunde A", "zugang": "fehlt"}])
    # Mandant inzwischen entfernt: nicht übernommen, kein Fehler, nächster Abgleich fragt neu
    Path(f"{host.log}.zugang").write_text('{"status": "kein_mandant", "detail": "x"}')
    Path(f"{host.log}.zugang-code").write_text("409")
    out = host("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "kunde-a: in Automato noch ohne Mandant" in out.stdout
    assert json.loads((host.dir / "sync-status.json").read_text())["zugang_offen"] == ["kunde-a"]
    # von Hand eingetragen, während der Agent lief: bleibt, Hinweis zum Prüfen
    Path(f"{host.log}.zugang").write_text('{"status": "vorhanden"}')
    Path(f"{host.log}.zugang-code").write_text("200")
    out = host("sync")
    assert out.returncode == 0
    assert "Automato hat inzwischen einen anderen Zugang (bleibt)" in out.stdout + out.stderr
    # Automato nimmt nicht an (Fehler): Abgleich nicht in Ordnung, beim nächsten wieder
    Path(f"{host.log}.zugang").write_text("kein JSON")
    Path(f"{host.log}.zugang-code").write_text("500")
    out = host("sync")
    assert out.returncode == 1
    assert "kunde-a: Zugang für Automato nicht übergeben (HTTP 500)" in out.stderr
    stand = json.loads((host.dir / "sync-status.json").read_text())
    assert stand["ok"] is False
    assert "Zugang für Automato nicht übergeben: kunde-a" in stand["meldung"]
    assert "Zugang für Automato nicht übergeben: kunde-a" in host("pruefen").stderr
    # NetBox antwortet nicht: keine Benutzer, nichts an Automato
    Path(f"{host.log}.put").unlink()
    Path(f"{host.log}.netbox-aus").write_text("")
    out = host("sync")
    assert out.returncode == 1
    assert "kunde-a: Benutzer für Automato nicht angelegt" in out.stderr
    assert _anfragen(host) == []
    for t in Path(f"{host.log}.lese").read_text().split():
        assert t not in out.stdout + out.stderr


def test_sync_ohne_zugang_bei_anderer_domain_oder_altem_automato(host):
    assert host("neu", "kunde-a").returncode == 0
    host("agent", "https://automato.int.example.ch", NBH_TOKEN=TOKEN)
    _antwort(host, [{"kurzname": "kunde-a", "name": "A", "zugang": "fehlt"}],
             domain="andere.example.ch")  # fmt: skip
    out = host("sync")
    assert "kein Zugang für Automato" in out.stdout + out.stderr
    _antwort(host, [{"kurzname": "kunde-a", "name": "A"}])  # Automato vor 1.13.0
    out = host("sync")
    assert out.returncode == 0
    assert "Automato meldet den Zugang nicht" in out.stdout
    assert _anfragen(host) == [] and not Path(f"{host.log}.lese").exists()


def test_sync_richtet_sso_ein_wo_es_fehlt(host):
    assert host("neu", "kunde-a").returncode == 0
    k = host.dir / "kunden" / "kunde-a"
    datei = k / "netbox" / "configuration" / "zz_automato_sso.py"
    stand = k / "sso-stand"
    assert stand.read_text().strip() == "ok"  # neu richtet alles ein
    host("agent", "https://automato.int.example.ch", NBH_TOKEN=TOKEN)
    _antwort(host, [{"kurzname": "kunde-a", "name": "Kunde A", "zugang": "vorhanden"}])

    def neustarts() -> int:
        return host.log.read_text().count("restart netbox netbox-worker")

    def shells() -> int:  # die der Anmeldung (ohne Nachführen der Sync-Rechte, 1.19.1)
        return sum(
            1 for z in host.log.read_text().splitlines()
            if "manage.py shell" in z and "NUR_RECHTE" not in z
        )  # fmt: skip

    # Instanz ohne Anmeldung über das Tor (vor 1.4.0 angelegt): einrichten und neu starten
    datei.unlink()
    stand.unlink()
    host.log.write_text("")
    out = host("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    assert datei.exists() and stand.read_text().strip() == "ok"
    assert neustarts() == 1 and shells() == 1
    assert "Anmeldung über den Realm" in out.stdout
    status = json.loads((host.dir / "sync-status.json").read_text())
    assert status["sso_eingerichtet"] == ["kunde-a"]
    assert "Anmeldung über das Tor eingerichtet: kunde-a" in host("pruefen").stdout

    # schon eingerichtet: nichts, kein Neustart
    host.log.write_text("")
    assert host("sync").returncode == 0
    assert neustarts() == 0 and shells() == 0

    # vor 1.13.0 mit sso eingerichtet (ohne Stand): nur die Rechte, kein Neustart
    stand.unlink()
    host.log.write_text("")
    assert host("sync").returncode == 0
    assert neustarts() == 0 and shells() == 1 and stand.read_text().strip() == "ok"

    # Einstellungen aus einer älteren Fassung: neu schreiben, neu starten
    datei.write_text("REMOTE_AUTH_ENABLED = True\n")
    host.log.write_text("")
    assert host("sync").returncode == 0
    assert neustarts() == 1 and "HTTP_X_AUTOMATO_GROUPS" in datei.read_text()

    # Neustart fehlgeschlagen: der nächste Abgleich startet wieder neu
    datei.unlink()
    Path(f"{host.log}.restart-aus").write_text("")
    out = host("sync")
    assert "NetBox nicht neu gestartet – der nächste Abgleich versucht es wieder" in out.stdout
    assert datei.exists() and stand.read_text().strip() == "neustart"
    Path(f"{host.log}.restart-aus").unlink()
    host.log.write_text("")
    assert host("sync").returncode == 0
    assert neustarts() == 1 and stand.read_text().strip() == "ok"

    # SSO=aus: der Agent richtet nichts ein und entfernt nichts (das tut nur sso <kunde|alle>)
    datei.unlink()
    host.log.write_text("")
    conf = host.dir / "host.conf"
    conf.write_text(conf.read_text() + "SSO=aus\n")
    assert host("sync").returncode == 0
    assert not datei.exists() and neustarts() == 0
    conf.write_text(conf.read_text().replace("SSO=aus\n", ""))

    # ohne Entfernen gefälschter Kopfzeilen im Proxy nie einrichten
    caddy = host.dir / "proxy" / "Caddyfile"
    caddy.write_text(caddy.read_text().replace("X-Automato-User", "X-Anders"))
    out = host("sync")
    assert "der Proxy entfernt gefälschte Anmelde-Kopfzeilen nicht" in out.stdout
    assert not datei.exists()


def test_sync_im_takt_nur_bei_aenderung_voll(host):
    """1.15.1: der Timer ruft jede Minute `sync --takt`; dieselbe Antwort wie beim letzten
    fehlerfreien Abgleich (juenger als 5 Minuten) endet nach der Abfrage, eine geaenderte gleicht
    sofort voll ab. Units einer aelteren Version (5 Minuten) werden dabei nachgefuehrt."""
    assert host("agent", "https://automato.int.example.ch", NBH_TOKEN=TOKEN).returncode == 0
    systemd = host.dir.parent / "systemd"
    timer = systemd / "netbox-host-sync.timer"
    timer.write_text(timer.read_text().replace("OnUnitActiveSec=1min", "OnUnitActiveSec=5min"))
    _antwort(host, [{"kurzname": "kunde-a", "name": "Kunde A"}])
    voll = host("sync", "--takt")
    assert voll.returncode == 0, voll.stdout + voll.stderr
    assert "OnUnitActiveSec=1min" in timer.read_text() and "Takt auf jede Minute" in voll.stdout
    assert (host.dir / ".letzter-abgleich").exists()
    vorher = host.log.read_text()
    gleich = host("sync", "--takt")
    assert gleich.returncode == 0 and "Automato unverändert" in gleich.stdout
    assert "compose" not in host.log.read_text()[len(vorher) :]  # kein Abgleich je Kunde
    # ohne --takt (von Hand) immer voll
    assert "Automato unverändert" not in host("sync").stdout
    # geaendert: sofort voll
    _antwort(host, [{"kurzname": "kunde-a", "name": "Kunde A"},
                    {"kurzname": "kunde-b", "name": "B"}])  # fmt: skip
    neu = host("sync", "--takt")
    assert "Automato unverändert" not in neu.stdout and "kunde-b" in neu.stdout


def test_ruecklink_zum_kunden_portal(host):
    """1.17.0 (Anwender: „Ein Shortcut wäre schön, wenn ich einfach wieder zu dem Kundenportal
    gelangen könnte.“): je Instanz ein BANNER_TOP mit einem Link auf https://<kunde>.<domain> –
    beim Anlegen vor dem ersten Start, im vollen Abgleich nachgeführt; unverändert kein Neustart,
    ein fehlgeschlagener Neustart wird im nächsten Abgleich wiederholt."""
    assert host("neu", "kunde-a").returncode == 0
    k = host.dir / "kunden" / "kunde-a"
    datei = k / "netbox" / "configuration" / "zz_automato_portal.py"
    stand = k / "portal-stand"
    text = datei.read_text()
    zuweisung = ast.parse(text).body[0]
    assert isinstance(zuweisung, ast.Assign) and zuweisung.targets[0].id == "BANNER_TOP"  # type: ignore[attr-defined]
    assert ast.literal_eval(zuweisung.value) == (
        '<a href="https://kunde-a.mgmt.example.ch">← Kundenportal</a>'
    )
    assert datei.stat().st_mode & 0o777 == 0o644
    assert stand.read_text().strip() == "ok"  # wirkt seit dem ersten Start
    host("agent", "https://automato.int.example.ch", NBH_TOKEN=TOKEN)
    _antwort(host, [{"kurzname": "kunde-a", "name": "Kunde A", "zugang": "vorhanden"}])

    def neustarts() -> int:
        return host.log.read_text().count("restart netbox netbox-worker")

    # unverändert: nichts, kein Neustart
    host.log.write_text("")
    out = host("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    assert neustarts() == 0 and datei.read_text() == text

    # vor 1.17.0 angelegt: einrichten und einmal neu starten
    datei.unlink()
    stand.unlink()
    host.log.write_text("")
    out = host("sync")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "kunde-a: Rücklink zum Kunden-Portal wird eingerichtet" in out.stdout
    assert datei.read_text() == text and stand.read_text().strip() == "ok"
    assert neustarts() == 1

    # ältere Fassung: neu schreiben, neu starten
    datei.write_text("BANNER_TOP = 'alt'\n")
    host.log.write_text("")
    assert host("sync").returncode == 0
    assert neustarts() == 1 and datei.read_text() == text

    # Neustart fehlgeschlagen: der nächste Abgleich startet wieder neu
    datei.unlink()
    Path(f"{host.log}.restart-aus").write_text("")
    out = host("sync")
    assert "NetBox nicht neu gestartet (Rücklink zum Kunden-Portal)" in out.stdout
    assert datei.exists() and stand.read_text().strip() == "neustart"
    Path(f"{host.log}.restart-aus").unlink()
    host.log.write_text("")
    assert host("sync").returncode == 0
    assert neustarts() == 1 and stand.read_text().strip() == "ok"

    # Domain mit Zeichen, die nicht in einen Namen gehören: kein Rücklink (sonst startet NetBox
    # nicht)
    conf = host.dir / "host.conf"
    alt = conf.read_text()
    domain = re.search(r"^DOMAIN=\"?([^\"\n]+)", alt, re.M)
    assert domain is not None
    conf.write_text(alt.replace(domain.group(0), 'DOMAIN="x\'.example.ch"'))
    datei.unlink()
    out = host("sync")
    assert "ist kein Name – kein Rücklink zum Kunden-Portal" in out.stdout
    assert not datei.exists()
