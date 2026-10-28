"""Kategorien der Applikations-Doku als Konfiguration (0.104.0, ADR 0012 Ergaenzung 0.104.0,
Anwender 2026-10-28: „die Kategorien-Auswahl anpassen oder erweitern können – im
Namenskonzept“): Vorgaben im Code, Anpassungen im Namensprofil (Kategorien, Bereiche der Zonen,
Optionen); die Doku, das Netzwerkdesign und das Ausblenden folgen dem Katalog des Mandanten.

Umgebung wie in `test_appdoc_zones` (Webshop, fw-04, Zonen Clients/Remote Access/Partner).
"""

from app import audit
from app.models import AuditLog, DictionaryEntry
from app.store import appdoc, appdoc_catalog, naming_profiles
from test_appdoc_names import ADMIN, OP
from test_appdoc_zones import _setup, _where

VIEW = {"X-Dev-Roles": "viewer"}


def _default_profile(session_factory) -> int:
    """Profil „default“ (gilt fuer Mandanten ohne eigenes); ohne Profil gelten die Vorgaben."""
    s = session_factory()
    pid = naming_profiles.seed_default_profile(s).id
    s.commit()
    s.close()
    return pid


def _body(cat: dict, **changes) -> dict:
    """Pflege-Text aus der Ausgabe (wie die Oberflaeche ihn schickt)."""
    ck = ("index", "title", "title_en", "zone_title", "zone_title_en", "layout",
          "only_with_zones")  # fmt: skip
    ak = ("key", "label", "label_en", "inbound", "outbound", "markers", "match", "off")
    cats = [{k: c[k] for k in ck} for c in cat["categories"]]
    areas = [{k: a[k] for k in ak} for a in cat["areas"]]
    return {"categories": cats + changes.get("categories", []),
            "areas": areas + changes.get("areas", []),
            "options": changes.get("options", cat["options"])}  # fmt: skip


def test_vorgaben_wie_bisher():
    cat = appdoc_catalog.default()
    assert [c.index for c in cat.ordered()] == list(range(1, 12))
    assert appdoc.table_title(3) == "Zugriff in das Unternehmensnetzwerk"
    assert cat.zone_title(2, ["Edge Internet"]) == "Zugriff aus Edge Internet"
    assert cat.zone_title(7, ["X"]) == "Zugriff innerhalb des Applikationsprofils"  # ohne Vorlage
    tables = {"inet": (2, 1), "bit": (3, 4), "vpn": (5,), "sra": (6,), "res": (9,), "mgmt": (11,)}
    assert {a.key: a.tables() for a in cat.active_areas()} == tables
    assert cat.only_with_zones() == {11} and cat.fallback == 10
    # Merkmal im Namen der Firewall-Zone und Vorschlag nach dem Zonennamen
    assert [cat.kind_of_zone(z) for z in ("L3O_inet", "L3O_sra_vpn", "lan", None)] == [
        "inet", "sra", None, None]  # fmt: skip
    assert [cat.suggest(n) for n in ("Edge Internet", "Edge Management", "Edge VPN", "RES-DC",
                                     "Edge Office", "DC")] == [
        "inet", "mgmt", "vpn", "res", "bit", None]  # fmt: skip
    # ohne Anpassung wird nichts gespeichert
    assert appdoc_catalog.entries_of(cat) == []


def test_eintraege_im_profil_passen_an_und_erweitern():
    def e(kind, key, value, extra=None):
        return DictionaryEntry(kind=kind, key=key, value=value, extra=extra or {})

    cat = appdoc_catalog.apply_entries(appdoc_catalog.default(), [
        e("appdoc_category", "12", "Zugriff Partner", {"title_en": "Partner access",
                                                       "zone_title": "Zugriff aus {z}"}),
        e("appdoc_category", "7", "Im AP", {"enforcement": "firewall"}),  # Rolle bleibt
        e("appdoc_area", "part", "Partner", {"inbound": 12, "outbound": 12,
                                             "match": ["*partner*"]}),
        e("appdoc_area", "vpn", "VPN", {"off": True}),
        e("appdoc_area", "ghost", "Weg", {"inbound": 42, "outbound": 42}),  # Kategorie fehlt
        e("appdoc_option", "outbound_broad_hidden", "off"),
    ])  # fmt: skip
    assert cat.title(12) == "Zugriff Partner" and cat.categories[12].enforcement == "firewall"
    assert cat.categories[7].enforcement == "aci" and cat.role("aci_ap") == 7
    assert cat.area_tables("part") == (12,) and cat.suggest("Partner Netz") == "part"
    assert cat.area("vpn") is None and cat.kind_of_zone("L3O_vpn") is None
    assert cat.area("ghost") is None  # nicht angeboten
    assert cat.options.outbound_broad_hidden is False
    # eigene Kategorie ueber den Bereich erreicht
    assert cat.categorize(direction="in", counterpart_zone="lan", counterpart_external=False,
                          is_dnat_target=False, area="part") == 12  # fmt: skip
    # gespeichert werden nur die Abweichungen
    kinds = sorted((k, key) for k, key, _v, _x in appdoc_catalog.entries_of(cat))
    assert ("appdoc_category", "12") in kinds and ("appdoc_area", "bit") not in kinds


def test_pflege_im_namenskonzept(client, session_factory):
    tid, app, ids = _setup(client)
    zc = client.get("/zones/appdoc/catalog", params={"tenant_id": tid}).json()
    assert zc["profile_id"] is None and len(zc["areas"]) == 6  # ohne Profil: Vorgaben
    pid = _default_profile(session_factory)
    url = f"/naming-profiles/{pid}/appdoc-catalog"
    cat = client.get(url, headers=VIEW).json()
    assert [c["index"] for c in cat["categories"]] == list(range(1, 12))
    assert cat["default"]["areas"] == cat["areas"]
    assert client.get("/naming-profiles/appdoc-catalog/default", headers=VIEW).status_code == 200

    # eigene Kategorie 12 mit eigenem Bereich; nur Admin
    new = {"categories": [{"index": 12, "title": "Zugriff Partner", "title_en": "Partner access",
                           "zone_title": "Zugriff aus {z}", "zone_title_en": "Access from {z}"}],
           "areas": [{"key": "part", "label": "Partner", "label_en": "Partner", "inbound": 12,
                      "outbound": 12, "match": ["*partner*", "*remote*"]}]}  # fmt: skip
    body = _body(cat, **new)
    assert client.put(url, json=body, headers=OP).status_code == 403
    r = client.put(url, json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert 12 in [c["index"] for c in r.json()["categories"]]

    # Netzwerkdesign: Auswahl und Vorschlag aus dem Katalog des Mandanten
    zc = client.get("/zones/appdoc/catalog", params={"tenant_id": tid}).json()
    assert "part" in [a["key"] for a in zc["areas"]]
    assert zc["suggestions"][str(ids["Partner"])] == "part"
    areas = {ids["Remote Access"]: "part", ids["Clients"]: "bit"}
    r = client.put("/zones/appdoc-areas", json={"tenant_id": tid, "areas": areas}, headers=OP)
    assert r.status_code == 200, r.text

    doc = client.get(f"/applications/{app['id']}/doc").json()
    assert _where(doc)["remote-to-web"] == {12}
    t12 = next(t for t in doc["tables"] if t["index"] == 12)
    assert t12["title"] == "Zugriff aus Remote Access"
    assert [z["name"] for z in t12["zones"]] == ["Remote Access"]
    en = client.get(f"/applications/{app['id']}/doc", headers={"Accept-Language": "en"}).json()
    assert next(t for t in en["tables"] if t["index"] == 12)["title"] == "Access from Remote Access"

    # Ausblenden: Kategorien des Mandanten
    rid = next(r["id"] for r in doc["rules"] if r["name"] == "remote-to-web")
    hide = {"rule_ids": [rid], "category": 12, "reason": "Partnerzugriff separat dokumentiert"}
    hidden = f"/applications/{app['id']}/hidden"
    assert client.post(hidden, json=hide, headers=OP).status_code == 201
    hide["category"] = 50
    assert client.post(hidden, json=hide, headers=OP).status_code == 422

    # Option: ausgehende Regeln mit breiter Quelle zeigen
    assert doc["summary"]["outbound_broad"] == 2
    body = _body(client.get(url, headers=VIEW).json(), options={
        "outbound_broad_hidden": False, "fallback_inbound_only": True})  # fmt: skip
    assert client.put(url, json=body, headers=ADMIN).status_code == 200
    assert client.get(f"/applications/{app['id']}/doc").json()["summary"]["outbound_broad"] == 0

    # zuruecksetzen: Bereich der Zone gilt nicht mehr, Auswahl ohne „part“
    assert client.delete(url, headers=OP).status_code == 403
    r = client.delete(url, headers=ADMIN)
    assert r.status_code == 200 and 12 not in [c["index"] for c in r.json()["categories"]]
    doc = client.get(f"/applications/{app['id']}/doc").json()
    assert _where(doc)["remote-to-web"] == {10}
    assert 12 not in [t["index"] for t in doc["tables"]]
    bad = {"tenant_id": tid, "areas": {ids["Partner"]: "part"}}
    assert client.put("/zones/appdoc-areas", json=bad, headers=OP).status_code == 422

    s = session_factory()
    actions = [(r.action, audit.decode_detail(r)) for r in s.query(AuditLog)
               if r.action.startswith("appdoc_catalog.")]  # fmt: skip
    s.close()
    assert [a for a, _d in actions] == ["appdoc_catalog.saved", "appdoc_catalog.saved",
                                        "appdoc_catalog.reset"]  # fmt: skip
    assert actions[0][1]["categories"] == 12 and actions[0][1]["stored"] == 2


def test_ungueltige_kataloge(client, session_factory):
    pid = _default_profile(session_factory)
    url = f"/naming-profiles/{pid}/appdoc-catalog"
    cat = client.get(url, headers=VIEW).json()

    def put(**changes):
        return client.put(url, json=_body(cat, **changes), headers=ADMIN).status_code

    area = {"key": "part", "label": "Partner", "inbound": 3, "outbound": 4}
    assert put(areas=[{**area, "inbound": 7}]) == 422  # ACI intern
    assert put(areas=[{**area, "inbound": 13}]) == 422  # Kategorie fehlt
    assert put(areas=[{**area, "key": "Lan!"}]) == 422
    assert put(areas=[area, area]) == 422  # doppelt
    assert put(categories=[{"index": 0, "title": "x"}]) == 422
    assert put(categories=[{"index": 12, "title": "  "}]) == 422
    assert put(categories=[{"index": 12, "title": "x", "layout": "wide"}]) == 422
    assert put(options={"nope": True}) == 422
    assert client.get("/naming-profiles/999999/appdoc-catalog", headers=VIEW).status_code == 404
    # eingebaute fehlen nie: ohne sie im Text gilt die Vorgabe
    r = client.put(url, json={"categories": [], "areas": [], "options": {}}, headers=ADMIN)
    assert r.status_code == 200 and len(r.json()["categories"]) == 11
