"""Anmelde-Tor Ende zu Ende (1.4.0, ADR 0051): die von deploy/front/front.sh erzeugte
Konfiguration im echten Caddy, je Realm ein echter oauth2-proxy, dazu ein Test-Keycloak (OIDC mit
PKCE, Realm-Rollen, Logout) und Echo-Dienste statt NetBox, Automato und Magister.

Geprüft wird, was ein Browser erlebt: ohne Anmeldung zu Keycloak, danach zurück auf die
ursprüngliche Adresse (samt Abfrage), Benutzer und Rollen kommen bei NetBox/Magister an, gefälschte
Kopfzeilen nie, die NetBox-API mit Token geht ohne Tor, Kunden ohne Realm sind gesperrt, das Portal
nur mit Rolle admin/operator, Abmelden beendet auch die Keycloak-Sitzung.

Braucht Docker, Port 443 und die Images caddy:2-alpine und oauth2-proxy - darum nur mit
AUTOMATO_TEST_TOR=1 (CI: Job docker). Anderes oauth2-proxy-Image: AUTOMATO_TEST_TOR_IMAGE.
"""

import base64
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "front" / "front.sh"
CADDY = "caddy:2-alpine"
NETZ = "automato-front-tor"
PROJEKT = "automato-tor-e2e"
SECRETS = {"vb": "geheim-vb-0123456789", "kunde-a": "geheim-ka-0123456789"}
BENUTZER = {  # Realm-Rollen wie in Keycloak (default-roles-…, offline_access kommen immer mit)
    "alice": {"roles": ["admin"], "email": "alice@example.ch"},
    "bob": {"roles": [], "email": None},  # ohne E-Mail (AD ohne mail, lokaler Benutzer)
    "olga": {"roles": ["operator"], "email": "olga@example.ch"},
}

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTOMATO_TEST_TOR") != "1" or not shutil.which("docker"),
    reason="nur mit AUTOMATO_TEST_TOR=1 und Docker (CI-Job docker)",
)


def _run(*args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), check=check, capture_output=True, text=True, **kw)  # noqa: S603


# --- Test-Keycloak ----------------------------------------------------------------------
class Idp:
    """Kleiner OIDC-Anbieter mit Realms unter /realms/<r>/protocol/openid-connect/…"""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.user = {"vb": "alice", "kunde-a": "alice"}
        self.codes: dict[str, dict] = {}
        self.refresh: dict[str, dict] = {}
        self.logouts: list[dict] = []
        self.server = ThreadingHTTPServer(("0.0.0.0", 0), self._handler())  # noqa: S104 - Container
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def jwks(self) -> dict:
        pub = self.key.public_key().public_numbers()

        def b64(n: int) -> str:
            raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        return {"keys": [{"kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256",
                          "n": b64(pub.n), "e": b64(pub.e)}]}  # fmt: skip

    def tokens(self, realm: str, user: str, nonce: str | None) -> dict:
        now = int(time.time())
        iss = f"{self.base}/realms/{realm}"
        info = BENUTZER[user]
        roles = [*info["roles"], f"default-roles-{realm}", "offline_access"]
        common = {"iss": iss, "sub": f"id-{user}", "iat": now, "exp": now + 300,
                  "preferred_username": user, "azp": "automato-front"}  # fmt: skip
        if info["email"]:
            common |= {"email": info["email"], "email_verified": False}
        access = {**common, "aud": "account", "typ": "Bearer", "realm_access": {"roles": roles},
                  "resource_access": {"account": {"roles": ["manage-account"]}}}  # fmt: skip
        ident = {**common, "aud": "automato-front", "typ": "ID"}
        if nonce:
            ident["nonce"] = nonce
        refresh = secrets.token_urlsafe(16)
        self.refresh[refresh] = {"realm": realm, "user": user}
        sign = {"algorithm": "RS256", "headers": {"kid": "k1"}}
        return {"access_token": jwt.encode(access, self.key, **sign),
                "id_token": jwt.encode(ident, self.key, **sign),
                "refresh_token": refresh, "token_type": "Bearer", "expires_in": 300}  # fmt: skip

    def _handler(self):
        idp = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # leise
                pass

            def _send(self, code: int, body: object = b"", headers: dict | None = None) -> None:
                data = json.dumps(body).encode() if isinstance(body, dict) else body
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if isinstance(body, dict):
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _realm(self) -> tuple[str, str]:
                parts = urllib.parse.urlsplit(self.path).path.split("/")
                return parts[2], parts[-1]  # /realms/<r>/protocol/openid-connect/<ende>

            def do_GET(self) -> None:  # noqa: N802
                url = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(url.query))
                if not url.path.startswith("/realms/"):
                    return self._send(404)
                realm, ende = self._realm()
                if ende == "auth":  # Anmeldung: der aktuelle Benutzer des Realms ist angemeldet
                    code = secrets.token_urlsafe(16)
                    idp.codes[code] = {"realm": realm, "user": idp.user[realm], **q}
                    target = (
                        q["redirect_uri"]
                        + "?"
                        + urllib.parse.urlencode({"code": code, "state": q["state"]})
                    )
                    return self._send(302, headers={"Location": target})
                if ende == "certs":
                    return self._send(200, idp.jwks())
                if ende == "logout":
                    idp.logouts.append({"realm": realm, **q})
                    return self._send(200, b"abgemeldet")
                if ende == "userinfo":
                    user = idp.user[realm]
                    return self._send(200, {"sub": f"id-{user}", "preferred_username": user})
                return self._send(404)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                form = dict(urllib.parse.parse_qsl(self.rfile.read(length).decode()))
                if self.path == "/_user":
                    idp.user[form["realm"]] = form["user"]
                    return self._send(204)
                realm, ende = self._realm()
                if ende != "token":
                    return self._send(404)
                cid, secret = form.get("client_id"), form.get("client_secret")
                auth = self.headers.get("Authorization", "")
                if auth.startswith("Basic "):
                    raw = base64.b64decode(auth[6:]).decode()
                    cid, secret = (urllib.parse.unquote(x) for x in raw.split(":", 1))
                if cid != "automato-front" or secret != SECRETS.get(realm):
                    return self._send(401, {"error": "invalid_client"})
                if form.get("grant_type") == "refresh_token":
                    old = idp.refresh.pop(form.get("refresh_token", ""), None)
                    if not old or old["realm"] != realm:
                        return self._send(400, {"error": "invalid_grant"})
                    return self._send(200, idp.tokens(realm, old["user"], None))
                grant = idp.codes.pop(form.get("code", ""), None)
                if not grant or grant["realm"] != realm:
                    return self._send(400, {"error": "invalid_grant"})
                challenge = (
                    base64.urlsafe_b64encode(
                        hashlib.sha256(form.get("code_verifier", "").encode()).digest()
                    )
                    .rstrip(b"=")
                    .decode()
                )
                if grant.get("code_challenge_method") != "S256" or challenge != grant.get(
                    "code_challenge"
                ):
                    return self._send(400, {"error": "invalid_grant", "pkce": "falsch"})
                return self._send(200, idp.tokens(realm, grant["user"], grant.get("nonce")))

        return Handler


# --- Aufbau -----------------------------------------------------------------------------
def _pki(d: Path) -> None:
    def ossl(*a: str) -> None:
        _run("openssl", *a)

    ossl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=Test-CA",
         "-keyout", str(d / "ca.key"), "-out", str(d / "ca.crt"))  # fmt: skip
    namen = (
        ("mgmt", "DNS:*.mgmt.example.ch"),
        ("mgmt-int", "DNS:mgmt.int.example.ch,DNS:*.mgmt.int.example.ch"),
    )
    for name, sans in namen:
        ossl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={name}",
             "-keyout", str(d / f"{name}.key"), "-out", str(d / f"{name}.csr"))  # fmt: skip
        ext = d / f"{name}.ext"
        ext.write_text(f"subjectAltName={sans}\n")
        ossl("x509", "-req", "-in", str(d / f"{name}.csr"), "-CA", str(d / "ca.crt"),
             "-CAkey", str(d / "ca.key"), "-CAcreateserial", "-days", "2",
             "-extfile", str(ext), "-out", str(d / f"{name}.leaf"))  # fmt: skip
        (d / f"{name}.crt").write_text(
            (d / f"{name}.leaf").read_text() + (d / "ca.crt").read_text()
        )
    for f in d.iterdir():
        f.chmod(0o644)


ECHO = {  # Dienste hinter dem Front-Proxy: zeigen, welcher Benutzer ankommt
    "web-ui": ':5173 {\n\trespond "doc user={http.request.header.X-Automato-User} path={uri}"\n}\n',
    "nb-echo": 'https://:8443 {\n\ttls /pki/mgmt.crt /pki/mgmt.key\n\trespond "nb '
    "user={http.request.header.X-Automato-User} realm={http.request.header.X-Automato-Realm} "
    "groups={http.request.header.X-Automato-Groups} auth={http.request.header.Authorization} "
    'uri={uri}"\n}\n',
    "um-echo": ':8080 {\n\trespond "um user={http.request.header.X-Automato-User} '
    'groups={http.request.header.X-Automato-Groups}"\n}\n',
}


@pytest.fixture(scope="module")
def tor(tmp_path_factory):  # noqa: C901 - ein Aufbau, Schritt für Schritt
    d = tmp_path_factory.mktemp("tor")
    pki = d / "pki"
    pki.mkdir()
    _pki(pki)
    tls = d / "tls"
    tls.mkdir()
    (tls / "upstream-off.caddy").write_text("\n")
    idp = Idp()
    namen: list[str] = []
    netz_neu = _run("docker", "network", "inspect", NETZ, check=False).returncode != 0
    if netz_neu:
        _run("docker", "network", "create", NETZ)
    gateway = json.loads(_run("docker", "network", "inspect", NETZ).stdout)[0]["IPAM"]["Config"][0][
        "Gateway"
    ]
    image = os.environ.get("AUTOMATO_TEST_TOR_IMAGE", "quay.io/oauth2-proxy/oauth2-proxy:v7.15.6")
    (d / "env").write_text(
        "AUTOMATO_FRONT_DOMAIN=mgmt.example.ch\nAUTOMATO_FRONT_DOMAIN_INT=mgmt.int.example.ch\n"
        "AUTOMATO_FRONT_WAF=10.0.40.1/32\nAUTOMATO_FRONT_MGMT=0.0.0.0/0\n"
        "AUTOMATO_FRONT_NETBOX=nb-echo:8443\nAUTOMATO_FRONT_NETBOX_CA=ca.crt\n"
        "AUTOMATO_FRONT_MAGISTER=http://um-echo:8080\nAUTOMATO_FRONT_TOR=an\n"
        f"AUTOMATO_FRONT_TOR_IMAGE={image}\n"
        f"AUTOMATO_OIDC_ISSUER={idp.base}/realms/vb\n"
        f"AUTOMATO_FRONT_KEYCLOAK_INTERN=http://{gateway}:{idp.port}\n"
    )
    stub = d / "docker"
    stub.write_text(
        '#!/usr/bin/env bash\ncase "$*" in *"ps --status running"*) echo proxy ;; esac\n'
    )
    stub.chmod(0o755)
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("AUTOMATO_")},
           "AUTOMATO_ENV_FILE": str(d / "env"), "FRONT_DIR": str(d / "front"),
           "FRONT_PKI": str(pki), "FRONT_SKIP_ROOT": "1", "DOCKER": str(stub)}  # fmt: skip

    def front(*args: str, eingabe: str | None = None) -> None:
        out = subprocess.run(["bash", str(SCRIPT), *args], input=eingabe, env=env,  # noqa: S603, S607
                             capture_output=True, text=True, check=False)  # fmt: skip
        assert out.returncode == 0, out.stdout + out.stderr

    front("kunde", "vb", "Eigene Firma", "--doc", "--um", "--realm", "vb")
    front("kunde", "kunde-a", "Kunde A", "--realm", "kunde-a")
    front("kunde", "kunde-b", "Kunde B")  # ohne Realm: gesperrt
    for realm, geheim in SECRETS.items():
        front("tor", realm, eingabe=geheim + "\n")
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

        def aufloesen(host, *a, **kw):  # *.example.ch → dieser Rechner
            if isinstance(host, str) and host.endswith(".example.ch"):
                host = "127.0.0.1"
            return echt(host, *a, **kw)

        socket.getaddrinfo = aufloesen
        for _ in range(60):
            try:
                r = httpx.get(
                    "https://vb.mgmt.example.ch/",
                    verify=False,  # noqa: S501 - Test-CA
                    timeout=2,
                    trust_env=False,
                )
                if r.status_code in (302, 200):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            logs = _run("docker", "logs", f"{PROJEKT}-front", check=False)
            pytest.fail("Front-Proxy antwortet nicht: " + logs.stdout + logs.stderr)
        yield idp
    finally:
        socket.getaddrinfo = echt
        for c in namen:
            _run("docker", "rm", "-f", c, check=False)
        _run("docker", "compose", "-p", PROJEKT, "-f", str(compose), "down", check=False)
        if netz_neu:
            _run("docker", "network", "rm", NETZ, check=False)
        idp.server.shutdown()


def _browser() -> httpx.Client:
    return httpx.Client(verify=False, follow_redirects=False, timeout=10, trust_env=False)  # noqa: S501


def _folgen(c: httpx.Client, url: str, **kw) -> tuple[httpx.Response, list[str]]:
    """Weiterleitungen folgen wie ein Browser; Rückgabe: letzte Antwort und besuchte Adressen."""
    weg = [url]
    r = c.get(url, **kw)
    while r.status_code in (301, 302, 303, 307, 308):
        url = urllib.parse.urljoin(url, r.headers["location"])
        weg.append(url)
        r = c.get(url)
    return r, weg


def test_ohne_anmeldung_zu_keycloak_und_zurueck(tor):
    with _browser() as c:
        r = c.get("https://vb.mgmt.example.ch/")
        assert r.status_code == 302
        assert r.headers["location"].startswith(
            f"{tor.base}/realms/vb/protocol/openid-connect/auth"
        )
        assert "code_challenge_method=S256" in r.headers["location"]
        r, weg = _folgen(c, "https://vb.mgmt.example.ch/")
        assert r.status_code == 200 and "Eigene Firma" in r.text and "/oauth2/sign_out" in r.text
        assert any(u.startswith("https://vb.mgmt.example.ch/oauth2/callback") for u in weg)


def test_netbox_bekommt_benutzer_mit_realm_und_rollen_nie_gefaelscht(tor):
    with _browser() as c:
        # zuerst eine tiefe Adresse mit Abfrage: nach der Anmeldung genau dorthin zurück
        falsch = {"X-Automato-User": "admin", "X-Auth-Request-Groups": "role:admin"}
        r, _ = _folgen(
            c, "https://vb-nb.mgmt.example.ch/dcim/devices/?site=a&role=b", headers=falsch
        )
        assert r.status_code == 200, r.text
        assert "uri=/dcim/devices/?site=a&role=b" in r.text
        assert "user=alice@vb " in r.text and "realm=vb " in r.text
        assert "groups=automato-lesen,role:admin" in r.text
        # angemeldet, aber mit gefälschten Kopfzeilen: der Benutzer kommt nur vom Tor
        falsch = {"X-Automato-User": "admin", "X-Automato-Groups": "role:admin"}
        r = c.get("https://vb-nb.mgmt.example.ch/x", headers=falsch)
        assert "user=alice@vb " in r.text and "user=admin" not in r.text


def test_netbox_api_mit_token_ohne_tor_und_ohne_benutzer(tor):
    with _browser() as c:
        token = {"Authorization": "Token abc123", "X-Automato-User": "admin"}
        r = c.get("https://vb-nb.mgmt.example.ch/api/dcim/sites/", headers=token)
        assert r.status_code == 200
        assert "auth=Token abc123" in r.text and "user= " in r.text
        # ohne Token: über das Tor
        assert c.get("https://vb-nb.mgmt.example.ch/api/dcim/sites/").status_code == 302


def test_doku_api_ohne_tor_oberflaeche_mit(tor):
    with _browser() as c:
        r = c.get("https://vb-doc.mgmt.example.ch/api/auth/config")
        assert r.status_code == 200 and "doc user=" in r.text
        assert c.get("https://vb-doc.mgmt.example.ch/api/internal/x").status_code == 404
        assert c.get("https://vb-doc.mgmt.example.ch/").status_code == 302


def test_magister_bekommt_benutzer_ohne_email(tor):
    httpx.post(f"{tor.base}/_user", trust_env=False, data={"realm": "vb", "user": "bob"})
    try:
        with _browser() as c:
            r, _ = _folgen(c, "https://vb-um.mgmt.example.ch/")
            assert r.status_code == 200, r.text
            assert "um user=bob@vb " in r.text and "role:admin" not in r.text
    finally:
        httpx.post(f"{tor.base}/_user", trust_env=False, data={"realm": "vb", "user": "alice"})


def test_kunde_ohne_realm_gesperrt_api_mit_token_bleibt(tor):
    with _browser() as c:
        r = c.get("https://kunde-b.mgmt.example.ch/")
        assert r.status_code == 403 and "Anmeldung nicht eingerichtet" in r.text
        assert c.get("https://kunde-b-nb.mgmt.example.ch/dcim/").status_code == 403
        r = c.get("https://kunde-b-nb.mgmt.example.ch/api/x", headers={"Authorization": "Token t"})
        assert r.status_code == 200 and "auth=Token t" in r.text


def test_eigener_realm_je_kunde(tor):
    with _browser() as c:
        r = c.get("https://kunde-a.mgmt.example.ch/")
        assert r.headers["location"].startswith(f"{tor.base}/realms/kunde-a/")
        r, _ = _folgen(c, "https://kunde-a-nb.mgmt.example.ch/")
        assert "user=alice@kunde-a " in r.text and "realm=kunde-a " in r.text


@pytest.mark.parametrize(("benutzer", "code"), [("alice", 200), ("olga", 200), ("bob", 403)])
def test_portal_nur_admin_und_operator(tor, benutzer, code):
    httpx.post(f"{tor.base}/_user", trust_env=False, data={"realm": "vb", "user": benutzer})
    try:
        with _browser() as c:
            r, _ = _folgen(c, "https://start.mgmt.int.example.ch/")
            assert r.status_code == code
            if code == 200:
                assert "Portal" in r.text and "Eigene Firma" in r.text
    finally:
        httpx.post(f"{tor.base}/_user", trust_env=False, data={"realm": "vb", "user": "alice"})


def test_abmelden_beendet_auch_keycloak(tor):
    with _browser() as c:
        r, _ = _folgen(c, "https://vb.mgmt.example.ch/")
        assert r.status_code == 200
        vorher = len(tor.logouts)
        r = c.get("https://vb.mgmt.example.ch/oauth2/sign_out")
        assert r.status_code == 302
        assert len(tor.logouts) == vorher + 1 and tor.logouts[-1].get("id_token_hint")
        assert c.get("https://vb.mgmt.example.ch/").status_code == 302  # wieder abgemeldet
