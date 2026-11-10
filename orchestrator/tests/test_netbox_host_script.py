"""deploy/netbox-host/netbox-host.sh (0.121.0, ADR 0044): NetBox je Kunde hinter einem Proxy –
mit Ersatz für docker, git, age und curl. Geprüft wird, was das Skript schreibt (Secrets nur in
der .env der Instanz, eigene Namen im Netz des Proxys) und dass Löschen und Update nie ohne
Sicherung laufen. Seit 1.1.0 (ADR 0044 Nachtrag) ein Backend hinter dem Front-Proxy der
Automato-VM: NetBox unter <kunde>-nb, keine Startseiten hier, Proxy gehärtet, Konfiguration in
der Sicherung."""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "netbox-host" / "netbox-host.sh"

pytestmark = pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("openssl"), reason="bash/openssl fehlen"
)

DOCKER = r"""
echo "$PWD|$*" >> "$NBH_LOG"
if [ "$1" = network ]; then [ "$2" = inspect ] && exit 1; exit 0; fi
[ "$1" = compose ] || exit 0
shift
case "$*" in
  "config --services") printf 'netbox\nnetbox-worker\npostgres\nredis\nredis-cache\n' ;;
  ps*--services*) echo proxy ;;
  ps*) echo "netbox running" ;;
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
        "CURL": str(_stub(bin_dir / "curl", "echo 200\n")),
        "AGE_IDENTITY": str(tmp_path / "backup.agekey"),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",  # sync-user.sh ruft docker direkt
    }

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603 - Testskript des Repos
            [shutil.which("bash") or "bash", str(SCRIPT), *args],
            capture_output=True, text=True, env=env, timeout=60, check=False,
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
    assert not list((d / "proxy" / "sites").glob("*.caddy"))
    assert "network create netbox-front" in host.log.read_text()


def test_proxy_ist_gehaertet(host):
    compose = (host.dir / "proxy" / "docker-compose.yml").read_text()
    assert 'security_opt: ["no-new-privileges:true"]' in compose
    assert "cap_drop: [ALL]" in compose and "cap_add: [NET_BIND_SERVICE]" in compose
    assert "read_only: true" in compose and "tmpfs: [/tmp]" in compose
    assert compose.count('"443:443"') == 1  # einziger veröffentlichter Port


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
    assert sites == ["kunde-a-nb.caddy"]  # keine Startseite hier (Front-Proxy)
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
    assert re.search(r"Lese-Token:\s+[0-9a-f]{40}", out.stdout)
    assert re.search(r"Schreib-Token:\s+[0-9a-f]{40}", out.stdout)
    shells = [z for z in host.log.read_text().splitlines() if "manage.py shell" in z]
    # Admin und Gruppen der Anmeldung bei "neu", Lese-Benutzer, Sync-Benutzer (sync-user.sh)
    assert len(shells) == 4


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
