"""Inventar fuers Cockpit (1.10.0, ADR 0053): GET /inventory nur mit dem Token des Cockpits,
Inhalt aus `topologie.json` des Front-Proxys - gepruefte Werte, nie mehr."""

import json

from app import __version__
from app.config import Settings, get_settings

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
    assert data["automato"] == {"version": __version__, "umgebung": "test",
                                "oberflaeche": "https://automato.int.example.ch"}  # fmt: skip
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
        "realm": None,
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
