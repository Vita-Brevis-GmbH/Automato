# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Verlauf des Routings (ADR 0030): Aenderungen von Lauf zu Lauf je Quellsystem.

Erster Lauf = Ausgangsstand; danach hinzugefuegt/entfernt/geaendert mit den verglichenen Feldern
vorher/nachher - Reihenfolge, Zaehler und Felder ausserhalb der Positivliste zaehlen nicht. Ein
nicht lesbarer Kontext erzeugt keine Flut „entfernt“, ein Hostname-Wechsel (HA) keine Flut
ueberhaupt; die Obergrenze je Lauf fasst den Rest zusammen. Dazu Aufbewahrung, API-Filter,
Kunden-Ausschnitt, Rechte und Englisch.
"""

import copy
from datetime import UTC, datetime, timedelta

from app.models import TopoChange, TopoContext, TopoL3Out, TopoPeer, TopoRoute
from app.store import topo_changes
from sqlalchemy import func, select
from test_customer_infra import _route as croute
from test_customer_infra import _seed as cseed
from test_customers import FW as CFW
from test_routing import ADMIN, FW, _deliver, _system

EN = {"Accept-Language": "en"}


def _seed(client) -> tuple[int, int]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    return tid, _system(client, tid, "fw-01", "fortigate")


def _changes(client, tid, headers=None, **params) -> tuple[list[dict], int]:
    r = client.get("/routing/changes", params={"tenant_id": tid, **params}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json(), int(r.headers["X-Total-Count"])


def _route(fw: dict, context: str, prefix: str, table: str = "rib") -> dict:
    return next(
        r
        for r in fw["routing"]["routes"]
        if (r["context"], r["prefix"], r["table"]) == (context, prefix, table)
        and (table != "static" or r.get("best", True))
    )


def _changed_run() -> dict:
    """Zweiter Lauf: ECMP weg, eine Route weg, eine neu, Backup-Route aktiv, Nachbar down,
    HA-Rollen getauscht, Policy-Route entfernt."""
    fw = copy.deepcopy(FW)
    routing = fw["routing"]
    _route(fw, "root", "0.0.0.0/0")["next_hops"] = [{"ip": "198.51.100.1", "interface": "wan1"}]
    routing["routes"] = [r for r in routing["routes"] if r["prefix"] != "10.20.0.0/24"]
    routing["routes"].append(
        {"node": "fw-01", "context": "root", "prefix": "10.30.0.0/24", "table": "rib",
         "protocol": "static", "distance": 10,
         "next_hops": [{"ip": "10.20.0.254", "interface": "srv"}]}
    )  # fmt: skip
    backup = next(r for r in routing["routes"] if r["table"] == "static" and not r.get("best", 1))
    backup["best"] = True
    routing["peers"][0]["state"] = "Idle"
    routing["ha"][0]["members"] = [{"hostname": "fw-a", "role": "secondary"},
                                   {"hostname": "fw-b", "role": "primary"}]  # fmt: skip
    routing["policy_routes"] = [p for p in routing["policy_routes"] if p.get("seq") != 6]
    return fw


def test_ausgangsstand_dann_aenderungen(client, session_factory):
    tid, fw = _seed(client)
    _deliver(client, tid, fw, "fortigate", FW)
    rows, total = _changes(client, tid)
    assert total == 1 and rows[0]["change"] == "baseline" and rows[0]["kind"] == "context"
    assert rows[0]["after"]["counts"] == {"contexts": 2, "rib": 4, "static": 2, "bgp": 2,
                                          "peers": 1, "policy_routes": 3, "ha": 1}  # fmt: skip
    assert rows[0]["summary"].startswith("Ausgangsstand: 2 Kontexte, 4 wirksame Routen")
    assert rows[0]["label"] == "fw-01" and rows[0]["run_exists"]

    # gleichwertig: andere Reihenfolge (Routen, ECMP-Next-Hops, Mitglieder) und Felder ausserhalb
    # der Positivliste (Zaehler) - keine Aenderung
    same = copy.deepcopy(FW)
    same["routing"]["routes"].reverse()
    _route(same, "root", "0.0.0.0/0")["next_hops"].reverse()
    same["routing"]["peers"][0]["attrs"] = {"uptime": 4711, "established_count": 3}
    _route(same, "root", "10.20.0.0/24")["attrs"] = {"tunnel_parent": "x", "comment": "neu"}
    same["routing"]["ha"][0]["members"].reverse()
    _deliver(client, tid, fw, "fortigate", same)
    assert _changes(client, tid)[1] == 1

    _deliver(client, tid, fw, "fortigate", _changed_run())
    rows, total = _changes(client, tid)
    by = {(c["kind"], c["key"], c["change"]): c for c in rows}
    assert total == 8, [(c["kind"], c["key"], c["change"]) for c in rows]
    default = by[("route", "0.0.0.0/0", "changed")]
    assert default["table"] == "rib" and default["context"] == "root"
    assert default["label"] == "fw-01 · VDOM root"
    assert [f["field"] for f in default["fields"]] == ["next_hops"]
    assert default["summary"] == (
        "Next-Hops: 198.51.100.1 via wan1, 198.51.100.5 via wan2 → 198.51.100.1 via wan1"
    )
    ecmp = [
        {"ip": "198.51.100.1", "interface": "wan1"},
        {"ip": "198.51.100.5", "interface": "wan2"},
    ]
    assert default["before"]["next_hops"] == ecmp
    gone = by[("route", "10.20.0.0/24", "removed")]
    assert gone["before"] == {"protocol": "connected", "next_hops": [{"interface": "srv"}]}
    assert gone["after"] is None
    assert gone["summary"] == "Next-Hops direkt via srv · Protokoll connected"
    assert by[("route", "10.30.0.0/24", "added")]["after"]["distance"] == 10
    backup = by[("route", "10.99.0.0/16 via=10.30.0.254", "changed")]
    assert backup["table"] == "static" and backup["summary"] == "aktiv/Backup: inaktiv → aktiv"
    peer = by[("bgp_peer", "198.51.100.1", "changed")]
    assert peer["summary"] == "Zustand: Established → Idle"
    ha = by[("ha", "", "changed")]
    assert ha["node"] == "fw-01" and "fw-a (primary)" in ha["fields"][0]["before"]
    assert by[("policy_route", "#6", "removed")]["table"] == "policy"
    # 8 = Ausgangsstand + 4 Routen, Nachbar, HA, Policy-Route; Unveraendertes fehlt
    assert ("route", "10.99.0.0/16 via=10.20.0.254", "changed") not in by

    # Reihenfolge: neuster Lauf zuerst, darin Wichtiges zuerst (HA, Nachbarn, Policy, Routen)
    assert [c["kind"] for c in rows[:3]] == ["ha", "bgp_peer", "policy_route"]
    assert rows[-1]["change"] == "baseline"

    # Englisch: Kurztext und Feldnamen in der Sprache der Anfrage, Werte bleiben
    en = {(c["kind"], c["key"]): c for c in _changes(client, tid, headers=EN)[0]}
    assert en[("bgp_peer", "198.51.100.1")]["summary"] == "State: Established → Idle"
    assert en[("route", "10.99.0.0/16 via=10.30.0.254")]["summary"] == (
        "active/backup: inactive → active"
    )
    assert en[("context", "")]["summary"].startswith("Baseline: 2 contexts, 4 effective routes")
    with session_factory() as s:
        stored = s.scalars(select(TopoChange).where(TopoChange.kind == "bgp_peer")).one()
        assert stored.after == {"state": "Idle", "remote_as": 64500}  # Daten, kein Text
        assert stored.collector_run_id and stored.tenant_id == tid

    # geaendert seit: an den Routen der Liste
    routes = client.get("/routing/routes", params={"tenant_id": tid, "context": "root"}).json()
    changed = {(r["prefix"], r["table"]): r["changed_at"] for r in routes}
    assert changed[("0.0.0.0/0", "rib")] and changed[("10.30.0.0/24", "rib")]
    assert changed[("2001:db8::/32", "rib")] is None and changed[("0.0.0.0/0", "bgp")] is None
    summary = client.get("/routing/summary", params={"tenant_id": tid}).json()
    assert summary["changes_7d"] == 7  # ohne den Ausgangsstand


def test_filter_der_api(client):
    tid, fw = _seed(client)
    _deliver(client, tid, fw, "fortigate", FW)
    _deliver(client, tid, fw, "fortigate", _changed_run())
    assert _changes(client, tid, kind="route")[1] == 4
    rows, total = _changes(client, tid, kind="route", change="removed")
    assert total == 1 and rows[0]["key"] == "10.20.0.0/24"
    assert _changes(client, tid, change="note")[1] == 1  # Ausgangsstand
    # Suche im Schluessel (Praefix, Gateway der statischen Route) und in vorher/nachher
    found = {c["key"] for c in _changes(client, tid, q="10.30")[0]}
    assert found == {"10.30.0.0/24", "10.99.0.0/16 via=10.30.0.254"}
    assert _changes(client, tid, q="wan2")[1] == 1  # Next-Hop im Vorher
    assert _changes(client, tid, q="10.30.0 added")[1] == 0  # Woerter: alle muessen passen
    assert _changes(client, tid, context="kunde-a")[1] == 0
    assert _changes(client, tid, context="root", source_system_id=fw)[1] == 6  # ohne HA
    assert _changes(client, tid, source_system_id=fw + 99)[1] == 0
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert _changes(client, tid, since=future)[1] == 0
    assert _changes(client, tid, until=future)[1] == 8
    past = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None).isoformat()  # = UTC
    assert _changes(client, tid, since=past)[1] == 8
    page, total = _changes(client, tid, limit=2, offset=7)
    assert total == 8 and [c["change"] for c in page] == ["baseline"]
    for bad in ({"kind": "egal"}, {"change": "weg"}, {"since": "gestern"}):
        r = client.get("/routing/changes", params={"tenant_id": tid, **bad})
        assert r.status_code == 422, bad
    # Kunden-Benutzer: nicht freigegeben (Freigabeliste, fail-closed)
    who = {"X-Dev-Roles": "customer", "X-Dev-Customers": "1", "X-Dev-Tenants": str(tid)}
    assert client.get("/routing/changes", params={"tenant_id": tid}, headers=who).status_code == 403


def test_nicht_lesbar_ohne_flut(client):
    tid, fw = _seed(client)
    _deliver(client, tid, fw, "fortigate", FW)
    # root: IPv4-Tabelle nicht lesbar -> die IPv4-Routen fehlen, sind aber nicht entfernt;
    # eine statische Route aendert sich trotzdem (ihre Tabelle ist lesbar)
    blind = copy.deepcopy(FW)
    routing = blind["routing"]
    routing["contexts"][0]["missing"] = ["Routing-Tabelle IPv4 (HTTP 403)"]
    routing["routes"] = [
        r for r in routing["routes"]
        if not (r["context"] == "root" and r["table"] == "rib" and ":" not in r["prefix"])
    ]  # fmt: skip
    _route(blind, "root", "10.99.0.0/16", "static")["distance"] = 5
    _deliver(client, tid, fw, "fortigate", blind)
    rows, total = _changes(client, tid)
    assert total == 3, [(c["kind"], c["key"], c["change"]) for c in rows]
    note = next(c for c in rows if c["change"] == "unreadable")
    assert note["context"] == "root" and note["after"]["not_compared"] == 2
    assert note["summary"] == (
        "nicht lesbar: Routing-Tabelle IPv4 – 2 Einträge nicht verglichen (keine Entfernung)"
    )
    assert not [c for c in rows if c["change"] == "removed"]
    assert next(c for c in rows if c["kind"] == "route")["summary"] == "Distanz: 10 → 5"

    # wieder lesbar: kein „hinzugefuegt“ fuer die Routen, sondern ein Vermerk (neuer Ausgangsstand)
    _deliver(client, tid, fw, "fortigate", FW)
    rows, total = _changes(client, tid)
    assert total == 5
    again = rows[0]
    assert (again["kind"], again["change"]) == ("context", "changed")
    assert again["after"]["not_compared"] == 2
    assert again["summary"].startswith("wieder lesbar: Routing-Tabelle IPv4")
    assert [c["summary"] for c in rows[1:2]] == ["Distanz: 5 → 10"]

    # unbekannte Angabe: der ganze Kontext wird nicht verglichen
    odd = copy.deepcopy(FW)
    odd["routing"]["contexts"][0]["missing"] = ["Etwas Neues (HTTP 500)"]
    odd["routing"]["routes"] = [r for r in odd["routing"]["routes"] if r["context"] != "root"]
    odd["routing"]["peers"] = []
    odd["routing"]["policy_routes"] = []
    _deliver(client, tid, fw, "fortigate", odd)
    rows, total = _changes(client, tid)
    assert total == 6 and rows[0]["change"] == "unreadable"
    assert rows[0]["after"]["not_compared"] == 11  # 7 Routen, 1 Nachbar, 3 Policy-Routen in root

    # Kontext nicht mehr gelesen (z. B. VDOM ohne Leserecht): ein Eintrag mit den Zahlen
    _deliver(client, tid, fw, "fortigate", FW)
    without = copy.deepcopy(FW)
    without["routing"]["contexts"] = without["routing"]["contexts"][:1]
    without["routing"]["routes"] = [r for r in without["routing"]["routes"]
                                    if r["context"] != "kunde-a"]  # fmt: skip
    before = _changes(client, tid)[1]
    _deliver(client, tid, fw, "fortigate", without)
    rows, total = _changes(client, tid)
    assert total == before + 1
    assert (rows[0]["kind"], rows[0]["change"], rows[0]["context"]) == ("context", "removed",
                                                                        "kunde-a")  # fmt: skip
    assert rows[0]["before"]["counts"] == {"rib": 1}
    assert rows[0]["summary"] == "Kontext nicht mehr gelesen: 1 wirksame Route"


def test_hostname_wechsel_und_obergrenze(client, monkeypatch):
    tid, fw = _seed(client)
    _deliver(client, tid, fw, "fortigate", FW)
    # HA-Wechsel: die Firewall meldet sich mit dem Hostnamen des anderen Mitglieds
    other = copy.deepcopy(FW)
    for part in ("contexts", "routes", "peers", "ha", "policy_routes"):
        for row in other["routing"][part]:
            row["node"] = "fw-01b"
    _deliver(client, tid, fw, "fortigate", other)
    rows, total = _changes(client, tid)
    assert total == 2 and rows[0]["before"] == {"node": "fw-01"}
    assert rows[0]["summary"].startswith("Gerät heisst jetzt fw-01b (vorher fw-01)")

    # Obergrenze je Lauf: der Rest als ein Sammel-Eintrag
    monkeypatch.setattr(topo_changes, "LIMIT", 3)
    many = copy.deepcopy(other)
    many["routing"]["routes"] += [
        {"node": "fw-01b", "context": "root", "prefix": f"10.40.{i}.0/24", "protocol": "static",
         "next_hops": [{"ip": "10.20.0.254"}]}
        for i in range(10)
    ]  # fmt: skip
    _deliver(client, tid, fw, "fortigate", many)
    rows, total = _changes(client, tid)
    assert total == 2 + 4
    assert [c["change"] for c in rows[:4]] == ["added", "added", "added", "truncated"]
    assert rows[3]["after"] == {"count": 7, "limit": 3, "kinds": {"route": 7},
                                "changes": {"added": 7}}  # fmt: skip
    assert rows[3]["summary"].startswith("7 weitere Änderungen nicht einzeln gespeichert")
    en = _changes(client, tid, headers=EN)[0]
    assert en[3]["summary"].startswith("7 more changes not stored individually (limit 3 per run)")


def test_aufbewahrung_und_loeschen(client, session_factory, monkeypatch):
    tid, fw = _seed(client)
    _deliver(client, tid, fw, "fortigate", FW)
    _deliver(client, tid, fw, "fortigate", _changed_run())
    old = datetime.now(UTC) - timedelta(days=200)
    with session_factory() as s:
        first = s.scalars(select(TopoChange).where(TopoChange.change == "baseline")).one()
        first.at = old
        s.commit()
    st = client.get("/system/retention", headers=ADMIN).json()
    assert st["policy"]["topo_changes_days"] == 180 and st["due"]["topo_changes"] == 1
    body = client.post("/system/retention/purge", headers=ADMIN).json()
    assert body["deleted"]["topo_changes"] == 1 and body["last"]["counts"]["topo_changes"] == 1
    assert _changes(client, tid)[1] == 7
    # Frist 0 = nie
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "retention_topo_changes_days", 0)
    with session_factory() as s:
        s.execute(TopoChange.__table__.update().values(at=old))
        s.commit()
    assert client.get("/system/retention", headers=ADMIN).json()["due"]["topo_changes"] == 0
    # Quellsystem loeschen: sein Verlauf mit
    r = client.delete(f"/systems/{fw}?confirm=fw-01", headers=ADMIN)
    assert r.status_code in (200, 204), r.text
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(TopoChange)) == 0


def test_kunde_oben_nur_sein_ausschnitt(client):
    tid, ids, a, b, _ = cseed(client)
    fw = copy.deepcopy(CFW)
    fw["routing"] = {
        "contexts": [{"node": "fw-k", "context": "root"}, {"node": "fw-k", "context": "kunde-b"}],
        "routes": [
            croute("fw-k", "root", "0.0.0.0/0", [{"ip": "10.255.0.9"}]),  # geaendert
            croute("fw-k", "root", "10.50.0.0/16", [{"ip": "10.255.0.9"}]),  # Kunde A
            croute("fw-k", "root", "10.60.0.0/16", [{"ip": "10.255.0.9"}]),  # Kunde B
            croute("fw-k", "kunde-b", "0.0.0.0/0", [{"ip": "10.255.1.9"}]),
        ],
        "peers": [
            {"node": "fw-k", "context": "root", "peer_ip": "10.255.0.1", "state": "Idle"},
            {"node": "fw-k", "context": "kunde-b", "peer_ip": "10.255.1.1", "state": "Idle"},
        ],
    }
    _deliver(client, tid, ids["fw-k"], "fortigate", fw)
    everything = _changes(client, tid, kind="route")[0]
    assert {(c["context"], c["key"]) for c in everything} == {
        ("root", "0.0.0.0/0"), ("root", "10.50.0.0/16"), ("root", "10.60.0.0/16"),
        ("kunde-b", "0.0.0.0/0"),
    }  # fmt: skip
    seen = _changes(client, tid, customer_id=a)[0]
    keys = {(c["kind"], c["context"], c["key"]) for c in seen}
    # geteiltes root: nur Default und das Netz von Kunde A, der Nachbar (geteilt) - kunde-b nicht
    assert ("route", "root", "0.0.0.0/0") in keys and ("route", "root", "10.50.0.0/16") in keys
    assert ("route", "root", "10.60.0.0/16") not in keys
    assert not any(ctx == "kunde-b" for _, ctx, _ in keys)
    assert ("bgp_peer", "root", "10.255.0.1") in keys and all(c["shared"] for c in seen)
    own = _changes(client, tid, customer_id=b, context="kunde-b")[0]
    assert own and not any(c["shared"] for c in own)


def test_aci_l3out_und_nachbarn_ohne_datenbank():
    """Reiner Vergleich: ACI-Leaf mit L3Out; BGP-Nachbarn nicht lesbar (ohne Familie = alle
    Nachbarn), die Routen werden weiter verglichen; L3Out ueber seine DN, Kontext = seine VRF."""
    ctx = {"node": "leaf-101", "context": "TE:VRF1", "kind": "vrf", "missing": None}
    route = {"node": "leaf-101", "context": "TE:VRF1", "prefix": "0.0.0.0/0", "table": "rib",
             "protocol": "bgp", "distance": 20, "attrs": {"owner": "bgp-65010"},
             "next_hops": [{"ip": "10.255.0.1", "interface": "vlan5"}]}  # fmt: skip
    peer = {"node": "leaf-101", "context": "TE:VRF1", "peer_ip": "10.255.0.1",
            "remote_as": 65010, "state": "established"}  # fmt: skip
    l3out = {"dn": "uni/tn-TE/out-L3O_inet", "vrf": "TE:VRF1", "ext_epgs": ["EN_inet"],
             "peers": [{"ip": "10.255.0.1", "asn": 65010}],
             "nodes": [{"pod": "1", "node": "101"}]}  # fmt: skip
    old = topo_changes.from_rows(
        {TopoContext: [ctx], TopoRoute: [route], TopoPeer: [peer], TopoL3Out: [l3out]}
    )
    new = topo_changes.from_rows({
        TopoContext: [{**ctx, "missing": ["BGP-Nachbarn (HTTP 403)"]}],
        TopoRoute: [{**route, "attrs": {"owner": "bgp-65011"}}],
        TopoL3Out: [{**l3out, "ext_epgs": ["EN_partner", "EN_inet"],
                     "nodes": [{"pod": "1", "node": "102"}, {"pod": "1", "node": "101"}]}],
    })  # fmt: skip
    entries = topo_changes.diff(old, new)
    assert [(e["kind"], e["change"], e["key"]) for e in entries] == [
        ("context", "unreadable", "TE:VRF1"),
        ("l3out", "changed", "uni/tn-TE/out-L3O_inet"),
        ("route", "changed", "0.0.0.0/0"),
    ]
    assert entries[0]["after"] == {"missing": ["BGP-Nachbarn (HTTP 403)"], "not_compared": 1}
    l3 = entries[1]
    assert l3["context"] == "TE:VRF1" and l3["after"]["ext_epgs"] == ["EN_inet", "EN_partner"]
    assert entries[2]["before"]["owner"] == "bgp-65010"
    assert (entries[2]["family"], entries[2]["lo"]) == (4, "0" * 32)
    # Obergrenze direkt: der Rest als ein Sammel-Eintrag
    cut = topo_changes.limit(entries, 1)
    assert [e["change"] for e in cut] == ["unreadable", "truncated"]
    assert cut[1]["after"]["kinds"] == {"l3out": 1, "route": 1}
