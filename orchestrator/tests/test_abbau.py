"""Abbau aus dem Cockpit und Ist-Stand (ADR 0059): `gesperrt` je Kunde, Loeschauftraege
(`entfernen` in PUT /freigaben), Auftraege fuer die Hosts (GET /netbox-host → `loeschen`,
`soll.json` → `tor_entfernen`), Meldung des NetBox-Hosts (PUT /netbox-host/stand) und der
Ist-Stand im Inventar (`ist`).

Geprueft wird vor allem, was nie passieren darf: ein Loeschauftrag fuer einen Kunden aus der
Oberflaeche, fuer einen Kurznamen, den Automato nie vom Cockpit hatte, oder fuer einen Kunden,
den es (wieder) gibt; ein Kunde in Automato mit Daten wird nie entfernt."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.audit import decode_detail
from app.auth import realms
from app.config import Settings, get_settings
from app.models import (
    AbbauAuftrag,
    AuditLog,
    Customer,
    CustomerSelector,
    IpamType,
    IstMeldung,
    PortalCustomer,
    Tenant,
)
from app.models.tenant import DnsType
from app.store import ist_stand, portal

COCKPIT = "c" * 64
NBH = "d" * 64
AUTH = {"Authorization": f"Bearer {COCKPIT}"}
NBH_AUTH = {"Authorization": f"Bearer {NBH}"}
ISSUER = "https://kc.example.ch/realms/automato"


@pytest.fixture
def api(client, tmp_path):
    settings = Settings(front_state_dir=str(tmp_path), front_domain="mgmt.example.ch",
                        cockpit_token=COCKPIT, netbox_host_token=NBH,
                        oidc_issuer=ISSUER)  # fmt: skip
    client.app.dependency_overrides[get_settings] = lambda: settings
    client.state_dir = tmp_path  # type: ignore[attr-defined]
    realms.invalidate()
    return client


def _kunde(kurz: str, **kw) -> dict:
    return {"kurzname": kurz, "name": kw.pop("name", f"Kunde {kurz}"),
            "werkzeuge": kw.pop("werkzeuge", ["nb", "doc"]), "zugriff": "ueberall", "netze": [],
            "realm": kw.pop("realm", kurz), **kw}  # fmt: skip


def _put(client, kunden: list[dict], **extra):
    return client.put("/freigaben", json={"vertrag": 1, "kunden": kunden, **extra}, headers=AUTH)


def _soll(client) -> dict:
    return json.loads((client.state_dir / "soll.json").read_text(encoding="utf-8"))


def _inventar(client) -> dict:
    r = client.get("/inventory", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def _ist_entfernen(client) -> dict:
    return {e["kurzname"]: e for e in _inventar(client)["ist"]["entfernen"]}


def _audit(session_factory, prefix: str = "") -> list[tuple[str, str, str, dict | None]]:
    with session_factory() as s:
        rows = s.query(AuditLog).order_by(AuditLog.id).all()
        return [(a.actor, a.action, a.subject, decode_detail(a)) for a in rows
                if a.action.startswith(prefix)]  # fmt: skip


def _front_status(client, dateien: list[str], **ist) -> None:
    """status.json wie front.sh sync (ADR 0059: Schluessel `ist`)."""
    data = {"ok": True, "zeit": "2026-11-16T10:00:00+00:00", "soll_sha256": "x", "meldung": "",
            "kunden": [], "tor": {"an": True},
            "ist": {"kunden": [], "gesperrt": [], "tor_realms_dateien": dateien,
                    "tor_dienste": [], "tor_dienste_fremd": [], "tor_verwaist": [],
                    "tor_entfernt": [], "tor_entfernen_abgelehnt": [], **ist}}  # fmt: skip
    (client.state_dir / "status.json").write_text(json.dumps(data), encoding="utf-8")


def _stand(client, instanzen: list[str], **mehr):
    body = {"vertrag": 1, "zeit": "2026-11-16T10:00:00+00:00", "instanzen": instanzen,
            "ohne_freigabe": [], "geloescht": [], "loeschen_fehlgeschlagen": [],
            **mehr}  # fmt: skip
    return client.put("/netbox-host/stand", json=body, headers=NBH_AUTH)


def _loeschen(client) -> list[dict]:
    r = client.get("/netbox-host", headers=NBH_AUTH)
    assert r.status_code == 200, r.text
    return r.json()["loeschen"]


# --- gesperrt -----------------------------------------------------------------------------------
def test_gesperrt_sperrt_front_und_realm_daten_bleiben(api, session_factory):
    r = _put(api, [_kunde("kunde-a", gesperrt=True), _kunde("kunde-b")])
    assert r.status_code == 200, r.text
    soll = {k["kurzname"]: k for k in _soll(api)["kunden"]}
    assert soll["kunde-a"]["gesperrt"] is True
    assert "gesperrt" not in soll["kunde-b"]  # nur, wenn gesperrt (Soll-Stand sonst wie bisher)
    with session_factory() as s:
        pc = s.query(PortalCustomer).filter_by(short_name="kunde-a").one()
        assert pc.gesperrt and pc.realm == "kunde-a" and pc.nb  # Daten bleiben
        # der Realm gibt in Automato keine Rechte mehr (fail-closed wie ein unbekannter Realm)
        assert set(portal.realm_scopes(s, "automato")) == {"kunde-b"}
    _, action, _, detail = _audit(session_factory, "portal.customer")[0]
    assert action == "portal.customer.created" and detail["gesperrt"] is True
    # in der Oberflaeche sichtbar
    liste = api.get("/portal/customers").json()["customers"]
    assert {c["short_name"]: c["gesperrt"] for c in liste} == {"kunde-a": True, "kunde-b": False}

    # rueckgaengig: ohne gesperrt (Vorgabe false) wieder offen
    r = _put(api, [_kunde("kunde-a"), _kunde("kunde-b")])
    assert r.json()["geaendert"] == ["kunde-a"]
    assert "gesperrt" not in {k["kurzname"]: k for k in _soll(api)["kunden"]}["kunde-a"]
    with session_factory() as s:
        assert set(portal.realm_scopes(s, "automato")) == {"kunde-a", "kunde-b"}


def test_gesperrt_nur_true_oder_false(api, session_factory):
    r = _put(api, [_kunde("kunde-a", gesperrt="ja")])
    assert r.status_code == 422
    assert r.json()["fehler"][0]["kurzname"] == "kunde-a"
    with session_factory() as s:
        assert s.query(PortalCustomer).count() == 0


# --- entfernen: pruefen und annehmen ------------------------------------------------------------
@pytest.mark.parametrize(
    "entfernen",
    [
        "kunde-a",
        [{"kurzname": "Kunde A", "netbox": True}],
        [{"kurzname": "kunde-a", "netbox": "ja"}],
        [{"kurzname": "kunde-a"}, {"kurzname": "kunde-a"}],
        ["kunde-a"],
    ],
)
def test_entfernen_ungueltig_aendert_nichts(api, session_factory, entfernen):
    r = _put(api, [_kunde("kunde-b")], entfernen=entfernen)
    assert r.status_code == 422, r.text
    assert any("entfernen" in f["grund"] for f in r.json()["fehler"])
    with session_factory() as s:  # alles oder nichts: auch kunde-b nicht angelegt
        assert s.query(PortalCustomer).count() == 0 and s.query(AbbauAuftrag).count() == 0


def test_entfernen_nach_der_frist_wird_angenommen(api, session_factory):
    assert _put(api, [_kunde("kunde-a", gesperrt=True), _kunde("kunde-b")]).status_code == 200
    # nach der Frist: aus kunden[] und in derselben Anfrage der Auftrag
    r = _put(api, [_kunde("kunde-b")],
             entfernen=[{"kurzname": "kunde-a", "netbox": True, "automato_kunde": True,
                         "tor": True}])  # fmt: skip
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entfernt"] == ["kunde-a"]
    assert body["entfernen"] == {"angenommen": ["kunde-a"], "abgelehnt": []}
    with session_factory() as s:
        a = s.query(AbbauAuftrag).one()
        assert (a.short_name, a.realm, a.netbox, a.automato_kunde, a.tor) == (
            "kunde-a", "kunde-a", True, True, True)  # fmt: skip
        assert len(a.kennung) == 32
    angenommen = _audit(session_factory, "portal.abbau.angenommen")
    assert angenommen == [("cockpit", "portal.abbau.angenommen", "kunde-a",
                           {"netbox": True, "automato_kunde": True, "tor": True,
                            "realm": "kunde-a"})]  # fmt: skip
    # Auftraege fuer die Hosts: NetBox-Host und Front-Proxy (mit Kennung des Auftrags)
    assert _loeschen(api) == [{"kurzname": "kunde-a", "auftrag": a.kennung}]
    assert _soll(api)["tor_entfernen"] == [{"realm": "kunde-a", "auftrag": a.kennung}]
    # Kunde in Automato: keiner angelegt (Verknuepfung `kunde_anlegen` fehlt noch) - erledigt
    e = _ist_entfernen(api)["kunde-a"]
    assert e["stand"] == "offen" and e["grund"] == "netbox_host"
    assert e["teile"] == {
        "netbox": {"stand": "offen", "grund": "netbox_host"},
        "automato_kunde": {"stand": "erledigt", "grund": "keiner_angelegt"},
        "tor": {"stand": "offen", "grund": "front_proxy"},
    }
    # zweimal dieselbe Anfrage: kein zweiter Auftrag, kein zweites Audit
    alle = {"kurzname": "kunde-a", "netbox": True, "automato_kunde": True, "tor": True}
    r = _put(api, [_kunde("kunde-b")], entfernen=[alle])
    assert r.json()["entfernen"]["angenommen"] == ["kunde-a"]
    assert len(_audit(session_factory, "portal.abbau.angenommen")) == 1
    with session_factory() as s:
        assert s.query(AbbauAuftrag).one().kennung == a.kennung


def test_entfernen_nie_fuer_kunden_aus_der_oberflaeche(api, session_factory):
    # aus der Oberflaeche angelegt
    r = api.post("/portal/customers", json={"short_name": "ui-kunde", "name": "UI", "nb": True})
    assert r.status_code in (200, 201), r.text
    _put(api, [_kunde("kunde-a")])
    r = _put(api, [_kunde("kunde-a")], entfernen=[
        {"kurzname": "ui-kunde", "netbox": True},  # aus der Oberflaeche
        {"kurzname": "kunde-a", "netbox": True},  # steht noch in kunden[]
        {"kurzname": "nie-da", "netbox": True},  # nie ein Kunde aus dem Cockpit
    ])  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["entfernen"] == {"angenommen": [], "abgelehnt": [
        {"kurzname": "ui-kunde", "grund": "oberflaeche"},
        {"kurzname": "kunde-a", "grund": "in_kunden"},
        {"kurzname": "nie-da", "grund": "nicht_vom_cockpit"},
    ]}  # fmt: skip
    with session_factory() as s:
        assert s.query(AbbauAuftrag).count() == 0
        assert (
            s.query(PortalCustomer).filter_by(short_name="ui-kunde").one().source == "oberflaeche"
        )
    assert _loeschen(api) == []
    assert "tor_entfernen" not in _soll(api)
    # im Inventar als abgelehnt
    ist = _ist_entfernen(api)
    assert ist["ui-kunde"]["stand"] == "abgelehnt" and ist["ui-kunde"]["grund"] == "oberflaeche"

    # in der Oberflaeche entfernt (nie vom Cockpit): bleibt abgelehnt
    ui_id = next(c["id"] for c in api.get("/portal/customers").json()["customers"]
                 if c["short_name"] == "ui-kunde")  # fmt: skip
    assert api.delete(f"/portal/customers/{ui_id}").status_code in (200, 204)
    r = _put(api, [_kunde("kunde-a")], entfernen=[{"kurzname": "ui-kunde", "netbox": True}])
    assert r.json()["entfernen"]["abgelehnt"] == [{"kurzname": "ui-kunde",
                                                   "grund": "nicht_vom_cockpit"}]  # fmt: skip


def test_entfernen_spaeter_nur_wenn_das_cockpit_ihn_zuletzt_entfernte(api, session_factory):
    _put(api, [_kunde("kunde-a"), _kunde("kunde-x")])
    _put(api, [])  # aelteres Cockpit: entfernt ohne Auftrag
    r = _put(api, [], entfernen=[{"kurzname": "kunde-a", "tor": True}])
    assert r.json()["entfernen"]["angenommen"] == ["kunde-a"]
    with session_factory() as s:
        assert s.query(AbbauAuftrag).one().realm == "kunde-a"  # Realm aus dem Audit
    # danach in der Oberflaeche gleichnamig angelegt und entfernt: nicht mehr vom Cockpit
    r = api.post("/portal/customers", json={"short_name": "kunde-x", "name": "X"})
    ui_id = r.json()["id"]
    api.delete(f"/portal/customers/{ui_id}")
    r = _put(api, [], entfernen=[{"kurzname": "kunde-a", "tor": True},
                                 {"kurzname": "kunde-x", "tor": True}])  # fmt: skip
    assert r.json()["entfernen"]["abgelehnt"] == [{"kurzname": "kunde-x",
                                                   "grund": "nicht_vom_cockpit"}]  # fmt: skip


def test_auftrag_endet_wenn_der_kunde_wieder_da_ist_oder_nicht_mehr_gesendet(api, session_factory):
    _put(api, [_kunde("kunde-a"), _kunde("kunde-b")])
    r = _put(api, [], entfernen=[{"kurzname": "kunde-a", "netbox": True},
                                 {"kurzname": "kunde-b", "netbox": True}])  # fmt: skip
    assert r.json()["entfernen"]["angenommen"] == ["kunde-a", "kunde-b"]
    assert [e["kurzname"] for e in _loeschen(api)] == ["kunde-a", "kunde-b"]
    # ohne `entfernen`: keine Aussage - die Auftraege bleiben
    _put(api, [])
    assert [e["kurzname"] for e in _loeschen(api)] == ["kunde-a", "kunde-b"]
    # kunde-a wieder angelegt (neuer Kunde gleichen Namens): nie abbauen
    r = _put(api, [_kunde("kunde-a")], entfernen=[{"kurzname": "kunde-b", "netbox": True}])
    assert r.json()["entfernen"]["angenommen"] == ["kunde-b"]
    assert [e["kurzname"] for e in _loeschen(api)] == ["kunde-b"]
    # nicht mehr gesendet: aufgehoben
    _put(api, [_kunde("kunde-a")], entfernen=[])
    assert _loeschen(api) == []
    aufgehoben = [
        (subj, d["grund"]) for _, _, subj, d in _audit(session_factory, "portal.abbau.aufgehoben")
    ]
    assert aufgehoben == [("kunde-a", "wieder_da"), ("kunde-b", "nicht_gesendet")]


# --- NetBox-Host --------------------------------------------------------------------------------
def test_netbox_teil_erledigt_erst_mit_meldung_nach_dem_annehmen(api, session_factory):
    # Meldung vor dem Auftrag zaehlt nicht
    assert _stand(api, ["kunde-a", "alt-b"]).status_code == 200
    _put(api, [_kunde("kunde-a")])
    _put(api, [], entfernen=[{"kurzname": "kunde-a", "netbox": True}])
    with session_factory() as s:  # Meldung zeitlich vor dem Annehmen
        m = s.query(IstMeldung).filter_by(quelle="netbox_host").one()
        m.daten = {**m.daten, "instanzen": []}
        m.empfangen = datetime.now(UTC) - timedelta(minutes=5)
        s.commit()
    _put(api, [], entfernen=[{"kurzname": "kunde-a", "netbox": True}])
    assert _ist_entfernen(api)["kunde-a"]["teile"]["netbox"]["stand"] == "offen"

    # Agent: Loeschen fehlgeschlagen (keine Sicherung) - bleibt offen, Auftrag bleibt
    r = _stand(api, ["kunde-a", "alt-b"], loeschen_fehlgeschlagen=["kunde-a"])
    assert r.status_code == 200 and r.json()["loeschen"][0]["kurzname"] == "kunde-a"
    assert _ist_entfernen(api)["kunde-a"]["teile"]["netbox"] == {
        "stand": "offen", "grund": "loeschen_fehlgeschlagen"}  # fmt: skip
    # gesichert und geloescht
    r = _stand(api, ["alt-b"], ohne_freigabe=["alt-b"], geloescht=["kunde-a"])
    assert r.json() == {"vertrag": 1, "status": "gespeichert", "loeschen": []}
    e = _ist_entfernen(api)["kunde-a"]
    assert e["stand"] == "erledigt" and e["teile"]["netbox"] == {"stand": "erledigt",
                                                                 "grund": "geloescht"}  # fmt: skip
    assert e["erledigt"] is not None
    assert _loeschen(api) == []
    actors = {(a, act) for a, act, _, _ in _audit(session_factory, "portal.abbau.")}
    assert ("netbox-host", "portal.abbau.fortschritt") in actors
    assert ("netbox-host", "portal.abbau.erledigt") in actors
    # Ist-Stand des NetBox-Hosts im Inventar
    nb = _inventar(api)["ist"]["netbox_host"]
    assert (nb["stand"], nb["instanzen"], nb["ohne_freigabe"], nb["geloescht"]) == (
        "2026-11-16T10:00:00+00:00", ["alt-b"], ["alt-b"], ["kunde-a"])  # fmt: skip
    # erledigt bleibt erledigt, auch wenn wieder eine Instanz gemeldet wird
    _stand(api, ["alt-b", "kunde-a"])
    assert _ist_entfernen(api)["kunde-a"]["stand"] == "erledigt" and _loeschen(api) == []
    # das Cockpit sendet ihn nicht mehr (erledigt) - ein spaeterer Auftrag nie ein zweites Mal
    _put(api, [], entfernen=[])
    r = _put(api, [], entfernen=[{"kurzname": "kunde-a", "netbox": True}])
    assert r.json()["entfernen"]["abgelehnt"] == [
        {"kurzname": "kunde-a", "grund": "schon_erledigt"}
    ]
    assert _loeschen(api) == []


@pytest.mark.parametrize(
    ("body", "feld"),
    [
        ({"vertrag": 2}, "vertrag"),
        ({"vertrag": 1, "zeit": "gestern"}, "zeit"),
        ({"vertrag": 1, "instanzen": ["Kunde A"]}, "instanzen"),
        ({"vertrag": 1, "geloescht": "kunde-a"}, "geloescht"),
        ({"vertrag": 1, "ohne_freigabe": ["x"] * 1001}, "ohne_freigabe"),
        ([], "Objekt"),
    ],
)
def test_stand_wird_geprueft(api, session_factory, body, feld):
    r = api.put("/netbox-host/stand", json=body, headers=NBH_AUTH)
    assert r.status_code == 422 and feld in r.text
    with session_factory() as s:
        assert s.query(IstMeldung).count() == 0


def test_stand_nur_mit_dem_token_des_netbox_hosts(api):
    assert api.put("/netbox-host/stand", json={"vertrag": 1}).status_code == 401
    assert api.put("/netbox-host/stand", json={"vertrag": 1}, headers=AUTH).status_code == 401


# --- Anmelde-Tor --------------------------------------------------------------------------------
def test_tor_teil_ueber_den_front_proxy(api, session_factory):
    _put(api, [_kunde("kunde-a"), _kunde("kunde-b")])
    _front_status(api, ["kunde-a", "kunde-b"])
    _put(api, [_kunde("kunde-b")], entfernen=[{"kurzname": "kunde-a", "tor": True}])
    with session_factory() as s:
        kennung = s.query(AbbauAuftrag).one().kennung
    assert _soll(api)["tor_entfernen"] == [{"realm": "kunde-a", "auftrag": kennung}]
    assert _ist_entfernen(api)["kunde-a"]["teile"]["tor"]["grund"] == "front_proxy"
    # front.sh sync hat das Tor-Geheimnis entfernt; der naechste Abgleich des Cockpits sieht es
    _front_status(api, ["kunde-b"], tor_entfernt=["kunde-a"])
    _put(api, [_kunde("kunde-b")], entfernen=[{"kurzname": "kunde-a", "tor": True}])
    e = _ist_entfernen(api)["kunde-a"]
    assert e["stand"] == "erledigt" and e["teile"]["tor"]["grund"] == "entfernt"
    assert "tor_entfernen" not in _soll(api)  # erledigt: nie noch einmal
    front = _inventar(api)["ist"]["front"]
    assert front["tor_realms_dateien"] == ["kunde-b"] and front["tor_entfernt"] == ["kunde-a"]


def test_tor_nie_fuer_einen_realm_in_verwendung(api, session_factory):
    _put(api, [_kunde("kunde-a")])
    _front_status(api, ["kunde-a"])
    # der Realm geht an einen neuen Kunden (gleiche Anfrage)
    r = _put(
        api, [_kunde("kunde-c", realm="kunde-a")], entfernen=[{"kurzname": "kunde-a", "tor": True}]
    )
    assert r.json()["entfernen"]["angenommen"] == ["kunde-a"]
    assert "tor_entfernen" not in _soll(api)
    tor = _ist_entfernen(api)["kunde-a"]["teile"]["tor"]
    assert tor == {"stand": "erledigt", "grund": "realm_in_verwendung"}


def test_tor_realm_von_automato_und_ohne_front(api, client, session_factory, tmp_path):
    _put(api, [_kunde("eigen-kunde", realm="automato")])
    _put(api, [], entfernen=[{"kurzname": "eigen-kunde", "tor": True}])
    assert _ist_entfernen(api)["eigen-kunde"]["teile"]["tor"]["grund"] == "haupt_realm"
    # ohne Front-Proxy (kein Ordner): nichts zu entfernen
    settings = Settings(cockpit_token=COCKPIT, netbox_host_token=NBH)
    client.app.dependency_overrides[get_settings] = lambda: settings
    _put(client, [_kunde("kunde-a")])
    _put(client, [], entfernen=[{"kurzname": "kunde-a", "tor": True},
                                {"kurzname": "eigen-kunde", "tor": True}])  # fmt: skip
    e = {
        x["kurzname"]: x for x in client.get("/inventory", headers=AUTH).json()["ist"]["entfernen"]
    }
    assert e["kunde-a"]["teile"]["tor"] == {"stand": "erledigt", "grund": "kein_front_proxy"}


# --- Kunde in Automato --------------------------------------------------------------------------
def _mandant(session_factory) -> tuple[int, int, int]:
    with session_factory() as s:
        t = Tenant(name="dc", ipam_type=IpamType.NETBOX, dns_type=DnsType.ACTIVE_DIRECTORY)
        s.add(t)
        s.flush()
        leer = Customer(tenant_id=t.id, name="Kunde A (Cockpit)")
        voll = Customer(tenant_id=t.id, name="Kunde B (Cockpit)")
        s.add_all([leer, voll])
        s.flush()
        s.add(CustomerSelector(customer_id=voll.id, kind="prefix", value="10.1.0.0/16"))
        s.commit()
        return t.id, leer.id, voll.id


def test_automato_kunde_nur_ohne_daten(api, session_factory, monkeypatch):
    tid, leer, voll = _mandant(session_factory)
    # Verknuepfung „von Automato fuer Portal-Kunde X angelegt“ (kommt mit `kunde_anlegen`)
    angelegt = {leer: "kunde-a", voll: "kunde-b"}
    monkeypatch.setattr(ist_stand, "angelegte_kunden", lambda session, kurzname=None: {
        cid: k for cid, k in angelegt.items() if kurzname in (None, k)})  # fmt: skip
    zuordnung = {"mandant": tid, "kunden": [leer], "rolle": "viewer"}
    _put(api, [_kunde("kunde-a", zuordnung=zuordnung), _kunde("kunde-b")])
    ist = {k["id"]: k for k in _inventar(api)["ist"]["automato_kunden"]}
    assert ist[leer] | {"name": None} == {
        "id": leer, "name": None, "mandant": tid, "portal_kunde": "kunde-a", "von_cockpit": True,
        "hat_daten": False, "daten": [], "zugeordnet": ["kunde-a"]}  # fmt: skip
    assert (ist[voll]["hat_daten"], ist[voll]["daten"]) == (True, ["selektoren"])

    # waehrend kunde-a noch zugeordnet ist, kann ihn niemand entfernen lassen (in_kunden); danach:
    r = _put(api, [], entfernen=[{"kurzname": "kunde-a", "automato_kunde": True},
                                 {"kurzname": "kunde-b", "automato_kunde": True}])  # fmt: skip
    assert r.json()["entfernen"]["angenommen"] == ["kunde-a", "kunde-b"]
    with session_factory() as s:
        assert s.get(Customer, leer) is None  # ohne Daten: entfernt
        assert s.get(Customer, voll) is not None  # mit Daten: bleibt
    deleted = _audit(session_factory, "customer.deleted")
    assert deleted == [("cockpit", "customer.deleted", str(leer),
                        {"tenant_id": tid, "customer_id": leer, "name": "Kunde A (Cockpit)",
                         "selectors": 0})]  # fmt: skip
    e = _ist_entfernen(api)
    assert e["kunde-a"]["stand"] == "erledigt"
    assert e["kunde-b"]["stand"] == "offen" and e["kunde-b"]["grund"] == "hat_daten"
    # Daten weg (von Hand): der naechste Abgleich entfernt ihn
    with session_factory() as s:
        s.query(CustomerSelector).delete()
        s.commit()
    _put(api, [], entfernen=[{"kurzname": "kunde-b", "automato_kunde": True}])
    with session_factory() as s:
        assert s.get(Customer, voll) is None
    assert _ist_entfernen(api)["kunde-b"]["stand"] == "erledigt"


def test_automato_kunde_bleibt_solange_ein_anderer_portal_kunde_ihn_nutzt(api, session_factory,
                                                                          monkeypatch):  # fmt: skip
    tid, leer, _ = _mandant(session_factory)
    monkeypatch.setattr(ist_stand, "angelegte_kunden", lambda session, kurzname=None: (
        {leer: "kunde-a"} if kurzname in (None, "kunde-a") else {}))  # fmt: skip
    zuordnung = {"mandant": tid, "kunden": [leer], "rolle": "viewer"}
    _put(api, [_kunde("kunde-a"), _kunde("kunde-z", zuordnung=zuordnung)])
    _put(api, [_kunde("kunde-z", zuordnung=zuordnung)],
         entfernen=[{"kurzname": "kunde-a", "automato_kunde": True}])  # fmt: skip
    with session_factory() as s:
        assert s.get(Customer, leer) is not None
    assert _ist_entfernen(api)["kunde-a"]["grund"] == "zugeordnet"


# --- Inventar -----------------------------------------------------------------------------------
def test_ist_im_inventar(api, session_factory):
    inv = _inventar(api)
    assert inv["ist"] == {"front": None, "netbox_host": None, "automato_kunden": [],
                          "entfernen": []}  # fmt: skip
    _front_status(api, ["kunde-a", "alt-r", "../x"], gesperrt=["kunde-a"], tor_verwaist=["alt-r"],
                  tor_dienste=["kunde-a"], tor_dienste_fremd=["tor-alt-12345678", "Böse; rm"],
                  kunden=["kunde-a", "Kaputt"])  # fmt: skip
    front = _inventar(api)["ist"]["front"]
    assert front == {
        "stand": "2026-11-16T10:00:00+00:00", "kunden": ["kunde-a"], "gesperrt": ["kunde-a"],
        "tor_realms_dateien": ["alt-r", "kunde-a"], "tor_dienste": ["kunde-a"],
        "tor_dienste_fremd": ["tor-alt-12345678"], "tor_verwaist": ["alt-r"], "tor_entfernt": [],
        "tor_entfernen_abgelehnt": [],
    }  # fmt: skip
    # aelteres front.sh ohne `ist`: Listen unbekannt
    (api.state_dir / "status.json").write_text(json.dumps(
        {"ok": True, "zeit": "2026-11-16T09:00:00+00:00", "kunden": ["kunde-a"]}))  # fmt: skip
    front = _inventar(api)["ist"]["front"]
    assert front["kunden"] == ["kunde-a"] and front["tor_realms_dateien"] is None
    # Auftraege ohne Meldung des Agenten
    _put(api, [_kunde("kunde-a")])
    _put(api, [], entfernen=[{"kurzname": "kunde-a", "netbox": True}])
    with session_factory() as s:
        s.query(IstMeldung).delete()
        s.commit()
    assert _ist_entfernen(api)["kunde-a"]["teile"]["netbox"]["grund"] == "netbox_host"
