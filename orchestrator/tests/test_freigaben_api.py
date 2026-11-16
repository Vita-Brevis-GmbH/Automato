"""Freigaben aus dem Cockpit (1.11.0, ADR 0054): PUT /freigaben nur mit dem Token des Cockpits.

Das Cockpit schickt den ganzen Stand seiner Kunden; Automato fuehrt sein Kunden-Portal nach -
idempotent, alles oder nichts, Uebernahme vorhandener Kunden (Mandant, Kunden, Rolle, Kacheln
bleiben), Entfernen nur von Kunden aus dem Cockpit. Vertrag im Cockpit:
docs/vertraege/freigaben.md (v1)."""

import base64
import functools
import hashlib
import json

import pytest
from app.audit import decode_detail
from app.auth import realms
from app.config import Settings, get_settings
from app.models import AuditLog, Customer, IpamType, PortalCustomer, Tenant
from app.models.tenant import DnsType
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

TOKEN = "c" * 64
AUTH = {"Authorization": f"Bearer {TOKEN}"}


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
            "werkzeuge": kw.pop("werkzeuge", ["nb"]), "zugriff": kw.pop("zugriff", "ueberall"),
            "netze": kw.pop("netze", []), "realm": kw.pop("realm", None), **kw}  # fmt: skip


def _put(client, kunden: list[dict], **extra):
    return client.put("/freigaben", json={"vertrag": 1, "kunden": kunden, **extra}, headers=AUTH)


def _audit(session_factory) -> list[tuple[str, str, str, dict | None]]:
    with session_factory() as s:
        rows = s.query(AuditLog).order_by(AuditLog.id).all()
        return [(a.actor, a.action, a.subject, decode_detail(a)) for a in rows]


def _soll(tmp_path) -> dict:
    data = json.loads((tmp_path / "soll.json").read_text(encoding="utf-8"))
    return {k["kurzname"]: k for k in data["kunden"]}


def _tenant(session) -> tuple[int, int]:
    t = Tenant(name="dc", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
    session.add(t)
    session.flush()
    c = Customer(tenant_id=t.id, name="Kunde dc")
    session.add(c)
    session.flush()
    return t.id, c.id


def test_nur_mit_token(api, tmp_path):
    body = {"vertrag": 1, "kunden": []}
    assert api.put("/freigaben", json=body).status_code == 401
    falsch = api.put("/freigaben", json=body, headers={"Authorization": "Bearer " + "d" * 64})
    assert falsch.status_code == 401
    assert (
        api.put("/freigaben", json=body, headers={"Authorization": f"Token {TOKEN}"}).status_code
        == 401
    )
    # ohne eingerichtetes Token gibt es den Endpunkt nicht - auch nicht fuer einen Admin
    api.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path, cockpit_token="")
    aus = api.put("/freigaben", json=body, headers={**AUTH, "X-Dev-Roles": "admin"})
    assert aus.status_code == 404


def test_anlegen_idempotent_aendern_entfernen(api, session_factory, tmp_path):
    kunden = [
        _kunde("kunde-a", werkzeuge=["doc", "nb", "um"], zugriff="netze",
               netze=["203.0.113.0/24", "198.51.100.7"], realm="kunde-a"),
        _kunde("kunde-b", werkzeuge=[], zugriff="intern"),
    ]  # fmt: skip
    r = _put(api, kunden)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["vertrag"] == 1 and body["angelegt"] == ["kunde-a", "kunde-b"]
    assert (body["geaendert"], body["uebernommen"], body["entfernt"], body["unveraendert"]) == (
        [], [], [], 0)  # fmt: skip
    # soll.json geschrieben, der Host-Agent hat ihn noch nicht uebernommen
    assert body["front"] == {"aktuell": False, "hinweis": None}
    soll = _soll(tmp_path)
    assert soll["kunde-a"] | {"links": None} == {
        "kurzname": "kunde-a", "name": "Kunde kunde-a", "doc": True, "nb": True, "um": True,
        "cmdb": False, "realm": "kunde-a", "zugriff": "netze",
        "netze": ["203.0.113.0/24", "198.51.100.7/32"], "links": None, "quelle": "cockpit",
    }  # fmt: skip
    assert soll["kunde-b"]["nb"] is False and soll["kunde-b"]["zugriff"] == "intern"
    audit = _audit(session_factory)
    assert [(a, act, subj) for a, act, subj, _ in audit] == [
        ("cockpit", "portal.customer.created", "kunde-a"),
        ("cockpit", "portal.customer.created", "kunde-b"),
    ]
    assert audit[0][3]["quelle"] == "cockpit" and audit[0][3]["realm"] == "kunde-a"
    with session_factory() as s:
        pc = s.query(PortalCustomer).filter_by(short_name="kunde-a").one()
        assert (pc.source, pc.updated_by, pc.tenant_id, pc.links) == (
            "cockpit",
            "cockpit",
            None,
            [],
        )

    # der Agent meldet den Stand -> aktuell; dieselbe Anfrage noch einmal: nichts, kein Audit,
    # soll.json bleibt (der Agent rechnet nicht neu)
    digest = hashlib.sha256((tmp_path / "soll.json").read_bytes()).hexdigest()
    (tmp_path / "status.json").write_text(json.dumps({"ok": True, "soll_sha256": digest}))
    vorher = (tmp_path / "soll.json").read_bytes()
    r = _put(api, kunden)
    assert r.status_code == 200
    body = r.json()
    assert body["unveraendert"] == 2 and not (body["angelegt"] or body["geaendert"])
    assert body["front"] == {"aktuell": True, "hinweis": None}
    assert (tmp_path / "soll.json").read_bytes() == vorher
    assert len(_audit(session_factory)) == 2

    # aendern: Name und Werkzeuge; kunde-b faellt weg -> entfernt
    r = _put(api, [_kunde("kunde-a", name="Kunde A neu", werkzeuge=["doc"], zugriff="netze",
                          netze=["203.0.113.0/24", "198.51.100.7"], realm="kunde-a")])  # fmt: skip
    body = r.json()
    assert body["geaendert"] == ["kunde-a"] and body["entfernt"] == ["kunde-b"]
    assert body["front"]["aktuell"] is False
    assert set(_soll(tmp_path)) == {"kunde-a"}
    assert _soll(tmp_path)["kunde-a"]["nb"] is False
    audit = _audit(session_factory)
    upd = [d for _, act, subj, d in audit if act == "portal.customer.updated"]
    assert upd == [{"name": "Kunde A neu", "nb": False, "um": False, "quelle": "cockpit"}]
    assert ("cockpit", "portal.customer.deleted", "kunde-b") in [a[:3] for a in audit]

    # leere Liste: alle Kunden aus dem Cockpit weg
    assert _put(api, []).json()["entfernt"] == ["kunde-a"]
    assert _soll(tmp_path) == {}


def test_uebernahme_behaelt_mandant_kunden_rolle_kacheln(api, session_factory, tmp_path):
    with session_factory() as s:
        tid, cid = _tenant(s)
        s.add(
            PortalCustomer(
                short_name="kunde-a",
                name="A alt",
                doc=True,
                realm="kunde-a",
                tenant_id=tid,
                customer_ids=[cid],
                default_role="triage",
                links=[{"title": "Handbuch", "url": "https://h.example.ch", "text": ""}],
            )
        )
        s.add(PortalCustomer(short_name="vb", name="VB", links=[], customer_ids=[]))
        s.commit()  # fmt: skip
    r = _put(api, [_kunde("kunde-a", name="Kunde A", werkzeuge=["doc", "nb"], realm="kunde-a")])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["uebernommen"] == ["kunde-a"] and body["entfernt"] == []
    with session_factory() as s:
        pc = s.query(PortalCustomer).filter_by(short_name="kunde-a").one()
        assert (pc.source, pc.name, pc.tenant_id, pc.customer_ids, pc.default_role) == (
            "cockpit", "Kunde A", tid, [cid], "triage")  # fmt: skip
        assert pc.links == [{"title": "Handbuch", "url": "https://h.example.ch", "text": ""}]
        # Kunden aus der Oberflaeche, die nicht in der Anfrage stehen, bleiben unberuehrt
        assert s.query(PortalCustomer).filter_by(short_name="vb").one().source == "oberflaeche"
    detail = [d for _, act, _, d in _audit(session_factory) if act == "portal.customer.updated"]
    assert detail == [{"name": "Kunde A", "quelle": "cockpit", "uebernommen": True}]
    soll = _soll(tmp_path)
    assert soll["kunde-a"]["quelle"] == "cockpit" and soll["vb"]["quelle"] == "oberflaeche"
    assert soll["kunde-a"]["links"][0]["title"] == "Handbuch"
    # auch eine leere Anfrage entfernt den Kunden aus der Oberflaeche nicht
    assert _put(api, []).json()["entfernt"] == ["kunde-a"]
    assert set(_soll(tmp_path)) == {"vb"}


def test_realms_tauschen(api, session_factory):
    """Zwei Kunden aus dem Cockpit tauschen ihre Realms - eindeutig auch beim Schreiben."""
    _put(api, [_kunde("kunde-a", realm="ra"), _kunde("kunde-b", realm="rb")])
    r = _put(api, [_kunde("kunde-a", realm="rb"), _kunde("kunde-b", realm="ra")])
    assert r.status_code == 200, r.text
    assert r.json()["geaendert"] == ["kunde-a", "kunde-b"]
    with session_factory() as s:
        realm = {pc.short_name: pc.realm for pc in s.query(PortalCustomer)}
    assert realm == {"kunde-a": "rb", "kunde-b": "ra"}


@pytest.mark.parametrize(
    ("kunde", "grund"),
    [
        (_kunde("start"), "reserviert"),
        (_kunde("konsole"), "reserviert"),
        (_kunde("kunde-nb"), "reserviert"),
        (_kunde("Kunde_A"), "ungültig"),
        (_kunde("kunde-x", name="A\tB"), "Tabulatoren"),
        (_kunde("kunde-x", name=""), "Name fehlt"),
        (_kunde("kunde-x", werkzeuge=["ssh"]), "Werkzeug"),
        (_kunde("kunde-x", werkzeuge="doc"), "keine Liste"),
        (_kunde("kunde-x", zugriff="vpn"), "Zugriff"),
        (_kunde("kunde-x", zugriff="netze", netze=[]), "mindestens ein Netz"),
        (_kunde("kunde-x", zugriff="netze", netze=["kein-netz"]), "ungültig"),
        (_kunde("kunde-x", realm="master"), "Realm"),
        ({"name": "ohne Kurzname"}, "Kurzname fehlt"),
    ],
)
def test_alles_oder_nichts(api, session_factory, tmp_path, kunde, grund):
    gut = _kunde("kunde-a", realm="kunde-a")
    r = _put(api, [gut, kunde])
    assert r.status_code == 422, r.text
    fehler = r.json()["fehler"]
    assert len(fehler) == 1 and grund in fehler[0]["grund"]
    assert fehler[0]["kurzname"] == (kunde.get("kurzname") or None)
    # nichts angelegt, nichts geschrieben, kein Audit
    with session_factory() as s:
        assert s.query(PortalCustomer).count() == 0
    assert not (tmp_path / "soll.json").exists() and _audit(session_factory) == []


@pytest.mark.parametrize(
    ("body", "grund"),
    [
        ({"vertrag": 2, "kunden": []}, "vertrag"),
        ({"vertrag": True, "kunden": []}, "vertrag"),
        ({"kunden": []}, "vertrag"),
        ({"vertrag": 1, "kunden": {}}, "keine Liste"),
        ({"vertrag": 1, "kunden": ["x"]}, "kein Objekt"),
        ({"vertrag": 1, "kunden": [_kunde("a")] * 2}, "doppelt"),
        ({"vertrag": 1, "kunden": [_kunde("a", realm="r"), _kunde("b", realm="r")]}, "doppelt"),
        ({"vertrag": 1, "kunden": [_kunde(f"k{i}") for i in range(1001)]}, "höchstens 1000"),
        ([], "kein JSON-Objekt"),
    ],
)
def test_anfrage_wird_geprueft(api, body, grund):
    r = api.put("/freigaben", json=body, headers=AUTH)
    assert r.status_code == 422, r.text
    assert grund in r.json()["fehler"][0]["grund"]


def test_realm_eines_kunden_aus_der_oberflaeche(api, session_factory):
    with session_factory() as s:
        s.add(PortalCustomer(short_name="vb", name="VB", realm="vb", links=[], customer_ids=[]))
        s.add(PortalCustomer(short_name="alt", name="Alt", realm="alt", source="cockpit",
                             links=[], customer_ids=[]))  # fmt: skip
        s.commit()
    r = _put(api, [_kunde("kunde-a", realm="vb"), _kunde("kunde-b", realm="alt")])
    assert r.status_code == 422
    # der Realm eines Kunden aus dem Cockpit, der wegfaellt, ist frei - aus der Oberflaeche nicht
    assert r.json()["fehler"] == [{"kurzname": "kunde-a", "grund": r.json()["fehler"][0]["grund"]}]
    assert "'vb'" in r.json()["fehler"][0]["grund"]
    # steht der Kunde aus der Oberflaeche selbst in der Anfrage (Uebernahme), gibt er ihn ab
    r = _put(api, [_kunde("kunde-a", realm="vb"), _kunde("vb", realm=None),
                   _kunde("kunde-b", realm="alt")])  # fmt: skip
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["angelegt"] == ["kunde-a", "kunde-b"] and body["uebernommen"] == ["vb"]
    assert body["entfernt"] == ["alt"]


def test_front_ohne_ordner_bzw_mit_fehler_des_agenten(api, tmp_path):
    api.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path, front_state_dir="")
    r = _put(api, [_kunde("kunde-a")])
    assert r.status_code == 200 and r.json()["front"]["aktuell"] is None
    assert "AUTOMATO_FRONT_STATE_DIR" in r.json()["front"]["hinweis"]

    api.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path)
    _put(api, [_kunde("kunde-a"), _kunde("kunde-b")])
    digest = hashlib.sha256((tmp_path / "soll.json").read_bytes()).hexdigest()
    (tmp_path / "status.json").write_text(
        json.dumps({"ok": False, "soll_sha256": digest, "meldung": "Kurzname ungültig"})
    )
    front = _put(api, [_kunde("kunde-a"), _kunde("kunde-b")]).json()["front"]
    assert front["aktuell"] is False and "Kurzname ungültig" in front["hinweis"]

    # Ordner nicht beschreibbar: gespeichert, Hinweis
    api.app.dependency_overrides[get_settings] = lambda: _settings(
        tmp_path, front_state_dir=str(tmp_path / "fehlt")
    )
    front = _put(api, [_kunde("kunde-a")]).json()["front"]
    assert front["aktuell"] is None and "nicht geschrieben" in front["hinweis"]


# --- Kunden aus dem Cockpit in der Oberflaeche (Administration -> Kunden-Portal) -------------


def test_oberflaeche_aendert_nur_mandant_kunden_rolle_kacheln(api, session_factory, tmp_path):
    with session_factory() as s:
        tid, cid = _tenant(s)
        s.commit()
    _put(api, [_kunde("kunde-a", werkzeuge=["doc"], zugriff="intern", realm="kunde-a")])
    listing = api.get("/portal/customers").json()["customers"]
    pc = listing[0]
    assert pc["source"] == "cockpit" and pc["nb"] is False
    assert "netbox" not in pc["addresses"] and "doc" in pc["addresses"]  # NetBox nur mit nb
    r = api.put(f"/portal/customers/{pc['id']}", json={
        "short_name": "anders", "name": "Anders", "doc": False, "nb": True, "um": True,
        "realm": "fremd", "access": "ueberall", "tenant_id": tid, "customer_ids": [cid],
        "default_role": "editor",
        "links": [{"title": "Handbuch", "url": "https://h.example.ch", "text": ""}],
    })  # fmt: skip
    assert r.status_code == 200, r.text
    neu = r.json()
    assert (neu["short_name"], neu["name"], neu["doc"], neu["nb"], neu["um"]) == (
        "kunde-a", "Kunde kunde-a", True, False, False)  # fmt: skip
    assert (neu["realm"], neu["access"], neu["source"]) == ("kunde-a", "intern", "cockpit")
    assert (neu["tenant_id"], neu["customer_ids"], neu["default_role"]) == (tid, [cid], "editor")
    assert neu["links"][0]["title"] == "Handbuch"
    # die naechste Freigabe aendert nichts an dem, was Automato pflegt
    body = _put(api, [_kunde("kunde-a", werkzeuge=["doc"], zugriff="intern",
                             realm="kunde-a")]).json()  # fmt: skip
    assert body["unveraendert"] == 1
    with session_factory() as s:
        pc_db = s.query(PortalCustomer).one()
        assert (pc_db.tenant_id, pc_db.default_role, len(pc_db.links)) == (tid, "editor", 1)

    # entfernen nur im Cockpit
    weg = api.delete(f"/portal/customers/{pc['id']}")
    assert weg.status_code == 409 and "im Cockpit entfernen" in weg.json()["detail"]
    assert "kunde-a" in _soll(tmp_path)


def test_oberflaeche_waehlt_netbox(api, tmp_path):
    """1.11.0: NetBox (-nb) als Werkzeug - Vorgabe an, abwaehlbar."""
    r = api.post("/portal/customers", json={"short_name": "kunde-a", "name": "A"})
    assert r.status_code == 201 and r.json()["nb"] is True and r.json()["source"] == "oberflaeche"
    assert r.json()["addresses"]["netbox"] == "https://kunde-a-nb.mgmt.example.ch"
    r = api.put(f"/portal/customers/{r.json()['id']}",
                json={"short_name": "kunde-a", "name": "A", "nb": False})  # fmt: skip
    assert r.status_code == 200 and "netbox" not in r.json()["addresses"]
    assert _soll(tmp_path)["kunde-a"]["nb"] is False


# --- Client-Secrets des Anmelde-Tors versiegelt aus dem Cockpit (1.13.0, ADR 0055) -----------


@functools.cache
def _empfang() -> tuple[rsa.RSAPrivateKey, str, str]:
    """Empfangsschluessel wie front.sh (RSA 3072): privater Schluessel, PEM, Kennung."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pub = key.public_key()
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    pem = pub.public_bytes(serialization.Encoding.PEM, spki)
    der = pub.public_bytes(serialization.Encoding.DER, spki)
    return key, pem.decode(), hashlib.sha256(der).hexdigest()


def _versiegeln(realm: str, secret: str = "testwert-nur-fuer-tests-01") -> str:  # noqa: S107
    _, pem, _ = _empfang()
    pub = serialization.load_pem_public_key(pem.encode())
    klar = json.dumps({"realm": realm, "client": "automato-front", "secret": secret},
                      separators=(",", ":")).encode()  # fmt: skip
    oaep = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    return base64.b64encode(pub.encrypt(klar, oaep)).decode()


def _front_status(tmp_path, tor: bool = True, schluessel: bool = True) -> str:
    """topologie.json wie front.sh erzeugen (Anmelde-Tor, Empfangsschluessel); Rueckgabe Kennung."""
    _, pem, kennung = _empfang()
    empfang = {"alg": "RSA-OAEP-256", "pem": pem, "kennung": kennung} if schluessel else None
    (tmp_path / "topologie.json").write_text(
        json.dumps({"tor": tor, "tor_empfang": empfang, "kunden": []}), encoding="utf-8"
    )
    return kennung


def _siegel(realm: str, kennung: str | None = None, **kw) -> dict:
    return {"realm": realm, "kennung": kennung or _empfang()[2],
            "versiegelt": kw.pop("versiegelt", None) or _versiegeln(realm), **kw}  # fmt: skip


def test_tor_geheimnisse_fehlen_keine_aussage(api, tmp_path):
    _front_status(tmp_path)
    r = _put(api, [_kunde("kunde-a", realm="kunde-a")])
    assert r.status_code == 200 and r.json()["tor_geheimnisse"] is None
    r = _put(api, [_kunde("kunde-a", realm="kunde-a")], tor_geheimnisse=None)
    assert r.status_code == 200 and r.json()["tor_geheimnisse"] is None
    assert "tor_geheimnisse" not in json.loads((tmp_path / "soll.json").read_text())


def test_tor_geheimnisse_nur_chiffrat_idempotent_audit(api, session_factory, tmp_path):
    kennung = _front_status(tmp_path)
    kunden = [_kunde("kunde-a", realm="kunde-a"), _kunde("kunde-b", realm="kunde-b")]
    siegel = _siegel("kunde-a")
    r = _put(api, kunden, tor_geheimnisse=[siegel])
    assert r.status_code == 200, r.text
    assert r.json()["tor_geheimnisse"] == {"uebernommen": ["kunde-a"], "abgelehnt": []}
    soll = json.loads((tmp_path / "soll.json").read_text(encoding="utf-8"))
    assert soll["tor_geheimnisse"] == [siegel]  # dieselben Felder, nur das Chiffrat
    with session_factory() as s:
        assert s.query(PortalCustomer).filter_by(short_name="kunde-a").one().tor_secret == siegel
    tor_audit = [(a, subj, d) for a, act, subj, d in _audit(session_factory)
                 if act == "portal.tor_secret.updated"]  # fmt: skip
    assert tor_audit == [("cockpit", "kunde-a", {"realm": "kunde-a", "kennung": kennung})]
    assert siegel["versiegelt"] not in json.dumps(_audit(session_factory))

    # dasselbe Chiffrat noch einmal: kein Audit, kein neuer Soll-Stand
    vorher, anzahl = (tmp_path / "soll.json").read_bytes(), len(_audit(session_factory))
    r = _put(api, kunden, tor_geheimnisse=[siegel])
    assert r.json()["tor_geheimnisse"] == {"uebernommen": ["kunde-a"], "abgelehnt": []}
    assert r.json()["unveraendert"] == 2
    assert (tmp_path / "soll.json").read_bytes() == vorher
    assert len(_audit(session_factory)) == anzahl
    # ohne das Feld bzw. ohne den Realm: keine Aussage, das Chiffrat bleibt
    assert _put(api, kunden).json()["tor_geheimnisse"] is None
    assert _put(api, kunden, tor_geheimnisse=[]).json()["tor_geheimnisse"] == {
        "uebernommen": [], "abgelehnt": []}  # fmt: skip
    assert json.loads((tmp_path / "soll.json").read_text())["tor_geheimnisse"] == [siegel]

    # erneuert (neues Chiffrat): ein Audit, neuer Soll-Stand
    neu = _siegel("kunde-a", versiegelt=_versiegeln("kunde-a", "testwert-erneuert-02"))
    r = _put(api, kunden, tor_geheimnisse=[neu, _siegel("kunde-b")])
    assert r.json()["tor_geheimnisse"]["uebernommen"] == ["kunde-a", "kunde-b"]
    assert len(_audit(session_factory)) == anzahl + 2
    eintraege = json.loads((tmp_path / "soll.json").read_text())["tor_geheimnisse"]
    assert [e["realm"] for e in eintraege] == ["kunde-a", "kunde-b"] and eintraege[0] == neu


def test_tor_geheimnis_mit_veralteter_kennung_abgelehnt(api, session_factory, tmp_path):
    """Kennung passt nicht zum Empfangsschluessel der VM: nur dieser Eintrag nicht - die Anfrage
    gelingt, das Cockpit versiegelt mit dem Schluessel aus dem naechsten Inventar neu."""
    _front_status(tmp_path)
    alt = _siegel("kunde-a", kennung="0" * 64)
    r = _put(api, [_kunde("kunde-a", realm="kunde-a"), _kunde("kunde-b", realm="kunde-b")],
             tor_geheimnisse=[alt, _siegel("kunde-b")])  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["angelegt"] == ["kunde-a", "kunde-b"]
    assert r.json()["tor_geheimnisse"] == {
        "uebernommen": ["kunde-b"],
        "abgelehnt": [{"realm": "kunde-a", "grund": "schluessel_veraltet"}],
    }
    soll = json.loads((tmp_path / "soll.json").read_text())
    assert [e["realm"] for e in soll["tor_geheimnisse"]] == ["kunde-b"]
    with session_factory() as s:
        assert s.query(PortalCustomer).filter_by(short_name="kunde-a").one().tor_secret is None


@pytest.mark.parametrize("stand", ["ohne_datei", "tor_aus", "ohne_schluessel", "ohne_ordner"])
def test_tor_geheimnis_ohne_empfangsschluessel(api, tmp_path, stand):
    if stand == "tor_aus":
        _front_status(tmp_path, tor=False)
    elif stand == "ohne_schluessel":
        _front_status(tmp_path, schluessel=False)
    elif stand == "ohne_ordner":
        api.app.dependency_overrides[get_settings] = lambda: _settings(tmp_path, front_state_dir="")
    r = _put(api, [_kunde("kunde-a", realm="kunde-a")], tor_geheimnisse=[_siegel("kunde-a")])
    assert r.status_code == 200, r.text
    abgelehnt = [{"realm": "kunde-a", "grund": "kein_empfang"}]
    assert r.json()["tor_geheimnisse"] == {"uebernommen": [], "abgelehnt": abgelehnt}


@pytest.mark.parametrize(
    ("tor", "grund"),
    [
        ({}, "keine Liste"),
        (["x"], "kein Objekt"),
        ([_siegel("fremd", kennung="1" * 64, versiegelt="QUJD")], "keinem Kunden der Anfrage"),
        ([_siegel("vb", kennung="1" * 64, versiegelt="QUJD")], "keinem Kunden der Anfrage"),
        ([{"kennung": "1" * 64, "versiegelt": "QUJD"}], "keinem Kunden der Anfrage"),
        ([_siegel("kunde-a", kennung="1" * 64, versiegelt="QUJD")] * 2, "doppelt"),
        ([_siegel("kunde-a", kennung="A" * 64, versiegelt="QUJD")], "kennung"),
        ([_siegel("kunde-a", kennung="1" * 63, versiegelt="QUJD")], "kennung"),
        ([_siegel("kunde-a", kennung="1" * 64 + "\n", versiegelt="QUJD")], "kennung"),
        ([_siegel("kunde-a", kennung="1" * 64, versiegelt="kein base64!")], "Base64"),
        ([_siegel("kunde-a", kennung="1" * 64, versiegelt="QUJ")], "Base64"),
        ([_siegel("kunde-a", kennung="1" * 64, versiegelt="QUJD" * 257)], "Base64"),
        ([_siegel("kunde-a", kennung="1" * 64, versiegelt=5)], "Base64"),
        ([_siegel("kunde-a", kennung="1" * 64, versiegelt="QUJD")] * 3, "höchstens so viele"),
    ],
)
def test_tor_geheimnisse_alles_oder_nichts(api, session_factory, tmp_path, tor, grund):
    with session_factory() as s:  # Realm eines Kunden aus der Oberflaeche, nicht in der Anfrage
        s.add(PortalCustomer(short_name="vb", name="VB", realm="vb", links=[], customer_ids=[]))
        s.commit()
    _front_status(tmp_path)
    r = _put(api, [_kunde("kunde-a", realm="kunde-a"), _kunde("kunde-b")], tor_geheimnisse=tor)
    assert r.status_code == 422, r.text
    fehler = r.json()["fehler"]
    assert len(fehler) == 1 and grund in fehler[0]["grund"], fehler
    with session_factory() as s:
        assert s.query(PortalCustomer).count() == 1  # nur vb, nichts angelegt
    assert not (tmp_path / "soll.json").exists()
    assert [a for a in _audit(session_factory) if a[1].startswith("portal.")] == []


def test_tor_geheimnis_gilt_nur_fuer_seinen_realm(api, session_factory, tmp_path):
    """Wechselt der Realm des Kunden, faellt das Siegel weg; entfernt das Cockpit den Kunden,
    auch."""
    _front_status(tmp_path)
    _put(api, [_kunde("kunde-a", realm="ra")], tor_geheimnisse=[_siegel("ra")])
    assert "tor_geheimnisse" in json.loads((tmp_path / "soll.json").read_text())
    r = _put(api, [_kunde("kunde-a", realm="rb")])
    assert r.json()["geaendert"] == ["kunde-a"]
    assert "tor_geheimnisse" not in json.loads((tmp_path / "soll.json").read_text())
    with session_factory() as s:
        assert s.query(PortalCustomer).one().tor_secret is None
    _put(api, [_kunde("kunde-a", realm="rb")], tor_geheimnisse=[_siegel("rb")])
    assert _put(api, []).json()["entfernt"] == ["kunde-a"]
    assert "tor_geheimnisse" not in json.loads((tmp_path / "soll.json").read_text())


# --- Zuordnung in Automato aus dem Cockpit (1.13.0, ADR 0054 Nachtrag) ------------------------


def test_zuordnung_aus_dem_cockpit_idempotent_und_nur_lesbar(api, session_factory, tmp_path):
    with session_factory() as s:
        tid, cid = _tenant(s)
        s.commit()
    zuordnung = {"mandant": tid, "kunden": [cid], "rolle": "triage"}
    r = _put(api, [_kunde("kunde-a", werkzeuge=["doc"], realm="kunde-a", zuordnung=zuordnung)])
    assert r.status_code == 200, r.text
    with session_factory() as s:
        pc = s.query(PortalCustomer).one()
        assert (pc.tenant_id, pc.customer_ids, pc.default_role, pc.scope_source) == (
            tid, [cid], "triage", "cockpit")  # fmt: skip
    created = [d for _, act, _, d in _audit(session_factory) if act == "portal.customer.created"]
    assert created[0]["tenant_id"] == tid and created[0]["customer_ids"] == [cid]
    assert created[0]["scope_source"] == "cockpit"
    # dieselbe Anfrage: nichts, kein Audit; die Zuordnung steht nicht im Soll-Stand (nur Automato)
    anzahl = len(_audit(session_factory))
    r = _put(api, [_kunde("kunde-a", werkzeuge=["doc"], realm="kunde-a", zuordnung=zuordnung)])
    assert r.json()["unveraendert"] == 1 and len(_audit(session_factory)) == anzahl
    assert "tenant_id" not in json.dumps(_soll(tmp_path))

    # in der Oberflaeche nur lesbar: Aendern 409, unveraendert speichern geht (Kacheln)
    pc = api.get("/portal/customers").json()["customers"][0]
    assert (pc["scope_source"], pc["assigned"]) == ("cockpit", True)
    body = {k: pc[k] for k in ("short_name", "name", "doc", "nb", "um", "cmdb", "realm",
                               "tenant_id", "customer_ids", "default_role", "access")}  # fmt: skip
    for aenderung in ({"default_role": "editor"}, {"customer_ids": []}, {"tenant_id": None}):
        r = api.put(f"/portal/customers/{pc['id']}", json={**body, **aenderung})
        assert r.status_code == 409 and "aus dem Cockpit" in r.json()["detail"], aenderung
    links = [{"title": "Handbuch", "url": "https://h.example.ch", "text": ""}]
    r = api.put(f"/portal/customers/{pc['id']}", json={**body, "links": links})
    assert r.status_code == 200, r.text

    # wieder ohne zuordnung: der Stand bleibt, die Felder sind in Automato wieder aenderbar
    r = _put(api, [_kunde("kunde-a", werkzeuge=["doc"], realm="kunde-a")])
    assert r.json()["geaendert"] == ["kunde-a"]
    upd = [d for _, act, _, d in _audit(session_factory) if act == "portal.customer.updated"]
    assert upd[-1] == {"scope_source": "automato", "quelle": "cockpit"}
    with session_factory() as s:
        pc_db = s.query(PortalCustomer).one()
        assert (pc_db.tenant_id, pc_db.default_role, pc_db.scope_source) == (
            tid, "triage", "automato")  # fmt: skip
    r = api.put(f"/portal/customers/{pc['id']}", json={**body, "default_role": "editor"})
    assert r.status_code == 200 and r.json()["scope_source"] == "automato"


def test_ohne_zuordnung_bleiben_die_felder_in_automato(api, session_factory):
    """Aelteres Cockpit ohne `zuordnung`: Automato bleibt Autor (wie 1.11.0)."""
    with session_factory() as s:
        tid, cid = _tenant(s)
        s.add(PortalCustomer(short_name="kunde-a", name="A", doc=True, realm="kunde-a",
                             tenant_id=tid, customer_ids=[cid], default_role="editor",
                             links=[]))  # fmt: skip
        s.commit()
    r = _put(api, [_kunde("kunde-a", werkzeuge=["doc"], realm="kunde-a", zuordnung=None)])
    assert r.json()["uebernommen"] == ["kunde-a"]
    with session_factory() as s:
        pc = s.query(PortalCustomer).one()
        assert (pc.tenant_id, pc.customer_ids, pc.default_role, pc.scope_source) == (
            tid, [cid], "editor", "automato")  # fmt: skip


@pytest.mark.parametrize(
    ("zuordnung", "grund"),
    [
        ("x", "kein Objekt"),
        ({"kunden": [], "rolle": "viewer"}, "mandant"),
        ({"mandant": True, "kunden": [], "rolle": "viewer"}, "mandant"),
        ({"mandant": "1", "kunden": [], "rolle": "viewer"}, "mandant"),
        ({"mandant": 999, "kunden": [], "rolle": "viewer"}, "unbekannt"),
        ({"mandant": 0, "kunden": [], "rolle": "viewer"}, "mandant"),
        ({"mandant": "T", "kunden": "C", "rolle": "viewer"}, "keine Liste"),
        ({"mandant": "T", "kunden": ["C", "C"], "rolle": "viewer"}, "eindeutige"),
        ({"mandant": "T", "kunden": ["F"], "rolle": "viewer"}, "gehören nicht zum Mandanten"),
        ({"mandant": "T", "kunden": [], "rolle": "admin"}, "Rolle 'admin'"),
        ({"mandant": "T", "kunden": [], "rolle": None}, "Rolle"),
    ],
)
def test_zuordnung_wird_geprueft(api, session_factory, tmp_path, zuordnung, grund):
    with session_factory() as s:
        tid, cid = _tenant(s)
        fremd = Tenant(name="fremd", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
        s.add(fremd)
        s.flush()
        fid = Customer(tenant_id=fremd.id, name="Fremder Kunde")
        s.add(fid)
        s.commit()
        fid = fid.id
    if isinstance(zuordnung, dict):  # Platzhalter: T = Mandant, C = Kunde, F = fremder Kunde
        werte = {"T": tid, "C": cid, "F": fid}
        zuordnung = {k: ([werte.get(x, x) for x in v] if isinstance(v, list)
                         else werte.get(v, v) if isinstance(v, str) and v in werte else v)
                     for k, v in zuordnung.items()}  # fmt: skip
    r = _put(api, [_kunde("kunde-a", realm="kunde-a", zuordnung=zuordnung)])
    assert r.status_code == 422, r.text
    fehler = r.json()["fehler"]
    assert len(fehler) == 1 and grund in fehler[0]["grund"] and fehler[0]["kurzname"] == "kunde-a"
    with session_factory() as s:
        assert s.query(PortalCustomer).count() == 0
    assert not (tmp_path / "soll.json").exists()


# --- Kacheln der Startseite aus dem Cockpit (1.13.0, ADR 0054 zweiter Nachtrag) --------------

KACHEL_A = {"titel": "Ticketsystem", "adresse": "https://tickets.example.ch/", "text": "Störungen"}
KACHEL_B = {"titel": "Handbuch", "adresse": "https://handbuch.example.ch/", "text": None}


def _links(*kacheln: dict) -> list[dict]:
    return [{"title": k["titel"].strip(), "url": k["adresse"].strip(),
             "text": (k.get("text") or "").strip()} for k in kacheln]  # fmt: skip


def _portal_body(pc: dict, **aenderung) -> dict:
    keys = ("short_name", "name", "doc", "nb", "um", "cmdb", "realm", "tenant_id",
            "customer_ids", "default_role", "access", "access_nets", "links")  # fmt: skip
    return {**{k: pc[k] for k in keys}, **aenderung}


def test_kacheln_aus_dem_cockpit_idempotent_reihenfolge_audit(api, session_factory, tmp_path):
    rand = {"titel": "  Ticketsystem ", "adresse": " https://tickets.example.ch/ ",
            "text": "Störungen "}  # fmt: skip
    r = _put(api, [_kunde("kunde-a", kacheln=[rand, KACHEL_B])])
    assert r.status_code == 200, r.text
    assert r.json()["angelegt"] == ["kunde-a"]
    with session_factory() as s:
        pc = s.query(PortalCustomer).one()
        assert (pc.links, pc.links_source) == (_links(KACHEL_A, KACHEL_B), "cockpit")
    assert _soll(tmp_path)["kunde-a"]["links"] == _links(KACHEL_A, KACHEL_B)
    created = [d for _, act, _, d in _audit(session_factory) if act == "portal.customer.created"]
    assert created[0]["kacheln"] == 2 and created[0]["links_source"] == "cockpit"
    # im Audit nur die Anzahl, nie Titel oder Adressen
    assert "example.ch" not in json.dumps(created) and "Ticketsystem" not in json.dumps(created)

    # dieselbe Anfrage (auch mit Leerraum am Rand): nichts, kein Audit, soll.json bleibt
    anzahl, vorher = len(_audit(session_factory)), (tmp_path / "soll.json").read_bytes()
    r = _put(api, [_kunde("kunde-a", kacheln=[rand, KACHEL_B])])
    assert r.json()["unveraendert"] == 1 and len(_audit(session_factory)) == anzahl
    assert (tmp_path / "soll.json").read_bytes() == vorher

    # Reihenfolge wie gesendet
    r = _put(api, [_kunde("kunde-a", kacheln=[KACHEL_B, KACHEL_A])])
    assert r.json()["geaendert"] == ["kunde-a"]
    assert _soll(tmp_path)["kunde-a"]["links"] == _links(KACHEL_B, KACHEL_A)
    upd = [d for _, act, _, d in _audit(session_factory) if act == "portal.customer.updated"]
    assert upd == [{"kacheln": 2, "quelle": "cockpit"}]

    # in der Oberflaeche nur lesbar: andere Kacheln 409, unveraendert speichern geht (auch mit
    # einer leeren Zeile) - und Mandant, Rolle usw. bleiben dort aenderbar
    pc = api.get("/portal/customers").json()["customers"][0]
    assert pc["links_source"] == "cockpit"
    for links in ([], _links(KACHEL_B), _links(KACHEL_A, KACHEL_B),
                  _links(KACHEL_B, {**KACHEL_A, "text": "anders"})):  # fmt: skip
        r = api.put(f"/portal/customers/{pc['id']}", json=_portal_body(pc, links=links))
        assert r.status_code == 409 and "aus dem Cockpit" in r.json()["detail"], links
    leer = {"title": "", "url": "", "text": ""}
    body = _portal_body(pc, links=[*pc["links"], leer], default_role="triage")
    r = api.put(f"/portal/customers/{pc['id']}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["links"] == _links(KACHEL_B, KACHEL_A) and r.json()["default_role"] == "triage"


def test_kacheln_leer_und_ohne_feld(api, session_factory, tmp_path):
    """Uebernahme eines Kunden aus der Oberflaeche: `[]` nimmt alle Kacheln weg (nur lesbar), ohne
    Feld bleibt der letzte Stand und ist in Automato wieder aenderbar."""
    with session_factory() as s:
        s.add(PortalCustomer(short_name="kunde-a", name="A", links=_links(KACHEL_A),
                             customer_ids=[]))  # fmt: skip
        s.commit()
    # aelteres Cockpit ohne `kacheln` (bzw. null): die Kacheln bleiben
    r = _put(api, [_kunde("kunde-a", kacheln=None)])
    assert r.json()["uebernommen"] == ["kunde-a"]
    with session_factory() as s:
        pc = s.query(PortalCustomer).one()
        assert (pc.links, pc.links_source) == (_links(KACHEL_A), "automato")

    r = _put(api, [_kunde("kunde-a", kacheln=[])])
    assert r.json()["geaendert"] == ["kunde-a"]
    upd = [d for _, act, _, d in _audit(session_factory) if act == "portal.customer.updated"]
    assert upd[-1] == {"kacheln": 0, "links_source": "cockpit", "quelle": "cockpit"}
    assert _soll(tmp_path)["kunde-a"]["links"] == []
    pc = api.get("/portal/customers").json()["customers"][0]
    assert (pc["links"], pc["links_source"]) == ([], "cockpit")
    r = api.put(f"/portal/customers/{pc['id']}", json=_portal_body(pc, links=_links(KACHEL_B)))
    assert r.status_code == 409

    # wieder ohne kacheln: der Stand bleibt (leer), in Automato wieder aenderbar
    r = _put(api, [_kunde("kunde-a")])
    assert r.json()["geaendert"] == ["kunde-a"]
    upd = [d for _, act, _, d in _audit(session_factory) if act == "portal.customer.updated"]
    assert upd[-1] == {"links_source": "automato", "quelle": "cockpit"}
    r = api.put(f"/portal/customers/{pc['id']}", json=_portal_body(pc, links=_links(KACHEL_B)))
    assert r.status_code == 200, r.text
    assert (r.json()["links"], r.json()["links_source"]) == (_links(KACHEL_B), "automato")
    assert _soll(tmp_path)["kunde-a"]["links"] == _links(KACHEL_B)
    # die naechste Freigabe ohne kacheln aendert nichts daran
    assert _put(api, [_kunde("kunde-a")]).json()["unveraendert"] == 1


def test_kacheln_an_den_grenzen(api, session_factory):
    erste = {"titel": "T" * 60, "adresse": "https://a.example.ch/" + "x" * 479, "text": "ä" * 200}
    weitere = [{"titel": f"Kachel {i}", "adresse": f"https://k{i}.example.ch"} for i in range(19)]
    r = _put(api, [_kunde("kunde-a", kacheln=[erste, *weitere])])
    assert r.status_code == 200, r.text
    with session_factory() as s:
        links = s.query(PortalCustomer).one().links
    assert len(links) == 20 and len(links[0]["url"]) == 500 and links[1]["text"] == ""


_GUT = {"titel": "A", "adresse": "https://a.example.ch"}


@pytest.mark.parametrize(
    ("kacheln", "grund"),
    [
        ("x", "kacheln ist keine Liste"),
        ({"titel": "A"}, "kacheln ist keine Liste"),
        ([_GUT] * 21, "höchstens 20"),
        (["x"], "Kachel 1 ist kein Objekt"),
        ([{"adresse": "https://a.example.ch"}], "Kachel 1 – titel und adresse sind Text"),
        ([{"titel": "A"}], "titel und adresse sind Text"),
        ([{**_GUT, "text": 5}], "text Text oder null"),
        ([_GUT, {**_GUT, "titel": " "}], "Kachel 2 – Kachel ohne Titel"),
        ([{**_GUT, "titel": "T" * 61}], "länger als 60"),
        ([{**_GUT, "titel": "A\x07B"}], "Steuerzeichen"),
        ([{**_GUT, "titel": "A\tB"}], "Steuerzeichen"),
        ([{**_GUT, "adresse": "http://a.example.ch"}], "https://"),
        ([{**_GUT, "adresse": "https://"}], "https://"),
        ([{**_GUT, "adresse": "https://a.example.ch/" + "x" * 480}], "höchstens 500"),
        ([{**_GUT, "adresse": "https://a.example.ch/a b"}], "ohne Leerzeichen"),
        ([{**_GUT, "adresse": "https://a.example.ch/a\u2003b"}], "ohne Leerzeichen"),
        ([{**_GUT, "adresse": "https://a.example.ch/a\x7fb"}], "ohne Leerzeichen"),
        ([{**_GUT, "text": "x" * 201}], "höchstens 200"),
        ([{**_GUT, "text": "a\nb"}], "Zeilenumbrüche"),
    ],
)
def test_kacheln_werden_geprueft(api, session_factory, tmp_path, kacheln, grund):
    r = _put(api, [_kunde("kunde-b"), _kunde("kunde-a", kacheln=kacheln)])
    assert r.status_code == 422, r.text
    fehler = r.json()["fehler"]
    assert len(fehler) == 1 and fehler[0]["kurzname"] == "kunde-a", fehler
    assert fehler[0]["grund"].startswith("kacheln") and grund in fehler[0]["grund"], fehler
    # alles oder nichts: auch der gueltige Kunde nicht, kein Soll-Stand, kein Audit
    with session_factory() as s:
        assert s.query(PortalCustomer).count() == 0
    assert not (tmp_path / "soll.json").exists() and _audit(session_factory) == []
