"""Neuer Mandant je Portal-Kunde aus dem Cockpit (1.19.2, ADR 0058 Nachtrag).

Befund des Anwenders 2026-11-18: in der gemeinsamen Instanz landete jeder neue Kunde aus dem
Cockpit im ersten Mandanten. `PUT /freigaben` mit `zuordnung.mandant_anlegen`:

- legt einen eigenen Mandanten mit einem Kunden an und ordnet beides zu - idempotent je Kurzname,
  Audit `tenant.created` und `customer.created` mit Akteur `cockpit`; Name belegt -> „<Name>
  (<Kurzname>)“, beide belegt bzw. ungueltig -> 422 ohne Aenderung;
- Umzug aus einem anderen Mandanten: der dort angelegte Kunde nur ohne Daten weg, sonst gemeldet;
  von Hand gewaehlte Kunden bleiben; nur beim Wechsel, spaeter nie still;
- Benutzer des Kunden-Realms sehen nur den eigenen Mandanten;
- Inventar (`mandant_angelegt`, `portal_kunde`, `funktionen`) und Kunden-Portal nennen ihn;
- der Abbau entfernt den Kunden darin, nie den Mandanten.
"""

import json

import jwt
import pytest
from app.audit import decode_detail
from app.auth import realms
from app.config import Settings, get_settings
from app.models import (
    AuditLog,
    CockpitCustomer,
    CockpitTenant,
    Customer,
    CustomerSelector,
    IpamType,
    PortalCustomer,
    Tenant,
)
from app.models.tenant import DnsType
from app.store import freigaben
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

TOKEN = "c" * 64
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ISSUER = "https://kc.example.ch/realms/haupt"
NEU = {"mandant_anlegen": True, "kunden": [], "rolle": "viewer"}


@pytest.fixture
def api(client, tmp_path):
    settings = Settings(front_state_dir=str(tmp_path), front_domain="mgmt.example.ch",
                        cockpit_token=TOKEN)  # fmt: skip
    client.app.dependency_overrides[get_settings] = lambda: settings
    client.state_dir = tmp_path  # type: ignore[attr-defined]
    realms.invalidate()
    return client


def _kunde(kurz: str, **kw) -> dict:
    return {"kurzname": kurz, "name": kw.pop("name", f"Kunde {kurz}"),
            "werkzeuge": kw.pop("werkzeuge", ["doc"]), "realm": kw.pop("realm", kurz),
            **kw}  # fmt: skip


def _put(client, kunden: list[dict], **extra):
    return client.put("/freigaben", json={"vertrag": 1, "kunden": kunden, **extra}, headers=AUTH)


def _erster(session_factory, name: str = "Erster",
            kunden: tuple[str, ...] = ()) -> tuple[int, list]:  # fmt: skip
    """Der erste Mandant der gemeinsamen Instanz (dort landeten bisher alle Kunden)."""
    with session_factory() as s:
        t = Tenant(name=name, ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
        s.add(t)
        s.flush()
        ids = []
        for n in kunden:
            c = Customer(tenant_id=t.id, name=n)
            s.add(c)
            s.flush()
            ids.append(c.id)
        s.commit()
        return t.id, ids


def _audit(session_factory, action: str) -> list[tuple[str, str, dict | None]]:
    with session_factory() as s:
        rows = s.query(AuditLog).filter_by(action=action).order_by(AuditLog.id).all()
        return [(a.actor, a.subject, decode_detail(a)) for a in rows]


def _stand(session_factory, kurz: str = "kunde-a") -> tuple[int, int, PortalCustomer]:
    """(angelegter Mandant, angelegter Kunde darin, Portal-Kunde)."""
    with session_factory() as s:
        tid = s.query(CockpitTenant).filter_by(short_name=kurz).one().tenant_id
        cid = s.query(CockpitCustomer).filter_by(short_name=kurz, tenant_id=tid).one().customer_id
        return tid, cid, s.query(PortalCustomer).filter_by(short_name=kurz).one()


# --- Anlegen ------------------------------------------------------------------------------------


def test_mandant_anlegen_idempotent_mit_audit(api, session_factory):
    erster, _ = _erster(session_factory)
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=NEU)])
    assert r.status_code == 200, r.text
    tid, cid, pc = _stand(session_factory)
    assert tid != erster
    assert r.json()["angelegt"] == ["kunde-a"]
    assert r.json()["mandanten_angelegt"] == [{"kurzname": "kunde-a", "id": tid, "name": "Kunde A"}]
    assert r.json()["umzug"] == []
    assert (pc.tenant_id, pc.customer_ids, pc.default_role, pc.scope_source) == (
        tid, [cid], "viewer", "cockpit")  # fmt: skip
    with session_factory() as s:
        t = s.get(Tenant, tid)
        assert (t.name, t.ipam_type, t.dns_type, t.code) == (
            "Kunde A", IpamType.NETBOX, DnsType.ACTIVE_DIRECTORY, None)  # fmt: skip
        assert s.get(Customer, cid).name == "Kunde A" and s.get(Customer, cid).tenant_id == tid
        assert s.query(Customer).filter_by(tenant_id=erster).count() == 0  # nicht im ersten
    assert _audit(session_factory, "tenant.created") == [("cockpit", str(tid), {
        "tenant_id": tid, "name": "Kunde A", "code": None, "quelle": "cockpit",
        "portal_kunde": "kunde-a"})]  # fmt: skip
    assert [d["tenant_id"] for _, _, d in _audit(session_factory, "customer.created")] == [tid]

    # dieselbe Anfrage noch einmal: kein weiterer Mandant bzw. Kunde, kein Audit, unveraendert
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=NEU)])
    assert r.status_code == 200, r.text
    assert (r.json()["unveraendert"], r.json()["mandanten_angelegt"]) == (1, [])
    with session_factory() as s:
        assert s.query(Tenant).count() == 2 and s.query(Customer).count() == 1
    assert len(_audit(session_factory, "tenant.created")) == 1
    assert len(_audit(session_factory, "customer.created")) == 1

    # ein anderer Name spaeter benennt nicht um (gilt nur beim Anlegen)
    r = _put(api, [_kunde("kunde-a", name="Kunde A neu",
                          zuordnung={**NEU, "mandant_name": "Anders"})])  # fmt: skip
    assert r.json()["geaendert"] == ["kunde-a"] and r.json()["mandanten_angelegt"] == []
    with session_factory() as s:
        assert s.get(Tenant, tid).name == "Kunde A" and s.query(Tenant).count() == 2


def test_je_kunde_ein_eigener_mandant(api, session_factory):
    _erster(session_factory)
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=NEU),
                   _kunde("kunde-b", name="Kunde B", zuordnung=NEU)])  # fmt: skip
    assert r.status_code == 200, r.text
    a, ca, _ = _stand(session_factory, "kunde-a")
    b, cb, _ = _stand(session_factory, "kunde-b")
    assert len({a, b}) == 2 and len({ca, cb}) == 2
    assert [m["kurzname"] for m in r.json()["mandanten_angelegt"]] == ["kunde-a", "kunde-b"]


def test_mandant_name_und_belegt(api, session_factory):
    """Mit `mandant_name` heisst der Mandant so; gibt es den Namen schon, „<Name> (<Kurzname>)“ -
    nie still uebernommen; sind beide belegt, aendert die Anfrage nichts (422)."""
    vorhanden, _ = _erster(session_factory, "Kunde A")
    r = _put(
        api,
        [
            _kunde("kunde-a", name="Kunde A", zuordnung=NEU),
            _kunde("kunde-b", zuordnung={**NEU, "mandant_name": "  Umgebung B "}),
        ],
    )
    assert r.status_code == 200, r.text
    with session_factory() as s:
        namen = {c.short_name: s.get(Tenant, c.tenant_id).name for c in s.query(CockpitTenant)}
    assert namen == {"kunde-a": "Kunde A (kunde-a)", "kunde-b": "Umgebung B"}
    assert _stand(session_factory)[0] != vorhanden

    _erster(session_factory, "Kunde C")
    _erster(session_factory, "Kunde C (kunde-c)")
    with session_factory() as s:
        anzahl = s.query(Tenant).count()
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=NEU),
                   _kunde("kunde-c", name="Kunde C", zuordnung=NEU)])  # fmt: skip
    assert r.status_code == 422, r.text
    fehler = r.json()["fehler"]
    assert [f["kurzname"] for f in fehler] == ["kunde-c"]
    assert "mandant_anlegen" in fehler[0]["grund"] and "mandant_name" in fehler[0]["grund"]
    with session_factory() as s:
        assert s.query(Tenant).count() == anzahl
        assert s.query(PortalCustomer).filter_by(short_name="kunde-c").count() == 0


@pytest.mark.parametrize(("zuordnung", "grund"), [
    ({**NEU, "mandant_anlegen": "ja"}, "mandant_anlegen ist kein Wahrheitswert"),
    ({**NEU, "mandant_anlegen": 1}, "mandant_anlegen ist kein Wahrheitswert"),
    ({**NEU, "mandant": 1}, "schliessen sich aus"),
    ({**NEU, "mandant_name": ""}, "mandant_name"),
    ({**NEU, "mandant_name": "A\tB"}, "mandant_name"),
    ({**NEU, "mandant_name": 7}, "mandant_name"),
    ({**NEU, "mandant_name": "x" * 201}, "mandant_name"),
    ({**NEU, "kunden": [1]}, "kunden erst"),
    ({**NEU, "rolle": "admin"}, "Rolle"),
])  # fmt: skip
def test_mandant_anlegen_wird_geprueft(api, session_factory, zuordnung, grund):
    _erster(session_factory, kunden=("Kunde X",))
    r = _put(api, [_kunde("kunde-a", zuordnung=zuordnung)])
    assert r.status_code == 422, r.text
    assert grund in r.json()["fehler"][0]["grund"]
    with session_factory() as s:
        assert s.query(Tenant).count() == 1 and s.query(CockpitTenant).count() == 0
        assert s.query(PortalCustomer).count() == 0


def test_kunden_nur_im_eigenen_mandanten(api, session_factory):
    """Nach dem Anlegen darf das Cockpit weitere Kunden des eigenen Mandanten nennen - nie Kunden
    eines anderen; ohne `mandant_anlegen` (false) gilt wieder `mandant`."""
    _, (fremd,) = _erster(session_factory, kunden=("Kunde X",))
    assert _put(api, [_kunde("kunde-a", zuordnung=NEU)]).status_code == 200
    tid, cid, _ = _stand(session_factory)
    with session_factory() as s:
        weiterer = Customer(tenant_id=tid, name="Zweiter")
        s.add(weiterer)
        s.commit()
        weiterer_id = weiterer.id
    r = _put(api, [_kunde("kunde-a", zuordnung={**NEU, "kunden": [fremd]})])
    assert r.status_code == 422 and "gehören nicht zum Mandanten" in r.json()["fehler"][0]["grund"]
    r = _put(api, [_kunde("kunde-a", zuordnung={**NEU, "kunden": [weiterer_id]})])
    assert r.status_code == 200, r.text
    assert _stand(session_factory)[2].customer_ids == sorted([cid, weiterer_id])
    # derselbe Mandant, jetzt als bestehender gewaehlt (mit kunde_anlegen): kein Umzug, kein neuer
    r = _put(api, [_kunde("kunde-a", zuordnung={"mandant": tid, "kunden": [], "rolle": "viewer",
                                                "kunde_anlegen": True, "mandant_anlegen": False,
                                                "mandant_name": "egal"})])  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["umzug"] == [] and _stand(session_factory)[2].customer_ids == [cid]


# --- Umzug aus einem anderen Mandanten ---------------------------------------------------------


def _im_ersten(api, session_factory) -> tuple[int, int, int]:
    """Bisheriger Stand: Kunde im ersten Mandanten angelegt (`kunde_anlegen`), dazu ein von Hand
    gewaehlter Kunde. Liefert (erster Mandant, angelegter Kunde, von Hand gewaehlter)."""
    erster, (hand,) = _erster(session_factory, kunden=("Von Hand",))
    z = {"mandant": erster, "kunden": [hand], "rolle": "viewer", "kunde_anlegen": True}
    assert _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=z)]).status_code == 200
    with session_factory() as s:
        alt = s.query(CockpitCustomer).filter_by(short_name="kunde-a").one().customer_id
    return erster, alt, hand


def test_umzug_ohne_daten(api, session_factory):
    erster, alt, hand = _im_ersten(api, session_factory)
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=NEU)])
    assert r.status_code == 200, r.text
    tid, cid, pc = _stand(session_factory)
    assert r.json()["umzug"] == [
        {
            "kurzname": "kunde-a",
            "kunde": alt,
            "name": "Kunde A",
            "mandant": erster,
            "stand": "entfernt",
            "grund": None,
        }
    ]
    assert (pc.tenant_id, pc.customer_ids) == (tid, [cid])
    with session_factory() as s:
        assert s.get(Customer, alt) is None  # ohne Daten: entfernt
        assert s.get(Customer, hand) is not None  # von Hand gewaehlt: bleibt immer
        assert s.query(CockpitCustomer).filter_by(tenant_id=erster).count() == 0
    assert [d["customer_id"] for _, _, d in _audit(session_factory, "customer.deleted")] == [alt]
    assert _audit(session_factory, "portal.customer.umzug") == [("cockpit", "kunde-a", {
        "von_mandant": erster, "nach_mandant": tid, "quelle": "cockpit", "entfernt": [alt],
        "bleibt": []})]  # fmt: skip
    # noch einmal: kein Umzug mehr
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=NEU)])
    assert r.json()["umzug"] == [] and r.json()["unveraendert"] == 1


@pytest.mark.parametrize("grund", ["hat_daten", "zugeordnet"])
def test_umzug_mit_daten_bleibt_und_wird_gemeldet(api, session_factory, grund):
    erster, alt, hand = _im_ersten(api, session_factory)
    andere = []
    if grund == "hat_daten":
        with session_factory() as s:
            s.add(CustomerSelector(customer_id=alt, kind="prefix", value="10.1.0.0/16"))
            s.commit()
    else:  # ein anderer Portal-Kunde nennt ihn
        andere = [_kunde("kunde-b", zuordnung={"mandant": erster, "kunden": [alt],
                                                "rolle": "viewer"})]  # fmt: skip
        assert _put(api, [_kunde("kunde-a", name="Kunde A",
                                 zuordnung={"mandant": erster, "kunden": [hand],
                                            "rolle": "viewer", "kunde_anlegen": True}),
                          *andere]).status_code == 200  # fmt: skip
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=NEU), *andere])
    assert r.status_code == 200, r.text
    tid, cid, pc = _stand(session_factory)
    assert r.json()["umzug"] == [
        {
            "kurzname": "kunde-a",
            "kunde": alt,
            "name": "Kunde A",
            "mandant": erster,
            "stand": "bleibt",
            "grund": grund,
        }
    ]
    assert (pc.tenant_id, pc.customer_ids) == (tid, [cid])  # der Umzug selbst gilt
    with session_factory() as s:
        assert s.get(Customer, alt) is not None
    assert _audit(session_factory, "customer.deleted") == []
    assert _audit(session_factory, "portal.customer.umzug")[0][2]["bleibt"] == [
        {"kunde": alt, "grund": grund}]  # fmt: skip
    # das Inventar meldet ihn weiter (Seite Aufraeumen im Cockpit)
    ist = {k["id"]: k for k in api.get("/inventory", headers=AUTH).json()["ist"]["automato_kunden"]}
    assert (ist[alt]["mandant"], ist[alt]["von_cockpit"], ist[alt]["portal_kunde"]) == (
        erster, True, "kunde-a")  # fmt: skip
    # spaeter ohne Daten: Automato entfernt ihn nicht still (nur beim Wechsel)
    with session_factory() as s:
        s.query(CustomerSelector).delete()
        s.commit()
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=NEU)])
    assert r.status_code == 200 and r.json()["umzug"] == []
    with session_factory() as s:
        assert s.get(Customer, alt) is not None


def test_umzug_nach_geloeschtem_mandanten_legt_neu_an(api, session_factory):
    _erster(session_factory)
    _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=NEU)])
    tid, _, _ = _stand(session_factory)
    r = api.delete(f"/tenants/{tid}", params={"confirm": "Kunde A"})
    assert r.status_code == 200, r.text
    with session_factory() as s:
        assert s.query(CockpitTenant).count() == 0
        assert s.query(PortalCustomer).one().tenant_id is None  # faellt mit (SET NULL)
    r = _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=NEU)])
    assert r.status_code == 200, r.text
    neu, cid, pc = _stand(session_factory)
    assert (pc.tenant_id, pc.customer_ids) == (neu, [cid]) and r.json()["umzug"] == []
    assert len(r.json()["mandanten_angelegt"]) == 1


# --- Inventar und Kunden-Portal -----------------------------------------------------------------


def test_inventar_und_portal_nennen_den_mandanten(api, session_factory):
    erster, _ = _erster(session_factory)
    _put(
        api,
        [
            _kunde("kunde-a", name="Kunde A", zuordnung=NEU),
            _kunde("kunde-b", zuordnung={"mandant": erster, "rolle": "viewer"}),
        ],
    )
    tid, cid, _ = _stand(session_factory)
    (api.state_dir / "topologie.json").write_text(json.dumps(
        {"kunden": [{"kurzname": "kunde-a"}, {"kurzname": "kunde-b"}]}))  # fmt: skip
    inv = api.get("/inventory", headers=AUTH).json()
    assert inv["automato"]["funktionen"] == ["mandant_anlegen"]
    mandanten = {m["id"]: m for m in inv["automato"]["mandanten"]}
    assert mandanten[tid] == {"id": tid, "name": "Kunde A", "portal_kunde": "kunde-a",
                              "kunden": [{"id": cid, "name": "Kunde A"}]}  # fmt: skip
    assert mandanten[erster]["portal_kunde"] is None
    kunden = {k["kurzname"]: k for k in inv["kunden"]}
    z = kunden["kunde-a"]["zuordnung"]
    assert (z["mandant"], z["kunden"], z["kunde_angelegt"], z["von"]) == (
        tid, [cid], cid, "cockpit")  # fmt: skip
    assert z["mandant_angelegt"] == {"id": tid, "name": "Kunde A"}
    assert kunden["kunde-a"]["automato_zugeordnet"] is True
    assert kunden["kunde-b"]["zuordnung"]["mandant_angelegt"] is None

    listing = {c["short_name"]: c for c in api.get("/portal/customers").json()["customers"]}
    assert listing["kunde-a"]["mandant_angelegt"] == {"id": tid, "name": "Kunde A"}
    assert listing["kunde-b"]["mandant_angelegt"] is None


# --- Abbau: der Kunde darin geht, der Mandant bleibt -------------------------------------------


def test_abbau_entfernt_den_kunden_nie_den_mandanten(api, session_factory):
    _erster(session_factory)
    _put(api, [_kunde("kunde-a", name="Kunde A", zuordnung=NEU)])
    tid, cid, _ = _stand(session_factory)
    # mandant_anlegen gilt wie kunde_anlegen: der Teil `automato_kunde` ist aktiv
    r = _put(api, [_kunde("kunde-a", name="Kunde A", werkzeuge=[], zuordnung=NEU)],
             entfernen=[{"kurzname": "kunde-a", "automato_kunde": True}])  # fmt: skip
    assert r.json()["entfernen"]["abgelehnt"] == [
        {"kurzname": "kunde-a", "grund": "aktiv", "teil": "automato_kunde"}]  # fmt: skip
    # nach der Frist: Kunde weg, Mandant bleibt (leer, nur von Hand loeschen)
    r = _put(api, [], entfernen=[{"kurzname": "kunde-a", "automato_kunde": True}])
    assert r.json()["entfernen"]["angenommen"] == ["kunde-a"]
    with session_factory() as s:
        assert s.get(Customer, cid) is None
        assert s.get(Tenant, tid) is not None
        assert s.query(CockpitTenant).one().tenant_id == tid
    inv = api.get("/inventory", headers=AUTH).json()
    assert {m["id"]: m["portal_kunde"] for m in inv["automato"]["mandanten"]}[tid] == "kunde-a"


# --- Benutzer des Kunden-Realms -----------------------------------------------------------------


@pytest.fixture
def keys():
    return {name: rsa.generate_private_key(public_exponent=65537, key_size=2048)
            for name in ("haupt", "kunde-a")}  # fmt: skip


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


def _token(key, realm: str, user: str = "max.muster", **claims) -> str:
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())  # fmt: skip
    body = {"iss": f"https://kc.example.ch/realms/{realm}", "aud": "automato-api",
            "sub": f"id-{user}", "preferred_username": user, **claims}  # fmt: skip
    return jwt.encode(body, pem, algorithm="RS256")


def test_realm_benutzer_sehen_nur_den_eigenen_mandanten(oidc, session_factory, keys):
    erster, (fremd,) = _erster(session_factory, kunden=("Kunde X",))
    with session_factory() as s:
        erg = freigaben.uebernehmen(s, {"vertrag": 1, "kunden": [
            _kunde("kunde-a", name="Kunde A", zuordnung=NEU,
                   benutzer=[{"benutzer": "max.muster", "rolle": "editor"}])]})  # fmt: skip
        assert erg.mandanten_angelegt
        s.commit()
    realms.invalidate()
    tid, cid, _ = _stand(session_factory)
    # auch eine Rolle oder Mandanten im Token zaehlen nicht (Rechte nur aus dem Kunden-Portal)
    auth = {"Authorization": "Bearer " + _token(keys["kunde-a"], "kunde-a",
                                                customer_role="customer_admin",
                                                tenants=[erster])}  # fmt: skip
    me = oidc.get("/auth/me", headers=auth).json()
    assert (me["tenant_ids"], me["customer_ids"], me["customer_role"]) == ([tid], [cid], "editor")
    assert me["is_admin"] is False
    assert [t["id"] for t in oidc.get("/tenants", headers=auth).json()] == [tid]
    assert oidc.get("/customers", params={"tenant_id": erster}, headers=auth).status_code == 403
    kunden = oidc.get("/customers", params={"tenant_id": tid}, headers=auth).json()
    assert fremd not in [k["id"] for k in kunden]
