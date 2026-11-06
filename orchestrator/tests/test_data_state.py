"""Datenstand nach Inhalt, nur Betroffenes neu, erst nach den Laeufen (0.141.0, ADR 0049).

Anwender 2026-11-06: „Berechnungen starten, auch wenn noch einige Läufe in der Warteschlaufe
stehen. Die Neu berechnungen verhindern schnelles einlesen. Dies erst ausführen, wenn die Läufe
durch sind. Die Berechnungen nur anstossen, wenn was geändert hat, also nur die Wege berechnen,
welche von der geänderten Firewall Regel betroffen sind.“

Geprueft wird: gleiche Kennung je Regel und Reihenfolge wie geliefert, ein Lauf ohne Aenderung
aendert den Datenstand nicht und rechnet nichts, eine Regel ohne Bezug zur Applikation rechnet
sie nicht neu, eine Regel der Applikation nur sie, angeschlossene Netze alle, und waehrend Laeufe
offen sind, rechnet nichts.
"""

import copy

import pytest
import test_aci_relevance as ar
import test_app_paths as tap
import test_paths as tp
from app.config import get_settings
from app.models import DataChange, Rule
from app.store import app_refresh, data_state, doc_snapshots
from app.store import rules as rules_store
from sqlalchemy import func, select

OP = {"X-Dev-Roles": "operator"}
DB = f"{ar.TE}/ap-AP1/epg-EPG_db"


@pytest.fixture
def snapshots(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "doc_snapshots", True)
    monkeypatch.setattr(settings, "doc_stale_seconds", 1000.0)
    calls: list[tuple[int, str]] = []
    compute = doc_snapshots.compute

    def counted(session, app, kind, params):
        calls.append((app.name, kind))
        return compute(session, app, kind, params)

    monkeypatch.setattr(doc_snapshots, "compute", counted)
    return calls


def _systems(client, tid) -> dict[str, int]:
    return {x["name"]: x["id"]
            for x in client.get(f"/systems?tenant_id={tid}", headers=tp.ADMIN).json()}  # fmt: skip


def _app(client, tid, name, member) -> int:
    r = client.post(f"/applications?tenant_id={tid}", json={"name": name, "members": [member]},
                    headers=OP)  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _open(client, *ids) -> None:
    for aid in ids:
        for path in ("doc", "connections"):
            r = client.get(f"/applications/{aid}/{path}")
            assert r.status_code == 200, r.text


def _fresh(client, aid) -> bool:
    return client.get(f"/applications/{aid}/doc/state").json()["fresh"]


def _generation(session_factory, tid) -> int:
    s = session_factory()
    try:
        return data_state.generation(s, tid)
    finally:
        s.close()


def _rule(ext, name, src, dst, port):
    return tap._fw(ext, name, src, dst, port, "to-aci", "to-fw02", ("aci", "transit"))


def test_gleiche_kennung_und_reihenfolge_wie_geliefert(client, session_factory):
    tid = tap._seed(client)
    sid = _systems(client, tid)["fw-01"]
    s = session_factory()
    try:
        before = {r.external_id: r.id for r in s.scalars(select(Rule).where(
            Rule.source_system_id == sid))}  # fmt: skip
    finally:
        s.close()
    swapped = {**tap.FW1, "rules": list(reversed(tap.FW1["rules"]))}
    tp._deliver(client, tid, sid, "fortigate", swapped)
    s = session_factory()
    try:
        rows = list(s.scalars(select(Rule).where(Rule.source_system_id == sid)
                              .order_by(*rules_store.ORDER)))  # fmt: skip
        assert {r.external_id: r.id for r in rows} == before  # gleiche Kennung je Regel
        assert [r.external_id for r in rows] == ["12", "11"]  # Reihenfolge wie geliefert
        last = s.scalars(select(DataChange).order_by(DataChange.seq.desc())).first()
        assert last.parts == ["rules"] and sorted(last.rules) == sorted(before.values())
    finally:
        s.close()


def test_nur_betroffene_neu(client, session_factory, snapshots):
    tid = tap._seed(client)
    apic = copy.deepcopy(tap.APIC)  # zweite EPG ohne Regeln auf fw-01
    apic["epgs"].append({"name": "EPG_db", "dn": DB, "tenant": "TE", "ap": "AP1", "bd": "BD_db",
                         "vrf": "VRF1", "bd_subnets": ["10.50.2.1/24"],
                         "endpoints": [{"ip": "10.50.2.10"}]})  # fmt: skip
    tp._deliver(client, tid, _systems(client, tid)["apic-01"], "cisco_aci", apic)
    web = _app(client, tid, "Web", {"kind": "epg", "ref": ar.WEB})
    other = _app(client, tid, "Andere", {"kind": "epg", "ref": DB})
    _open(client, web, other)
    sid = _systems(client, tid)["fw-01"]
    gen = _generation(session_factory, tid)
    snapshots.clear()

    # gleicher Lauf: kein neuer Datenstand, nichts gerechnet
    tp._deliver(client, tid, sid, "fortigate", tap.FW1)
    assert _generation(session_factory, tid) == gen and snapshots == []
    assert _fresh(client, web) and _fresh(client, other)

    # neue Regel ohne Bezug (andere Adressen) auf der Firewall des Wegs: uebernommen
    extra = copy.deepcopy(tap.FW1)
    extra["rules"].append(_rule("13", "fremd", "10.99.1.1", "10.99.2.2", 22))
    tp._deliver(client, tid, sid, "fortigate", extra)
    assert _generation(session_factory, tid) == gen + 1
    assert snapshots == [], "Regel ohne Bezug darf nichts neu rechnen"
    assert _fresh(client, web) and _fresh(client, other)

    # Regel der Applikation geaendert: nur sie neu
    changed = copy.deepcopy(extra)
    changed["rules"][0]["services"] = ["tcp/443", "tcp/8443"]
    tp._deliver(client, tid, sid, "fortigate", changed)
    assert sorted(snapshots) == [("Web", "connections"), ("Web", "doc")]
    assert _fresh(client, web) and _fresh(client, other)
    snapshots.clear()

    # angeschlossenes Netz neu: kann jede Applikation betreffen - alle neu
    routed = copy.deepcopy(changed)
    routed["routing"]["routes"].append(
        tp._r("fw-01", "root", "10.98.0.0/24", [{"interface": "srv"}], "connected")
    )
    tp._deliver(client, tid, sid, "fortigate", routed)
    assert sorted(snapshots) == [("Andere", "connections"), ("Andere", "doc"),
                                 ("Web", "connections"), ("Web", "doc")]  # fmt: skip


def test_erst_nach_den_laeufen(client, session_factory, snapshots):
    tid = tap._seed(client)
    web = _app(client, tid, "Web", {"kind": "epg", "ref": ar.WEB})
    _open(client, web)
    systems = _systems(client, tid)
    snapshots.clear()
    waiting = tp._run(client, tid, systems["fw-02"], "fortigate")  # wartet (Warteschlange)
    changed = copy.deepcopy(tap.FW1)
    changed["rules"][0]["services"] = ["tcp/443", "tcp/8443"]
    tp._deliver(client, tid, systems["fw-01"], "fortigate", changed)
    assert snapshots == [] and app_refresh.waiting(tid) == 1  # erst nach dem offenen Lauf
    assert not _fresh(client, web)
    r = client.post(f"/internal/collector-runs/{waiting}/result", headers=tp.TOKEN, json=tap.FW2)
    assert r.status_code == 200, r.text
    assert sorted(snapshots) == [("Web", "connections"), ("Web", "doc")]
    assert app_refresh.waiting(tid) == 0 and _fresh(client, web)


def test_aenderungen_werden_aufgeraeumt(client, session_factory, monkeypatch):
    """Nur die letzten `KEEP` Aenderungen bleiben - aeltere Staende rechnen dann neu."""
    monkeypatch.setattr(data_state, "KEEP", 2)
    tid = tap._seed(client)
    sid = _systems(client, tid)["fw-01"]
    for port in (8443, 9443, 10443):
        changed = copy.deepcopy(tap.FW1)
        changed["rules"][0]["services"] = [f"tcp/{port}"]
        tp._deliver(client, tid, sid, "fortigate", changed)
    s = session_factory()
    try:
        count = s.scalar(select(func.count()).select_from(DataChange)
                         .where(DataChange.tenant_id == tid))  # fmt: skip
        gen = data_state.generation(s, tid)
        assert count == 2 and data_state.changes(s, tid, gen - 3, gen) is None
        assert len(data_state.changes(s, tid, gen - 2, gen)) == 2
    finally:
        s.close()
