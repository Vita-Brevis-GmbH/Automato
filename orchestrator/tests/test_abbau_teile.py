"""Teil-Abbau bleibender Kunden (1.14.0, ADR 0059 Nachtrag): ein Werkzeug abwaehlen entfernt auch
die Instanz.

Anwender 2026-11-16: „weiter müssen wir schauen, dass das Cockpit alles orchestriert, also die
Tools zufügt aber auch entfernt, wir haben auf dem dev Server nun leichen liegen“. Ein Eintrag in
`entfernen` darf jetzt auch einen Kunden nennen, der in `kunden[]` derselben Anfrage steht - dann
gelten nur Teile, deren Werkzeug bei diesem Kunden aus ist (`netbox` ohne `nb`, `automato_kunde`
ohne `doc`, `tor` fuer den frueheren Realm). Geprueft wird vor allem, was nie passieren darf: ein
Teil, dessen Werkzeug an ist (`aktiv`), bzw. der vor der Ausfuehrung wieder gewaehlt wurde
(`aufgehoben`); ein Kunde in Automato mit Daten; ein Kunde aus der Oberflaeche."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models import AbbauAuftrag, Customer, CustomerSelector, IstMeldung, PortalCustomer
from test_abbau import (  # noqa: F401 - Fixture api
    NBH_AUTH,
    _angelegt,
    _audit,
    _front_status,
    _inventar,
    _ist_entfernen,
    _kunde,
    _leerer_mandant,
    _loeschen,
    _put,
    _soll,
    _stand,
    api,
)
from test_netbox_host_script import TOKEN, host  # noqa: F401 - Fixture

NB = {"kurzname": "kunde-a", "netbox": True}


def _auftrag(session_factory, kurz: str = "kunde-a") -> dict | None:
    with session_factory() as s:
        a = s.query(AbbauAuftrag).filter_by(short_name=kurz).one_or_none()
        if a is None:
            return None
        return {"kennung": a.kennung, "realm": a.realm, "netbox": a.netbox, "tor": a.tor,
                "automato_kunde": a.automato_kunde, "teile": dict(a.teile or {})}  # fmt: skip


def _gemeldet(host) -> dict:  # noqa: F811
    """Letzte Meldung des Agenten an Automato (Inhalt von PUT /api/netbox-host/stand)."""
    zeilen = Path(f"{host.log}.stand").read_text().splitlines()
    return json.loads([z for z in zeilen if z.strip()][-1])


# --- ganzer Weg mit dem Agenten des NetBox-Hosts ------------------------------------------------
def test_netbox_abwaehlen_loescht_die_instanz_mit_dem_agenten(api, host, session_factory,  # noqa: F811
                                                              tmp_path):  # fmt: skip
    """Kunde bleibt mit doc+nb -> NetBox abgewaehlt (waehrend der Frist ist das Werkzeug aus, die
    NetBox bleibt) -> nach der Frist `entfernen` mit dem Teil NetBox, der Kunde steht weiter in
    `kunden[]` -> der Agent sichert und loescht -> der Ist-Stand meldet sie weg. Der Agent bekommt
    die Antwort von Automato, Automato die Meldung des Agenten."""

    def abgleich() -> str:
        r = api.get("/netbox-host", headers=NBH_AUTH)
        assert r.status_code == 200, r.text
        Path(f"{host.log}.antwort").write_text(r.text)
        out = host("sync")
        assert out.returncode == 0, out.stdout + out.stderr
        r = api.put("/netbox-host/stand", json=_gemeldet(host), headers=NBH_AUTH)
        assert r.status_code == 200, r.text
        return out.stdout + out.stderr

    assert host("neu", "kunde-a").returncode == 0
    assert host("neu", "kunde-b").returncode == 0
    assert host("agent", "https://automato.int.example.ch", NBH_TOKEN=TOKEN).returncode == 0
    assert _put(api, [_kunde("kunde-a"), _kunde("kunde-b")]).status_code == 200
    abgleich()

    # NetBox abgewaehlt (Frist): Adresse weg, die NetBox bleibt (nur gemeldet)
    nur_doc = _kunde("kunde-a", werkzeuge=["doc"])
    assert _put(api, [nur_doc, _kunde("kunde-b")]).json()["geaendert"] == ["kunde-a"]
    assert "kunde-a: in Automato ohne NetBox freigegeben – bleibt" in abgleich()
    assert (host.dir / "kunden" / "kunde-a" / "netbox").is_dir()
    assert _inventar(api)["ist"]["netbox_host"]["ohne_freigabe"] == ["kunde-a"]

    # nach der Frist: Auftrag nur fuer den Teil NetBox, der Kunde bleibt
    r = _put(api, [nur_doc, _kunde("kunde-b")], entfernen=[NB])
    assert r.status_code == 200, r.text
    assert r.json()["entfernen"] == {"angenommen": ["kunde-a"], "abgelehnt": []}
    assert r.json()["entfernt"] == []
    a = _auftrag(session_factory)
    assert (a["netbox"], a["automato_kunde"], a["tor"]) == (True, False, False)
    assert _loeschen(api) == [{"kurzname": "kunde-a", "auftrag": a["kennung"]}]
    assert _ist_entfernen(api)["kunde-a"]["teile"] == {
        "netbox": {"stand": "offen", "grund": "netbox_host"}}  # fmt: skip

    text = abgleich()
    assert "kunde-a: gesichert und gelöscht (Abbau aus dem Cockpit)" in text
    assert not (host.dir / "kunden" / "kunde-a").exists()
    assert (host.dir / "kunden" / "kunde-b" / "netbox").is_dir()
    assert list((tmp_path / "backups" / "kunde-a").glob("kunde-a-*.sql.gz.age"))
    assert (host.dir / "geloescht" / "kunde-a").read_text().strip() == a["kennung"]
    ist = _inventar(api)["ist"]
    assert (ist["netbox_host"]["instanzen"], ist["netbox_host"]["geloescht"]) == (
        ["kunde-b"], ["kunde-a"])  # fmt: skip
    e = {x["kurzname"]: x for x in ist["entfernen"]}["kunde-a"]
    assert e["stand"] == "erledigt" and e["erledigt"] is not None
    assert e["teile"] == {"netbox": {"stand": "erledigt", "grund": "geloescht"}}
    assert _loeschen(api) == []
    with session_factory() as s:  # der Kunde bleibt - mit Automato, ohne NetBox
        pc = s.query(PortalCustomer).filter_by(short_name="kunde-a").one()
        assert (pc.doc, pc.nb, pc.realm) == (True, False, "kunde-a")

    # erledigt: das Cockpit sendet ihn nicht mehr; der naechste Abgleich loescht nichts mehr
    _put(api, [nur_doc, _kunde("kunde-b")], entfernen=[])
    assert _auftrag(session_factory) is None
    abgleich()
    assert (host.dir / "kunden" / "kunde-b" / "netbox").is_dir()


# --- annehmen: nur Teile, deren Werkzeug aus ist ------------------------------------------------
def test_teil_mit_gewaehltem_werkzeug_wird_abgelehnt(api, session_factory):  # noqa: F811
    _put(api, [_kunde("kunde-a")])
    alle = {"kurzname": "kunde-a", "netbox": True, "automato_kunde": True, "tor": True}
    r = _put(api, [_kunde("kunde-a")], entfernen=[alle])
    assert r.status_code == 200, r.text
    assert r.json()["entfernen"] == {"angenommen": [], "abgelehnt": [
        {"kurzname": "kunde-a", "grund": "aktiv", "teil": "netbox"},
        {"kurzname": "kunde-a", "grund": "aktiv", "teil": "automato_kunde"},
        {"kurzname": "kunde-a", "grund": "aktiv", "teil": "tor"},  # sein Realm ist sein Tor
    ]}  # fmt: skip
    assert _auftrag(session_factory) is None and _loeschen(api) == []
    e = _ist_entfernen(api)["kunde-a"]
    assert (e["stand"], e["grund"]) == ("abgelehnt", "aktiv")
    assert e["teile"]["netbox"] == {"stand": "abgelehnt", "grund": "aktiv"}

    # NetBox abgewaehlt, Automato noch an: nur der Teil NetBox gilt, die uebrigen bleiben abgelehnt
    nur_doc = _kunde("kunde-a", werkzeuge=["doc"])
    r = _put(api, [nur_doc], entfernen=[alle])
    assert r.json()["entfernen"] == {"angenommen": ["kunde-a"], "abgelehnt": [
        {"kurzname": "kunde-a", "grund": "aktiv", "teil": "automato_kunde"},
        {"kurzname": "kunde-a", "grund": "aktiv", "teil": "tor"},
    ]}  # fmt: skip
    a = _auftrag(session_factory)
    assert (a["netbox"], a["automato_kunde"], a["tor"]) == (True, False, False)
    assert [x["kurzname"] for x in _loeschen(api)] == ["kunde-a"]
    assert "tor_entfernen" not in _soll(api)
    e = _ist_entfernen(api)["kunde-a"]
    assert (e["stand"], e["grund"]) == ("offen", "netbox_host")
    assert e["teile"] == {
        "netbox": {"stand": "offen", "grund": "netbox_host"},
        "automato_kunde": {"stand": "abgelehnt", "grund": "aktiv"},
        "tor": {"stand": "abgelehnt", "grund": "aktiv"},
    }
    with session_factory() as s:  # der Kunde bleibt, wie das Cockpit ihn schickt
        assert s.query(PortalCustomer).filter_by(short_name="kunde-a").one().doc is True
    # angenommen ohne Audit „entfernt“; zweimal dieselbe Anfrage aendert nichts
    assert [d[1] for d in _audit(session_factory, "portal.abbau.")] == [
        "portal.abbau.angenommen"]  # fmt: skip
    _put(api, [nur_doc], entfernen=[alle])
    assert len(_audit(session_factory, "portal.abbau.")) == 1
    assert _auftrag(session_factory)["kennung"] == a["kennung"]


def test_nie_fuer_einen_kunden_aus_der_oberflaeche(api, session_factory):  # noqa: F811
    from test_abbau import _ui

    r = _ui(api, "post", "/portal/customers", json={"short_name": "ui-kunde", "name": "UI",
                                                   "nb": False, "doc": True})  # fmt: skip
    assert r.status_code in (200, 201), r.text
    # in derselben Anfrage vom Cockpit uebernommen: der Teil-Abbau gilt erst danach
    r = _put(api, [_kunde("ui-kunde", werkzeuge=["doc"], realm=None)],
             entfernen=[{"kurzname": "ui-kunde", "netbox": True}])  # fmt: skip
    assert r.json()["uebernommen"] == ["ui-kunde"]
    assert r.json()["entfernen"] == {"angenommen": [], "abgelehnt": [
        {"kurzname": "ui-kunde", "grund": "oberflaeche"}]}  # fmt: skip
    assert _auftrag(session_factory, "ui-kunde") is None


# --- wieder gewaehlt: aufgehoben ----------------------------------------------------------------
def test_wieder_gewaehlt_vor_der_ausfuehrung_hebt_den_teil_auf(api, session_factory):  # noqa: F811
    nur_doc = _kunde("kunde-a", werkzeuge=["doc"])
    _put(api, [_kunde("kunde-a")])
    _put(api, [nur_doc], entfernen=[NB])
    erste = _auftrag(session_factory)["kennung"]
    assert [x["auftrag"] for x in _loeschen(api)] == [erste]

    # NetBox wieder gewaehlt, bevor der Agent loescht: aufgehoben, nie mehr geloescht
    r = _put(api, [_kunde("kunde-a")], entfernen=[NB])  # das Cockpit sendet den Auftrag noch
    assert r.json()["entfernen"] == {"angenommen": [], "abgelehnt": [
        {"kurzname": "kunde-a", "grund": "aktiv", "teil": "netbox"}]}  # fmt: skip
    assert _loeschen(api) == []
    e = _ist_entfernen(api)["kunde-a"]
    assert e["stand"] == "erledigt"
    assert e["teile"] == {"netbox": {"stand": "erledigt", "grund": "aufgehoben"}}
    aufgehoben = _audit(session_factory, "portal.abbau.aufgehoben")
    assert [(subj, d["grund"], d["netbox"]) for _, _, subj, d in aufgehoben] == [
        ("kunde-a", "wieder_da", True)]  # fmt: skip
    # eine spaetere Meldung des Agenten aendert daran nichts
    assert _stand(api, []).status_code == 200
    assert _ist_entfernen(api)["kunde-a"]["teile"]["netbox"]["grund"] == "aufgehoben"

    # erneut abgewaehlt und nach der Frist wieder gesendet: gilt wieder, mit neuer Kennung
    r = _put(api, [nur_doc], entfernen=[NB])
    assert r.json()["entfernen"] == {"angenommen": ["kunde-a"], "abgelehnt": []}
    zweite = _auftrag(session_factory)["kennung"]
    assert zweite != erste
    assert _loeschen(api) == [{"kurzname": "kunde-a", "auftrag": zweite}]
    assert _ist_entfernen(api)["kunde-a"]["teile"]["netbox"]["stand"] == "offen"

    # nicht mehr gesendet: aufgehoben (wie bisher)
    _put(api, [nur_doc], entfernen=[])
    assert _auftrag(session_factory) is None and _loeschen(api) == []


def test_agent_hatte_den_auftrag_schon_abgeholt(api, session_factory):  # noqa: F811
    """NetBox genau waehrend des Loeschens wieder gewaehlt: meldet der Agent die Instanz danach als
    geloescht, zeigt der Stand das (nicht `aufgehoben`)."""
    nur_doc = _kunde("kunde-a", werkzeuge=["doc"])
    _put(api, [_kunde("kunde-a")])
    _put(api, [nur_doc], entfernen=[NB])
    _put(api, [_kunde("kunde-a")], entfernen=[NB])
    assert _ist_entfernen(api)["kunde-a"]["teile"]["netbox"]["grund"] == "aufgehoben"
    with session_factory() as s:  # aufgehoben vor einer Weile (Zeit hat Sekunden-Genauigkeit)
        a = s.query(AbbauAuftrag).one()
        frueher = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
        a.teile = {"netbox": {**a.teile["netbox"], "zeit": frueher}}
        s.commit()
    _stand(api, [], geloescht=["kunde-a"])
    assert _ist_entfernen(api)["kunde-a"]["teile"]["netbox"] == {"stand": "erledigt",
                                                                 "grund": "geloescht"}  # fmt: skip
    # ausgefuehrt und das Werkzeug ist an: der Teil faellt aus dem Auftrag (die NetBox, die der
    # Agent jetzt anlegt, ist eine andere); abgewaehlt und gesendet: neuer Auftrag, neue Kennung
    kennung = _auftrag(session_factory)["kennung"]
    _put(api, [_kunde("kunde-a")], entfernen=[NB])
    assert _auftrag(session_factory) is None
    r = _put(api, [nur_doc], entfernen=[NB])
    assert r.json()["entfernen"]["angenommen"] == ["kunde-a"]
    neu = _auftrag(session_factory)
    assert neu["kennung"] != kennung and neu["teile"]["netbox"]["stand"] == "offen"


def test_ganzer_abbau_nur_teile_die_der_neue_kunde_nicht_nutzt(api, session_factory):  # noqa: F811
    """Gibt es den Kurznamen eines ganz entfernten Kunden wieder, enden nur die Teile, deren
    Werkzeug der neue Kunde nutzt - die NetBox des alten Kunden geht trotzdem, wenn der neue keine
    hat."""
    _put(api, [_kunde("kunde-a")])
    _front_status(api, ["kunde-a"])
    weg = [{"kurzname": "kunde-a", "netbox": True, "tor": True}]
    _put(api, [], entfernen=weg)
    # neuer Kunde gleichen Namens, gleicher Realm, ohne NetBox
    r = _put(api, [_kunde("kunde-a", werkzeuge=["doc"])], entfernen=weg)
    assert r.json()["entfernen"] == {"angenommen": ["kunde-a"], "abgelehnt": [
        {"kurzname": "kunde-a", "grund": "aktiv", "teil": "tor"}]}  # fmt: skip
    teile = _ist_entfernen(api)["kunde-a"]["teile"]
    assert teile["tor"] == {"stand": "erledigt", "grund": "aufgehoben"}
    assert teile["netbox"]["stand"] == "offen"
    assert [x["kurzname"] for x in _loeschen(api)] == ["kunde-a"]
    assert "tor_entfernen" not in _soll(api)


# --- Kunde in Automato --------------------------------------------------------------------------
def test_automato_abwaehlen_entfernt_den_angelegten_kunden(api, session_factory):  # noqa: F811
    tid, fremd = _leerer_mandant(session_factory)
    z = {"mandant": tid, "kunden": [fremd], "rolle": "viewer", "kunde_anlegen": True}
    kunden = [_kunde("kunde-a", name="Kunde A", zuordnung=z),
              _kunde("kunde-b", name="Kunde B", zuordnung=z)]  # fmt: skip
    assert _put(api, kunden).status_code == 200
    a, b = _angelegt(session_factory, "kunde-a")[tid], _angelegt(session_factory, "kunde-b")[tid]
    with session_factory() as s:
        s.add(CustomerSelector(customer_id=b, kind="prefix", value="10.2.0.0/16"))
        s.commit()
    weg = [{"kurzname": k, "automato_kunde": True} for k in ("kunde-a", "kunde-b")]

    # `doc` abgewaehlt, das Cockpit will den Kunden aber noch (kunde_anlegen): aktiv
    ohne_doc = [{**k, "werkzeuge": ["nb"]} for k in kunden]
    r = _put(api, ohne_doc, entfernen=weg)
    assert [x["grund"] for x in r.json()["entfernen"]["abgelehnt"]] == ["aktiv", "aktiv"]
    with session_factory() as s:
        assert {c.id for c in s.query(Customer)} == {fremd, a, b}

    # das Cockpit nennt den angelegten Kunden selbst in `zuordnung.kunden`: bleibt (`zugeordnet`) -
    # sonst waere seine naechste Anfrage ungueltig (unbekannter Kunde)
    selbst = {"mandant": tid, "kunden": [fremd, a], "rolle": "viewer"}
    ohne = [{**k, "werkzeuge": ["nb"], "zuordnung": None} for k in kunden]
    r = _put(api, [{**ohne[0], "zuordnung": selbst}, ohne[1]], entfernen=weg)
    assert r.json()["entfernen"] == {"angenommen": ["kunde-a", "kunde-b"], "abgelehnt": []}
    assert _ist_entfernen(api)["kunde-a"]["teile"]["automato_kunde"] == {
        "stand": "offen", "grund": "zugeordnet"}  # fmt: skip

    # ohne `doc` und ohne Zuordnung aus dem Cockpit: der leere Kunde geht (auch aus der Zuordnung
    # in Automato), der mit Daten bleibt (`hat_daten`)
    r = _put(api, ohne, entfernen=weg)
    assert r.json()["entfernen"] == {"angenommen": ["kunde-a", "kunde-b"], "abgelehnt": []}
    assert r.json()["entfernt"] == []
    with session_factory() as s:
        assert {c.id for c in s.query(Customer)} == {fremd, b}
        pcs = {pc.short_name: pc.customer_ids for pc in s.query(PortalCustomer)}
        assert pcs == {"kunde-a": [fremd], "kunde-b": [fremd, b]}  # die Portal-Kunden bleiben
    assert _angelegt(session_factory, "kunde-a") == {}
    e = _ist_entfernen(api)
    assert e["kunde-a"]["teile"] == {"automato_kunde": {"stand": "erledigt", "grund": "geloescht"}}
    assert e["kunde-b"]["teile"] == {"automato_kunde": {"stand": "offen", "grund": "hat_daten"}}
    deleted = _audit(session_factory, "customer.deleted")
    assert [(act, subj) for act, _, subj, _ in deleted] == [("cockpit", str(a))]

    # Daten weg: der naechste Abgleich entfernt ihn
    with session_factory() as s:
        s.query(CustomerSelector).filter_by(customer_id=b).delete()
        s.commit()
    _put(api, ohne, entfernen=weg)
    with session_factory() as s:
        assert {c.id for c in s.query(Customer)} == {fremd}
    assert _ist_entfernen(api)["kunde-b"]["stand"] == "erledigt"

    # `doc` wieder gewaehlt: Automato legt ihn neu an; der erledigte Teil faellt weg
    r = _put(api, kunden, entfernen=weg)
    assert [x["teil"] for x in r.json()["entfernen"]["abgelehnt"]] == ["automato_kunde"] * 2
    with session_factory() as s:
        assert s.query(Customer).count() == 3
        assert s.query(AbbauAuftrag).count() == 0


# --- Anmelde-Tor: frueherer Realm ---------------------------------------------------------------
def test_tor_des_frueheren_realms_nach_dem_wechsel(api, session_factory):  # noqa: F811
    _put(api, [_kunde("kunde-a"), _kunde("kunde-b")])
    _front_status(api, ["kunde-a", "kunde-b"])
    tor = {"kurzname": "kunde-a", "tor": True}
    # Realm gewechselt: das Tor des frueheren Realms geht, wenn ihn kein Kunde mehr nutzt
    r = _put(api, [_kunde("kunde-a", realm="neu-a"), _kunde("kunde-b")], entfernen=[tor])
    assert r.json()["entfernen"] == {"angenommen": ["kunde-a"], "abgelehnt": []}
    a = _auftrag(session_factory)
    assert a["realm"] == "kunde-a"
    assert _soll(api)["tor_entfernen"] == [{"realm": "kunde-a", "auftrag": a["kennung"]}]
    _front_status(api, ["kunde-b", "neu-a"], tor_entfernt=["kunde-a"])
    _put(api, [_kunde("kunde-a", realm="neu-a"), _kunde("kunde-b")], entfernen=[tor])
    assert _ist_entfernen(api)["kunde-a"]["teile"]["tor"] == {"stand": "erledigt",
                                                              "grund": "entfernt"}  # fmt: skip
    assert "tor_entfernen" not in _soll(api)
    # spaeter erneut (Abgleich des Cockpits): derselbe Realm, bleibt erledigt
    _put(api, [_kunde("kunde-a", realm="neu-a"), _kunde("kunde-b")], entfernen=[tor])
    assert _auftrag(session_factory)["kennung"] == a["kennung"]
    _put(api, [_kunde("kunde-a", realm="neu-a"), _kunde("kunde-b")], entfernen=[])

    # der fruehere Realm gehoert inzwischen einem anderen Kunden: nichts zu tun
    r = _put(api, [_kunde("kunde-a", realm="dritt-a"), _kunde("kunde-b", realm="neu-a")],
             entfernen=[tor])  # fmt: skip
    assert r.json()["entfernen"]["angenommen"] == ["kunde-a"]
    assert _ist_entfernen(api)["kunde-a"]["teile"]["tor"] == {
        "stand": "erledigt", "grund": "realm_in_verwendung"}  # fmt: skip
    assert "tor_entfernen" not in _soll(api)


def test_tor_ohne_frueheren_realm(api, session_factory):  # noqa: F811
    # nie ein anderer Realm: sein Tor ist in Verwendung (aktiv)
    _put(api, [_kunde("kunde-a")])
    r = _put(api, [_kunde("kunde-a")], entfernen=[{"kurzname": "kunde-a", "tor": True}])
    assert r.json()["entfernen"]["abgelehnt"] == [{"kurzname": "kunde-a", "grund": "aktiv",
                                                   "teil": "tor"}]  # fmt: skip
    # nie ein Realm: nichts zu tun
    _put(api, [_kunde("kunde-c", realm=None)])
    r = _put(api, [_kunde("kunde-c", realm=None)], entfernen=[{"kurzname": "kunde-c", "tor": True}])
    assert r.json()["entfernen"] == {"angenommen": ["kunde-c"], "abgelehnt": []}
    assert _ist_entfernen(api)["kunde-c"]["teile"]["tor"] == {"stand": "erledigt",
                                                              "grund": "kein_realm"}  # fmt: skip
    # Realm entfernt: der fruehere geht
    _put(api, [_kunde("kunde-a", realm=None)], entfernen=[{"kurzname": "kunde-a", "tor": True}])
    assert _auftrag(session_factory)["realm"] == "kunde-a"


# --- vom Teil- zum ganzen Abbau -----------------------------------------------------------------
def test_danach_ganz_entfernt_uebernimmt_den_offenen_auftrag(api, session_factory):  # noqa: F811
    nur_doc = _kunde("kunde-a", werkzeuge=["doc"])
    _put(api, [_kunde("kunde-a")])
    _put(api, [nur_doc], entfernen=[NB])
    teil = _auftrag(session_factory)
    assert teil["realm"] is None
    # nach der Frist des ganzen Kunden: aus kunden[] und alle Teile
    alle = {"kurzname": "kunde-a", "netbox": True, "automato_kunde": True, "tor": True}
    r = _put(api, [], entfernen=[alle])
    assert r.json()["entfernt"] == ["kunde-a"]
    assert r.json()["entfernen"] == {"angenommen": ["kunde-a"], "abgelehnt": []}
    ganz = _auftrag(session_factory)
    assert (ganz["realm"], ganz["netbox"], ganz["automato_kunde"], ganz["tor"]) == (
        "kunde-a", True, True, True)  # fmt: skip
    assert ganz["kennung"] != teil["kennung"]  # neue Teile: neue Kennung
    assert _loeschen(api) == [{"kurzname": "kunde-a", "auftrag": ganz["kennung"]}]
    assert _soll(api)["tor_entfernen"] == [{"realm": "kunde-a", "auftrag": ganz["kennung"]}]
    geaendert = _audit(session_factory, "portal.abbau.geaendert")
    assert geaendert[-1][3] == {"netbox": True, "automato_kunde": True, "tor": True,
                                "realm": "kunde-a"}  # fmt: skip
    with session_factory() as s:
        assert s.query(IstMeldung).filter_by(quelle="netbox_host").count() == 0
