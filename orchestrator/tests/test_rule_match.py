# ruff: noqa: S104 - 0.0.0.0/0 sind Routen, keine Bind-Adressen
"""Regel-Zuordnung nach echtem Treffer (ADR 0071, `store/rule_match`).

Ergänzt die Prüffälle aus `test_fable_regeln.py` (B-4 bis B-7, B-12): wirksamer Rest mit
mehreren früheren Policies, was nie überdeckt, Kennzeichen „breit“ und „Weg nicht geprüft“,
Befund „Regel überdeckt“, Fassung im Fingerabdruck. Eine Firewall `fw-r` wie dort.
"""

import test_fable_regeln as fr
import test_policy_check as pc
from app.models import Proto
from app.store import rule_match


def _hits(doc: dict) -> dict[str, list[dict]]:
    names = {r["id"]: r["name"] for r in doc["rules"]}
    out: dict[str, list[dict]] = {}
    for h in doc["hits"]:
        out.setdefault(names.get(h["rule_id"]), []).append(h)
    return out


def _in_tables(doc: dict) -> set[str]:
    return {r.get("Description") for t in doc["tables"] for r in t["rows"]}


# -- wirksamer Rest: reine Rechnung -----------------------------------------------------------


def test_dienste_mehrerer_frueherer_policies_decken_zusammen():
    """tcp/1-100 und tcp/101-200 decken tcp/50-150 zusammen; eine Lücke bei 101 deckt nicht."""
    need = (False, {"tcp": [(50, 150)]})
    a, b = (False, {"tcp": [(1, 100)]}), (False, {"tcp": [(101, 200)]})
    assert rule_match._covers_services(need, [a, b])
    assert not rule_match._covers_services(need, [a, (False, {"tcp": [(102, 200)]})])
    assert not rule_match._covers_services(need, [(False, {"udp": [(1, 65535)]})])
    assert rule_match._covers_services(need, [(True, {})])  # ALL deckt alles
    assert not rule_match._covers_services((True, {}), [a, b])  # ALL deckt nur ALL
    assert not rule_match._covers_services(None, [a])  # unbekannte Dienste: nur ALL


def test_menge_enthaelt_menge():
    net = [(10, 20), (30, 40)]
    assert rule_match._inside(net, [(12, 18), (30, 40)])
    assert not rule_match._inside(net, [(18, 32)])  # über die Lücke
    assert rule_match._inside(rule_match.ANY, net) and not rule_match._inside(net, rule_match.ANY)


def test_interfaces_nach_namen():
    assert rule_match._ports_cover(["any"], ["lan"])
    assert rule_match._ports_cover(["lan", "dmz"], ["lan"])
    assert not rule_match._ports_cover(["lan"], ["any"])
    assert not rule_match._ports_cover(["lan"], ["lan", "dmz"])


# -- Doku: überdeckt, breit, nie auf Vermutung ---------------------------------------------------


def test_zwei_fruehere_policies_ueberdecken_zusammen(client):
    """Zwei frühere Policies mit je einem Teil der Ports überdecken die dritte zusammen."""
    tid = fr._seed(client, [
        pc._policy(1, "lan-low", "lan", "dmz", ["10.10.0.0/24"], ["10.30.0.0/24"],
                   ["tcp/1-1024"], "deny"),
        pc._policy(2, "lan-high", "lan", "dmz", ["10.10.0.0/16"], ["10.30.0.10"],
                   ["tcp/1025-9000"], "deny"),
        pc._policy(3, "lan-to-web", "lan", "dmz", ["10.10.0.0/24"], ["10.30.0.10"],
                   ["tcp/443", "tcp/8443"]),
    ])  # fmt: skip
    doc = fr._doc(client, tid, "10.30.0.10")
    (hit,) = _hits(doc)["lan-to-web"]
    assert hit["_shadowed"] == "überdeckt durch Policy 1 „lan-low“, Policy 2 „lan-high“"
    assert "lan-to-web" not in _in_tables(doc)  # kein Zugriff mehr - nur noch Treffer
    assert doc["summary"]["shadowed"] == 1


def test_nicht_pruefbare_fruehere_policy_ueberdeckt_nie(client):
    """Zeitplan, Benutzer, abgeschaltet, FQDN in der Quelle, anderer Dienst bzw. anderes
    Interface davor: die Regel dahinter kann Verkehr treffen - nie als überdeckt ausgewiesen."""
    before = [
        pc._policy(1, "nachts", "lan", "dmz", ["all"], ["all"], ["all"], "deny", schedule="night"),
        pc._policy(2, "benutzer", "lan", "dmz", ["all"], ["all"], ["all"], "deny",
                   groups=[{"name": "GRP_admin"}]),
        pc._policy(3, "aus", "lan", "dmz", ["all"], ["all"], ["all"], "deny", status="disable"),
        pc._policy(4, "fqdn", "lan", "dmz", ["fqdn:client.example.ch"], ["all"], ["all"], "deny"),
        pc._policy(5, "udp", "lan", "dmz", ["all"], ["all"], ["udp/1-65535"], "deny"),
        pc._policy(6, "wan", "internet", "dmz", ["all"], ["all"], ["all"], "deny"),
        pc._policy(7, "teil", "lan", "dmz", ["10.10.0.0/25"], ["all"], ["all"], "deny"),
    ]  # fmt: skip
    tid = fr._seed(client, [
        *before,
        pc._policy(8, "lan-to-web", "lan", "dmz", ["10.10.0.0/24"], ["10.30.0.10"], ["tcp/443"]),
    ])  # fmt: skip
    doc = fr._doc(client, tid, "10.30.0.10")
    assert not any(h["_shadowed"] for h in _hits(doc)["lan-to-web"])
    assert "lan-to-web" in _in_tables(doc)
    assert doc["summary"]["shadowed"] == 0


def test_breite_regel_die_real_trifft_ist_gezeigt_und_gekennzeichnet(client):
    """Policy 1 (ganzes LAN -> ganze DMZ) trifft real, Policy 2 nennt den Server und wird nie
    erreicht. Gezeigt wird die real treffende Policy an ihrer Stelle (Kategorie, Gegenstelle und
    Dienst des gemeinten Zugriffs), als „breit“ gekennzeichnet; die überdeckte Regel ist nur noch
    Treffer. Die breite Policy selbst bleibt, was sie war (unspezifischer Zugriff)."""
    tid = fr._seed(client, [
        pc._policy(1, "lan-dmz", "lan", "dmz", ["10.10.0.0/16"], ["10.30.0.0/24"], ["all"]),
        pc._policy(2, "admin-to-web", "lan", "dmz", ["10.10.0.7"], ["10.30.0.10"], ["tcp/22"]),
    ])  # fmt: skip
    doc = fr._doc(client, tid, "10.30.0.10")
    hits = _hits(doc)
    assert hits["admin-to-web"][0]["_shadowed"] == "überdeckt durch Policy 1 „lan-dmz“"
    instead = [h for h in hits["lan-dmz"] if h["_broad"]]
    assert [(h["_broad"], h["counterpart"], h["services"]) for h in instead] == [
        ("breit – trifft anstelle von admin-to-web", "10.10.0.7", "tcp/22")]  # fmt: skip
    assert "lan-dmz" in _in_tables(doc) and "admin-to-web" not in _in_tables(doc)
    rows = [r for t in doc["tables"] for r in t["rows"] if r.get("_broad")]
    assert [r["Aktion"] for r in rows] == ["Allow (breit)"]  # auch im Export sichtbar
    assert (doc["summary"]["broad_hits"], doc["summary"]["shadowed"]) == (1, 1)


def test_negierte_seite_ist_das_komplement(client):
    """`Matcher.covers`: eine negierte Seite trifft, was sie nicht nennt - mit Bezug „nicht …“."""
    tid = fr._seed(client, [
        pc._policy(1, "nicht-mgmt", "lan", "dmz", ["10.10.0.5"], ["10.30.0.128/25"], ["tcp/22"],
                   **{"dstaddr-negate": "enable"}),
        pc._policy(2, "lan-to-web", "lan", "dmz", ["10.10.0.0/24"], ["10.30.0.10"], ["tcp/443"]),
    ])  # fmt: skip
    doc = fr._doc(client, tid, "10.30.0.10")
    (hit,) = _hits(doc)["nicht-mgmt"]
    assert (hit["scope"], hit["via"]) == ("subnet", "nicht 10.30.0.128/25")


# -- Weg als harte Bedingung --------------------------------------------------------------------


def test_weg_unbekannt_bleibt_markiert_und_gezaehlt(client):
    """Die Gegenstelle hängt an keinem gelesenen Gerät und die Firewall hat keine Route zu ihr:
    der Weg ist nicht bekannt - die Regel bleibt, „Weg nicht geprüft“, gezählt."""
    routes = [r for r in fr.ROUTES if r["prefix"] != "0.0.0.0/0"]
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=pc.ADMIN).json()["id"]
    body = {"tenant_id": tid, "name": "fw-r", "platform": "fortigate", "base_url": "https://x"}
    sid = client.post("/systems", json=body, headers=pc.ADMIN).json()["id"]
    run = {"platform": "fortigate", "tenant_id": tid, "source_system_id": sid}
    run_id = client.post("/jobs/collect", json=run).json()["run_id"]
    rules = [pc._policy(1, "partner-to-web", "any", "any", ["172.20.5.5"], ["10.30.0.10"],
                        ["tcp/443"])]  # fmt: skip
    result = pc._fw("fw-r", rules, fr.IFACES, routes)
    r = client.post(f"/internal/collector-runs/{run_id}/result", headers=pc.TOKEN, json=result)
    assert r.status_code == 200, r.text
    doc = fr._doc(client, tid, "10.30.0.10")
    (hit,) = _hits(doc)["partner-to-web"]
    assert hit["_path_unchecked"] == "Weg nicht geprüft"
    assert doc["summary"]["path_unchecked"] == 1
    assert "partner-to-web" in _in_tables(doc)


def test_regel_abseits_des_wegs_ist_gezaehlt_nicht_gezeigt(client):
    """B-12 mit dem Zähler: die Regel von `fw-vpn` steht nicht in der Doku, aber unter den
    gezählten („nicht auf dem Weg“, konkrete Regel) und für die Verbindungen in `off_path`."""
    tid, ids = fr._seed_verbund(client)
    doc = fr._doc(client, tid, "10.30.0.10")
    counted = {f["key"]: f["count"] for f in doc["summary"]["filtered"]}
    assert counted == {"off_path_specific": 1}
    by_id = {r["id"]: r for r in doc["rules"]}
    assert all(by_id[h["rule_id"]]["source_system_id"] != ids["fw-vpn"] for h in doc["hits"])
    assert len(doc["off_path"]) == 1 and doc["off_path"][0]["relevance"] == "impossible"


# -- Befund „Regel überdeckt“ --------------------------------------------------------------------


def test_befund_regel_ueberdeckt(client):
    """Ganz überdeckte Policy als Befund: Warnung, wenn die frühere anders entscheidet; eine
    Policy, die nur teilweise überdeckt ist, ist kein Befund."""
    tid = fr._seed(client, [
        pc._policy(1, "lan-to-dmz-deny", "lan", "dmz", ["all"], ["all"], ["all"], "deny"),
        pc._policy(2, "lan-to-web", "lan", "dmz", ["10.10.0.0/24"], ["10.30.0.10"], ["tcp/443"]),
        pc._policy(3, "any-to-web", "any", "dmz", ["all"], ["10.30.0.10"], ["tcp/443"]),
    ])  # fmt: skip
    assert client.post("/reconcile", params={"tenant_id": tid}, headers=fr.OP).status_code == 200
    found = client.get("/findings", params={"tenant_id": tid, "type": "rule_shadowed"}).json()
    assert [(f["detail"]["name"], f["severity"]) for f in found] == [("lan-to-web", "warning")]
    (f,) = found
    assert [b["name"] for b in f["detail"]["by"]] == ["lan-to-dmz-deny"]
    assert "überdeckt durch Policy 1 „lan-to-dmz-deny“" in f["message"]


def test_dienst_objekt_all_protokolle():
    """`_services`: `all` deckt alles, ICMP als eigenes Protokoll, Unbekanntes nicht als Menge."""

    class P:
        service_negate = False
        services = [(Proto.TCP, 443, 443, "tcp/443"), (Proto.ICMP, None, None, "icmp")]

    assert rule_match._services(P) == (False, {"tcp": [(443, 443)], "icmp": [(0, 65535)]})
    P.services = [(Proto.IP, None, None, "all")]
    assert rule_match._services(P)[0] is True
    P.services = [(Proto.IP, None, None, "gre")]
    assert rule_match._services(P) is None
