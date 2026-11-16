"""Inventar fuers Cockpit (1.10.0, ADR 0053): GET /inventory nur mit dem Token des Cockpits,
Inhalt aus `topologie.json` des Front-Proxys - gepruefte Werte, nie mehr."""

import functools
import hashlib
import json

import pytest
from app import __version__
from app.config import Settings, get_settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

TOKEN = "a" * 64
ISSUER = "https://kc.example.ch/realms/vb"


def _settings(tmp_path, **kw) -> Settings:
    base = {"front_state_dir": str(tmp_path), "cockpit_token": TOKEN, "environment": "test",
            "auth_mode": "oidc", "oidc_issuer": ISSUER, "oidc_audience": "automato-api",
            "oidc_jwks_url": f"{ISSUER}/protocol/openid-connect/certs"}  # fmt: skip
    base.update(kw)
    return Settings(**base)


def _get(client, tmp_path, token: str | None = TOKEN, **kw):
    client.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path, **kw)
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.get("/inventory", headers=headers)


def _topologie(tmp_path, **extra) -> None:
    data = {
        "zeit": "2026-11-13T08:00:00+00:00",
        "vm": {"name": "automato-vm.int.example.ch", "adressen": ["10.0.20.5", "kaputt; rm"]},
        "oberflaeche": "https://automato.int.example.ch",
        "portal": "https://start.mgmt.int.example.ch",
        "domain": "mgmt.example.ch",
        "netbox_host": "10.0.20.10:8444",
        "uebrige": "10.0.20.10",
        "magister": None,
        "cmdb": None,
        "tor": True,
        "kunden": [
            {"kurzname": "kunde-a", "name": "Kunde A", "startseite": "https://kunde-a.mgmt.example.ch",
             "netbox": "https://kunde-a-nb.mgmt.example.ch", "automato": None, "magister": None,
             "cmdb": None, "geheim": "nie"},
            {"name": "ohne Kurzname"},
            "kein Objekt",
        ],
        **extra,
    }  # fmt: skip
    (tmp_path / "topologie.json").write_text(json.dumps(data), encoding="utf-8")


def test_nur_mit_token(client, tmp_path):
    assert _get(client, tmp_path, token=None).status_code == 401
    assert _get(client, tmp_path, token="b" * 64).status_code == 401
    falsch = client.get("/inventory", headers={"Authorization": f"Token {TOKEN}"})
    assert falsch.status_code == 401
    # Anmeldung einer Person zaehlt nicht (Dev-Rollen-Kopf), ohne eingerichtetes Token kein Endpunkt
    client.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path, cockpit_token="")
    aus = client.get(
        "/inventory", headers={"Authorization": f"Bearer {TOKEN}", "X-Dev-Roles": "admin"}
    )
    assert aus.status_code == 404


def test_inventar_aus_der_topologie(client, tmp_path):
    _topologie(tmp_path)
    r = _get(client, tmp_path)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["vertrag"] == 1
    rollen = ["viewer", "triage", "editor", "customer_admin"]
    assert data["automato"] == {"version": __version__, "umgebung": "test",
                                "oberflaeche": "https://automato.int.example.ch",
                                # 1.13.0: Auswahl fuer die Zuordnung (hier ohne Mandanten)
                                "mandanten": [], "rollen": rollen}  # fmt: skip
    assert data["keycloak"] == {"issuer": ISSUER}
    front = data["front"]
    assert front["vm"] == {"name": "automato-vm.int.example.ch", "adressen": ["10.0.20.5"]}
    assert front["portal"] == "https://start.mgmt.int.example.ch"
    assert (front["netbox_host"], front["uebrige"], front["tor"]) == (
        "10.0.20.10:8444",
        "10.0.20.10",
        True,
    )
    # nur die bekannten Felder, nur gueltige Kunden; topologie.json vor 1.11.0 ohne Herkunft
    assert data["kunden"] == [{
        "kurzname": "kunde-a", "name": "Kunde A", "startseite": "https://kunde-a.mgmt.example.ch",
        "netbox": "https://kunde-a-nb.mgmt.example.ch", "automato": None, "magister": None,
        "cmdb": None, "quelle": None, "werkzeuge": None, "zugriff": None, "netze": [],
        "realm": None, "automato_zugeordnet": None, "zuordnung": None,
        "kacheln": None, "kacheln_von": None,
    }]  # fmt: skip


def test_kunden_mit_herkunft_werkzeugen_und_zugriff(client, tmp_path):
    """1.11.0 (ADR 0054, Gegenrichtung der Freigaben): je Kunde Herkunft, Werkzeuge, Zugriff,
    Netze und Realm - nur bekannte bzw. gueltige Werte, sonst null/leer."""
    basis = {"startseite": None, "netbox": None, "automato": None, "magister": None,
             "cmdb": None}  # fmt: skip
    _topologie(tmp_path, kunden=[
        {"kurzname": "kunde-a", "name": "A", **basis, "quelle": "cockpit",
         "werkzeuge": ["nb", "doc", "nb", "ssh", {"x": 1}], "zugriff": "netze",
         "netze": ["203.0.113.0/24", "203.0.113.7", "kein-netz", 5, "2001:db8::/48"],
         "realm": "kunde-a"},
        {"kurzname": "kunde-b", "name": "B", **basis, "quelle": "fremd", "werkzeuge": "doc",
         "zugriff": "intern", "netze": ["10.0.0.0/8"], "realm": "master"},
        {"kurzname": "kunde-c", "name": "C", **basis, "quelle": "befehl", "werkzeuge": [],
         "zugriff": "vpn", "netze": ["10.0.0.0/8"], "realm": "a b"},
    ])  # fmt: skip
    kunden = {k["kurzname"]: k for k in _get(client, tmp_path).json()["kunden"]}
    a, b, c = kunden["kunde-a"], kunden["kunde-b"], kunden["kunde-c"]
    assert (a["quelle"], a["werkzeuge"], a["zugriff"], a["realm"]) == (
        "cockpit", ["doc", "nb"], "netze", "kunde-a")  # fmt: skip
    assert a["netze"] == ["203.0.113.0/24", "203.0.113.7/32", "2001:db8::/48"]
    # unbekannte Herkunft, keine Liste, Netze nur bei "netze", nie der Realm "master"
    assert (b["quelle"], b["werkzeuge"], b["zugriff"], b["netze"], b["realm"]) == (
        None, None, "intern", [], None)  # fmt: skip
    assert (c["quelle"], c["werkzeuge"], c["zugriff"], c["netze"], c["realm"]) == (
        "befehl", [], None, [], None)  # fmt: skip


def test_ohne_front_proxy_nur_automato(client, tmp_path):
    r = _get(client, tmp_path, auth_mode="dev")
    data = r.json()
    assert r.status_code == 200 and data["front"] is None and data["kunden"] == []
    assert data["keycloak"] == {"issuer": None} and data["automato"]["oberflaeche"] is None
    (tmp_path / "topologie.json").write_text("kein json", encoding="utf-8")
    assert _get(client, tmp_path).json()["front"] is None


# --- Empfangsschluessel und Client-Secrets je Realm (1.13.0, ADR 0055) ------------------------


def _schluessel(key) -> tuple[str, str]:
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    pub = key.public_key()
    pem = pub.public_bytes(serialization.Encoding.PEM, spki).decode()
    der = pub.public_bytes(serialization.Encoding.DER, spki)
    return pem, hashlib.sha256(der).hexdigest()


@functools.cache
def _rsa(bits: int):
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def test_empfangsschluessel_und_realms_des_tors(client, tmp_path):
    pem, kennung = _schluessel(_rsa(3072))
    _topologie(tmp_path, tor_empfang={"alg": "RSA-OAEP-256", "pem": pem, "kennung": kennung,
                                      "privat": "nie"})  # fmt: skip
    (tmp_path / "status.json").write_text(json.dumps({"ok": True, "tor": {
        "an": True, "realms_bereit": ["kunde-a", "vb", "master", "a b", 5],
        "realms_ohne_secret": ["kunde-b"], "vom_cockpit": ["kunde-a"],
        "verworfen": [{"realm": "kunde-c", "grund": "format"}]}}))  # fmt: skip
    front = _get(client, tmp_path).json()["front"]
    assert front["tor_empfang"] == {"alg": "RSA-OAEP-256", "pem": pem, "kennung": kennung}
    assert front["tor_realms"] == {"bereit": ["kunde-a", "vb"], "ohne_secret": ["kunde-b"],
                                   "vom_cockpit": ["kunde-a"]}  # fmt: skip


def test_ohne_status_bzw_aelterer_status(client, tmp_path):
    _topologie(tmp_path)
    front = _get(client, tmp_path).json()["front"]
    assert front["tor_empfang"] is None and front["tor_realms"] is None
    # status.json vor 1.13.0: ohne vom_cockpit
    alt = {"tor": {"an": True, "realms_bereit": ["vb"], "realms_ohne_secret": []}}
    (tmp_path / "status.json").write_text(json.dumps(alt))
    assert _get(client, tmp_path).json()["front"]["tor_realms"] == {
        "bereit": ["vb"], "ohne_secret": [], "vom_cockpit": []}  # fmt: skip


def _ec_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1()).public_key()
    return key.public_bytes(serialization.Encoding.PEM,
                            serialization.PublicFormat.SubjectPublicKeyInfo).decode()  # fmt: skip


@pytest.mark.parametrize(
    "fall",
    ["tor_aus", "kennung_falsch", "kennung_gross", "rsa_2048", "ec", "kein_pem", "zu_lang", "alg",
     "privat"],
)  # fmt: skip
def test_empfangsschluessel_nur_geprueft(client, tmp_path, fall):
    """Nur ein oeffentlicher RSA-Schluessel (ab 3072 Bit), dessen Kennung stimmt - sonst null."""
    pem, kennung = _schluessel(_rsa(3072))
    empfang, tor = {"alg": "RSA-OAEP-256", "pem": pem, "kennung": kennung}, True
    if fall == "tor_aus":
        tor = False
    elif fall == "kennung_falsch":
        empfang["kennung"] = "0" * 64
    elif fall == "kennung_gross":
        empfang["kennung"] = kennung.upper()
    elif fall == "rsa_2048":
        empfang["pem"], empfang["kennung"] = _schluessel(_rsa(2048))
    elif fall == "ec":
        empfang["pem"] = _ec_pem()
    elif fall == "kein_pem":
        empfang["pem"] = "kein Schluessel"
    elif fall == "zu_lang":
        empfang["pem"] = pem + "\n" * 400
    elif fall == "alg":
        empfang["alg"] = "RSA1_5"
    elif fall == "privat":
        empfang["pem"] = _rsa(3072).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()  # fmt: skip
    _topologie(tmp_path, tor=tor, tor_empfang=empfang)
    assert _get(client, tmp_path).json()["front"]["tor_empfang"] is None


# --- Auswahl und Stand der Zuordnung fuers Cockpit (1.13.0, ADR 0054 Nachtrag) ---------------


def test_mandanten_rollen_und_zuordnung_je_kunde(client, tmp_path, session_factory):
    from app.models import Customer, IpamType, PortalCustomer, Tenant
    from app.models.tenant import DnsType

    with session_factory() as s:
        ids = {}
        for name in ("Zentrum", "Rechenzentrum"):
            t = Tenant(name=name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
            s.add(t)
            s.flush()
            ids[name] = t.id
        kunden = [Customer(tenant_id=ids["Rechenzentrum"], name=n) for n in ("Kunde B", "Kunde A")]
        s.add_all(kunden)
        s.flush()
        a, b = kunden[1].id, kunden[0].id
        s.add_all([
            PortalCustomer(short_name="kunde-a", name="A", doc=True, realm="kunde-a",
                           tenant_id=ids["Rechenzentrum"], customer_ids=[b, a],
                           default_role="triage", scope_source="cockpit", links=[]),
            PortalCustomer(short_name="kunde-b", name="B", doc=True, realm="kunde-b",
                           tenant_id=ids["Zentrum"], customer_ids=[], links=[]),
            PortalCustomer(short_name="kunde-c", name="C", doc=False, links=[], customer_ids=[]),
        ])  # fmt: skip
        s.commit()
    basis = {"startseite": None, "netbox": None, "automato": None, "magister": None,
             "cmdb": None}  # fmt: skip
    namen = ("kunde-a", "kunde-b", "kunde-c", "kunde-d")
    _topologie(tmp_path, kunden=[{"kurzname": k, "name": k, **basis} for k in namen])
    data = _get(client, tmp_path).json()
    assert data["automato"]["rollen"] == ["viewer", "triage", "editor", "customer_admin"]
    assert data["automato"]["mandanten"] == [
        {"id": ids["Rechenzentrum"], "name": "Rechenzentrum",
         "kunden": [{"id": a, "name": "Kunde A"}, {"id": b, "name": "Kunde B"}]},
        {"id": ids["Zentrum"], "name": "Zentrum", "kunden": []},
    ]  # fmt: skip
    kunden = {k["kurzname"]: k for k in data["kunden"]}
    assert kunden["kunde-a"]["automato_zugeordnet"] is True
    assert kunden["kunde-a"]["zuordnung"] == {"mandant": ids["Rechenzentrum"],
                                              "kunden": sorted([a, b]), "rolle": "triage",
                                              "von": "cockpit"}  # fmt: skip
    assert kunden["kunde-b"]["automato_zugeordnet"] is False  # Mandant ohne Kunden
    assert kunden["kunde-b"]["zuordnung"]["von"] == "automato"
    assert kunden["kunde-c"]["automato_zugeordnet"] is None  # ohne Doku
    assert (kunden["kunde-d"]["automato_zugeordnet"], kunden["kunde-d"]["zuordnung"]) == (
        None, None)  # fmt: skip


# --- Kacheln der Startseite fuers Cockpit (1.13.0, ADR 0054 zweiter Nachtrag) -----------------


def test_kacheln_je_kunde(client, tmp_path, session_factory):
    from app.models import PortalCustomer

    alt = [
        {"title": "Handbuch", "url": "https://h.example.ch", "text": "Doku"},
        # aeltere Eintraege (vor 1.13.0 bis 80 Zeichen, \r erlaubt): auf die Grenzen gekuerzt
        {"title": "T" * 70, "url": "https://t.example.ch", "text": "a\rb"},
        {"title": "kaputt", "url": "http://k.example.ch", "text": ""},  # faellt weg
        "kein Objekt",
    ]
    with session_factory() as s:
        s.add_all([
            PortalCustomer(short_name="kunde-a", name="A", links=alt, customer_ids=[]),
            PortalCustomer(short_name="kunde-b", name="B", links=[], customer_ids=[],
                           source="cockpit", links_source="cockpit"),
        ])  # fmt: skip
        s.commit()
    basis = {"startseite": None, "netbox": None, "automato": None, "magister": None,
             "cmdb": None}  # fmt: skip
    _topologie(tmp_path, kunden=[{"kurzname": k, "name": k, **basis}
                                 for k in ("kunde-a", "kunde-b", "kunde-c")])  # fmt: skip
    kunden = {k["kurzname"]: k for k in _get(client, tmp_path).json()["kunden"]}
    assert kunden["kunde-a"]["kacheln"] == [
        {"titel": "Handbuch", "adresse": "https://h.example.ch", "text": "Doku"},
        {"titel": "T" * 60, "adresse": "https://t.example.ch", "text": "ab"},
    ]
    assert kunden["kunde-a"]["kacheln_von"] == "automato"
    assert (kunden["kunde-b"]["kacheln"], kunden["kunde-b"]["kacheln_von"]) == ([], "cockpit")
    # ohne Eintrag im Kunden-Portal (z. B. per Befehl angelegt): unbekannt
    assert (kunden["kunde-c"]["kacheln"], kunden["kunde-c"]["kacheln_von"]) == (None, None)
