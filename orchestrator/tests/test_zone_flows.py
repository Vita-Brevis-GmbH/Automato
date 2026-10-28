"""Zonentypen, Richtungen und Ausgaenge (0.105.0, ADR 0035, Anwender 2026-10-28):

- Zonentyp = Zone des Grundschemas (hier die eingebaute Vorlage): erkannt nach dem Namen
  („DC Web“ → DC) bzw. von Hand (Netzwerkdesign → Kategorien);
- Kategorien mit Richtungen Zonentyp → Zonentyp im Namenskonzept; zuerst die Sonderfaelle
  „innerhalb der Applikation“ (7) und „innerhalb der eigenen Zone“ (8);
- Ausgaenge: eine Gegenstelle ueber die Firewall-Zone bzw. das Interface der Regel gehoert zur
  Zone mit diesem Ausgang (auch any);
- Befund, wenn die Firewall ein Netz der Zone nicht ueber ihre Ausgaenge lernt.

Umgebung wie in `test_appdoc_zones` (Webshop mit web01 10.20.0.5 und db01 10.20.1.7, fw-04 mit
Regeln aus Clients, Remote Access und Partner), dazu fw-05 mit einer Regel innerhalb des DC und
einer Regel von any ueber die Firewall-Zone „ext“.
"""

import test_paths as tp
from app.store import appdoc_catalog, naming_profiles
from test_appdoc_names import ADMIN, OP
from test_appdoc_zones import ZONES, _row, _setup, _where
from test_vmware import _deliver, _system

VIEW = {"X-Dev-Roles": "viewer"}


def _fw(pid, name, src, dst, src_zone, dst_zone):
    return {"source_platform": "fortigate", "external_id": str(pid), "name": name,
            "action": "allow", "sources": src, "destinations": dst, "services": ["tcp/443"],
            "family": "v4", "src_zone": src_zone, "dst_zone": dst_zone}  # fmt: skip


FW5 = {
    "status": "completed",
    "rules": [
        _fw(61, "dc-intern", ["10.20.9.9"], ["10.20.0.5"], "srv", "srv"),
        _fw(62, "any-to-web", ["any"], ["10.20.0.5"], "ext", "srv"),
    ],
}


def _profile(session_factory) -> int:
    s = session_factory()
    pid = naming_profiles.seed_default_profile(s).id
    s.commit()
    s.close()
    return pid


def _catalog(client, pid: int, flows: dict[int, list[list[str]]], titles: dict[int, str]) -> None:
    cat = client.get(f"/naming-profiles/{pid}/appdoc-catalog", headers=VIEW).json()
    keys = ("index", "title", "title_en", "zone_title", "zone_title_en", "layout",
            "only_with_zones", "flows")  # fmt: skip
    cats = [{k: c[k] for k in keys} for c in cat["categories"]]
    for c in cats:
        c["flows"] = flows.get(c["index"], c["flows"])
    cats += [{"index": i, "title": t, "zone_title": "Zugriff aus {z}", "flows": flows[i]}
             for i, t in titles.items()]  # fmt: skip
    body = {"categories": cats, "areas": [], "options": cat["options"]}
    r = client.put(f"/naming-profiles/{pid}/appdoc-catalog", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text


def test_richtungen_nach_zonentyp(client, session_factory):
    tid, app, ids = _setup(client)
    fw5 = _system(client, tid, "fw-05", "fortigate", base_url="https://fw5")
    _deliver(client, tid, fw5, "fortigate", FW5)
    body = {"tenant_id": tid, "name": "DC Web", "kind": "dc",
            "selectors": [{"kind": "prefix", "value": "10.20.0.0/16"}]}  # fmt: skip
    dc = client.post("/zones", json=body, headers=OP).json()["id"]
    # Ausgang: Gegenstellen ueber die Firewall-Zone „ext“ von fw-05 gehoeren zu Partner
    sel = [{"kind": "prefix", "value": n} for n in ZONES["Partner"]]
    sel.append({"kind": "via_fw_zone", "value": "ext", "source_system_id": fw5})
    body = {"tenant_id": tid, "name": "Partner", "kind": "access", "selectors": sel}
    r = client.put(f"/zones/{ids['Partner']}", json=body, headers=OP)
    assert r.status_code == 200, r.text

    # Zonentypen: DC erkannt nach dem Namen, die anderen von Hand (Vorlage ohne Grundschema)
    zc = client.get("/zones/appdoc/catalog", params={"tenant_id": tid}).json()
    assert "Edge Office" in zc["zone_types"] and zc["has_flows"] is False
    assert zc["types"][str(dc)] == "DC" and zc["types"][str(ids["Clients"])] is None
    types = [{"id": ids["Clients"], "zone_type": "Edge Office"},
             {"id": ids["Remote Access"], "zone_type": "edge vpn"},
             {"id": ids["Partner"], "zone_type": "Edge Internet"}]  # fmt: skip
    r = client.put("/zones/appdoc", json={"tenant_id": tid, "zones": types}, headers=OP)
    assert r.status_code == 200, r.text
    assert {z["name"]: z["zone_type"] for z in r.json()}["Remote Access"] == "Edge VPN"
    bad = {"tenant_id": tid, "zones": [{"id": dc, "zone_type": "Mond"}]}
    assert client.put("/zones/appdoc", json=bad, headers=OP).status_code == 422
    listed = {z["name"]: z for z in client.get(f"/zones?tenant_id={tid}").json()}
    assert listed["Clients"]["schema"] == "Edge Office" and listed["DC Web"]["schema"] == "DC"

    url = f"/applications/{app['id']}/doc"
    before = _where(client.get(url).json())
    assert before["clients-to-web"] == {10}  # ohne Richtungen wie bisher

    # Richtungen im Namenskonzept
    pid = _profile(session_factory)
    _catalog(client, pid, {4: [["DC", "Edge Office"]], 12: [["Edge Office", "DC"]],
                           13: [["Edge VPN", "*"]], 14: [["Edge Internet", "DC"]]},
             {12: "Zugriff aus dem Office", 13: "Zugriff aus dem VPN",
              14: "Zugriff aus dem Internet (Partner)"})  # fmt: skip
    doc = client.get(url).json()
    where = _where(doc)
    assert where["clients-to-web"] == {12} and where["group-to-web"] == {12}
    assert where["remote-to-web"] == {13}  # Edge VPN → * (genaueste Richtung)
    assert where["web-to-clients"] == {4}  # ausgehend DC → Edge Office
    assert where["wide-to-web"] == {10}  # mehrere Zonentypen: unspezifisch
    assert where["dc-intern"] == {8}  # innerhalb der eigenen Zone
    assert where["any-to-web"] == {14}  # any ueber den Ausgang „ext“ → Partner (Edge Internet)
    assert _row(doc, "any-to-web")["Zone"] == "Partner"
    assert where["to-db"] == {7}  # beide Seiten in der Applikation
    t12 = next(t for t in doc["tables"] if t["index"] == 12)
    assert t12["title"] == "Zugriff aus Clients"  # Kopf: Zonen der Gegenstellen
    assert [(z["name"], z["type"]) for z in t12["zones"]] == [("Clients", "Edge Office")]
    # Doku je System wie die Applikation
    system = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}).json()
    assert _where(system)["clients-to-web"] == {12} and _where(system)["dc-intern"] == {8}


def test_richtungen_im_katalog():
    cat = appdoc_catalog.default()
    assert not cat.has_flows and cat.by_flow("DC", "Edge Office") is None
    entries = [
        type(
            "E",
            (),
            {
                "kind": "appdoc_category",
                "key": "12",
                "value": "Office",
                "extra": {"flows": ["Edge Office → DC", ["*", "DC"]]},
            },
        )()
    ]
    cat = appdoc_catalog.apply_entries(cat, entries)
    assert cat.has_flows and cat.categories[12].flows == (("Edge Office", "DC"), ("*", "DC"))
    assert cat.by_flow("edge office", "dc") == 12  # normalisiert wie das Grundschema
    assert cat.by_flow("Core", "DC") == 12  # `*`
    assert cat.by_flow("DC", "Core") is None
    stored = dict((k, x) for _kind, k, _v, x in appdoc_catalog.entries_of(cat))
    assert stored["12"]["flows"] == [["Edge Office", "DC"], ["*", "DC"]]


def test_befund_netz_nicht_ueber_den_ausgang(client):
    tid = tp._seed(client)
    fw1 = next(s["id"] for s in client.get(f"/systems?tenant_id={tid}", headers=ADMIN).json()
               if s["name"] == "fw-01")  # fmt: skip

    def zone(name, net, exit_):
        body = {"tenant_id": tid, "name": name, "kind": "internal",
                "selectors": [{"kind": "prefix", "value": net},
                              {"kind": "via_fw_interface", "value": exit_,
                               "source_system_id": fw1}]}  # fmt: skip
        r = client.post("/zones", json=body, headers=OP)
        assert r.status_code == 201, r.text

    zone("Server", "10.20.0.0/24", "srv")  # lernt fw-01 ueber srv: in Ordnung
    zone("Lan", "10.60.0.0/24", "to-aci")  # lernt fw-01 ueber to-fw02
    zone("ACI", "10.50.0.0/16", "to-aci")  # in Ordnung
    zone("Clients", "10.80.0.0/16", "to-fw02")  # nur ueber die Default-Route (wan1/wan2)
    found = client.get("/findings", params={"tenant_id": tid, "type": "zone_net_exit"}).json()
    by = {f["subject_id"]: f for f in found}
    assert set(by) == {"Lan · 10.60.0.0/24", "Clients · 10.80.0.0/16"}, set(by)
    lan = by["Lan · 10.60.0.0/24"]
    assert "über to-fw02" in lan["message"] and "Ausgang der Zone Clients" in lan["message"]
    assert "nur über die Default-Route (wan1, wan2)" in by["Clients · 10.80.0.0/16"]["message"]
    assert lan["severity"] == "warning"
