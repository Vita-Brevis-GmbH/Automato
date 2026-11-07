"""Wege einmal je Mandant und Datenstand, Urteile je Adressklasse (0.140.0, ADR 0048).

Anwender 2026-11-06: „Wir rechnen heute nicht alle Wege durch und lassen auch sonst immer wieder
viel liegen, dies ist nicht zuverlaessig und daher gefaehrlich“. Geprueft wird: dasselbe Ergebnis
mit und ohne Speicher, ein Weg wird je Datenstand nur einmal gerechnet, ein neuer Lauf verwirft
den Speicher, Adressen derselben Klasse haben dasselbe Urteil, und eine Kuerzung ist sichtbar
(Befund `app_coverage`).
"""

import copy
import ipaddress
import json

import test_aci_relevance as ar
import test_app_paths as tap
import test_paths as tp
from app.models import Application, Rule
from app.store import app_paths, path_store, paths, policy_check
from sqlalchemy import select

OP = {"X-Dev-Roles": "operator"}


def _apps(client, tid) -> list[int]:
    ids = []
    for name, member in (("Web", {"kind": "epg", "ref": ar.WEB}),
                         ("Host", {"kind": "host", "ref": tap.WEB})):  # fmt: skip
        r = client.post(f"/applications?tenant_id={tid}", json={"name": name, "members": [member]},
                        headers=OP)  # fmt: skip
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


def _plain(out: dict) -> str:
    out = {k: v for k, v in out.items() if k != "timing"}
    return json.dumps(out, sort_keys=True, default=str)


def test_gleiches_ergebnis_und_jeder_weg_nur_einmal(client, session_factory):
    tid = tap._seed(client)
    web, host = _apps(client, tid)
    s = session_factory()
    try:
        apps = {a.id: a for a in s.scalars(select(Application))}
        before = _plain(app_paths.build(s, apps[web]))  # ohne Speicher (wie die Weg-Suche)
        with path_store.active(s, tid) as store:
            first = _plain(app_paths.build(s, apps[web]))
            misses = store.misses
            again = _plain(app_paths.build(s, apps[web]))
            assert store.misses == misses and store.hits > 0  # nichts neu gerechnet
            app_paths.build(s, apps[host])  # dieselben Wege einer anderen Applikation
        assert first == before == again
        assert path_store.store_for(s, tid) is store  # gleicher Datenstand: derselbe Speicher
    finally:
        s.close()


def test_eigene_kopie_je_abruf(client, session_factory):
    tid = tap._seed(client)
    s = session_factory()
    try:
        with path_store.active(s, tid):
            res = paths.trace(s, tid, tap.SRV, tap.WEB, None, 0, ("tcp", 443))
            res["paths"].clear()  # der Aufrufer ergaenzt bzw. veraendert sein Ergebnis
            again = paths.trace(s, tid, tap.SRV, tap.WEB, None, 0, ("tcp", 443))
        assert again["paths"], "der Speicher darf nicht mitveraendert werden"
    finally:
        s.close()


def test_neuer_lauf_neuer_speicher(client, session_factory):
    tid = tap._seed(client)
    s = session_factory()
    try:
        old = path_store.store_for(s, tid)
    finally:
        s.close()
    fw3 = copy.deepcopy(tap.FW3)
    tp._deliver(client, tid, tp._system(client, tid, "fw-04", "fortigate"), "fortigate", fw3)
    s = session_factory()
    try:
        assert path_store.store_for(s, tid) is not old  # anderer Datenstand
    finally:
        s.close()


def test_adressklassen_gleiches_urteil(client, session_factory):
    """Adressen derselben Klasse treffen dieselben Objekte der Policies - ihr Urteil je Hop ist
    gleich; die Klassen trennen an jeder Grenze eines Adressbereichs."""
    tid = tap._seed(client)
    s = session_factory()
    try:
        rule = s.scalars(select(Rule).where(Rule.external_id == "11")).first()
        sid, vdom = rule.source_system_id, rule.vdom or "root"
        checker = policy_check.Checker(s, tid)
        ingress = {"interface": "to-aci"}
        egress = {"interface": "to-fw02"}
        ips = ["10.50.1.9", "10.50.1.10", "10.50.1.11", "10.60.0.4", "10.60.0.5", "10.60.0.6",
               "192.0.2.1", "8.8.8.8", "10.60.0.0/24", "10.50.1.0/24"]  # fmt: skip
        groups: dict[tuple, list[str]] = {}
        for src in ips:
            for dst in ips:
                a, b = (paths.endpoint(x) for x in (src, dst))
                key = (checker.addr_class(sid, vdom, "src", a),
                       checker.addr_class(sid, vdom, "dst", b))  # fmt: skip
                verdict = checker.check(sid, vdom, ingress, egress, a, b, ("tcp", 443))
                groups.setdefault(key, []).append(json.dumps(verdict, sort_keys=True))
        assert all(len(set(v)) == 1 for v in groups.values()), "gleiche Klasse, anderes Urteil"
        # die Regel 10.50.1.10 -> 10.60.0.5 trennt den Host von seinen Nachbarn
        cls = [checker.addr_class(sid, vdom, "src", ipaddress.ip_address(x))
               for x in ("10.50.1.9", "10.50.1.10", "10.50.1.11")]  # fmt: skip
        assert cls[0] != cls[1] != cls[2]
    finally:
        s.close()


def test_kuerzung_wird_befund(client, monkeypatch):
    """Greift eine Grenze, ist die Applikation unvollstaendig geprueft - Hinweis und Befund
    `app_coverage` statt stiller Kuerzung (bis 0.139)."""
    monkeypatch.setattr(app_paths, "MAX_SERVICES", 1)
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=tp.ADMIN).json()["id"]
    fw1 = copy.deepcopy(tap.FW1)
    fw1["rules"][0]["services"] = ["tcp/443", "tcp/8443", "tcp/9443"]
    for name, platform, result in (("fw-01", "fortigate", fw1), ("fw-02", "fortigate", tap.FW2),
                                   ("apic-01", "cisco_aci", tap.APIC)):  # fmt: skip
        tp._deliver(client, tid, tp._system(client, tid, name, platform), platform, result)
    out = tap._connections(client, tid)
    cov = out["coverage"]
    assert not cov["complete"] and cov["cuts"]["services"] == {"rules": 1, "missing": 2}
    assert any("2 Dienste von 1 Regeln nicht geprüft" in n for n in out["notes"])
    r = client.get("/findings", params={"tenant_id": tid, "type": "app_coverage"})
    (f,) = r.json()
    assert f["subject_id"] == "Web" and f["subject_kind"] == "application"
    assert "nicht vollständig geprüft" in f["message"]
