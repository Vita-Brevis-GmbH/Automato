"""Anmelde-Tor gegen einen **echten Keycloak** (ADR 0051, 1.12.2): die von deploy/front/front.sh
erzeugte Konfiguration im echten Caddy, je Realm ein echter oauth2-proxy und Keycloak selbst mit
Realms aus deploy/keycloak/make_realm.py (Vorlage realm-automato.json, Client automato-front).

`test_front_tor_e2e.py` prüft das Tor gegen einen nachgebauten Keycloak. Mehrere Fehler kamen
trotzdem erst beim Anwender ans Licht (Anwender 2026-11-16: „die ganze Kette als automatischer
Test … bei jeder Änderung“). Hier erlebt ein Browser die Kette wie in Betrieb: Startseite →
Formular von Keycloak → zurück auf die Adresse samt Abfrage; Realm-Rollen aus dem echten Token
(Portal nur admin/operator); kein Anmelden über Realms hinweg; „Abmelden“ der Startseite und
NetBox `/logout/` beenden die Sitzung in Keycloak über `--backend-logout-url` - danach zeigt
Keycloak wieder sein Formular, und er kennt keine Sitzung des Benutzers mehr.

Braucht Docker, Port 443, die Images wie test_front_tor_e2e.py und einen Keycloak (nur Test, mit
Realms zum Wegwerfen):

* `AUTOMATO_TEST_KEYCLOAK_URL` - Adresse für Browser und Issuer (z. B. `http://127.0.0.1:8180`);
  Keycloak läuft mit genau diesem `--hostname`, wie in Betrieb, sonst trägt das Token die
  Adresse, über die das Tor es holte, und oauth2-proxy lehnt den Issuer ab;
* `AUTOMATO_TEST_KEYCLOAK_INTERN` - Adresse für das Tor im Docker-Netz `automato-front-tor` (CI:
  `http://keycloak:8080`, Container mit Alias im Netz); ohne Angabe das Gateway des Netzes;
* `AUTOMATO_TEST_KEYCLOAK_ADMIN`/`…_PASSWORD` - Admin des Test-Keycloaks (Vorgabe: Testwerte
  der CI).

Nur mit AUTOMATO_TEST_TOR=1 und AUTOMATO_TEST_KEYCLOAK_URL (CI: Job docker).
"""

import html
import importlib.util
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
from test_front_tor_e2e import CADDY, ECHO, NETZ, REPO, SCRIPT, _pki, _run

KC_URL = os.environ.get("AUTOMATO_TEST_KEYCLOAK_URL", "").rstrip("/")
KC_INTERN = os.environ.get("AUTOMATO_TEST_KEYCLOAK_INTERN", "").rstrip("/")
# nur Testwerte für einen Wegwerf-Keycloak (CI: Container je Lauf), kein Geheimnis
KC_ADMIN = os.environ.get("AUTOMATO_TEST_KEYCLOAK_ADMIN", "admin")
KC_ADMIN_PASSWORT = os.environ.get("AUTOMATO_TEST_KEYCLOAK_ADMIN_PASSWORD", "nur-test-nur-test")
PASSWORT = "Test-Passwort-0123!"  # Testbenutzer, erfüllt die Passwort-Regeln der Vorlage
DOMAIN = "mgmt.example.ch"
PORTAL = "https://start.mgmt.int.example.ch"
PROJEKT = "automato-tor-kc-e2e"
GEHEIM = {"vb": "geheim-vb-kc-0123456789", "kunde-a": "geheim-ka-kc-0123456789"}  # nur Test

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTOMATO_TEST_TOR") != "1" or not shutil.which("docker") or not KC_URL,
    reason="nur mit AUTOMATO_TEST_TOR=1, Docker und AUTOMATO_TEST_KEYCLOAK_URL (CI-Job docker)",
)

_spec = importlib.util.spec_from_file_location(
    "make_realm", REPO / "deploy" / "keycloak" / "make_realm.py"
)
make_realm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_realm)  # type: ignore[union-attr]


# --- Keycloak ---------------------------------------------------------------------------
class KcAdmin:
    """admin-cli im Realm master: legt die Test-Realms an und sieht nach, was Keycloak weiss."""

    def __init__(self, basis: str) -> None:
        self.basis = basis
        self.http = httpx.Client(timeout=30, trust_env=False)
        self._token, self._bis = "", 0.0

    def warten(self) -> None:
        for _ in range(120):
            try:
                if self.http.get(f"{self.basis}/realms/master").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        pytest.fail(f"Keycloak unter {self.basis} antwortet nicht")

    def rufen(self, methode: str, pfad: str, **kw) -> httpx.Response:
        if time.monotonic() > self._bis:  # Token im master gilt nur 60 s
            r = self.http.post(
                f"{self.basis}/realms/master/protocol/openid-connect/token",
                data={"grant_type": "password", "client_id": "admin-cli", "username": KC_ADMIN,
                      "password": KC_ADMIN_PASSWORT},
            )  # fmt: skip
            r.raise_for_status()
            self._token = r.json()["access_token"]
            self._bis = time.monotonic() + int(r.json()["expires_in"]) - 10
        kopf = {"Authorization": f"Bearer {self._token}"}
        return self.http.request(methode, f"{self.basis}/admin/realms{pfad}", headers=kopf, **kw)

    def sitzungen(self, realm: str, benutzer: str) -> set[str]:
        """Kennungen der Sitzungen des Benutzers, die Keycloak kennt."""
        r = self.rufen("GET", f"/{realm}/users", params={"username": benutzer, "exact": "true"})
        (konto,) = r.json()
        r = self.rufen("GET", f"/{realm}/users/{konto['id']}/sessions")
        return {s["id"] for s in r.json()}


def realm_einrichten(
    admin: KcAdmin, realm: str, kunde: str, benutzer: dict[str, list[str]], portal: bool = False
) -> None:
    """Realm wie in Betrieb aus der Vorlage (make_realm.py), dazu Testbenutzer mit Passwort.

    Das Geheimnis von automato-front erzeugt Keycloak in Betrieb selbst; hier setzt es der Test,
    damit front.sh tor es bekommt."""
    vorlage = json.loads((REPO / "deploy" / "keycloak" / "realm-automato.json").read_text())
    rep = make_realm.build_realm(
        vorlage, realm, [f"https://{kunde}-doc.{DOMAIN}"], f"Test {kunde}", False,
        make_realm.front_urls(kunde, DOMAIN, [PORTAL] if portal else []),
    )  # fmt: skip
    for client in rep["clients"]:
        if client["clientId"] == make_realm.FRONT_CLIENT:
            client["secret"] = GEHEIM[kunde]
    for name, rollen in benutzer.items():
        rep["users"].append({
            "username": name, "enabled": True, "email": f"{name}@example.ch",
            "emailVerified": True, "firstName": name.title(), "lastName": "Test",
            "realmRoles": rollen,
            "credentials": [{"type": "password", "value": PASSWORT, "temporary": False}],
        })  # fmt: skip
    admin.rufen("DELETE", f"/{realm}")  # Rest eines abgebrochenen Laufs
    r = admin.rufen("POST", "", json=rep)
    assert r.status_code == 201, r.text


@dataclass
class Kette:
    """Was die Tests brauchen: Keycloak (Adresse des Browsers) und die zwei Realms."""

    kc: str
    admin: KcAdmin
    vb: str  # Realm der Plattform: Startseite vb, Portal start.<intern>
    ka: str  # Realm von Kunde A


def kette_realms(admin: KcAdmin) -> Kette:
    """Zwei Realms mit eigenem Namen je Lauf: vb (tester = admin, leser ohne Rolle), kunde-a."""
    nr = secrets.token_hex(3)
    k = Kette(admin.basis, admin, f"tor-vb-{nr}", f"tor-ka-{nr}")
    realm_einrichten(admin, k.vb, "vb", {"tester": ["admin"], "leser": []}, portal=True)
    realm_einrichten(admin, k.ka, "kunde-a", {"kunde": []})
    return k


# --- Browser ----------------------------------------------------------------------------
class Browser:
    """Wie ein Browser: folgt Weiterleitungen, füllt das Formular von Keycloak aus. Keycloak setzt
    seine Cookies mit Secure; ein Browser schickt sie an 127.0.0.1 trotzdem (sicherer Kontext),
    httpx über http nicht - darum ohne Secure im Cookie-Jar."""

    def __init__(self) -> None:
        self.c = httpx.Client(verify=False, follow_redirects=False, timeout=15, trust_env=False)  # noqa: S501 - Test-CA

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *_: object) -> None:
        self.c.close()

    def _rufen(self, methode: str, url: str, **kw) -> httpx.Response:
        r = self.c.request(methode, url, **kw)
        for cookie in self.c.cookies.jar:
            cookie.secure = False
        return r

    def get(self, url: str, **kw) -> httpx.Response:
        return self._rufen("GET", url, **kw)

    def folgen(self, url: str, methode: str = "GET", **kw) -> tuple[httpx.Response, list[str]]:
        """Weiterleitungen folgen; letzte Antwort und besuchte Adressen."""
        weg = [url]
        r = self._rufen(methode, url, **kw)
        while r.status_code in (301, 302, 303, 307, 308):
            url = urllib.parse.urljoin(url, r.headers["location"])
            weg.append(url)
            r = self._rufen("GET", url)
        return r, weg

    def anmelden(self, url: str, benutzer: str) -> tuple[httpx.Response, list[str]]:
        """`url` aufrufen, im Formular von Keycloak anmelden, zurück zur Adresse."""
        r, weg = self.folgen(url)
        assert formular(r), (weg, r.status_code, r.text[:300])
        aktion = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', r.text)
        assert aktion, r.text[:500]
        daten = {"username": benutzer, "password": PASSWORT, "credentialId": ""}
        r, zurueck = self.folgen(html.unescape(aktion.group(1)), "POST", data=daten)
        return r, weg + zurueck


def sitzung(weg: list[str]) -> str:
    """Sitzung in Keycloak, aus der Rückkehr zum Tor (`/oauth2/callback?…&session_state=…`)."""
    for url in weg:
        if "/oauth2/callback?" in url:
            (wert,) = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["session_state"]
            return wert
    raise AssertionError(f"keine Rückkehr zum Tor: {weg}")


def formular(r: httpx.Response) -> bool:
    """Zeigt Keycloak sein Anmeldeformular (keine bestehende Sitzung)?"""
    return r.status_code == 200 and 'id="kc-form-login"' in r.text


# --- Aufbau mit Docker (CI-Job docker) ----------------------------------------------------
@pytest.fixture(scope="module")
def kette(tmp_path_factory) -> Iterator[Kette]:
    admin = KcAdmin(KC_URL)
    admin.warten()
    k = kette_realms(admin)
    try:
        yield from _aufbau(k, tmp_path_factory.mktemp("tor-kc"))
    finally:
        for realm in (k.vb, k.ka):
            admin.rufen("DELETE", f"/{realm}")


def _aufbau(k: Kette, d) -> Iterator[Kette]:
    """front.sh mit den Realms von `k`, Tor und Front-Proxy in Docker wie test_front_tor_e2e.py."""
    pki = d / "pki"
    pki.mkdir()
    _pki(pki)
    tls = d / "tls"
    tls.mkdir()
    (tls / "upstream-off.caddy").write_text("\n")
    namen: list[str] = []
    netz_neu = _run("docker", "network", "inspect", NETZ, check=False).returncode != 0
    if netz_neu:
        _run("docker", "network", "create", NETZ)
    gateway = json.loads(_run("docker", "network", "inspect", NETZ).stdout)[0]["IPAM"]["Config"][0][
        "Gateway"
    ]
    intern = KC_INTERN or f"http://{gateway}:{urllib.parse.urlsplit(KC_URL).port or 80}"
    image = os.environ.get("AUTOMATO_TEST_TOR_IMAGE", "quay.io/oauth2-proxy/oauth2-proxy:v7.15.6")
    (d / "env").write_text(
        f"AUTOMATO_FRONT_DOMAIN={DOMAIN}\nAUTOMATO_FRONT_DOMAIN_INT=mgmt.int.example.ch\n"
        "AUTOMATO_FRONT_WAF=10.0.40.1/32\nAUTOMATO_FRONT_MGMT=0.0.0.0/0\n"
        "AUTOMATO_FRONT_NETBOX=nb-echo:8443\nAUTOMATO_FRONT_NETBOX_CA=ca.crt\n"
        "AUTOMATO_FRONT_MAGISTER=http://um-echo:8080\nAUTOMATO_FRONT_TOR=an\n"
        f"AUTOMATO_FRONT_TOR_IMAGE={image}\n"
        f"AUTOMATO_OIDC_ISSUER={KC_URL}/realms/{k.vb}\n"
        f"AUTOMATO_FRONT_KEYCLOAK_INTERN={intern}\n"
    )
    stub = d / "docker"
    stub.write_text(
        '#!/usr/bin/env bash\ncase "$*" in *"ps --status running"*) echo proxy ;; esac\n'
    )
    stub.chmod(0o755)
    env = {**{n: v for n, v in os.environ.items() if not n.startswith("AUTOMATO_")},
           "AUTOMATO_ENV_FILE": str(d / "env"), "FRONT_DIR": str(d / "front"),
           "FRONT_PKI": str(pki), "FRONT_SKIP_ROOT": "1", "DOCKER": str(stub)}  # fmt: skip

    def front(*args: str, eingabe: str | None = None) -> None:
        out = subprocess.run(["bash", str(SCRIPT), *args], input=eingabe, env=env,  # noqa: S603, S607
                             capture_output=True, text=True, check=False)  # fmt: skip
        assert out.returncode == 0, out.stdout + out.stderr

    front("kunde", "vb", "Eigene Firma", "--doc", "--um", "--realm", k.vb)
    front("kunde", "kunde-a", "Kunde A", "--realm", k.ka)
    front("tor", k.vb, eingabe=GEHEIM["vb"] + "\n")
    front("tor", k.ka, eingabe=GEHEIM["kunde-a"] + "\n")
    gen = d / "front" / "generated"
    compose = gen / "tor" / "compose.yml"
    echt = socket.getaddrinfo
    try:
        _run("docker", "compose", "-p", PROJEKT, "-f", str(compose), "up", "-d")
        for name, conf in ECHO.items():
            f = d / f"{name}.caddy"
            f.write_text(conf)
            cname = f"{PROJEKT}-{name}"
            namen.append(cname)
            _run("docker", "run", "-d", "--name", cname, "--network", NETZ, "--network-alias", name,
                 "-v", f"{f}:/etc/caddy/Caddyfile:ro", "-v", f"{pki}:/pki:ro", CADDY)  # fmt: skip
        namen.append(f"{PROJEKT}-front")
        _run("docker", "run", "-d", "--name", f"{PROJEKT}-front", "--network", NETZ,
             "-p", "127.0.0.1:443:443",
             "-e", "AUTOMATO_UI_HOST=automato.int.example.ch", "-e", "AUTOMATO_UI_ALLOW=0.0.0.0/0",
             "-e", "AUTOMATO_FRONT_WAF=10.0.40.1/32",
             "-v", f"{REPO / 'deploy' / 'proxy' / 'Caddyfile'}:/etc/caddy/Caddyfile:ro",
             "-v", f"{gen}:/etc/caddy/front:ro", "-v", f"{gen / 'www'}:/srv/front:ro",
             "-v", f"{pki}:/etc/caddy/pki:ro", "-v", f"{tls}:/etc/caddy/automato-tls:ro",
             CADDY)  # fmt: skip
        socket.getaddrinfo = aufloesen(echt)
        bereit(lambda: _run("docker", "logs", f"{PROJEKT}-front", check=False).stderr)
        yield k
    finally:
        socket.getaddrinfo = echt
        for c in namen:
            _run("docker", "rm", "-f", c, check=False)
        _run("docker", "compose", "-p", PROJEKT, "-f", str(compose), "down", check=False)
        if netz_neu:
            _run("docker", "network", "rm", NETZ, check=False)


def aufloesen(echt):
    """*.example.ch → dieser Rechner (Front-Proxy auf 127.0.0.1:443)."""

    def neu(host, *a, **kw):
        if isinstance(host, str) and host.endswith(".example.ch"):
            host = "127.0.0.1"
        return echt(host, *a, **kw)

    return neu


def bereit(protokoll) -> None:
    """Warten, bis der Front-Proxy die Startseite über das Tor ausliefert."""
    for _ in range(60):
        try:
            r = httpx.get(f"https://vb.{DOMAIN}/", verify=False, timeout=2, trust_env=False)  # noqa: S501 - Test-CA
            if r.status_code == 302:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    pytest.fail("Front-Proxy antwortet nicht: " + protokoll())


# --- Tests ------------------------------------------------------------------------------
def test_anmelden_ueber_keycloak_und_zurueck(kette):
    with Browser() as b:
        r = b.get(f"https://vb.{DOMAIN}/")
        assert r.status_code == 302
        anmeldung = f"{kette.kc}/realms/{kette.vb}/protocol/openid-connect/auth"
        assert r.headers["location"].startswith(anmeldung)
        assert "code_challenge_method=S256" in r.headers["location"]
        r, weg = b.anmelden(f"https://vb.{DOMAIN}/", "tester")
        assert r.status_code == 200 and "Eigene Firma" in r.text and "/oauth2/sign_out" in r.text
        assert any(u.startswith(f"https://vb.{DOMAIN}/oauth2/callback") for u in weg)
        assert weg[-1] == f"https://vb.{DOMAIN}/"
        # andere Adresse, gleicher Realm: Keycloak kennt die Sitzung, kein Formular - zurück
        # genau auf die Adresse samt Abfrage, Benutzer und Rollen aus dem echten Token
        r, weg = b.folgen(f"https://vb-nb.{DOMAIN}/dcim/devices/?site=a&role=b")
        assert r.status_code == 200 and any(u.startswith(anmeldung) for u in weg), weg
        assert "uri=/dcim/devices/?site=a&role=b" in r.text
        assert f"user=tester@{kette.vb} " in r.text and f"realm={kette.vb} " in r.text
        assert "groups=automato-lesen," in r.text and "role:admin" in r.text
        assert sitzung(weg) in kette.admin.sitzungen(kette.vb, "tester")


@pytest.mark.parametrize(("benutzer", "code"), [("tester", 200), ("leser", 403)])
def test_portal_nur_mit_rolle_aus_keycloak(kette, benutzer, code):
    """--allowed-role gegen die Realm-Rollen im echten Token (tester = admin, leser ohne)."""
    with Browser() as b:
        r, _ = b.anmelden(f"{PORTAL}/", benutzer)
        assert r.status_code == code, r.text[:300]
        if code == 200:
            assert "Portal" in r.text


def test_eigener_realm_je_kunde(kette):
    with Browser() as b:
        b.anmelden(f"https://vb.{DOMAIN}/", "tester")
        # angemeldet im Realm vb heisst nicht angemeldet bei Kunde A: Formular seines Realms
        r, weg = b.folgen(f"https://kunde-a.{DOMAIN}/")
        assert formular(r) and weg[1].startswith(f"{kette.kc}/realms/{kette.ka}/"), weg
        # tester gibt es im Realm von Kunde A nicht; kunde schon
        r, _ = b.anmelden(f"https://kunde-a-nb.{DOMAIN}/", "kunde")
        assert r.status_code == 200, r.text[:300]
        assert f"user=kunde@{kette.ka} " in r.text and f"realm={kette.ka} " in r.text


def test_abmelden_beendet_die_sitzung_in_keycloak(kette):
    """1.12.2: der Link der Startseite - das Tor löscht sein Cookie, beendet die Sitzung in
    Keycloak selbst (--backend-logout-url) und zeigt „Abgemeldet“ ausserhalb des Tors."""
    with Browser() as b:
        r, weg = b.anmelden(f"https://vb.{DOMAIN}/", "tester")
        assert r.status_code == 200
        sid = sitzung(weg)
        assert sid in kette.admin.sitzungen(kette.vb, "tester")
        link = html.unescape(re.search(r'href="(/oauth2/sign_out[^"]*)"', r.text).group(1))
        r, weg = b.folgen(urllib.parse.urljoin(f"https://vb.{DOMAIN}/", link))
        assert weg[-1] == f"https://vb.{DOMAIN}/abgemeldet", weg
        assert r.status_code == 200 and "Abgemeldet" in r.text
        assert sid not in kette.admin.sitzungen(kette.vb, "tester")  # auch in Keycloak abgemeldet
        # wieder auf die Startseite: zu Keycloak, und der fragt wieder nach dem Passwort
        r = b.get(f"https://vb.{DOMAIN}/")
        assert r.status_code == 302 and r.headers["location"].startswith(kette.kc)
        r, weg = b.folgen(f"https://vb.{DOMAIN}/")
        assert formular(r), (weg, r.status_code)


def test_netbox_abmelden(kette):
    """NetBox /logout/ geht über dasselbe Abmelden des Tors auf die Seite „Abgemeldet“."""
    with Browser() as b:
        r, weg = b.anmelden(f"https://vb-nb.{DOMAIN}/", "tester")
        assert r.status_code == 200 and f"user=tester@{kette.vb} " in r.text
        sid = sitzung(weg)
        assert sid in kette.admin.sitzungen(kette.vb, "tester")
        r = b.get(f"https://vb-nb.{DOMAIN}/logout/")
        assert r.status_code == 302 and "abgemeldet" in r.headers["location"]
        r, weg = b.folgen(f"https://vb-nb.{DOMAIN}/logout/")
        assert weg[-1] == f"https://vb.{DOMAIN}/abgemeldet" and r.status_code == 200, weg
        assert sid not in kette.admin.sitzungen(kette.vb, "tester")
        r, _ = b.folgen(f"https://vb-nb.{DOMAIN}/")
        assert formular(r)
