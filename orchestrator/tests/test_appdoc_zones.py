"""Kategorien der Applikations-Doku mit Zonen (0.101.0, seit 0.106.0 nach dem Zonentyp, ADR
0035): Firewall-Regeln mit grossem Quell-Bereich, die direkt auf das System zeigen, stehen in der
Kategorie nach dem Zonentyp der Zone, aus der sie kommen - nicht unter „Unspezifische Zugriffe“.
Ihre Netze (Selektoren) bestimmen die Gegenstellen; Spalte „Zone“ und die Zonen mit Netzen im
Kopf der Kategorie. Dazu die Namen breiter Quellen: die Gruppe, wie sie in der Regel steht.

Umgebung wie in `test_appdoc_names` (Webshop mit web01 10.20.0.5), dazu eine FortiGate fw-04
mit Firewall-Zonen ohne Zonentyp im Namen (lan/srv) - ohne Zonentyp alles Kategorie 10.
"""

from app import audit
from app.models import AuditLog
from test_appdoc_names import ADMIN, OP, _env
from test_vmware import _deliver, _system


def _rule(pid, name, src, dst, **kw):
    inbound = dst == ["10.20.0.5"]
    return {"source_platform": "fortigate", "external_id": str(pid), "name": name,
            "action": "allow", "sources": src, "destinations": dst, "services": ["tcp/443"],
            "family": "v4", "src_zone": "lan" if inbound else "srv",
            "dst_zone": "srv" if inbound else "lan", **kw}  # fmt: skip


FW4 = {
    "status": "completed",
    "rules": [
        _rule(51, "clients-to-web", ["10.70.0.0/15"], ["10.20.0.5"]),  # Bereich ueber Clients
        _rule(52, "group-to-web", ["10.70.0.0/16", "10.71.0.0/16"], ["10.20.0.5"],
              source_refs=["G_clients"]),  # fmt: skip
        _rule(53, "wide-to-web", ["10.64.0.0/10"], ["10.20.0.5"]),  # mehrere Bereiche
        _rule(54, "remote-to-web", ["10.77.5.0/24"], ["10.20.0.5"]),  # in Remote Access
        _rule(55, "web-to-clients", ["10.20.0.5"], ["10.71.0.0/16"]),  # ausgehend
        _rule(56, "mgmt-to-web", ["10.79.1.0/24"], ["10.20.0.5"]),  # aus dem DC Management
        # ausgehend nur ueber eine breite Quelle (0.103.0): kein Zugriff der Applikation
        _rule(57, "wide-out", ["10.0.0.0/8"], ["10.78.0.0/16"], src_zone="srv", dst_zone="lan"),
    ],
    "objects": [
        {"name": "G_clients", "category": "address", "family": "v4",
         "members": ["N_c70", "N_c71"]},
        {"name": "N_c70", "category": "address", "family": "v4", "values": ["10.70.0.0/16"]},
        {"name": "N_c71", "category": "address", "family": "v4", "values": ["10.71.0.0/16"]},
    ],
}  # fmt: skip
ZONES = {
    "Clients": ["10.70.0.0/16", "10.71.0.0/16"],
    "Remote Access": ["10.77.0.0/16"],
    "Partner": ["10.78.0.0/16"],
}


def _setup(client) -> tuple[int, dict, dict[str, int]]:
    tid, app = _env(client)
    fw4 = _system(client, tid, "fw-04", "fortigate", base_url="https://fw4")
    _deliver(client, tid, fw4, "fortigate", FW4)
    ids = {}
    for name, nets in ZONES.items():
        body = {"tenant_id": tid, "name": name, "kind": "access",
                "selectors": [{"kind": "prefix", "value": n} for n in nets]}  # fmt: skip
        r = client.post("/zones", json=body, headers=OP)
        assert r.status_code == 201, r.text
        assert r.json()["zone_type"] is None and "appdoc_area" not in r.json()
        ids[name] = r.json()["id"]
    return tid, app, ids


def _where(doc: dict) -> dict[str, set[int]]:
    """Kategorien je Regel (Description)."""
    out: dict[str, set[int]] = {}
    for t in doc["tables"]:
        for r in t["rows"]:
            out.setdefault(r["Description"], set()).add(t["index"])
    return out


def _row(doc: dict, name: str) -> dict:
    return next(r for t in doc["tables"] for r in t["rows"] if r["Description"] == name)


def _types(client, tid: int, types: dict[int, str | None], headers=OP):
    zones = [{"id": zid, "zone_type": t} for zid, t in types.items()]
    return client.put("/zones/appdoc", json={"tenant_id": tid, "zones": zones}, headers=headers)


def test_kategorie_nach_dem_zonentyp_der_gegenstelle(client, session_factory):
    tid, app, ids = _setup(client)
    url = f"/applications/{app['id']}/doc"
    doc = client.get(url).json()
    where = _where(doc)
    # ohne Zonentyp (Namen ohne Muster im Grundschema): unspezifisch, ausgehend nicht da
    for name in ("clients-to-web", "group-to-web", "wide-to-web", "remote-to-web"):
        assert where[name] == {10}, (name, where[name])
    assert "web-to-clients" not in where
    # die Spalte „Zone“ nennt schon die Zonen der Quelle (auch Netze darin)
    assert _row(doc, "clients-to-web")["Zone"] == "Clients"
    assert _row(doc, "wide-to-web")["Zone"] == "Clients\nRemote Access\nPartner"
    assert _row(doc, "remote-to-web")["Zone"] == "Remote Access"
    t10 = next(t for t in doc["tables"] if t["index"] == 10)
    assert "Zone" in t10["columns"]
    assert {z["name"] for z in t10["zones"]} == {"Clients", "Remote Access", "Partner"}

    # Zonentyp je Zone (Operator): Clients = Office Access, Remote Access = Edge VPN
    viewer = {"X-Dev-Roles": "viewer"}
    types = {ids["Clients"]: "Office Access", ids["Remote Access"]: "edge vpn"}
    assert _types(client, tid, types, viewer).status_code == 403
    r = _types(client, tid, types)
    assert r.status_code == 200, r.text
    assert {z["name"]: z["zone_type"] for z in r.json()} == {
        "Clients": "Office Access", "Remote Access": "Edge VPN", "Partner": None}  # fmt: skip

    doc = client.get(url).json()
    where = _where(doc)
    assert where["clients-to-web"] == {4}  # Office Access → DC (System ohne Zone: DC)
    assert where["group-to-web"] == {4}
    assert where["remote-to-web"] == {5}  # Edge VPN → DC
    assert where["web-to-clients"] == {3}  # ausgehend DC → Office Access
    assert where["wide-to-web"] == {10}  # mehrere Zonentypen bzw. ohne: unspezifisch
    # Kopf der Kategorie: die Zonen ihrer Gegenstellen mit den Netzen und dem Zonentyp
    heads = {t["index"]: t["zones"] for t in doc["tables"]}
    assert [(z["name"], z["nets"], z["nets_total"], z["type"]) for z in heads[4]] == [
        ("Clients", ["10.70.0.0/16", "10.71.0.0/16"], 2, "Office Access")]  # fmt: skip
    assert [z["name"] for z in heads[5]] == ["Remote Access"]
    assert {t["index"]: t["title"] for t in doc["tables"]}[4] == (
        "Zugriff aus dem Unternehmensnetzwerk"
    )  # Titel ohne Zonen (0.106.0)

    # Doku je System: gleiche Kategorien und Zonen im Kopf
    system = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}).json()
    sys_where = _where(system)
    assert sys_where["clients-to-web"] == {4} and sys_where["remote-to-web"] == {5}
    assert [z["name"] for t in system["tables"] if t["index"] == 5 for z in t["zones"]] == [
        "Remote Access"]  # fmt: skip

    # wieder automatisch (leer) bzw. unbekannt
    assert _types(client, tid, {ids["Clients"]: "Mond"}).status_code == 422
    assert _types(client, tid, {999999: "DC"}).status_code == 404
    _types(client, tid, {ids["Clients"]: None})
    assert _where(client.get(url).json())["clients-to-web"] == {10}

    s = session_factory()
    rows = [r for r in s.query(AuditLog) if r.action == "zone.appdoc_set"]
    assert audit.decode_detail(rows[0])["changes"] == [
        {"zone_id": ids["Clients"], "type": "Office Access"},
        {"zone_id": ids["Remote Access"], "type": "Edge VPN"}]  # fmt: skip
    s.close()


def test_zonen_editor_ohne_bereich(client):
    """0.106.0: Bereiche gibt es nicht mehr - ein mitgeschickter Bereich (aeltere Aufrufe) wird
    ignoriert, der Zonentyp bleibt ohne Angabe."""
    tid, _app_, ids = _setup(client)
    zid = ids["Clients"]
    base = {"tenant_id": tid, "name": "Clients", "kind": "access",
            "selectors": [{"kind": "prefix", "value": n} for n in ZONES["Clients"]]}  # fmt: skip
    r = client.put(f"/zones/{zid}", json={**base, "appdoc_area": "x", "zone_type": "DC"},
                   headers=OP)  # fmt: skip
    assert r.status_code == 200 and r.json()["zone_type"] == "DC"
    assert "appdoc_area" not in r.json()
    assert client.put(f"/zones/{zid}", json=base, headers=OP).json()["zone_type"] == "DC"
    assert client.put("/zones/appdoc-areas", json={"tenant_id": tid, "areas": {}},
                      headers=OP).status_code in (404, 405, 422)  # fmt: skip


def test_kunden_benutzer_ohne_zonen(client):
    tid, app, ids = _setup(client)
    _types(client, tid, {ids["Clients"]: "Office Access"})
    body = {"tenant_id": tid, "name": "Kunde A",
            "selectors": [{"kind": "aci_tenant", "value": "TE_ka"}]}  # fmt: skip
    customer = client.post("/customers", json=body, headers=OP).json()["id"]
    user = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(customer)}
    r = client.get(f"/applications/{app['id']}/doc", headers=user)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert all("Zone" not in t["columns"] and t["zones"] == [] for t in doc["tables"])
    assert all("Zone" not in row for t in doc["tables"] for row in t["rows"])
    # die Kategorie gilt fuer sie gleich
    assert _where(doc).get("clients-to-web") in (None, {4})
    system = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}, headers=user)
    assert system.status_code == 200 and '"Zone"' not in system.text
    assert _types(client, tid, {}, user).status_code == 403


def test_namen_breiter_quellen_ueber_die_gruppe_der_regel(client):
    tid, app, _ids = _setup(client)
    url = f"/applications/{app['id']}/doc"
    assert _row(client.get(url).json(), "group-to-web")["Source"] == "10.70.0.0/16\n10.71.0.0/16"
    client.patch(f"/tenants/{tid}", json={"appdoc_foreign_names": True}, headers=ADMIN)
    doc = client.get(url).json()
    assert _row(doc, "group-to-web")["Source"] == "G_clients (10.70.0.0/16, 10.71.0.0/16)"
    assert doc["peer_names"] is True
    # ein Bereich ohne Gruppe bleibt die Adresse (kein Objekt, kein IPAM-Netz)
    assert _row(doc, "clients-to-web")["Source"] == "10.70.0.0/15"


def _titles(doc: dict) -> dict[int, str]:
    return {t["index"]: t["title"] for t in doc["tables"]}


def test_alle_kategorien_und_dc_management(client):
    """0.102.0 (Anwender): DC Management hinter einer eigenen Firewall - seit 0.106.0 die Zone mit
    dem Zonentyp Edge Management (Vorlage), Kategorie 11; alle Kategorien des Katalogs stehen da,
    die Titel ohne Zonen."""
    tid, app, ids = _setup(client)
    url = f"/applications/{app['id']}/doc"
    doc = client.get(url).json()
    assert [t["index"] for t in doc["tables"]] == list(range(1, 12))
    assert _titles(doc)[3] == "Zugriff in das Unternehmensnetzwerk"
    assert _where(doc)["mgmt-to-web"] == {10}
    assert _where(doc)["CT_app:SJ"] == {7}

    body = {"tenant_id": tid, "name": "DC Management", "kind": "dc",
            "selectors": [{"kind": "prefix", "value": "10.79.0.0/16"}]}  # fmt: skip
    mgmt = client.post("/zones", json=body, headers=OP).json()["id"]
    assert _types(client, tid, {mgmt: "Edge Management"}).status_code == 200
    doc = client.get(url).json()
    assert _where(doc)["mgmt-to-web"] == {11}
    assert _titles(doc)[11] == "Zugriff Management"
    head = next(t for t in doc["tables"] if t["index"] == 11)
    assert head["zones"][0]["name"] == "DC Management" and "Zone" in head["columns"]
    en = client.get(url, headers={"Accept-Language": "en"}).json()
    assert _titles(en)[11] == "Management access"
    system = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}).json()
    assert _where(system)["mgmt-to-web"] == {11}


def test_netze_mit_ausschluessen_im_netzwerkdesign(client, session_factory):
    """0.102.0 (Anwender): Netze einer Zone zuweisen und Ausschluesse machen - im
    Netzwerkdesign; die anderen Selektoren bleiben. Der Kopf der Kategorie nennt die Netze wie
    erfasst mit „ohne“."""
    tid, app, ids = _setup(client)
    url = f"/applications/{app['id']}/doc"
    zid = ids["Clients"]
    base = {"tenant_id": tid, "name": "Clients", "kind": "access",
            "selectors": [*({"kind": "prefix", "value": n} for n in ZONES["Clients"]),
                          {"kind": "fw_zone", "value": "lan-x"}]}  # fmt: skip
    assert client.put(f"/zones/{zid}", json=base, headers=OP).status_code == 200
    item = {"id": zid, "zone_type": "Office Access",
            "nets": ["10.70.0.0/15", " 10.70.0.0/15"], "excluded": ["10.71.0.0/16"]}  # fmt: skip
    r = client.put("/zones/appdoc", json={"tenant_id": tid, "zones": [item]}, headers=OP)
    assert r.status_code == 200, r.text
    out = {z["id"]: z for z in r.json()}[zid]
    assert (out["nets"], out["excluded"], out["zone_type"]) == (
        ["10.70.0.0/15"], ["10.71.0.0/16"], "Office Access")  # fmt: skip
    listed = next(z for z in client.get(f"/zones?tenant_id={tid}").json() if z["id"] == zid)
    assert {(s["kind"], s["value"], s["exclude"]) for s in listed["selectors"]} == {
        ("fw_zone", "lan-x", False), ("prefix", "10.70.0.0/15", False),
        ("prefix", "10.71.0.0/16", True)}  # fmt: skip

    doc = client.get(url).json()
    where = _where(doc)
    assert where["clients-to-web"] == {4}  # der Rest 10.70.0.0/16 liegt in der Zone
    assert "web-to-clients" not in where  # 10.71.0.0/16 ausgeschlossen: ausgehend unspezifisch
    head = next(t for t in doc["tables"] if t["index"] == 4)["zones"]
    assert [(z["name"], z["nets"], z["excluded"]) for z in head] == [
        ("Clients", ["10.70.0.0/15"], ["10.71.0.0/16"])]  # fmt: skip

    # nur der Zonentyp: Netze bleiben; ungueltig bzw. unbekannt
    r = client.put("/zones/appdoc", json={"tenant_id": tid, "zones": [{"id": zid,
                   "zone_type": None}]}, headers=OP)  # fmt: skip
    assert {z["id"]: z for z in r.json()}[zid]["nets"] == ["10.70.0.0/15"]
    bad = {"tenant_id": tid, "zones": [{"id": zid, "excluded": ["10.71.0.0/33"]}]}
    assert client.put("/zones/appdoc", json=bad, headers=OP).status_code == 422
    unknown = {"tenant_id": tid, "zones": [{"id": 999999, "zone_type": "DC"}]}
    assert client.put("/zones/appdoc", json=unknown, headers=OP).status_code == 404

    s = session_factory()
    rows = [r for r in s.query(AuditLog) if r.action == "zone.appdoc_set"]
    assert audit.decode_detail(rows[0])["changes"] == [
        {"zone_id": zid, "type": "Office Access", "nets": 1, "excluded": 1}]  # fmt: skip
    s.close()


def test_ausgehend_mit_breiter_quelle_nicht_in_der_doku(client):
    """0.103.0 (Anwender): Firewall-Regeln, deren Quelle offen ist (10.0.0.0/8), damit der
    Server irgendwo zugreifen kann, zeigen bei dem Server nicht - ausgehend mit dem Server als
    Host (web-to-clients) bzw. eingehend ueber einen grossen Bereich (clients-to-web) schon."""
    tid, app, _ids = _setup(client)
    url = f"/applications/{app['id']}/doc"
    doc = client.get(url).json()
    rules = {r["name"]: r["id"] for r in doc["rules"]}
    assert "wide-out" not in rules and "clients-to-web" in rules
    assert not [h for h in doc["hits"] if h["rule_id"] not in rules.values()]
    assert doc["summary"]["outbound_broad"] == 2  # dazu any-out (Quelle any) der Grundumgebung
    assert "wide-out" not in _where(doc)
    system = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}).json()
    assert "wide-out" not in _where(system)
    assert "wide-out" not in {x["description"] for x in system["impossible"]}
    # direkt der Applikation zugeordnet: bleibt da
    rid = next(r["id"] for r in client.get("/rules", params={"tenant_id": tid, "q": "wide-out"})
               .json() if r["name"] == "wide-out")  # fmt: skip
    r = client.post(f"/applications/{app['id']}/rules", json={"rule_id": rid}, headers=OP)
    assert r.status_code == 200, r.text
    doc = client.get(url).json()
    assert "wide-out" in {r["name"] for r in doc["rules"]}
    assert doc["summary"]["outbound_broad"] == 1
