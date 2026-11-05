"""Neuberechnen besser gesteuert (0.139.0, Anwender 2026-11-05).

- „Wenn ich Imports am Laufen habe, startet er jedes Mal neu, wenn ein Gerät importiert hat …
  vorberechnen ungeachtet der Daten mal starten und dann wenn alle vorberechnet sind, die
  komplette Berechnung durchführen“: das Vorrechnen arbeitet in Durchgaengen - je Stand einmal,
  ein Lauf waehrenddessen merkt nur einen weiteren Durchgang; die Befunde Regel <-> Weg laufen
  zusammengefasst hinter den Laeufen.
- „nicht alle Firewalls und ACI neu integrieren, sondern nur die Regeln neu berechnen, welche
  geändert haben“: Regeln einer Applikation neu einlesen - Teil-Lauf nur der Regeln (Inventar
  und Routing bleiben), danach rechnet nur diese Applikation neu.
"""

import copy
from datetime import UTC, datetime

import pytest
import test_paths as tp
from app.config import get_settings
from app.models import CollectorRun, RunStatus
from app.store import app_refresh, bgp_cache, doc_snapshots
from test_app_paths import FW1, WEB, _fw, _seed

OP = {"X-Dev-Roles": "operator"}


@pytest.fixture
def computed(monkeypatch):
    """Doku-Staende an, jede Berechnung mit (Applikation, Art) gezaehlt."""
    settings = get_settings()
    monkeypatch.setattr(settings, "doc_snapshots", True)
    monkeypatch.setattr(settings, "doc_stale_seconds", 1000.0)
    monkeypatch.setattr(settings, "doc_warm_all", True)
    calls: list[tuple[int, str]] = []
    compute = doc_snapshots.compute

    def counted(session, app, kind, params):
        calls.append((app.id, kind))
        return compute(session, app, kind, params)

    monkeypatch.setattr(doc_snapshots, "compute", counted)
    return calls


def _apps(client, tid) -> dict[str, int]:
    web = [{"kind": "epg", "ref": "uni/tn-TE/ap-AP1/epg-EPG_web"}]
    for body in ({"name": "Web", "members": web}, {"name": "Leer", "members": []}):
        r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
        assert r.status_code == 201, r.text
    apps = client.get("/applications", params={"tenant_id": tid}).json()
    return {a["name"]: a["id"] for a in apps}


def _new_run(session_factory, tid) -> None:
    """Ein weiterer Lauf ist fertig (aendert den Abdruck aller Applikationen)."""
    s = session_factory()
    s.add(CollectorRun(platform="fortigate", tenant_id=tid, status=RunStatus.COMPLETED,
                       finished_at=datetime.now(UTC)))  # fmt: skip
    s.commit()
    s.close()


def _states(client, tid) -> dict:
    r = client.get("/applications/doc-states", params={"tenant_id": tid})
    assert r.status_code == 200, r.text
    return r.json()


def test_vorrechnen_in_durchgaengen_ein_lauf_bricht_nichts_ab(client, session_factory, computed):
    tid = _seed(client)
    ids = _apps(client, tid)
    _new_run(session_factory, tid)  # alles veraltet
    computed.clear()
    seen: list[dict | None] = []

    compute = doc_snapshots.compute

    def during(session, app, kind, params):
        # in den ersten zwei Berechnungen kommt je ein Lauf dazu - wie Importe im Gang
        if len(computed) < 2:
            _new_run(session_factory, tid)
            assert doc_snapshots.warm(session_factory, tid) == 0  # wartet nicht, merkt sich
        seen.append(doc_snapshots.round_of(tid))
        return compute(session, app, kind, params)

    doc_snapshots.compute = during
    try:
        doc_snapshots.warm(session_factory, tid)
    finally:
        doc_snapshots.compute = compute
    states = 4  # Doku + Verbindungen je Applikation
    first = [k for k, r in zip(computed, seen, strict=True) if r["number"] == 1]
    # Durchgang 1: jeder Stand genau einmal - bis 0.138.0 kamen nach jedem Lauf die schnellen
    # erneut zuerst dran
    assert len(first) == states and len(set(first)) == states, computed
    r1 = seen[states - 1]
    assert (r1["apps_total"], r1["total"], r1["done"]) == (2, 4, 3)
    assert r1["again"] is True  # Lauf waehrenddessen: danach ein weiterer Durchgang
    # Durchgang 2: nur, was vor den Laeufen gerechnet war (die ersten beiden)
    second = [k for k, r in zip(computed, seen, strict=True) if r["number"] == 2]
    assert sorted(second) == sorted(first[:2])
    assert doc_snapshots.round_of(tid) is None
    out = _states(client, tid)
    assert out["round"] is None and {a["state"] for a in out["apps"]} == {"fresh"}
    assert set(ids) == {"Web", "Leer"}


def test_routing_filter_nach_anstoss_waehrenddessen_noch_einmal(session_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "doc_snapshots", True)
    calls = []

    def once(factory, tid):
        calls.append(tid)
        if len(calls) == 1:
            assert bgp_cache.warm(factory, tid) == 0  # rechnet schon: danach noch einmal
        return 1

    monkeypatch.setattr(bgp_cache, "_warm_once", once)
    assert bgp_cache.warm(session_factory, 7) == 2 and calls == [7, 7]
    assert bgp_cache.warm(session_factory, 7) == 1  # ohne Anstoss: einmal


def test_befunde_nach_laeufen_zusammengefasst(session_factory, monkeypatch):
    from fastapi import BackgroundTasks

    ran = []
    monkeypatch.setattr(app_refresh, "run_runs", lambda *a: ran.append(a))
    bg = BackgroundTasks()
    assert app_refresh.schedule_runs(bg, session_factory, 5) is True
    assert app_refresh.schedule_runs(bg, session_factory, 5) is False  # steht schon aus
    assert app_refresh.pending_count(5) == 1
    # Lauf nur der Regeln einer Applikation: eigener Durchgang, nur sie
    assert app_refresh.schedule_runs(bg, session_factory, 5, 9, full=False) is True
    assert len(bg.tasks) == 2
    app_refresh._release(("runs", 5, 0))
    app_refresh._release(("runs", 5, 9))


def _rules_run(client, aid, sids) -> list[dict]:
    r = client.post(f"/applications/{aid}/read-rules", json={"source_system_ids": sids},
                    headers=OP)  # fmt: skip
    assert r.status_code == 202, r.text
    return r.json()["items"]


def test_regeln_einer_applikation_neu_einlesen(client, session_factory, computed):
    tid = _seed(client)
    ids = _apps(client, tid)
    web, leer = ids["Web"], ids["Leer"]
    assert client.get(f"/applications/{web}/connections").status_code == 200
    assert client.get(f"/applications/{web}/doc").status_code == 200

    src = client.get(f"/applications/{web}/rule-sources", headers=OP)
    assert src.status_code == 200, src.text
    by_name = {s["name"]: s for s in src.json()}
    assert set(by_name) == {"fw-01", "fw-02", "fw-03", "apic-01"}  # nur FortiGate und ACI
    own = [s["name"] for s in src.json() if s["own"]]
    assert {"fw-01", "fw-02", "apic-01"} <= set(own)
    assert src.json()[: len(own)] == [s for s in src.json() if s["own"]]  # die eigenen zuerst
    assert "path" in by_name["fw-01"]["reasons"]
    last_full = by_name["fw-01"]["last_full_run"]
    assert last_full is not None
    cust = {"X-Dev-Roles": "customer", "X-Dev-Customers": "1"}
    assert client.get(f"/applications/{web}/rule-sources", headers=cust).status_code == 403

    fw1 = by_name["fw-01"]["source_system_id"]
    (item,) = _rules_run(client, web, [fw1])
    assert item["run_id"] and item["skipped"] is None
    run_id = item["run_id"]
    # schon offen: uebersprungen mit Grund
    (again,) = _rules_run(client, web, [fw1])
    assert again["run_id"] is None and again["skipped"]
    runs = {r["id"]: r for r in client.get("/jobs", params={"tenant_id": tid}).json()}
    assert (runs[run_id]["scope"], runs[run_id]["application_id"]) == ("rules", web)
    # der Worker bekommt „nur Regeln“
    t = client.get(f"/internal/collector-runs/{run_id}/target", headers=tp.TOKEN).json()
    assert t["target"]["scope"] == "rules"

    # fw-01 liefert nur Regeln: 8080 ist jetzt 8443 - ohne Inventar und Routing
    rules = [r for r in copy.deepcopy(FW1["rules"]) if r["external_id"] == "11"]
    rules.append(_fw("12", "web-lan-8443", WEB, "10.60.0.5", 8443, "to-aci", "to-fw02",
                     ("aci", "transit")))  # fmt: skip
    computed.clear()
    r = client.post(
        f"/internal/collector-runs/{run_id}/result",
        headers=tp.TOKEN,
        json={"status": "completed", "rules": rules, "message": "nur Regeln"},
    )
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    # nur die Applikation rechnet neu - Doku und Verbindungen, die leere nicht
    assert computed and {a for a, _k in computed} == {web}, computed
    out = {a["name"]: a for a in _states(client, tid)["apps"]}
    assert out["Web"]["state"] == "fresh" and out["Leer"]["state"] == "stale"
    # Inventar und Routing von fw-01 sind geblieben: derselbe Weg, der neue Dienst
    conns = client.get(f"/applications/{web}/connections").json()["connections"]
    services = {c["service"] for c in conns}
    assert "tcp/8443" in services and "tcp/8080" not in services, services
    https = next(c for c in conns if c["service"] == "tcp/443")
    assert [h["label"] for h in https["hops"]] == ["apic-01 · VRF TE:VRF1", "fw-01 · VDOM root",
                                                   "fw-02"]  # fmt: skip
    # zaehlt nicht als voller Lauf (Datenqualitaet, Routing-Filter)
    by_name = {s["name"]: s for s in client.get(f"/applications/{web}/rule-sources",
                                                headers=OP).json()}  # fmt: skip
    assert by_name["fw-01"]["last_full_run"] == last_full
    assert leer  # die andere Applikation blieb unberuehrt


def test_regeln_einlesen_nur_fortigate_und_aci(client):
    tid = _seed(client)
    ids = _apps(client, tid)
    ddi = tp._system(client, tid, "ddi", "infoblox", username="u", password="p")
    (item,) = _rules_run(client, ids["Web"], [ddi])
    assert item["run_id"] is None and "FortiGate" in item["skipped"]
    # ohne Angabe: die Geraete der Applikation (hier ohne Stand: keine)
    r = client.post(f"/applications/{ids['Leer']}/read-rules", json={}, headers=OP)
    assert r.status_code == 202 and r.json() == {"queued": 0, "items": []}
    viewer = {"X-Dev-Roles": "viewer"}
    r = client.post(f"/applications/{ids['Web']}/read-rules", json={}, headers=viewer)
    assert r.status_code == 403


def test_befunde_aus_dem_stand_der_verbindungen(client, session_factory, computed, monkeypatch):
    """0.139.1 (Anwender: „Ich habe Fehler bei der Doku Erstellung“ - nach dem Update rechnete der
    Orchestrator je Applikation minutenlang die Befunde mit denselben Wegen wie die Verbindungen,
    die Doku kam nicht dran): mit dem Stand der Doku kommen die Befunde der Applikationen aus
    ihren eben gerechneten Verbindungen - Laeufe, Updates und Aenderungen rechnen die Wege nicht
    ein zweites Mal; „Abgleich neu berechnen“ von Hand weiterhin."""
    from app.store import reconcile

    tid = _seed(client)
    traced: list[int] = []
    issues = reconcile._app_issues

    def counted(session, tenant_id, apps, budget=None):
        traced.append(len(apps))
        return issues(session, tenant_id, apps, budget)

    monkeypatch.setattr(reconcile, "_app_issues", counted)
    ids = _apps(client, tid)  # Anlegen: das Vorrechnen rechnet Doku und Verbindungen

    def unused() -> list[dict]:
        r = client.get("/findings", params={"tenant_id": tid, "type": "app_rule_unused"})
        assert r.status_code == 200, r.text
        return r.json()

    found = unused()
    assert found and {f["detail"]["application_id"] for f in found} == {ids["Web"]}
    assert traced == []  # keine zweite Berechnung der Wege
    # ein Lauf: Befunde kommen mit den neu gerechneten Verbindungen
    fw3 = next(s["id"] for s in client.get("/systems").json() if s["name"] == "fw-03")
    computed.clear()
    from test_app_paths import FW3

    tp._deliver(client, tid, fw3, "fortigate", FW3)
    assert traced == [] and (ids["Web"], "connections") in computed
    assert unused()
    # von Hand („Abgleich neu berechnen“): wie bisher mit eigenen Wegen
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=OP).status_code == 200
    assert traced and unused()
