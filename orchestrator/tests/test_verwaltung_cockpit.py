"""Verwaltung nur aus dem Cockpit (1.14.0, ADR 0058).

- `PUT /freigaben` mit `zuordnung.kunde_anlegen` (Kunde in Automato anlegen, idempotent, Audit)
  und `benutzer` (Rolle je Keycloak-Benutzername; gesetzt haben nur diese Benutzer des
  Kunden-Realms Rechte, alle anderen eine eigene Meldung).
- Bei Kunden aus dem Cockpit vergeben Kunden keine Rechte: kein Zuordnen durch den Kunden-Admin
  (409), Zuordnungen eines Kunden-Admins wirken nicht mehr, die Rolle aus dem Token eines
  Kunden-Realms zaehlt nicht; der globale Admin bleibt Notzugang.
- Mit verbundenem Cockpit (gekoppelt bzw. Token aus `.env`) ist das Kunden-Portal in Oberflaeche
  und API nur lesbar (409); der Soll-Stand fuer den Front-Proxy nennt `verwaltung: cockpit`.
"""

import json

import jwt
import pytest
from app.audit import decode_detail
from app.auth import capabilities as caps
from app.auth import realms
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.models import (
    AuditLog,
    CockpitCustomer,
    Customer,
    CustomerUser,
    IpamType,
    PortalCustomer,
    Tenant,
)
from app.models.tenant import DnsType
from app.store import verwaltung
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

TOKEN = "c" * 64
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ISSUER = "https://kc.example.ch/realms/haupt"


def _settings(tmp_path, **kw) -> Settings:
    base = {"front_state_dir": str(tmp_path), "front_domain": "mgmt.example.ch",
            "cockpit_token": TOKEN}  # fmt: skip
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def api(client, tmp_path):
    client.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path)
    realms.invalidate()
    return client


def _kunde(kurz: str, **kw) -> dict:
    return {"kurzname": kurz, "name": kw.pop("name", f"Kunde {kurz}"),
            "werkzeuge": kw.pop("werkzeuge", ["doc"]), "realm": kw.pop("realm", kurz),
            **kw}  # fmt: skip


def _put(client, kunden: list[dict]):
    return client.put("/freigaben", json={"vertrag": 1, "kunden": kunden}, headers=AUTH)


def _tenant(session, name: str = "dc", kunden: tuple[str, ...] = ("Kunde dc",)) -> tuple[int, list]:
    t = Tenant(name=name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    session.add(t)
    session.flush()
    ids = []
    for n in kunden:
        c = Customer(tenant_id=t.id, name=n)
        session.add(c)
        session.flush()
        ids.append(c.id)
    return t.id, ids


def _audit(session_factory, action: str) -> list[tuple[str, str, dict | None]]:
    with session_factory() as s:
        rows = s.query(AuditLog).filter_by(action=action).order_by(AuditLog.id).all()
        return [(a.actor, a.subject, decode_detail(a)) for a in rows]


def _soll(tmp_path) -> dict:
    return json.loads((tmp_path / "soll.json").read_text(encoding="utf-8"))


# --- zuordnung.kunde_anlegen ------------------------------------------------------------------


def test_kunde_anlegen_idempotent_mit_audit(api, session_factory, tmp_path):
    with session_factory() as s:
        tid, (cid,) = _tenant(s)
        s.commit()
    zuordnung = {"mandant": tid, "kunden": [cid], "rolle": "viewer", "kunde_anlegen": True}
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=zuordnung)])
    assert r.status_code == 200, r.text
    assert r.json()["angelegt"] == ["kunde-a"]
    with session_factory() as s:
        neu = s.query(Customer).filter_by(name="Kunde A").one()
        link = s.query(CockpitCustomer).one()
        assert (neu.tenant_id, link.short_name, link.tenant_id, link.customer_id) == (
            tid, "kunde-a", tid, neu.id)  # fmt: skip
        pc = s.query(PortalCustomer).one()
        assert pc.customer_ids == sorted([cid, neu.id]) and pc.scope_source == "cockpit"
        neu_id = neu.id
    angelegt = _audit(session_factory, "customer.created")
    assert angelegt == [("cockpit", str(neu_id), {
        "tenant_id": tid, "customer_id": neu_id, "name": "Kunde A", "selectors": 0,
        "quelle": "cockpit", "portal_kunde": "kunde-a"})]  # fmt: skip

    # dieselbe Anfrage: kein weiterer Kunde, kein Audit, unveraendert
    anzahl = len(_audit(session_factory, "portal.customer.updated"))
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=zuordnung)])
    assert r.json()["unveraendert"] == 1, r.json()
    with session_factory() as s:
        assert s.query(Customer).count() == 2 and s.query(CockpitCustomer).count() == 1
    assert len(_audit(session_factory, "customer.created")) == 1
    assert len(_audit(session_factory, "portal.customer.updated")) == anzahl

    # Kunden-Portal und Inventar nennen den angelegten Kunden
    pc = api.get("/portal/customers").json()["customers"][0]
    assert pc["kunde_angelegt"] == neu_id and pc["customer_ids"] == sorted([cid, neu_id])
    (tmp_path / "topologie.json").write_text(json.dumps({"kunden": [{"kurzname": "kunde-a"}]}))
    inv = api.get("/inventory", headers=AUTH).json()["kunden"][0]
    assert inv["zuordnung"]["kunde_angelegt"] == neu_id
    assert inv["zuordnung"]["kunden"] == sorted([cid, neu_id])

    # ohne kunde_anlegen: der angelegte bleibt (nur nicht mehr in der Zuordnung, wenn nicht genannt)
    r = _put(api, [_kunde("kunde-a", name="Kunde A",
                          zuordnung={**zuordnung, "kunde_anlegen": False})])  # fmt: skip
    assert r.json()["geaendert"] == ["kunde-a"]
    with session_factory() as s:
        assert s.query(PortalCustomer).one().customer_ids == [cid]
        assert s.get(Customer, neu_id) is not None
    # wieder mit kunde_anlegen: derselbe Kunde, kein neuer
    _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=zuordnung)])
    with session_factory() as s:
        assert s.query(PortalCustomer).one().customer_ids == sorted([cid, neu_id])
        assert s.query(Customer).count() == 2


def test_kunde_anlegen_je_mandant_und_nach_dem_loeschen(api, session_factory):
    with session_factory() as s:
        t1, _ = _tenant(s, "dc", ())
        t2, _ = _tenant(s, "dr", ())
        s.commit()
    z = {"rolle": "viewer", "kunde_anlegen": True}
    _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung={**z, "mandant": t1})])
    _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung={**z, "mandant": t2})])
    with session_factory() as s:
        links = {(c.tenant_id, c.short_name) for c in s.query(CockpitCustomer)}
        assert links == {(t1, "kunde-a"), (t2, "kunde-a")}  # der alte bleibt (Abbau, ADR 0058)
        assert s.query(Customer).filter_by(name="Kunde A").count() == 2
        pc = s.query(PortalCustomer).one()
        alt = s.query(CockpitCustomer).filter_by(tenant_id=t1).one().customer_id
        assert pc.tenant_id == t2 and alt not in pc.customer_ids
        neu = s.query(CockpitCustomer).filter_by(tenant_id=t2).one().customer_id
    # in Automato geloescht: die Verknuepfung faellt mit, die naechste Anfrage legt neu an
    assert api.delete(f"/customers/{neu}").status_code == 204
    with session_factory() as s:
        assert s.query(CockpitCustomer).filter_by(tenant_id=t2).count() == 0
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung={**z, "mandant": t2})])
    assert r.status_code == 200, r.text
    with session_factory() as s:  # SQLite vergibt die Id des geloeschten Kunden wieder
        neu2 = s.query(CockpitCustomer).filter_by(tenant_id=t2).one().customer_id
        assert s.get(Customer, neu2).name == "Kunde A"
        assert s.query(PortalCustomer).one().customer_ids == [neu2]
    assert len(_audit(session_factory, "customer.created")) == 3


def test_mandant_loeschen_mit_angelegtem_kunden(api, session_factory):
    with session_factory() as s:
        tid, _ = _tenant(s, "weg", ())
        s.commit()
    z = {"mandant": tid, "rolle": "viewer", "kunde_anlegen": True}
    assert _put(api, [_kunde("kunde-a", zuordnung=z)]).status_code == 200
    r = api.delete(f"/tenants/{tid}", params={"confirm": "weg"})
    assert r.status_code == 200, r.text
    with session_factory() as s:
        assert s.query(CockpitCustomer).count() == 0 and s.query(Customer).count() == 0


def test_kunde_anlegen_name_belegt(api, session_factory):
    """Gibt es den Namen im Mandanten schon, heisst der neue „<Name> (<Kurzname>)“ - nie still
    uebernommen; gibt es auch den, aendert die Anfrage nichts (422)."""
    with session_factory() as s:
        tid, (vorhanden,) = _tenant(s, "dc", ("Kunde A",))
        s.commit()
    z = {"mandant": tid, "rolle": "viewer", "kunde_anlegen": True}
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=z),
                   _kunde("kunde-b", name="Kunde A", zuordnung=z)])  # fmt: skip
    assert r.status_code == 200, r.text
    with session_factory() as s:
        namen = {c.short_name: s.get(Customer, c.customer_id).name
                 for c in s.query(CockpitCustomer)}  # fmt: skip
        assert namen == {"kunde-a": "Kunde A (kunde-a)", "kunde-b": "Kunde A (kunde-b)"}
        assert vorhanden not in s.query(PortalCustomer).filter_by(short_name="kunde-a").one(
        ).customer_ids  # fmt: skip

    with session_factory() as s:
        s.add(Customer(tenant_id=tid, name="Kunde C"))
        s.add(Customer(tenant_id=tid, name="Kunde C (kunde-c)"))
        s.commit()
        anzahl = s.query(Customer).count()
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=z),
                   _kunde("kunde-c", name="Kunde C", zuordnung=z)])  # fmt: skip
    assert r.status_code == 422, r.text
    fehler = r.json()["fehler"]
    assert [f["kurzname"] for f in fehler] == ["kunde-c"] and "kunde_anlegen" in fehler[0]["grund"]
    with session_factory() as s:
        assert s.query(Customer).count() == anzahl
        assert s.query(PortalCustomer).filter_by(short_name="kunde-c").count() == 0


@pytest.mark.parametrize("wert", ["ja", 1, []])
def test_kunde_anlegen_wird_geprueft(api, session_factory, wert):
    with session_factory() as s:
        tid, _ = _tenant(s)
        s.commit()
    z = {"mandant": tid, "rolle": "viewer", "kunde_anlegen": wert}
    r = _put(api, [_kunde("kunde-a", zuordnung=z)])
    assert r.status_code == 422 and "kunde_anlegen" in r.json()["fehler"][0]["grund"]
    with session_factory() as s:
        assert s.query(PortalCustomer).count() == 0 and s.query(CockpitCustomer).count() == 0


# --- benutzer -----------------------------------------------------------------------------------


def test_benutzer_idempotent_audit_inventar(api, session_factory, tmp_path):
    benutzer = [{"benutzer": "Max.Muster", "rolle": "editor"},
                {"benutzer": "anna@example.ch", "rolle": "viewer"}]  # fmt: skip
    r = _put(api, [_kunde("kunde-a", benutzer=benutzer)])
    assert r.status_code == 200, r.text
    gespeichert = [{"benutzer": "anna@example.ch", "rolle": "viewer"},
                   {"benutzer": "max.muster", "rolle": "editor"}]  # fmt: skip
    with session_factory() as s:
        assert s.query(PortalCustomer).one().users == gespeichert
    created = _audit(session_factory, "portal.customer.created")[0][2]
    assert created["benutzer"] == 2
    assert created["benutzer_neu"] == ["anna@example.ch:viewer", "max.muster:editor"]
    # gleicher Stand in anderer Reihenfolge: unveraendert
    r = _put(api, [_kunde("kunde-a", benutzer=list(reversed(benutzer)))])
    assert r.json()["unveraendert"] == 1
    # Rolle aendern, einen entfernen, einen dazu
    zwei = [{"benutzer": "max.muster", "rolle": "triage"}, {"benutzer": "neu", "rolle": "viewer"}]
    r = _put(api, [_kunde("kunde-a", benutzer=zwei)])
    assert r.json()["geaendert"] == ["kunde-a"]
    upd = _audit(session_factory, "portal.customer.updated")[-1][2]
    assert upd == {"benutzer": 2, "benutzer_neu": ["neu:viewer"],
                   "benutzer_entfernt": ["anna@example.ch"],
                   "benutzer_rolle": ["max.muster:triage"], "quelle": "cockpit"}  # fmt: skip
    # Kunden-Portal (nur lesbar) und Inventar mit dem aktuellen Stand
    assert api.get("/portal/customers").json()["customers"][0]["users"] == [
        {"benutzer": "max.muster", "rolle": "triage"},
        {"benutzer": "neu", "rolle": "viewer"},
    ]
    (tmp_path / "topologie.json").write_text(json.dumps({"kunden": [{"kurzname": "kunde-a"}]}))
    inv = api.get("/inventory", headers=AUTH).json()["kunden"][0]
    assert inv["benutzer"] == [{"benutzer": "max.muster", "rolle": "triage"},
                               {"benutzer": "neu", "rolle": "viewer"}]  # fmt: skip
    # null: keine Aussage mehr (alle Benutzer des Realms mit der Standard-Rolle)
    r = _put(api, [_kunde("kunde-a", benutzer=None)])
    assert r.json()["geaendert"] == ["kunde-a"]
    assert _audit(session_factory, "portal.customer.updated")[-1][2]["benutzer"] is None
    assert api.get("/inventory", headers=AUTH).json()["kunden"][0]["benutzer"] is None
    # leer ist eine Aussage: niemand
    _put(api, [_kunde("kunde-a", benutzer=[])])
    with session_factory() as s:
        assert s.query(PortalCustomer).one().users == []


@pytest.mark.parametrize(
    ("benutzer", "grund"),
    [
        ("max", "keine Liste"),
        ([{"benutzer": f"u{i}", "rolle": "viewer"} for i in range(2001)], "höchstens 2000"),
        (["max"], "kein Objekt"),
        ([{"benutzer": "", "rolle": "viewer"}], "leer"),
        ([{"benutzer": "x" * 256, "rolle": "viewer"}], "länger als 255"),
        ([{"benutzer": "max muster", "rolle": "viewer"}], "ohne Leerraum"),
        ([{"benutzer": "max\u0007", "rolle": "viewer"}], "druckbare"),
        ([{"benutzer": 5, "rolle": "viewer"}], "kein Text"),
        ([{"benutzer": "max", "rolle": "admin"}], "Rolle 'admin'"),
        ([{"benutzer": "max", "rolle": None}], "Rolle"),
        ([{"benutzer": "max", "rolle": "viewer"}, {"benutzer": "MAX", "rolle": "editor"}],
         "doppelt"),
    ],
)  # fmt: skip
def test_benutzer_wird_geprueft(api, session_factory, benutzer, grund):
    r = _put(api, [_kunde("kunde-a", benutzer=benutzer), _kunde("kunde-b")])
    assert r.status_code == 422, r.text
    fehler = r.json()["fehler"]
    assert len(fehler) == 1 and fehler[0]["kurzname"] == "kunde-a" and grund in fehler[0]["grund"]
    with session_factory() as s:
        assert s.query(PortalCustomer).count() == 0  # alles oder nichts


# --- Anmeldung der Benutzer eines Kunden-Realms -------------------------------------------------


@pytest.fixture
def keys():
    return {name: rsa.generate_private_key(public_exponent=65537, key_size=2048)
            for name in ("haupt", "kunde-a")}  # fmt: skip


def _token(key, realm: str, user: str = "max.muster", **claims) -> str:
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())  # fmt: skip
    body = {"iss": f"https://kc.example.ch/realms/{realm}", "aud": "automato-api",
            "sub": f"id-{user}", "preferred_username": user, **claims}  # fmt: skip
    return jwt.encode(body, pem, algorithm="RS256")


@pytest.fixture
def oidc(client, keys, monkeypatch, tmp_path):
    settings = Settings(auth_mode="oidc", oidc_issuer=ISSUER, oidc_audience="automato-api",
                        oidc_jwks_url=f"{ISSUER}/protocol/openid-connect/certs",
                        front_state_dir=str(tmp_path))  # fmt: skip
    monkeypatch.setattr("app.auth.deps.get_settings", lambda: settings)
    client.app.dependency_overrides[get_settings] = lambda: settings

    class _Jwks:
        def __init__(self, url):
            self.url = url

        def get_signing_key_from_jwt(self, token):
            realm = self.url.split("/realms/")[1].split("/")[0]

            class _K:
                key = keys[realm].public_key()

            return _K()

    monkeypatch.setattr("app.auth.oidc._jwks_client", _Jwks)
    realms.invalidate()
    return client


def _realm_kunde(session_factory, source="cockpit", users=None, role="viewer") -> tuple[int, int]:
    with session_factory() as s:
        tid, (cid,) = _tenant(s)
        s.add(PortalCustomer(short_name="kunde-a", name="Kunde A", doc=True, realm="kunde-a",
                             tenant_id=tid, customer_ids=[cid], default_role=role, links=[],
                             source=source, users=users))  # fmt: skip
        s.commit()
    realms.invalidate()
    return tid, cid


def _me(client, keys, user="max.muster", **claims) -> tuple[dict, dict]:
    auth = {"Authorization": f"Bearer {_token(keys['kunde-a'], 'kunde-a', user, **claims)}"}
    r = client.get("/auth/me", headers=auth)
    assert r.status_code == 200, r.text
    return r.json(), auth


def test_realm_benutzer_mit_und_ohne_eintrag(oidc, session_factory, keys):
    users = [{"benutzer": "max.muster", "rolle": "editor"}]
    tid, cid = _realm_kunde(session_factory, users=users)
    # mit Eintrag: Rolle aus dem Cockpit - nicht aus dem Token (dort customer_admin)
    me, auth = _me(oidc, keys, "Max.Muster", customer_role="customer_admin")
    assert (me["customer_ids"], me["customer_role"], me["rolle_fehlt"]) == ([cid], "editor", None)
    assert caps.USERS_MANAGE not in me["capabilities"] and caps.APPS_EDIT in me["capabilities"]
    assert oidc.get("/tenants", headers=auth).status_code == 200

    # ohne Eintrag: keine Rechte, eigene Meldung (nicht „kein Mandant“)
    me, auth = _me(oidc, keys, "anna", customer_role="customer_admin")
    assert me["is_customer"] and me["capabilities"] == [] and me["customers"] == []
    assert me["tenant_ids"] == [] and me["zuordnung_fehlt"] is None
    fehlt = me["rolle_fehlt"]
    assert (fehlt["kurzname"], fehlt["name"]) == ("kunde-a", "Kunde A")
    assert "keine Rolle vergeben" in fehlt["meldung"] and "Cockpit" in fehlt["meldung"]
    for pfad in ("/tenants", "/applications", "/customer-users", "/portal/customers"):
        r = oidc.get(pfad, headers=auth)
        assert r.status_code == 403 and "keine Rolle vergeben" in r.json()["detail"], pfad
    # ohne Benutzernamen im Token: ebenso
    me, _ = _me(oidc, keys, "")
    assert me["rolle_fehlt"] is not None

    # leere Liste: niemand hat Rechte
    with session_factory() as s:
        s.query(PortalCustomer).one().users = []
        s.commit()
    realms.invalidate()
    assert _me(oidc, keys)[0]["rolle_fehlt"] is not None


def test_realm_ohne_benutzerliste_standard_rolle_ohne_token(oidc, session_factory, keys):
    """null = keine Aussage: alle Benutzer des Realms mit der Standard-Rolle; bei Kunden aus dem
    Cockpit zaehlt die Rolle aus dem Token nicht (Rechte nur aus dem Cockpit)."""
    _, cid = _realm_kunde(session_factory, users=None, role="triage")
    me, _ = _me(oidc, keys, "anna", customer_role="customer_admin",
                realm_access={"roles": ["operator"]})  # fmt: skip
    assert (me["customer_ids"], me["customer_role"], me["rolle_fehlt"]) == ([cid], "triage", None)


def test_realm_aus_der_oberflaeche_wie_bisher(oidc, session_factory, keys):
    """Kunden aus der Oberflaeche (ohne Cockpit): die Rolle aus dem Token gilt wie bisher."""
    _, cid = _realm_kunde(session_factory, source="oberflaeche")
    me, _ = _me(oidc, keys, "anna", customer_role="editor")
    assert (me["customer_ids"], me["customer_role"]) == ([cid], "editor")
    assert me["cockpit_kunden"] == []


def test_realm_kunden_admin_ordnet_bei_kunden_aus_dem_cockpit_nichts_zu(
    oidc, session_factory, keys
):
    users = [{"benutzer": "chef", "rolle": "customer_admin"}]
    _, cid = _realm_kunde(session_factory, users=users)
    me, auth = _me(oidc, keys, "chef")
    assert me["customer_role"] == "customer_admin" and me["cockpit_kunden"] == [cid]
    assert caps.USERS_MANAGE not in me["capabilities"]  # Oberflaeche blendet die Seite aus
    r = oidc.post("/customer-users", json={"customer_id": cid, "login": "x@example.ch"},
                  headers=auth)  # fmt: skip
    assert r.status_code == 409 and "im Cockpit" in r.json()["detail"]
    r = oidc.get("/customer-users", headers=auth)
    assert r.status_code == 409 and "Betreiber im Cockpit" in r.json()["detail"]


# --- Kunden-Admin aus dem Haupt-Realm (Zuordnung in Automato) ----------------------------------


def _cockpit_kunde(session_factory) -> tuple[int, int, int]:
    """Mandant mit Kunde A (aus dem Cockpit) und Kunde B (nur in Automato)."""
    with session_factory() as s:
        tid, (a, b) = _tenant(s, "dc", ("Kunde A", "Kunde B"))
        s.add(PortalCustomer(short_name="kunde-a", name="Kunde A", source="cockpit",
                             tenant_id=tid, customer_ids=[a], links=[]))  # fmt: skip
        s.commit()
    return tid, a, b


def _assign(client, customer_id, login, role, headers=None):
    body = {"customer_id": customer_id, "login": login, "role": role}
    return client.post("/customer-users", json=body, headers=headers or {})


def test_kunden_admin_keine_rechte_vergeben_bei_kunden_aus_dem_cockpit(client, session_factory):
    tid, a, b = _cockpit_kunde(session_factory)
    # der globale Admin bleibt Notzugang
    for cid in (a, b):
        assert _assign(client, cid, "chef@example.ch", "customer_admin").status_code == 201
    me = {"X-Dev-User": "chef@example.ch"}
    who = client.get("/auth/me", headers=me).json()
    assert who["cockpit_kunden"] == [a] and caps.USERS_MANAGE in who["capabilities"]  # wegen B
    r = _assign(client, a, "neu@example.ch", "viewer", headers=me)
    assert r.status_code == 409 and "Betreiber im Cockpit" in r.json()["detail"]
    assert _assign(client, b, "neu@example.ch", "viewer", headers=me).status_code == 201
    # Liste nur mit Kunden ausserhalb des Cockpits; Aendern/Entfernen bei A: 409
    listed = client.get("/customer-users", params={"tenant_id": tid}, headers=me).json()
    assert {x["customer_id"] for x in listed} == {b}
    zeilen = client.get("/customer-users", params={"tenant_id": tid}).json()
    fremd = next(x for x in zeilen if x["customer_id"] == a)
    with session_factory() as s:  # ein weiterer Benutzer von A (vom Betreuer)
        s.add(CustomerUser(tenant_id=tid, customer_id=a, login="kollege@example.ch",
                           role="viewer", origin="staff"))  # fmt: skip
        s.commit()
        kollege = s.query(CustomerUser).filter_by(login="kollege@example.ch").one().id
    assert fremd["login"] == "chef@example.ch"
    r = client.patch(f"/customer-users/{kollege}", json={"role": "editor"}, headers=me)
    assert r.status_code == 409
    assert client.delete(f"/customer-users/{kollege}", headers=me).status_code == 409


def test_nur_kunden_aus_dem_cockpit_ohne_seite(client, session_factory):
    """Ist der Kunden-Admin nur bei Kunden aus dem Cockpit, fehlt ihm das Recht ganz (die
    Oberflaeche blendet „Kunden-Benutzer“ aus); die API antwortet 409 mit Hinweis."""
    tid, a, _ = _cockpit_kunde(session_factory)
    headers = {"X-Dev-Tenants": str(tid), "X-Dev-Customers": str(a),
               "X-Dev-Customer-Role": "customer_admin"}  # fmt: skip
    who = client.get("/auth/me", headers=headers).json()
    assert caps.USERS_MANAGE not in who["capabilities"] and who["cockpit_kunden"] == [a]
    r = client.get("/customer-users", params={"tenant_id": tid}, headers=headers)
    assert r.status_code == 409 and "Cockpit" in r.json()["detail"]
    # Editor bleibt Editor (kein 409 fuer jemanden, der ohnehin keine Benutzer verwaltet)
    editor = {**headers, "X-Dev-Customer-Role": "editor"}
    assert client.get("/customer-users", headers=editor).status_code == 403


def test_zuordnung_eines_kunden_admins_wirkt_nicht_bei_kunden_aus_dem_cockpit(
    client, session_factory
):
    tid, a, b = _cockpit_kunde(session_factory)
    with session_factory() as s:
        for cid, origin in ((a, "customer"), (b, "customer")):
            s.add(CustomerUser(tenant_id=tid, customer_id=cid, login="neu@example.ch",
                               role="editor", origin=origin))  # fmt: skip
        s.add(CustomerUser(tenant_id=tid, customer_id=a, login="betreut@example.ch",
                           role="viewer", origin="staff"))  # fmt: skip
        s.commit()
    neu = client.get("/auth/me", headers={"X-Dev-User": "neu@example.ch"}).json()
    assert neu["customer_ids"] == [b]  # die Zuordnung des Kunden-Admins bei A zaehlt nicht
    betreut = client.get("/auth/me", headers={"X-Dev-User": "betreut@example.ch"}).json()
    assert betreut["customer_ids"] == [a] and betreut["cockpit_kunden"] == [a]


def test_principal_ohne_benutzerverwaltung_bei_kunden_aus_dem_cockpit():
    p = Principal(subject="x", customer_ids=(1, 2), tenant_ids=(9,),
                  customer_roles=((1, "customer_admin"), (2, "customer_admin")),
                  cockpit_customers=(1,))  # fmt: skip
    assert not p.can(caps.USERS_MANAGE, 1) and p.can(caps.USERS_MANAGE, 2)
    assert p.can(caps.APPS_EDIT, 1) and p.customers_with(caps.USERS_MANAGE) == (2,)
    assert caps.USERS_MANAGE in p.capabilities
    nur = Principal(subject="x", customer_ids=(1,), tenant_ids=(9,),
                    customer_roles=((1, "customer_admin"),), cockpit_customers=(1,))  # fmt: skip
    assert caps.USERS_MANAGE not in nur.capabilities


def test_cockpit_kunden(session_factory):
    with session_factory() as s:
        tid, (a, b, c) = _tenant(s, "dc", ("A", "B", "C"))
        fremd, (f,) = _tenant(s, "fremd", ("F",))
        s.add_all([
            PortalCustomer(short_name="k1", name="K1", source="cockpit", tenant_id=tid,
                           customer_ids=[a, f, 999], links=[]),
            PortalCustomer(short_name="k2", name="K2", source="oberflaeche", tenant_id=tid,
                           customer_ids=[b], links=[]),
            CockpitCustomer(short_name="weg", tenant_id=tid, customer_id=c),
        ])  # fmt: skip
        s.commit()
        assert verwaltung.cockpit_kunden(s) == {a, c}  # nur Kunden dieses Mandanten
        assert verwaltung.cockpit_kunden(s, [fremd]) == set()
        assert verwaltung.cockpit_kunden(s, []) == set()


# --- Kunden-Portal nur aus dem Cockpit, sobald eines verbunden ist ------------------------------


def test_portal_nur_lesbar_mit_cockpit(api, session_factory, tmp_path):
    with session_factory() as s:
        s.add(PortalCustomer(short_name="alt", name="Alt", links=[], customer_ids=[]))
        s.commit()
        alt = s.query(PortalCustomer).one().id
    listing = api.get("/portal/customers").json()
    assert listing["verwaltung"] == "cockpit"
    body = {"short_name": "neu", "name": "Neu"}
    for r in (api.post("/portal/customers", json=body),
              api.put(f"/portal/customers/{alt}", json={"short_name": "alt", "name": "Anders"}),
              api.delete(f"/portal/customers/{alt}")):  # fmt: skip
        assert r.status_code == 409 and "nur noch im Cockpit" in r.json()["detail"], r.text
    with session_factory() as s:
        assert [pc.name for pc in s.query(PortalCustomer)] == ["Alt"]
    # das Cockpit uebernimmt den Kunden aus der Oberflaeche
    r = _put(api, [_kunde("alt", name="Alt")])
    assert r.json()["uebernommen"] == ["alt"]
    assert _soll(tmp_path)["verwaltung"] == "cockpit"

    # ohne Cockpit wie bisher
    api.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path, cockpit_token="")
    assert api.get("/portal/customers").json()["verwaltung"] == "automato"
    assert api.post("/portal/customers", json=body).status_code == 201
    assert "verwaltung" not in _soll(tmp_path)


def test_kopplung_sperrt_portal_und_front(client, tmp_path):
    client.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path, cockpit_token="")
    assert (
        client.post("/portal/customers", json={"short_name": "a", "name": "A"}).status_code == 201
    )
    assert "verwaltung" not in _soll(tmp_path)
    code = client.post("/kopplung/cockpit/code").json()["code"]
    r = client.post("/kopplung", json={"vertrag": 1, "code": code, "cockpit": {"name": "Cockpit"}})
    assert r.status_code == 200, r.text
    assert _soll(tmp_path)["verwaltung"] == "cockpit"
    r = client.post("/portal/customers", json={"short_name": "b", "name": "B"})
    assert r.status_code == 409
    assert client.delete("/kopplung/cockpit").status_code == 200
    assert "verwaltung" not in _soll(tmp_path)
    assert (
        client.post("/portal/customers", json={"short_name": "b", "name": "B"}).status_code == 201
    )
