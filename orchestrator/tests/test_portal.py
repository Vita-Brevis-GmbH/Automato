"""Kunden-Portal (1.3.0, ADR 0050): Pflege in der Oberflaeche, Soll-Stand fuer den Host-Agenten
und Anmeldung je Kunden-Realm (Ausschnitt nur aus dem Portal, nie aus dem Token)."""

import hashlib
import json

import jwt
import pytest
from app.auth import realms
from app.config import Settings, get_settings
from app.models import AuditLog, Customer, IpamType, PortalCustomer, Tenant
from app.models.tenant import DnsType
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://kc.example.ch/realms/vb"


def _settings(tmp_path, **kw) -> Settings:
    base = {"front_state_dir": str(tmp_path), "front_domain": "mgmt.example.ch"}
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def portal_client(client, tmp_path):
    client.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path)
    realms.invalidate()
    return client


def _tenant(session, name: str) -> tuple[int, int]:
    t = Tenant(name=name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    session.add(t)
    session.flush()
    c = Customer(tenant_id=t.id, name=f"Kunde {name}")
    session.add(c)
    session.flush()
    return t.id, c.id


def test_kunde_anlegen_schreibt_soll_stand(portal_client, session_factory, tmp_path):
    with session_factory() as s:
        tid, cid = _tenant(s, "dc")
        s.commit()
    r = portal_client.post("/portal/customers", json={
        "short_name": "kunde-a", "name": "Kunde A", "doc": True,
        "links": [{"title": "Handbuch", "url": "https://handbuch.example.ch", "text": "Doku"}],
        "realm": "kunde-a", "tenant_id": tid, "customer_ids": [cid],
    })  # fmt: skip
    assert r.status_code == 201, r.text
    body = r.json()
    assert (
        body["addresses"]["doc"] == "https://kunde-a-doc.mgmt.example.ch" and body["note"] is None
    )
    soll = json.loads((tmp_path / "soll.json").read_text(encoding="utf-8"))
    assert soll["kunden"] == [{
        "kurzname": "kunde-a", "name": "Kunde A", "doc": True, "um": False, "cmdb": False,
        "realm": "kunde-a",  # 1.4.0: das Anmelde-Tor fragt im Realm des Kunden (ADR 0051)
        "links": [{"title": "Handbuch", "url": "https://handbuch.example.ch", "text": "Doku"}],
    }]  # fmt: skip
    # Mandant, Kunden und Rolle bleiben in Automato
    assert "tenant" not in json.dumps(soll) and "customer_ids" not in json.dumps(soll)

    listing = portal_client.get("/portal/customers").json()
    assert listing["agent"]["configured"] and listing["agent"]["pending"] is True
    # der Agent meldet den angewandten Soll-Stand -> nichts mehr offen
    digest = hashlib.sha256((tmp_path / "soll.json").read_bytes()).hexdigest()
    (tmp_path / "status.json").write_text(json.dumps({"ok": True, "soll_sha256": digest}))
    assert portal_client.get("/portal/customers").json()["agent"]["pending"] is False

    with session_factory() as s:
        actions = [a.action for a in s.query(AuditLog).all()]
    assert "portal.customer.created" in actions


@pytest.mark.parametrize(
    ("data", "status", "text"),
    [
        ({"short_name": "start"}, 422, "reserviert"),
        ({"short_name": "kunde-doc"}, 422, "reserviert"),
        ({"short_name": "Kunde_A"}, 422, "ungültig"),
        ({"realm": "master"}, 422, "Realm"),
        ({"links": [{"title": "x", "url": "http://a.example.ch"}]}, 422, "https://"),
        ({"name": "A\tB"}, 422, "Tabulator"),
        ({"default_role": "admin"}, 422, "Rolle"),
    ],
)
def test_angaben_werden_geprueft(portal_client, data, status, text):
    payload = {"short_name": "kunde-a", "name": "Kunde A", **data}
    r = portal_client.post("/portal/customers", json=payload)
    assert r.status_code == status and text in r.json()["detail"]


def test_kunden_muessen_zum_mandanten_gehoeren(portal_client, session_factory):
    with session_factory() as s:
        tid_a, _ = _tenant(s, "a")
        _, cid_b = _tenant(s, "b")
        s.commit()
    payload = {"short_name": "kunde-a", "name": "A", "tenant_id": tid_a, "customer_ids": [cid_b]}
    r = portal_client.post("/portal/customers", json=payload)
    assert r.status_code == 422 and "gehören nicht zum Mandanten" in r.json()["detail"]


def test_kurzname_und_realm_eindeutig_aendern_entfernen(portal_client, tmp_path):
    a = portal_client.post(
        "/portal/customers", json={"short_name": "a", "name": "A", "realm": "ra"}
    )
    portal_client.post("/portal/customers", json={"short_name": "b", "name": "B"})
    assert (
        portal_client.post("/portal/customers", json={"short_name": "a", "name": "X"}).status_code
        == 409
    )
    dup = portal_client.post(
        "/portal/customers", json={"short_name": "c", "name": "C", "realm": "ra"}
    )
    assert dup.status_code == 409 and "ra" in dup.json()["detail"]

    pid = a.json()["id"]
    r = portal_client.put(
        f"/portal/customers/{pid}", json={"short_name": "a", "name": "A neu", "um": True}
    )
    assert r.status_code == 200 and r.json()["name"] == "A neu" and r.json()["realm"] is None
    assert portal_client.delete(f"/portal/customers/{pid}").status_code == 200
    soll = json.loads((tmp_path / "soll.json").read_text(encoding="utf-8"))
    assert [k["kurzname"] for k in soll["kunden"]] == ["b"]


def test_nur_admin(portal_client):
    r = portal_client.get("/portal/customers", headers={"X-Dev-Roles": "operator"})
    assert r.status_code == 403


def test_ohne_ordner_nur_in_der_datenbank(client, tmp_path):
    client.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path, front_state_dir="")
    r = client.post("/portal/customers", json={"short_name": "a", "name": "A"})
    assert r.status_code == 201 and r.json()["note"] is None
    assert client.get("/portal/customers").json()["agent"]["configured"] is False


# --- Anmeldung je Kunden-Realm --------------------------------------------------------------


@pytest.fixture
def keys():
    return {name: rsa.generate_private_key(public_exponent=65537, key_size=2048)
            for name in ("vb", "kunde-a")}  # fmt: skip


def _token(key, realm: str, **claims) -> str:
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())  # fmt: skip
    body = {"iss": f"https://kc.example.ch/realms/{realm}", "aud": "automato-api", "sub": "u1",
            "preferred_username": "u1", **claims}  # fmt: skip
    return jwt.encode(body, pem, algorithm="RS256")


@pytest.fixture
def oidc_client(client, session_factory, keys, monkeypatch, tmp_path):
    settings = Settings(auth_mode="oidc", oidc_issuer=ISSUER, oidc_audience="automato-api",
                        oidc_jwks_url=f"{ISSUER}/protocol/openid-connect/certs",
                        front_state_dir=str(tmp_path))  # fmt: skip
    monkeypatch.setattr("app.auth.deps.get_settings", lambda: settings)
    client.app.dependency_overrides[get_settings] = lambda: settings
    fetched: list[str] = []

    class _Jwks:
        def __init__(self, url):
            self.url = url

        def get_signing_key_from_jwt(self, token):
            fetched.append(self.url)
            realm = self.url.split("/realms/")[1].split("/")[0]

            class _K:
                key = keys[realm].public_key()

            return _K()

    monkeypatch.setattr("app.auth.oidc._jwks_client", _Jwks)
    realms.invalidate()
    client.fetched = fetched
    return client


def test_kunden_realm_sieht_nur_seinen_ausschnitt(oidc_client, session_factory, keys):
    with session_factory() as s:
        tid, cid = _tenant(s, "dc")
        other_tid, _ = _tenant(s, "fremd")
        s.add(PortalCustomer(short_name="kunde-a", name="A", doc=True, realm="kunde-a",
                             tenant_id=tid, customer_ids=[cid], links=[]))  # fmt: skip
        s.commit()
    # der Realm des Kunden meldet Admin und fremde Mandanten - zählt nicht
    token = _token(keys["kunde-a"], "kunde-a", realm_access={"roles": ["admin"]},
                   tenant_ids=[other_tid], customer_ids=[999])  # fmt: skip
    me = oidc_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["is_admin"] is False and me["is_customer"] is True
    assert me["tenant_ids"] == [tid] and me["customer_ids"] == [cid]
    assert me["customer_role"] == "viewer" and me["customer_source"] == "realm:kunde-a"
    assert me["subject"] == "kunde-a:u1" and me["logins"] == []
    assert any("/realms/kunde-a/" in url for url in oidc_client.fetched)
    # Verwaltung bleibt zu
    r = oidc_client.get("/portal/customers", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403

    # Haupt-Realm unverändert: Admin
    main = _token(keys["vb"], "vb", realm_access={"roles": ["admin"]})
    me = oidc_client.get("/auth/me", headers={"Authorization": f"Bearer {main}"}).json()
    assert me["is_admin"] is True and me["customer_source"] is None


def test_unbekannter_realm_wird_abgelehnt(oidc_client, keys):
    token = _token(keys["kunde-a"], "kunde-a", realm_access={"roles": ["admin"]})
    r = oidc_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401  # nicht im Portal -> nur der Haupt-Realm zählt


def test_auth_config_je_adresse(oidc_client, session_factory):
    with session_factory() as s:
        for short, doc in (("kunde-a", True), ("kunde-b", False), ("vb", True)):
            s.add(PortalCustomer(short_name=short, name=short, doc=doc, realm=short,
                                 links=[], customer_ids=[]))  # fmt: skip
        s.commit()
    get = lambda host: oidc_client.get("/auth/config", params={"host": host}).json()  # noqa: E731
    a = get("kunde-a-doc.mgmt.example.ch")
    assert a["issuer"] == "https://kc.example.ch/realms/kunde-a" and a["admin_console_url"] == ""
    assert get("kunde-b-doc.mgmt.example.ch")["issuer"] == ISSUER  # ohne -doc kein eigener Login
    assert get("vb-doc.mgmt.example.ch")["issuer"] == ISSUER  # gemeinsamer Realm
    assert get("automato.int.example.ch")["issuer"] == ISSUER
