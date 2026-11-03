"""Firewall-Regeln mit Gegenstellen in mehreren Zonen (0.125.0, ADR 0046, Anwender 2026-11-03:
„lieber aufgeteilt, nicht dass wir die Ziele im MPLS auch auf dem anderen Tenant sehen“): je Zone
bzw. Kategorie eine Zeile mit nur ihren Gegenstellen; Zonentypen mit Zusatz „(Applikation)“ bzw.
„(andere)“; je Kategorie und Seite die Auswahl spezifisch/unspezifisch - abgewaehlte Zeilen nur
als Treffer unter „Regeln & Contracts“.

Umgebung wie in `test_appdoc_zones` (Webshop mit web01 10.20.0.5, fw-04, Zonen Clients = Office
Access, Remote Access = Edge VPN, Partner = DC).
"""

import pytest
from app.models import Rule
from app.store import appdoc_catalog
from app.store.applications import rule_ref
from test_appdoc_catalog import VIEW, _default_profile
from test_appdoc_names import ADMIN, OP, _env
from test_appdoc_zones import FW4, ZONES, _rule, _types, _where
from test_vmware import _deliver, _system

SPLIT = {
    **FW4,
    "rules": [
        *FW4["rules"],
        # eingehend aus zwei Zonen: vorher eine Zeile in „unspezifisch“ (mehrere Zonentypen)
        _rule(61, "many-to-web", ["10.70.1.1", "10.77.1.1"], ["10.20.0.5"]),
        # ausgehend in drei Zonen: Clients, Remote Access, Partner (anderes DC)
        _rule(62, "web-to-many", ["10.20.0.5"], ["10.71.1.10", "10.77.5.5", "10.78.1.1"]),
        # eingehend aus dem Partner-DC und einem Netz ueber alle Zonen (unspezifisch)
        _rule(63, "partner-to-web", ["10.78.2.2", "10.64.0.0/10"], ["10.20.0.5"]),
    ],
}


def _setup(client) -> tuple[int, dict, dict[str, int]]:
    tid, app = _env(client)
    fw4 = _system(client, tid, "fw-04", "fortigate", base_url="https://fw4")
    _deliver(client, tid, fw4, "fortigate", SPLIT)
    ids = {}
    for name, nets in ZONES.items():
        body = {"tenant_id": tid, "name": name, "kind": "access",
                "selectors": [{"kind": "prefix", "value": n} for n in nets]}  # fmt: skip
        r = client.post("/zones", json=body, headers=OP)
        assert r.status_code == 201, r.text
        ids[name] = r.json()["id"]
    types = {ids["Clients"]: "Office Access", ids["Remote Access"]: "Edge VPN",
             ids["Partner"]: "DC"}  # fmt: skip
    assert _types(client, tid, types).status_code == 200
    return tid, app, ids


def _rows(doc: dict, name: str) -> dict[int, dict]:
    """Zeilen einer Regel je Kategorie."""
    return {t["index"]: r for t in doc["tables"] for r in t["rows"] if r["Description"] == name}


def _hits(doc: dict, name: str) -> list[dict]:
    rid = next(r["id"] for r in doc["rules"] if r["name"] == name)
    return [h for h in doc["hits"] if h["rule_id"] == rid]


def test_zusatz_und_auswahl_im_katalog():
    cat = appdoc_catalog.apply_entries(appdoc_catalog.default(), [])
    assert appdoc_catalog.split_type("DC (Applikation)") == ("DC", "app")
    assert appdoc_catalog.split_type("DC (other)") == ("DC", "other")
    assert appdoc_catalog.split_type("Edge VPN") == ("Edge VPN", None)
    assert appdoc_catalog.split_type("DC (Test)") == ("DC (Test)", None)
    # ohne Zusatz: wie bisher; mit Zusatz genauer als ohne, aber nur mit bekannter Seite
    cat.categories[12] = appdoc_catalog.Category(
        12, "Andere DC", "Other DC", ("DC (Applikation)",), ("DC (andere)",)
    )
    assert cat.by_flow("DC", "DC") == 9
    assert cat.by_flow("DC", "DC", app_side="src") == 12  # Applikation → anderes DC
    assert cat.by_flow("DC", "DC", app_side="dst") == 9  # anderes DC → Applikation: 12 passt nicht
    assert appdoc_catalog.scopes_of(None) == appdoc_catalog.SCOPES
    assert appdoc_catalog.scopes_of(["unspecific", "x"]) == ("unspecific",)
    assert appdoc_catalog.scopes_of([]) == ()
    # gespeichert nur abweichend; leere Auswahl abgelehnt
    item = {"index": 12, "title": "Andere DC", "src": ["DC (Applikation)"],
            "dst": ["DC (andere)"], "dst_scope": ["specific"]}  # fmt: skip
    checked = appdoc_catalog.validate({"categories": [item]})
    assert checked.get(12).dst_scope == ("specific",)
    assert checked.get(12).src_scope == appdoc_catalog.SCOPES
    stored = {k: x for _kind, k, _v, x in appdoc_catalog.entries_of(checked)}
    assert stored["12"]["dst_scope"] == ["specific"] and "src_scope" not in stored["12"]
    back = appdoc_catalog.apply_entries(appdoc_catalog.default(), [
        appdoc_catalog.DictionaryEntry(kind="appdoc_category", key="12", value="Andere DC",
                                       extra=stored["12"])])  # fmt: skip
    assert back.get(12) == checked.get(12)
    # feste Rolle: Auswahl aenderbar, Richtung nicht
    fixed = appdoc_catalog.validate({"categories": [
        {"index": 10, "title": "Unspezifisch", "src": ["DC"], "dst": ["DC"],
         "src_scope": ["unspecific"]}]})  # fmt: skip
    assert fixed.get(10).src == () and fixed.get(10).src_scope == ("unspecific",)
    with pytest.raises(appdoc_catalog.Invalid):
        appdoc_catalog.validate({"categories": [{**item, "src_scope": []}]})


def test_regel_je_zone_aufgeteilt(client):
    tid, app, ids = _setup(client)
    doc = client.get(f"/applications/{app['id']}/doc").json()
    where = _where(doc)
    # eingehend aus Clients (Office Access → DC) und Remote Access (Edge VPN → DC)
    assert where["many-to-web"] == {4, 5}
    rows = _rows(doc, "many-to-web")
    assert (rows[4]["Source"], rows[4]["Zone"]) == ("10.70.1.1", "Clients")
    assert (rows[5]["Source"], rows[5]["Zone"]) == ("10.77.1.1", "Remote Access")
    # ausgehend: je Zone nur ihre Ziele - das andere DC (Partner) in 9, nicht beim VPN
    assert where["web-to-many"] == {3, 6, 9}
    rows = _rows(doc, "web-to-many")
    assert {i: r["Destination"] for i, r in rows.items()} == {
        3: "10.71.1.10", 6: "10.77.5.5", 9: "10.78.1.1"}  # fmt: skip
    # Treffer je Kategorie (Regeln & Contracts: eine Regel, Kategorien zusammen)
    hits = _hits(doc, "web-to-many")  # je System (VM und ihre EPG) je Kategorie ein Treffer
    assert sorted(h["table"] for h in hits if h["system"] == hits[0]["system"]) == [3, 6, 9]
    assert {h["counterpart"] for h in hits if h["table"] == 6} == {"10.77.5.5"}
    assert [r["name"] for r in doc["rules"]].count("web-to-many") == 1
    # Netz ueber mehrere Zonen bleibt unspezifisch, der Host im Partner-DC in 9
    assert where["partner-to-web"] == {9, 10}
    assert _rows(doc, "partner-to-web")[9]["Source"] == "10.78.2.2"


def test_dc_der_applikation_und_andere(client, session_factory):
    tid, app, ids = _setup(client)
    pid = _default_profile(session_factory)
    url = f"/naming-profiles/{pid}/appdoc-catalog"
    cat = client.get(url, headers=VIEW).json()
    new = {"index": 12, "title": "Zugriff auf andere DC", "title_en": "Access to other DC",
           "src": ["DC (Applikation)"], "dst": ["DC (andere)"]}  # fmt: skip
    body = {"categories": [*cat["categories"], new]}
    r = client.put(url, json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    doc = client.get(f"/applications/{app['id']}/doc").json()
    where = _where(doc)
    assert where["web-to-many"] == {3, 6, 12}  # Applikation → anderes DC
    assert where["partner-to-web"] == {9, 10}  # anderes DC → Applikation: weiter 9


def test_auswahl_spezifisch_unspezifisch(client, session_factory):
    tid, app, ids = _setup(client)
    pid = _default_profile(session_factory)
    url = f"/naming-profiles/{pid}/appdoc-catalog"
    cat = client.get(url, headers=VIEW).json()
    assert cat["categories"][0]["src_scope"] == ["specific", "unspecific"]
    cats = []
    for c in cat["categories"]:
        if c["index"] == 6:  # in das VPN: nur unspezifische Ziele
            c = {**c, "dst_scope": ["unspecific"]}
        if c["index"] == 10:  # feste Rolle: nur spezifische Quellen
            c = {**c, "src_scope": ["specific"]}
        cats.append(c)
    bad = [{**c, "dst_scope": []} if c["index"] == 6 else c for c in cats]
    assert client.put(url, json={"categories": bad}, headers=ADMIN).status_code == 422
    r = client.put(url, json={"categories": cats}, headers=ADMIN)
    assert r.status_code == 200, r.text
    saved = {c["index"]: c for c in r.json()["categories"]}
    assert saved[6]["dst_scope"] == ["unspecific"] and saved[10]["src_scope"] == ["specific"]

    doc = client.get(f"/applications/{app['id']}/doc").json()
    where = _where(doc)
    # die spezifischen Ziele im VPN fehlen in 6, bleiben als Treffer der Kategorie 6
    assert where["web-to-many"] == {3, 9}
    hidden = {h["table"]: h["category_hidden"] for h in _hits(doc, "web-to-many")}
    assert hidden == {3: False, 6: True, 9: False}
    assert "web-to-many" in {r["name"] for r in doc["rules"]}
    # Netz ueber mehrere Zonen (unspezifisch) nicht mehr in 10, „wide-to-web“ ebenso
    assert where["partner-to-web"] == {9}
    assert "wide-to-web" not in where
    # direkt zugeordnet: immer in der Kategorie
    rid = next(r["id"] for r in doc["rules"] if r["name"] == "wide-to-web")
    s = session_factory()
    ref = rule_ref(s.get(Rule, rid))
    s.close()
    body = {"members": [{"kind": "rule", "ref": ref}]}
    assert client.post(f"/applications/{app['id']}/members", json=body,
                       headers=OP).status_code == 200  # fmt: skip
    assert _where(client.get(f"/applications/{app['id']}/doc").json())["wide-to-web"] == {10}
