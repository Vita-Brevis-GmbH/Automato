"""Datenstand nach Inhalt, nur Betroffenes neu, erst nach den Laeufen (0.141.0, ADR 0049).

Anwender 2026-11-06: „Berechnungen starten, auch wenn noch einige Läufe in der Warteschlaufe
stehen. Die Neu berechnungen verhindern schnelles einlesen. Dies erst ausführen, wenn die Läufe
durch sind. Die Berechnungen nur anstossen, wenn was geändert hat, also nur die Wege berechnen,
welche von der geänderten Firewall Regel betroffen sind.“

Geprueft wird: gleiche Kennung je Regel und Reihenfolge wie geliefert, ein Lauf ohne Aenderung
aendert den Datenstand nicht und rechnet nichts, eine Regel ohne Bezug zur Applikation rechnet
sie nicht neu, eine Regel der Applikation nur sie, angeschlossene Netze alle, und waehrend Laeufe
offen sind, rechnet nichts. 0.141.2 (Anwender: Klick auf die Regel in den Findings - „404“):
umbenannte Regeln und Regeln mit gleichem Schluessel behalten ihre Kennung, Befunde der
Applikationen zeigen gleich nach dem Einlesen auf die Regel bzw. fallen weg.
"""

import copy

import pytest
import test_aci_relevance as ar
import test_app_paths as tap
import test_paths as tp
from app.config import get_settings
from app.models import DataChange, Finding, FindingType, Rule
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


def _ids(session_factory, sid) -> dict[str, list[int]]:
    s = session_factory()
    try:
        out: dict[str, list[int]] = {}
        for r in s.scalars(select(Rule).where(Rule.source_system_id == sid).order_by(Rule.id)):
            out.setdefault(r.external_id, []).append(r.id)
        return out
    finally:
        s.close()


def test_umbenannt_und_dubletten_behalten_kennung(client, session_factory):
    """0.141.2: umbenannt (gleiche Policy-ID) und mehrere Regeln mit gleichem Schluessel behalten
    ihre Kennung; Umbenennen und Loeschen zugleich spielt ein (SQLite: keine doppelte Kennung)."""
    tid = tap._seed(client)
    sid = _systems(client, tid)["fw-01"]
    twins = copy.deepcopy(tap.FW1)
    twins["rules"] += [_rule("13", "zwilling", "10.99.1.1", "10.99.2.2", 22),
                       _rule("13", "zwilling", "10.99.1.3", "10.99.2.4", 22)]  # fmt: skip
    tp._deliver(client, tid, sid, "fortigate", twins)
    before = _ids(session_factory, sid)
    assert len(before["13"]) == 2
    tp._deliver(client, tid, sid, "fortigate", twins)
    assert _ids(session_factory, sid) == before  # Dubletten: gleiche Kennungen
    renamed = copy.deepcopy(twins)
    renamed["rules"][0]["name"] = "web-lan-neu"
    del renamed["rules"][1]  # 12 geloescht
    tp._deliver(client, tid, sid, "fortigate", renamed)
    after = _ids(session_factory, sid)
    assert after["11"] == before["11"] and "12" not in after and after["13"] == before["13"]


def _app_findings(session_factory, tid) -> dict[str, int]:
    s = session_factory()
    try:
        stmt = select(Finding).where(Finding.tenant_id == tid,
                                     Finding.type == FindingType.APP_RULE_UNUSED)  # fmt: skip
        return {f.type.value: f.detail["rule_id"] for f in s.scalars(stmt)}
    finally:
        s.close()


def test_befund_folgt_der_regel_schon_beim_einlesen(client, session_factory, snapshots):
    """0.141.2: Befunde der Applikationen rechnen erst nach den Laeufen neu - ihr Link auf die
    Regel stimmt aber schon nach dem Einlesen: umbenannt -> dieselbe Regel, geloescht -> weg."""
    tid = tap._seed(client)
    web = _app(client, tid, "Web", {"kind": "epg", "ref": ar.WEB})
    _open(client, web)
    systems = _systems(client, tid)
    (rule_id,) = _app_findings(session_factory, tid).values()  # fw-03: nicht auf dem Weg
    tp._run(client, tid, systems["fw-02"], "fortigate")  # wartet: Befunde rechnen noch nicht
    renamed = copy.deepcopy(tap.FW3)
    renamed["rules"][0]["name"] = "web-lan-alt-neu"
    tp._deliver(client, tid, systems["fw-03"], "fortigate", renamed)
    assert app_refresh.waiting(tid) == 1
    assert _app_findings(session_factory, tid) == {"app_rule_unused": rule_id}
    assert client.get(f"/rules/{rule_id}").status_code == 200
    gone = {**tap.FW3, "rules": []}
    tp._deliver(client, tid, systems["fw-03"], "fortigate", gone)
    assert _app_findings(session_factory, tid) == {}  # Regel weg: Befund trifft nicht mehr zu
