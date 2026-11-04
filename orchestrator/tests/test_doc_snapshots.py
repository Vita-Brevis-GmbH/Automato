"""Doku je Applikation als Stand (0.108.0, ADR 0037, Anwender: „das Speichern dauert zu lange“).

- der Stand wird gespeichert und wiederverwendet, solange sich nichts aendert, das die Doku
  betrifft (Fingerabdruck); eine Aenderung an der Applikation, an Zonen bzw. ein neuer Lauf
  rechnet neu - Befunde sind immer aktuell, ohne neue Berechnung;
- dauert die Berechnung lange, kommt sofort der letzte Stand (`fresh=false, building=true`), neu
  gerechnet wird hinter der Antwort; `…/doc/state` sagt, wann er fertig ist;
- Varianten je Sprache und Ausschnitt eines Kunden; Verbindungen ebenso (nur Betreuer);
- nach einer Aenderung rechnet der Hintergrund die angesehenen Staende vor.
"""

import time

import pytest
import test_paths as tp
from app.config import get_settings
from app.models import DocSnapshot
from app.store import doc_snapshots
from test_app_paths import FW1, _connections, _seed

OP = {"X-Dev-Roles": "operator"}


@pytest.fixture
def snapshots(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "doc_snapshots", True)
    monkeypatch.setattr(settings, "doc_stale_seconds", 1000.0)  # Tests: immer schnell genug
    calls: list[str] = []
    compute = doc_snapshots.compute

    def counted(session, app, kind, params):
        calls.append(kind)
        return compute(session, app, kind, params)

    monkeypatch.setattr(doc_snapshots, "compute", counted)
    return calls


def _app(client) -> tuple[int, int]:
    tid = _seed(client)
    _connections(client, tid)  # Applikation „Web“
    return tid, client.get("/applications", params={"tenant_id": tid}).json()[0]["id"]


def _doc(client, aid, **params):
    r = client.get(f"/applications/{aid}/doc", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _rows(session_factory) -> list[tuple[str, str]]:
    s = session_factory()
    try:
        return sorted((r.kind, r.variant) for r in s.query(DocSnapshot))
    finally:
        s.close()


def test_stand_wiederverwendet_und_neu_nur_bei_aenderung(client, session_factory, snapshots):
    tid, aid = _app(client)
    snapshots.clear()
    first = _doc(client, aid)
    assert first["snapshot"]["fresh"] and not first["snapshot"]["building"]
    assert snapshots == ["doc"]
    again = _doc(client, aid)  # nichts geaendert: derselbe Stand, nicht neu gerechnet
    assert snapshots == ["doc"]
    assert again["snapshot"]["built_at"] == first["snapshot"]["built_at"]
    assert again["tables"] == first["tables"] and again["summary"] == first["summary"]

    # Befunde aktuell ohne neue Berechnung: Triage aendert die Doku sofort
    assert first["findings"], "Seed ohne Befund"
    fid = first["findings"][0]["id"]
    r = client.patch(f"/findings/{fid}", json={"status": "resolved"}, headers=OP)
    assert r.status_code == 200, r.text
    after = _doc(client, aid)
    assert fid not in {x["id"] for x in after["findings"]}
    assert after["summary"]["findings"] == first["summary"]["findings"] - 1
    assert snapshots == ["doc"]

    # Aenderung an der Applikation: hinter der Antwort vorgerechnet (Doku und Verbindungen),
    # die Seite bekommt danach den neuen Stand ohne Warten
    snapshots.clear()
    r = client.patch(f"/applications/{aid}", json={"description": "neu"}, headers=OP)
    assert r.status_code == 200
    assert sorted(snapshots) == ["connections", "doc"]
    snapshots.clear()
    assert _doc(client, aid)["snapshot"]["fresh"] and snapshots == []
    # neuer Lauf eines Quellsystems (gleiche Daten): ebenso
    sid = next(x["id"] for x in client.get(f"/systems?tenant_id={tid}", headers=tp.ADMIN).json()
               if x["name"] == "fw-01")  # fmt: skip
    tp._deliver(client, tid, sid, "fortigate", FW1)
    assert sorted(snapshots) == ["connections", "doc"]
    snapshots.clear()
    assert _doc(client, aid)["snapshot"]["fresh"] and snapshots == []
    # Zone geaendert (Konfiguration): ebenso, die Befunde der Zonen im Hintergrund
    z = client.post("/zones", json={"tenant_id": tid, "name": "Z1", "selectors": [
        {"kind": "prefix", "value": "10.99.0.0/16"}]}, headers=OP)  # fmt: skip
    assert z.status_code == 201, z.text
    assert sorted(snapshots) == ["connections", "doc"]
    # ohne Vorrechnen (z. B. nicht angesehen): beim naechsten Aufruf neu
    s = session_factory()
    s.query(DocSnapshot).update({"fingerprint": "alt"})
    s.commit()
    s.close()
    snapshots.clear()
    _doc(client, aid)
    assert snapshots == ["doc"]
    assert _rows(session_factory) == [("connections", "de|-|all|connections"),
                                      ("doc", "de|-|all")]  # fmt: skip


def test_langsam_letzter_stand_sofort_neu_im_hintergrund(client, monkeypatch, snapshots):
    _tid, aid = _app(client)
    first = _doc(client, aid)
    monkeypatch.setattr(get_settings(), "doc_stale_seconds", 0.0)  # jede Berechnung ist „lang“
    client.patch(f"/applications/{aid}", json={"description": "x"}, headers=OP)
    monkeypatch.setattr(doc_snapshots, "warm", lambda *a, **k: 0)  # kein Vorrechnen
    client.patch(f"/applications/{aid}", json={"description": "y"}, headers=OP)
    snapshots.clear()
    stale = _doc(client, aid)
    # sofort der letzte Stand - neu gerechnet wird hinter der Antwort (TestClient: danach)
    assert stale["snapshot"]["fresh"] is False and stale["snapshot"]["building"] is True
    assert stale["tables"] == first["tables"]
    assert snapshots == ["doc"]
    state = client.get(f"/applications/{aid}/doc/state").json()
    assert state["fresh"] is True and state["building"] is False
    fresh = _doc(client, aid)
    assert fresh["snapshot"]["fresh"] is True
    assert fresh["snapshot"]["built_at"] > first["snapshot"]["built_at"]
    # „neu berechnen“: letzter Stand sofort, neu im Hintergrund
    snapshots.clear()
    forced = _doc(client, aid, fresh="true")
    assert forced["snapshot"]["building"] is True and snapshots == ["doc"]


def test_varianten_sprache_kunde_und_verbindungen(client, session_factory, snapshots):
    tid, aid = _app(client)
    _doc(client, aid)
    en = client.get(f"/applications/{aid}/doc", headers={"Accept-Language": "en"})
    assert en.status_code == 200 and en.json()["snapshot"]["fresh"]
    conns = client.get(f"/applications/{aid}/connections").json()
    assert conns["connections"] and conns["snapshot"]["fresh"]
    snapshots.clear()
    again = client.get(f"/applications/{aid}/connections").json()
    assert snapshots == [] and again["connections"] == conns["connections"]
    assert again["plan"] == conns["plan"]
    state = client.get(f"/applications/{aid}/doc/state", params={"kind": "connections"}).json()
    assert state["fresh"] is True
    # Plan-Export aus dem Stand
    svg = client.get(f"/applications/{aid}/connections/plan/export", params={"format": "svg"})
    assert svg.status_code == 200 and snapshots == []
    rows = _rows(session_factory)
    assert ("doc", "de|-|all") in rows and ("doc", "en|-|all") in rows
    assert ("connections", "de|-|all|connections") in rows

    # Kunden-Benutzer: eigene Variante (ihr Ausschnitt), keine Verbindungen
    cust = client.post("/customers", json={"tenant_id": tid, "name": "K1", "selectors": [
        {"kind": "aci_tenant", "value": "TE"}]}, headers=OP)  # fmt: skip
    assert cust.status_code in (200, 201), cust.text
    user = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid),
            "X-Dev-Customers": str(cust.json()["id"])}  # fmt: skip
    r = client.get(f"/applications/{aid}/doc", headers=user)
    assert r.status_code == 200, r.text
    assert any(v.startswith("de|-|c") for k, v in _rows(session_factory) if k == "doc")
    s = client.get(f"/applications/{aid}/doc/state", headers=user)
    assert s.status_code == 200 and s.json()["fresh"]
    r = client.get(f"/applications/{aid}/doc/state", params={"kind": "connections"},
                   headers=user)  # fmt: skip
    assert r.status_code == 403

    # Applikation geloescht: ihre Staende mit
    assert client.delete(f"/applications/{aid}", headers=OP).status_code == 204
    assert _rows(session_factory) == []


def test_aus_wie_bisher(client, session_factory, monkeypatch):
    """`doc_snapshots=false` (Vorgabe der Tests): jedes Mal neu, nichts gespeichert – ausdrücklich
    aus, auch mit `AUTOMATO_TEST_DOC_SNAPSHOTS=1`."""
    monkeypatch.setattr(get_settings(), "doc_snapshots", False)
    _tid, aid = _app(client)
    out = _doc(client, aid)
    assert out["snapshot"]["fresh"] is True and out["tables"]
    assert client.get(f"/applications/{aid}/doc/state").json()["built_at"] is None
    assert _rows(session_factory) == []


def test_uebersicht_stand_der_doku(client, monkeypatch, snapshots):
    """0.129.0 (Anwender: „ein Fenster erstellen, wo man sieht, wie der Stand der
    Neuberechnungen der Dokumentationen ist“): je Applikation passend, veraltet bzw. noch nie
    gerechnet, je Art Varianten, letzte Berechnung; nicht fuer Kunden-Benutzer."""
    monkeypatch.setattr(get_settings(), "doc_warm_days", 0)  # ohne Vorrechnen: bleibt veraltet
    tid, aid = _app(client)
    _doc(client, aid)
    client.post(f"/applications?tenant_id={tid}", json={"name": "Neu", "members": []},
                headers=OP)  # fmt: skip

    def states():
        r = client.get("/applications/doc-states", params={"tenant_id": tid})
        assert r.status_code == 200, r.text
        return r.json()

    out = states()
    apps = {a["name"]: a for a in out["apps"]}
    assert out["enabled"] and out["total"] == 2 and not out["warming"]
    assert apps["Neu"]["state"] == "never"
    web = apps["Web"]
    assert web["state"] == "fresh" and web["stale"] == 0
    assert (web["kinds"]["doc"]["variants"], web["kinds"]["doc"]["fresh"]) == (1, 1)
    assert web["kinds"]["connections"]["fresh"] == 1 and web["kinds"]["doc"]["built_at"]
    assert out["counts"] == {"fresh": 1, "stale": 0, "building": 0, "never": 1}
    assert out["last_built_at"]

    # Aenderung an der Applikation: beide Staende veraltet (ohne Vorrechnen erst beim Oeffnen)
    client.patch(f"/applications/{aid}", json={"description": "neu"}, headers=OP)
    web = {a["name"]: a for a in states()["apps"]}["Web"]
    assert (web["state"], web["stale"], web["auto"]) == ("stale", 2, False)
    _doc(client, aid)  # geoeffnet: die Doku passt wieder, die Verbindungen noch nicht
    web = {a["name"]: a for a in states()["apps"]}["Web"]
    assert (web["state"], web["stale"]) == ("stale", 1)
    assert web["kinds"]["doc"]["fresh"] == 1 and web["kinds"]["connections"]["fresh"] == 0

    # 0.133.1 (Anwender: „der "rechnet gerade" verschwindet immer wieder … er aktuell bei den
    # Verbindungen am Messen ist“): waehrend die Verbindungen rechnen, wie weit und seit wann
    key = (aid, doc_snapshots.CONNECTIONS, "de|connections")
    assert doc_snapshots._claim(key)
    with doc_snapshots._lock:
        doc_snapshots._progress[aid] = {"since": time.time() - 125, "done": 340, "total": 2000}
    try:
        web = {a["name"]: a for a in states()["apps"]}["Web"]
        assert web["state"] == "building"
        p = web["kinds"]["connections"]["progress"]
        assert (p["done"], p["total"]) == (340, 2000) and p["seconds"] >= 125
    finally:
        doc_snapshots._release(key)
    assert doc_snapshots.progress_of(aid) is None  # fertig: kein Fortschritt mehr
    assert "progress" not in {a["name"]: a for a in states()["apps"]}["Web"]["kinds"]["connections"]

    cust = {"X-Dev-Roles": "customer", "X-Dev-Customers": "1"}
    r = client.get("/applications/doc-states", params={"tenant_id": tid}, headers=cust)
    assert r.status_code == 403


def test_alle_vorrechnen_fehler_und_ohne_warten(client, session_factory, monkeypatch, snapshots):
    """0.133.2 (Anwender: „Ich habe noch immer 83 App, welche nie berechnet wurden … bei 14 steht
    veraltet und wird vorgerechnet“): `doc_warm_all` rechnet Doku und Verbindungen jeder
    Applikation im Standard vor - auch nie geoeffnete; ein Anstoss waehrend des Vorrechnens
    wartet nicht; ein Fehler bleibt bis zu neuen Eingaben stehen und zeigt sich in der
    Uebersicht; vorgerechnet ist nicht angesehen."""
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr(get_settings(), "doc_warm_all", True)
    tid, aid = _app(client)  # Anlegen: die neue Applikation wird vorgerechnet, ohne Oeffnen
    assert _rows(session_factory) == [("connections", "de|-|all|connections"),
                                      ("doc", "de|-|all")]  # fmt: skip

    def states() -> dict:
        r = client.get("/applications/doc-states", params={"tenant_id": tid})
        assert r.status_code == 200, r.text
        return r.json()

    out = states()
    assert out["warm_all"] and {a["name"]: a["state"] for a in out["apps"]} == {"Web": "fresh"}
    # nur die Doku gerechnet (z. B. geoeffnet): die Verbindungen fehlen - „noch nie berechnet“,
    # werden vorgerechnet
    s = session_factory()
    s.query(DocSnapshot).filter_by(kind="connections").delete()
    s.commit()
    s.close()
    web = states()["apps"][0]
    assert (web["state"], web["auto"]) == ("never", True)
    snapshots.clear()
    assert doc_snapshots.warm(session_factory, tid) == 1 and snapshots == ["connections"]

    # waehrend des Vorrechnens: ein Anstoss wartet nicht, das Vorrechnen schaut danach erneut
    assert doc_snapshots._start_warm(tid)
    assert doc_snapshots.warm(session_factory, tid) == 0
    assert states()["warming"]
    assert doc_snapshots._end_warm(tid) is False  # Anstoss waehrenddessen: weiter
    assert doc_snapshots._end_warm(tid) is True and not states()["warming"]

    # Fehler beim Rechnen: gemerkt, in der Uebersicht, nicht erneut mit denselben Eingaben
    compute = doc_snapshots.compute

    def broken(session, app, kind, params):
        if kind == doc_snapshots.CONNECTIONS:
            raise AttributeError("'IPv4Address' object has no attribute 'prefixlen'")
        return compute(session, app, kind, params)

    monkeypatch.setattr(doc_snapshots, "compute", broken)
    client.patch(f"/applications/{aid}", json={"description": "neu"}, headers=OP)
    web = states()["apps"][0]
    assert web["state"] == "stale" and web["auto"] is False
    assert "AttributeError" in web["error"]
    assert "prefixlen" in web["kinds"]["connections"]["error"]["text"]
    assert "error" not in web["kinds"]["doc"]
    assert doc_snapshots.warm(session_factory, tid) == 0  # dieselben Eingaben: nicht erneut
    monkeypatch.setattr(doc_snapshots, "compute", compute)
    client.patch(f"/applications/{aid}", json={"description": "neu 2"}, headers=OP)
    web = states()["apps"][0]  # neue Eingaben: erneut gerechnet, der Fehler ist weg
    assert web["state"] == "fresh" and web["error"] is None

    # vorgerechnet ist nicht angesehen: ein Kunden-Ausschnitt, vor 8 Tagen angesehen, bleibt
    # veraltet; der Standard wird immer vorgerechnet
    old = datetime.now(UTC) - timedelta(days=8)
    s = session_factory()
    s.add(
        DocSnapshot(
            application_id=aid,
            kind="doc",
            variant="de|-|cabc",
            fingerprint="x",
            data=doc_snapshots._pack({"params": {}, "result": {}}),
            viewed_at=old,
        )
    )
    s.query(DocSnapshot).update({"fingerprint": "alt", "viewed_at": old})
    s.commit()
    s.close()
    snapshots.clear()
    doc_snapshots.warm_all(session_factory)  # wie nach dem Start: alle Mandanten
    assert sorted(snapshots) == ["connections", "doc"]
    s = session_factory()
    rows = {r.variant: r for r in s.query(DocSnapshot)}
    assert rows["de|-|cabc"].fingerprint == "alt"
    assert all(doc_snapshots._aware(r.viewed_at) < datetime.now(UTC) - timedelta(days=7)
               for r in rows.values())  # fmt: skip
    s.close()
