"""Kategorien der Applikations-Doku mit Zonen (0.101.0, ADR 0012 Ergaenzung 0.101.0, Anwender
2026-10-28): Firewall-Regeln mit grossem Quell-Bereich, die direkt auf das System zeigen, stehen
in der Kategorie der Zone, aus der sie kommen - nicht mehr unter „Unspezifische Zugriffe“. Die
Zonen haengen an den Kategorien (`logical_zone.appdoc_area`), ihre Netze (Selektoren) bestimmen
die Gegenstellen; Spalte „Zone“ und die Zonen mit Netzen im Kopf der Kategorie. Dazu die Namen
breiter Quellen: die Gruppe, wie sie in der Regel steht.

Umgebung wie in `test_appdoc_names` (Webshop mit web01 10.20.0.5), dazu eine FortiGate fw-04
mit Firewall-Zonen ohne Merkmal im Namen (lan/srv) - bisher alles Kategorie 10.
"""

from app import audit
from app.models import AuditLog
from app.store import appdoc
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
        assert r.json()["appdoc_area"] is None
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


def test_kategorie_nach_der_zone_der_gegenstelle(client, session_factory):
    tid, app, ids = _setup(client)
    url = f"/applications/{app['id']}/doc"
    doc = client.get(url).json()
    where = _where(doc)
    # ohne Bereich an den Zonen: wie bisher unspezifisch (Kategorie 10), ausgehend nicht da
    for name in ("clients-to-web", "group-to-web", "wide-to-web", "remote-to-web"):
        assert where[name] == {10}, (name, where[name])
    assert "web-to-clients" not in where
    # die Spalte „Zone“ nennt schon die Zonen der Quelle (auch Netze darin)
    assert _row(doc, "clients-to-web")["Zone"] == "Clients"
    assert _row(doc, "wide-to-web")["Zone"] == "Clients\nRemote Access\nPartner"
    assert _row(doc, "remote-to-web")["Zone"] == "Remote Access"
    t10 = next(t for t in doc["tables"] if t["index"] == 10)
    assert "Zone" in t10["columns"] and t10["zones"] == []

    # Zonen den Kategorien zuordnen (Operator): Clients = Unternehmensnetzwerk, Remote Access
    body = {"tenant_id": tid, "areas": {ids["Clients"]: "bit", ids["Remote Access"]: "sra"}}
    viewer = {"X-Dev-Roles": "viewer"}
    assert client.put("/zones/appdoc-areas", json=body, headers=viewer).status_code == 403
    r = client.put("/zones/appdoc-areas", json=body, headers=OP)
    assert r.status_code == 200, r.text
    assert {z["name"]: z["appdoc_area"] for z in r.json()} == {
        "Clients": "bit", "Remote Access": "sra", "Partner": None}  # fmt: skip
    listed = {z["name"]: z["appdoc_area"] for z in client.get(f"/zones?tenant_id={tid}").json()}
    assert listed["Clients"] == "bit"

    doc = client.get(url).json()
    where = _where(doc)
    assert where["clients-to-web"] == {3}  # Bereich ganz ueber Netze der Zone Clients
    assert where["group-to-web"] == {3}
    assert where["remote-to-web"] == {6}
    assert where["web-to-clients"] == {4}  # ausgehend in die Zone Clients
    assert where["wide-to-web"] == {10}  # Clients und Remote Access: kein eindeutiger Bereich
    # Kopf der Kategorie: ihre Zonen mit den Netzen
    heads = {t["index"]: t["zones"] for t in doc["tables"]}
    assert [(z["name"], z["nets"], z["nets_total"]) for z in heads[3]] == [
        ("Clients", ["10.70.0.0/16", "10.71.0.0/16"], 2)]  # fmt: skip
    assert heads[3] == heads[4]
    assert [z["name"] for z in heads[6]] == ["Remote Access"] and heads[10] == []

    # Doku je System: gleiche Kategorien und Zonen im Kopf
    system = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}).json()
    sys_where = _where(system)
    assert sys_where["clients-to-web"] == {3} and sys_where["remote-to-web"] == {6}
    assert [z["name"] for t in system["tables"] if t["index"] == 6 for z in t["zones"]] == [
        "Remote Access"]  # fmt: skip

    # Zone wieder ohne Bereich (leer) bzw. ungueltig
    bad = {"tenant_id": tid, "areas": {ids["Clients"]: "lan"}}
    assert client.put("/zones/appdoc-areas", json=bad, headers=OP).status_code == 422
    other = {"tenant_id": tid, "areas": {999999: "bit"}}
    assert client.put("/zones/appdoc-areas", json=other, headers=OP).status_code == 404
    client.put("/zones/appdoc-areas", json={"tenant_id": tid, "areas": {ids["Clients"]: None}},
               headers=OP)  # fmt: skip
    assert _where(client.get(url).json())["clients-to-web"] == {10}

    s = session_factory()
    rows = [r for r in s.query(AuditLog) if r.action == "zone.appdoc_areas_set"]
    assert audit.decode_detail(rows[0])["changes"] == [[ids["Clients"], "bit"],
                                                       [ids["Remote Access"], "sra"]]  # fmt: skip
    s.close()


def test_bereich_im_zonen_editor_bleibt_ohne_angabe(client):
    tid, _app_, ids = _setup(client)
    zid = ids["Clients"]
    base = {"tenant_id": tid, "name": "Clients", "kind": "access",
            "selectors": [{"kind": "prefix", "value": n} for n in ZONES["Clients"]]}  # fmt: skip
    r = client.put(f"/zones/{zid}", json={**base, "appdoc_area": "bit"}, headers=OP)
    assert r.status_code == 200 and r.json()["appdoc_area"] == "bit"
    # ohne Feld (aeltere Aufrufe): bleibt
    assert client.put(f"/zones/{zid}", json=base, headers=OP).json()["appdoc_area"] == "bit"
    assert client.put(f"/zones/{zid}", json={**base, "appdoc_area": "x"},
                      headers=OP).status_code == 422  # fmt: skip
    assert client.put(f"/zones/{zid}", json={**base, "appdoc_area": None},
                      headers=OP).json()["appdoc_area"] is None  # fmt: skip


def test_kunden_benutzer_ohne_zonen(client):
    tid, app, ids = _setup(client)
    client.put("/zones/appdoc-areas", json={"tenant_id": tid, "areas": {ids["Clients"]: "bit"}},
               headers=OP)  # fmt: skip
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
    assert _where(doc).get("clients-to-web") in (None, {3})
    system = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}, headers=user)
    assert system.status_code == 200 and '"Zone"' not in system.text
    assert client.put("/zones/appdoc-areas", json={"tenant_id": tid, "areas": {}},
                      headers=user).status_code == 403  # fmt: skip


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


def test_kategorie_mit_bereich_pure():
    cat = appdoc.categorize
    assert cat(direction="in", counterpart_zone="lan", counterpart_external=False,
               is_dnat_target=False) == 10  # fmt: skip
    assert cat(direction="in", counterpart_zone="lan", counterpart_external=False,
               is_dnat_target=False, area="bit") == 3  # fmt: skip
    assert cat(direction="out", counterpart_zone="lan", counterpart_external=False,
               is_dnat_target=False, area="vpn") == 5  # fmt: skip
    # der Bereich der Zone geht dem Merkmal im Namen der Firewall-Zone vor; extern bleibt 2
    assert cat(direction="in", counterpart_zone="L3O_vpn", counterpart_external=False,
               is_dnat_target=False, area="sra") == 6  # fmt: skip
    assert cat(direction="in", counterpart_zone="lan", counterpart_external=True,
               is_dnat_target=False, area="bit") == 2  # fmt: skip


def _titles(doc: dict) -> dict[int, str]:
    return {t["index"]: t["title"] for t in doc["tables"]}


def test_titel_nach_den_zonen_und_dc_management(client):
    """0.102.0 (Anwender): Zuordnung in Netzwerkdesign → Zonen, Titel nach den Zonen; DC
    Management liegt hinter einer eigenen Firewall - eigene Firewall-Regeln und Contracts im ACI
    in Kategorie 11."""
    tid, app, ids = _setup(client)
    url = f"/applications/{app['id']}/doc"
    doc = client.get(url).json()
    assert [t["index"] for t in doc["tables"]] == list(range(1, 11))  # ohne Zone keine 11
    assert _titles(doc)[3] == "Zugriff in das Unternehmensnetzwerk"
    assert _where(doc)["mgmt-to-web"] == {10}
    assert _where(doc)["CT_app:SJ"] == {7}

    body = {"tenant_id": tid, "name": "DC Management", "kind": "dc",
            "selectors": [{"kind": "prefix", "value": "10.79.0.0/16"},
                          {"kind": "aci_epg", "value": "TE_ka/AP_007/EPG_db01"}]}  # fmt: skip
    mgmt = client.post("/zones", json=body, headers=OP).json()["id"]
    zones = [{"id": ids["Clients"], "appdoc_area": "bit"},
             {"id": ids["Remote Access"], "appdoc_area": "sra"},
             {"id": mgmt, "appdoc_area": "mgmt"}]  # fmt: skip
    assert client.put("/zones/appdoc", json={"tenant_id": tid, "zones": zones},
                      headers={"X-Dev-Roles": "viewer"}).status_code == 403  # fmt: skip
    r = client.put("/zones/appdoc", json={"tenant_id": tid, "zones": zones}, headers=OP)
    assert r.status_code == 200, r.text
    assert {z["name"]: z["appdoc_area"] for z in r.json()}["DC Management"] == "mgmt"

    doc = client.get(url).json()
    titles = _titles(doc)
    assert (titles[3], titles[4]) == ("Zugriff in Clients", "Zugriff aus Clients")
    assert (titles[6], titles[11]) == ("Zugriff Remote Access", "Zugriff DC Management")
    assert titles[2] == "Zugriff aus dem Internet"  # ohne Zone der Titel der Vorlage
    where = _where(doc)
    assert where["mgmt-to-web"] == {11}
    # Contract: beim System web01 ist db01 (EPG der Zone) die Gegenstelle -> 11; bei db01 bleibt
    # web01 die Gegenstelle (ACI intern, 7)
    assert where["CT_app:SJ"] == {7, 11}
    head = next(t for t in doc["tables"] if t["index"] == 11)
    assert head["zones"][0]["name"] == "DC Management" and "Zone" in head["columns"]
    en = client.get(url, headers={"Accept-Language": "en"}).json()
    assert (_titles(en)[4], _titles(en)[11]) == ("Access from Clients", "Access DC Management")
    # Doku je System ebenso
    system = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}).json()
    assert _titles(system)[11] == "Zugriff DC Management"
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
    item = {"id": zid, "appdoc_area": "bit", "nets": ["10.70.0.0/15", " 10.70.0.0/15"],
            "excluded": ["10.71.0.0/16"]}  # fmt: skip
    r = client.put("/zones/appdoc", json={"tenant_id": tid, "zones": [item]}, headers=OP)
    assert r.status_code == 200, r.text
    out = {z["id"]: z for z in r.json()}[zid]
    assert (out["nets"], out["excluded"], out["appdoc_area"]) == (
        ["10.70.0.0/15"], ["10.71.0.0/16"], "bit")  # fmt: skip
    listed = next(z for z in client.get(f"/zones?tenant_id={tid}").json() if z["id"] == zid)
    assert {(s["kind"], s["value"], s["exclude"]) for s in listed["selectors"]} == {
        ("fw_zone", "lan-x", False), ("prefix", "10.70.0.0/15", False),
        ("prefix", "10.71.0.0/16", True)}  # fmt: skip

    doc = client.get(url).json()
    where = _where(doc)
    assert where["clients-to-web"] == {3}  # der Rest 10.70.0.0/16 liegt in der Zone
    assert "web-to-clients" not in where  # 10.71.0.0/16 ausgeschlossen: ausgehend unspezifisch
    head = next(t for t in doc["tables"] if t["index"] == 3)["zones"]
    assert [(z["name"], z["nets"], z["excluded"]) for z in head] == [
        ("Clients", ["10.70.0.0/15"], ["10.71.0.0/16"])]  # fmt: skip

    # nur die Kategorie: Netze bleiben; ungueltig bzw. unbekannt
    r = client.put("/zones/appdoc", json={"tenant_id": tid, "zones": [{"id": zid,
                   "appdoc_area": None}]}, headers=OP)  # fmt: skip
    assert {z["id"]: z for z in r.json()}[zid]["nets"] == ["10.70.0.0/15"]
    bad = {"tenant_id": tid, "zones": [{"id": zid, "excluded": ["10.71.0.0/33"]}]}
    assert client.put("/zones/appdoc", json=bad, headers=OP).status_code == 422
    unknown = {"tenant_id": tid, "zones": [{"id": 999999, "appdoc_area": "bit"}]}
    assert client.put("/zones/appdoc", json=unknown, headers=OP).status_code == 404

    s = session_factory()
    rows = [r for r in s.query(AuditLog) if r.action == "zone.appdoc_set"]
    assert audit.decode_detail(rows[0])["changes"] == [
        {"zone_id": zid, "area": "bit", "nets": 1, "excluded": 1}]  # fmt: skip
    s.close()
