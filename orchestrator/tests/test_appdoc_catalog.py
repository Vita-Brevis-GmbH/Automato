"""Kategorien der Applikations-Doku als Konfiguration (0.104.0, seit 0.106.0 einfach, ADR 0035,
Anwender 2026-10-28: „Titel, Titel English, Source Zone, Dest Zone“): Vorgaben im Code als
Richtungen auf der Vorlage des Grundschemas, Anpassungen im Namensprofil; drei feste Kategorien
(innerhalb der Applikation, innerhalb des Security Tenants, unspezifisch) mit nur dem Titel.

Umgebung wie in `test_appdoc_zones` (Webshop, fw-04, Zonen Clients/Remote Access/Partner).
"""

import pytest
from app import audit
from app.models import AuditLog, DictionaryEntry
from app.store import appdoc, appdoc_catalog, naming_profiles
from test_appdoc_names import ADMIN, OP
from test_appdoc_zones import _setup, _types, _where

VIEW = {"X-Dev-Roles": "viewer"}
KEYS = ("index", "title", "title_en", "src", "dst")


def _default_profile(session_factory) -> int:
    """Profil „default“ (gilt fuer Mandanten ohne eigenes); ohne Profil gelten die Vorgaben."""
    s = session_factory()
    pid = naming_profiles.seed_default_profile(s).id
    s.commit()
    s.close()
    return pid


def _body(cat: dict, drop: tuple[int, ...] = (), add: list[dict] | None = None) -> dict:
    """Pflege-Text aus der Ausgabe (wie die Oberflaeche ihn schickt)."""
    cats = [{k: c[k] for k in KEYS} for c in cat["categories"] if c["index"] not in drop]
    return {"categories": cats + (add or [])}


def _e(key: str, value: str, extra: dict) -> DictionaryEntry:
    return DictionaryEntry(kind="appdoc_category", key=key, value=value, extra=extra)


def test_vorgaben():
    cat = appdoc_catalog.default()
    assert [c.index for c in cat.ordered()] == list(range(1, 12))
    assert appdoc.table_title(3) == "Zugriff in das Unternehmensnetzwerk"
    assert (cat.role("aci_ap"), cat.role("aci_tenant"), cat.fallback) == (7, 8, 10)
    assert cat.get(2).out() == {
        "index": 2, "title": "Zugriff aus dem Internet", "title_en": "Access from the Internet",
        "src": ["Edge Internet", "Public"], "dst": ["DC"], "role": "", "enforcement": "firewall",
        "builtin": True}  # fmt: skip
    assert cat.get(7).src == () and cat.get(7).enforcement == "aci"
    assert cat.by_flow("public", "dc") == 2  # normalisiert wie das Grundschema
    assert appdoc_catalog.entries_of(cat) == []  # ohne Anpassung nichts gespeichert


def test_eintraege_im_profil_passen_an_und_erweitern():
    cat = appdoc_catalog.apply_entries(appdoc_catalog.default(), [
        _e("12", "Zugriff Partner", {"title_en": "Partner access", "src": ["Public"],
                                     "dst": ["*"]}),
        _e("7", "Server intern", {"src": ["DC"], "dst": ["DC"]}),  # feste Rolle: nur der Titel
        _e("5", "Zugriff aus dem VPN", {"off": True}),  # entfernte Vorgabe
        _e("8", "x", {"off": True}),  # feste Kategorien bleiben
        _e("13", "Office", {"flows": ["Edge Office → DC", ["Office Access", "DC"]]}),  # 0.105.0
        _e("3", "Zugriff Office", {"title_en": "Office access", "zone_title": "Zugriff in {z}"}),
        DictionaryEntry(kind="appdoc_area", key="part", value="Partner", extra={}),  # 0.104.0
    ])  # fmt: skip
    assert cat.title(12) == "Zugriff Partner" and cat.get(12).src == ("Public",)
    assert cat.get(7).title == "Server intern" and cat.get(7).src == () and cat.role("aci_ap") == 7
    assert 5 not in cat.categories and 8 in cat.categories
    assert (cat.get(13).src, cat.get(13).dst) == (("Edge Office", "Office Access"), ("DC",))
    assert cat.get(3).dst == appdoc_catalog.default().get(3).dst  # Richtung der Vorgabe bleibt
    # genaueste Richtung: beide genannt vor `*`, bei Gleichstand die kleinere Nummer
    assert cat.by_flow("Public", "DC") == 2 and cat.by_flow("Public", "Core") == 12
    assert cat.by_flow("Edge VPN", "DC") is None  # 5 entfernt
    assert cat.by_flow("Edge Office", "DC") == 4  # 4 vor 13
    stored = {key: x for _kind, key, _v, x in appdoc_catalog.entries_of(cat)}
    assert stored["5"] == {"off": True} and stored["12"]["dst"] == ["*"]
    assert "8" not in stored and stored["7"] == {"title_en": cat.get(7).title_en, "src": [],
                                                 "dst": []}  # fmt: skip


def test_pruefen():
    assert appdoc_catalog.types_of("Edge Internet, Public, Edge Internet") == (
        "Edge Internet", "Public")  # fmt: skip
    assert appdoc_catalog.types_of(["DC", "*"]) == ("*",)
    cats = [{"index": 12, "title": "Partner", "src": ["Public"], "dst": ["DC"]},
            {"index": 7, "title": "Server intern", "src": ["DC"], "dst": ["DC"]}]  # fmt: skip
    cat = appdoc_catalog.validate({"categories": cats})
    # fehlende Vorgaben fallen weg, die festen bleiben (Titel geaendert, ohne Richtung)
    assert sorted(cat.categories) == [7, 8, 10, 12]
    assert cat.get(7).title == "Server intern" and cat.get(7).src == ()
    for bad in ({"index": 0, "title": "x", "src": ["DC"], "dst": ["DC"]},
                {"index": 12, "title": "  ", "src": ["DC"], "dst": ["DC"]},
                {"index": 12, "title": "x", "src": [], "dst": ["DC"]}):  # fmt: skip
        with pytest.raises(appdoc_catalog.Invalid):
            appdoc_catalog.validate({"categories": [bad]})
    with pytest.raises(appdoc_catalog.Invalid):
        appdoc_catalog.validate({"categories": [cats[0], cats[0]]})


def test_pflege_im_namenskonzept(client, session_factory):
    tid, app, ids = _setup(client)
    zc = client.get("/zones/appdoc/catalog", params={"tenant_id": tid}).json()
    assert zc["profile_id"] is None and "areas" not in zc  # ohne Profil: Vorgaben
    pid = _default_profile(session_factory)
    url = f"/naming-profiles/{pid}/appdoc-catalog"
    cat = client.get(url, headers=VIEW).json()
    assert [c["index"] for c in cat["categories"]] == list(range(1, 12))
    assert cat["default"]["categories"] == cat["categories"]
    assert "Edge VPN" in cat["zone_types"]  # Vorlage des Grundschemas (ohne eigenes)
    assert client.get("/naming-profiles/appdoc-catalog/default", headers=VIEW).status_code == 200

    # eigene Kategorie 12 aus dem VPN, Vorgabe 5 entfernt; nur Admin
    new = {"index": 12, "title": "Zugriff Remote Access", "title_en": "Remote access",
           "src": ["Edge VPN"], "dst": ["DC"]}  # fmt: skip
    body = _body(cat, drop=(5,), add=[new])
    assert client.put(url, json=body, headers=OP).status_code == 403
    r = client.put(url, json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert [c["index"] for c in r.json()["categories"]] == [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12]

    _types(client, tid, {ids["Remote Access"]: "Edge VPN"})
    doc = client.get(f"/applications/{app['id']}/doc").json()
    assert _where(doc)["remote-to-web"] == {12}
    t12 = next(t for t in doc["tables"] if t["index"] == 12)
    assert t12["title"] == "Zugriff Remote Access"
    assert [z["name"] for z in t12["zones"]] == ["Remote Access"]
    en = client.get(f"/applications/{app['id']}/doc", headers={"Accept-Language": "en"}).json()
    assert next(t for t in en["tables"] if t["index"] == 12)["title"] == "Remote access"

    # Ausblenden: Kategorien des Mandanten
    rid = next(r["id"] for r in doc["rules"] if r["name"] == "remote-to-web")
    hide = {"rule_ids": [rid], "category": 12, "reason": "Remote Access separat dokumentiert"}
    hidden = f"/applications/{app['id']}/hidden"
    assert client.post(hidden, json=hide, headers=OP).status_code == 201
    hide["category"] = 50
    assert client.post(hidden, json=hide, headers=OP).status_code == 422

    # zuruecksetzen: wieder die Vorgaben
    assert client.delete(url, headers=OP).status_code == 403
    r = client.delete(url, headers=ADMIN)
    assert r.status_code == 200 and 12 not in [c["index"] for c in r.json()["categories"]]
    doc = client.get(f"/applications/{app['id']}/doc").json()
    assert _where(doc)["remote-to-web"] == {5}
    assert 12 not in [t["index"] for t in doc["tables"]]

    s = session_factory()
    actions = [(r.action, audit.decode_detail(r)) for r in s.query(AuditLog)
               if r.action.startswith("appdoc_catalog.")]  # fmt: skip
    s.close()
    assert [a for a, _d in actions] == ["appdoc_catalog.saved", "appdoc_catalog.reset"]
    assert actions[0][1] == {"profile_id": pid, "categories": 11, "stored": 2}


def test_ungueltige_kataloge(client, session_factory):
    pid = _default_profile(session_factory)
    url = f"/naming-profiles/{pid}/appdoc-catalog"
    cat = client.get(url, headers=VIEW).json()

    def put(*add):
        return client.put(url, json=_body(cat, add=list(add)), headers=ADMIN).status_code

    assert put({"index": 0, "title": "x", "src": ["DC"], "dst": ["DC"]}) == 422
    assert put({"index": 12, "title": "  ", "src": ["DC"], "dst": ["DC"]}) == 422
    assert put({"index": 12, "title": "x", "src": ["DC"]}) == 422  # ohne Ziel
    assert put({"index": 3, "title": "doppelt", "src": ["DC"], "dst": ["DC"]}) == 422
    assert client.get("/naming-profiles/999999/appdoc-catalog", headers=VIEW).status_code == 404
    # die festen fehlen nie: ohne sie im Text gelten sie trotzdem
    r = client.put(url, json={"categories": []}, headers=ADMIN)
    assert r.status_code == 200 and [c["index"] for c in r.json()["categories"]] == [7, 8, 10]
