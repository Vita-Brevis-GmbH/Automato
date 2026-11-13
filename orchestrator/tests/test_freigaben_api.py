"""Freigaben aus dem Cockpit (1.11.0, ADR 0054): PUT /freigaben nur mit dem Token des Cockpits.

Das Cockpit schickt den ganzen Stand seiner Kunden; Automato fuehrt sein Kunden-Portal nach -
idempotent, alles oder nichts, Uebernahme vorhandener Kunden (Mandant, Kunden, Rolle, Kacheln
bleiben), Entfernen nur von Kunden aus dem Cockpit. Vertrag im Cockpit:
docs/vertraege/freigaben.md (v1)."""

import hashlib
import json

import pytest
from app.audit import decode_detail
from app.auth import realms
from app.config import Settings, get_settings
from app.models import AuditLog, Customer, IpamType, PortalCustomer, Tenant
from app.models.tenant import DnsType

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
