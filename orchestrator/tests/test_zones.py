# ruff: noqa: S104 - 0.0.0.0/0 sind Netze, keine Bind-Adressen
"""Logische Zonen (0.51.0, ADR 0024): Netze je Zone aus Selektoren, Zonenmatrix mit Absicht
und Befunden, Zonenplan mit Uebergaengen, Rechte.

Topologie wie in der Weg-Suche (test_paths): fw-01 (Servernetz, Transfer zur ACI und zu
fw-02, zwei Provider, VDOM kunde-a), fw-02 (LAN), ACI-VRF TE:VRF1 mit EPG_web. Dazu Policies
auf fw-01.
"""

import copy

from test_paths import APIC, DDI, FW1, FW2, TOKEN, _deliver, _run, _system

ADMIN = {"X-Dev-Roles": "admin"}
OP = {"X-Dev-Roles": "operator"}
VIEW = {"X-Dev-Roles": "viewer"}


def _policy(pid, name, src, dst, action="allow"):
    return {"source_platform": "fortigate", "external_id": str(pid), "name": name,
            "action": action, "sources": src, "destinations": dst, "services": ["tcp/443"],
            "vdom": "root", "family": "v4",
            "raw": {"policyid": pid, "srcintf": [{"name": "any"}], "dstintf": [{"name": "any"}],
                    "status": "enable"}}  # fmt: skip


RULES = [
    _policy(1, "srv-to-web", ["10.20.0.0/24"], ["10.50.1.10"]),
    _policy(2, "srv-out", ["10.20.0.5"], ["all"]),
    _policy(3, "lan-to-srv", ["10.60.0.0/24"], ["10.20.0.5"]),
    _policy(4, "lan-to-web", ["10.60.0.0/24"], ["10.50.1.0/24"], action="deny"),
    _policy(5, "inet-to-srv", ["203.0.113.7"], ["10.20.0.5"]),
    _policy(6, "wide", ["10.20.0.0/16"], ["10.99.1.1"]),
]


def _seed(client) -> tuple[int, dict[str, int]]:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    fw1 = copy.deepcopy(FW1)
    fw1["rules"] = RULES
    apic = copy.deepcopy(APIC)
    apic["epgs"] = [{"name": "EPG_web", "dn": "uni/tn-TE/ap-AP1/epg-EPG_web", "tenant": "TE",
                     "ap": "AP1", "bd": "BD_web", "vrf": "VRF1",
                     "bd_subnets": ["10.50.1.1/24"]}]  # fmt: skip
    ids = {}
    for name, platform, result in (("fw-01", "fortigate", fw1), ("fw-02", "fortigate", FW2),
                                   ("apic-01", "cisco_aci", apic)):  # fmt: skip
        ids[name] = _system(client, tid, name, platform)
        _deliver(client, tid, ids[name], platform, result)
    ddi = _system(client, tid, "ddi", "infoblox", username="u", password="p")
    run_id = _run(client, tid, ddi, "infoblox")
    client.post(f"/internal/collector-runs/{run_id}/references", headers=TOKEN,
                json={"records": DDI})  # fmt: skip
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN,
                json={"status": "completed", "reference_count": len(DDI)})  # fmt: skip
    exit_body = {
        "tenant_id": tid,
        "name": "Internet",
        "kind": "internet",
        "next_hops": ["198.51.100.1", "198.51.100.5"],
        "prefixes": ["0.0.0.0/0"],
    }
    assert client.post("/routing/exits", json=exit_body, headers=ADMIN).status_code in (200, 201)
    return tid, ids


def _sel(kind, value, exclude=False, source_system_id=None):
    return {"kind": kind, "value": value, "exclude": exclude, "source_system_id": source_system_id}


def _zone(client, tid, name, selectors, headers=OP, **extra) -> dict:
    r = client.post("/zones", json={"tenant_id": tid, "name": name, "selectors": selectors,
                                    **extra}, headers=headers)  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def _zones(client, tid, ids) -> dict[str, dict]:
    return {
        "server": _zone(client, tid, "Server", [_sel("fw_interface", "srv",
                                                     source_system_id=ids["fw-01"])]),
        "web": _zone(client, tid, "ACI Web", [_sel("aci_tenant", "TE")]),
        "lan": _zone(client, tid, "LAN", [_sel("prefix", "10.60.0.0/24")]),
        "inet": _zone(client, tid, "Internet", [_sel("exit", "Internet"),
                                               _sel("prefix", "10.0.0.0/8", exclude=True)],
                      kind="external", color="#cc3333"),
        "transfer": _zone(client, tid, "Transfer", [_sel("prefix", "10.70.0.0/30"),
                                                    _sel("prefix", "10.255.0.0/29")],
                          kind="transfer"),
    }  # fmt: skip


def _cells(matrix) -> dict[tuple, dict]:
    return {(c["src"], c["dst"]): c for c in matrix["cells"]}


def test_zonen_aus_selektoren_und_vorschau(client):
    tid, ids = _seed(client)
    z = _zones(client, tid, ids)
    assert z["server"]["nets"] == 1 and z["web"]["epgs"] == 1 and z["web"]["nets"] == 1
    assert z["inet"]["kind"] == "external" and z["inet"]["color"] == "#cc3333"
    # Reihenfolge: ohne Angabe ans Ende
    listed = client.get("/zones", params={"tenant_id": tid}).json()
    assert [x["name"] for x in listed] == ["Server", "ACI Web", "LAN", "Internet", "Transfer"]
    nets = client.get(f"/zones/{z['server']['id']}/nets").json()
    assert nets["items"] == [{"network": "10.20.0.0/24", "via": "Interface srv"}]
    # Internet: 0.0.0.0/0 ohne die privaten 10.0.0.0/8
    inet = client.get(f"/zones/{z['inet']['id']}/nets").json()["items"]
    behind = [n for n in inet if n["via"] == "Anbindung Internet"]
    assert "10.0.0.0/8" not in {n["network"] for n in inet} and len(behind) == 8
    # dazu (seit 0.56.0) die Netze der Provider-Beine von fw-01 (Knoten ohne Zone)
    assert {n["network"] for n in inet} - {n["network"] for n in behind} == {
        "198.51.100.0/30", "198.51.100.4/30"}  # fmt: skip

    # Vorschau: gleiches Praefix in einer anderen Zone
    body = {"tenant_id": tid, "selectors": [_sel("prefix", "10.20.0.0/24")]}
    p = client.post("/zones/preview", json=body, headers=VIEW).json()
    assert p["counts"] == {"net": 1, "epg": 0}
    assert p["overlaps"] == [{"network": "10.20.0.0/24", "zone": "Server"}]
    body["zone_id"] = z["server"]["id"]  # die Zone selbst zaehlt nicht
    assert client.post("/zones/preview", json=body).json()["overlaps"] == []
    # ungueltige Selektoren
    bad = {"tenant_id": tid, "name": "X", "selectors": [_sel("prefix", "nope")]}
    assert client.post("/zones", json=bad, headers=OP).status_code == 422
    bad["selectors"] = [_sel("magic", "x")]
    assert client.post("/zones", json=bad, headers=OP).status_code == 422
    dup = {"tenant_id": tid, "name": "Server", "selectors": []}
    assert client.post("/zones", json=dup, headers=OP).status_code == 409
    assert client.post("/zones", json={**dup, "name": "Y", "kind": "magic"},
                       headers=OP).status_code == 422  # fmt: skip


def test_zonenmatrix_mit_absicht_und_befund(client):
    tid, ids = _seed(client)
    z = _zones(client, tid, ids)
    srv, web, lan, inet = (z[k]["id"] for k in ("server", "web", "lan", "inet"))
    m = client.get("/zones/matrix", params={"tenant_id": tid}).json()
    cells = _cells(m)
    assert cells[(srv, web)]["allow"] == 1
    assert cells[(srv, "any")]["allow"] == 1  # any als eigene Spalte
    assert cells[(lan, srv)]["allow"] == 1
    assert cells[(lan, web)] == {**cells[(lan, web)], "allow": 0, "deny": 1}
    assert cells[(inet, srv)]["allow"] == 1
    # 10.20.0.0/16: Servernetz und der Rest ohne Zone; Ziel ohne Zone
    assert cells[(srv, "none")]["allow"] == 1 and cells[("none", "none")]["allow"] == 1
    assert m["axes"][-2:] == ["any", "none"]
    assert m["summary"]["rules"] == 6 and m["summary"]["assigned"] == 6
    assert m["summary"]["violations"] == 0

    # Absicht: Internet -> Server gesperrt; die erlaubende Regel ist ein Verstoss
    body = {"tenant_id": tid, "src_zone_id": inet, "dst_zone_id": srv, "intent": "deny",
            "note": "nur über Reverse Proxy"}  # fmt: skip
    assert client.put("/zones/policy", json=body, headers=VIEW).status_code == 403
    r = client.put("/zones/policy", json=body, headers=OP)
    assert r.status_code == 200 and r.json()["intent"] == "deny"
    m = client.get("/zones/matrix", params={"tenant_id": tid}).json()
    cell = _cells(m)[(inet, srv)]
    assert (cell["intent"], cell["violations"], cell["note"]) == (
        "deny",
        1,
        "nur über Reverse Proxy",
    )
    assert m["summary"]["violations"] == 1
    rules = client.get("/zones/matrix/rules", params={"tenant_id": tid, "src": inet,
                                                      "dst": srv}).json()  # fmt: skip
    assert rules["total"] == 1 and rules["violations"] == 1
    assert rules["items"][0]["name"] == "inet-to-srv" and rules["items"][0]["violation"]
    any_rules = client.get("/zones/matrix/rules", params={"tenant_id": tid, "src": srv,
                                                          "dst": "any"}).json()  # fmt: skip
    assert [x["name"] for x in any_rules["items"]] == ["srv-out"]
    bad = client.get("/zones/matrix/rules", params={"tenant_id": tid, "src": "x", "dst": srv})
    assert bad.status_code == 422

    # Befund sofort (ohne neuen Lauf), mit Text
    f = client.get("/findings", params={"tenant_id": tid, "type": "zone_policy_violation"}).json()
    items = f["items"] if isinstance(f, dict) else f
    assert len(items) == 1 and items[0]["subject_id"] == "inet-to-srv"
    assert "Internet → Server" in items[0]["message"]
    # Absicht „eingeschraenkt“: kein Verstoss mehr; entfernen
    body["intent"] = "limited"
    client.put("/zones/policy", json=body, headers=OP)
    f = client.get("/findings", params={"tenant_id": tid, "type": "zone_policy_violation"}).json()
    assert (f["items"] if isinstance(f, dict) else f) == []
    body["intent"] = None
    client.put("/zones/policy", json=body, headers=OP)
    assert client.get("/zones/matrix", params={"tenant_id": tid}).json()["intents"] == []
    body["intent"] = "magic"
    assert client.put("/zones/policy", json=body, headers=OP).status_code == 422


def test_gleiches_netz_in_zwei_zonen(client):
    tid, ids = _seed(client)
    z = _zones(client, tid, ids)
    _zone(client, tid, "Server alt", [_sel("prefix", "10.20.0.0/24")])
    f = client.get("/findings", params={"tenant_id": tid, "type": "zone_overlap"}).json()
    items = f["items"] if isinstance(f, dict) else f
    assert [i["subject_id"] for i in items] == ["10.20.0.0/24"]
    assert "Server, Server alt" in items[0]["message"]
    # Regeln zaehlen in beiden Zonen
    cells = _cells(client.get("/zones/matrix", params={"tenant_id": tid}).json())
    alt = next(x["id"] for x in client.get(f"/zones?tenant_id={tid}").json()
               if x["name"] == "Server alt")  # fmt: skip
    assert cells[(z["lan"]["id"], alt)]["allow"] == 1
    # Zone loeschen: Befund faellt weg, auch beim naechsten Abgleich
    assert client.delete(f"/zones/{alt}", headers=OP).status_code == 204
    f = client.get("/findings", params={"tenant_id": tid, "type": "zone_overlap"}).json()
    assert (f["items"] if isinstance(f, dict) else f) == []


def test_zonenplan_nur_netze(client):
    """Zonen nur ueber Netze (ohne Knoten): die Netze bleiben in ihren Zonen, Knoten ohne Zone
    haengen gestrichelt daran (Kontakt); ein Uebergang nur, wo eine Zone einen Knoten hat."""
    tid, ids = _seed(client)
    z = _zones(client, tid, ids)
    plan = client.get("/zones/plan", params={"tenant_id": tid}).json()
    assert plan["format"] == 2 and plan["view"]["mode"] == "all"
    byname = {x["name"]: x for x in plan["zones"]}
    assert byname["Knoten ohne Zone"]["nodes"] == ["1|kunde-a", "1|root", "2|root"]
    assert byname["ACI Web"]["nodes"] == ["3|TE:VRF1"]
    trans = {t["name"]: t for t in plan["transitions"]}
    web = trans["ACI Web-Transfer"]
    assert web["l3out"] and not web["contact"] and web["count"] == 1
    (net,) = web["networks"]
    assert net["network"] == "10.255.0.0/29"
    assert {(x["label"], x["interface"]) for x in net["legs"]} == {
        ("apic-01 · VRF TE:VRF1", "vpc_fw"), ("fw-01 · VDOM root", "to-aci")}  # fmt: skip
    contacts = {t["name"] for t in plan["transitions"] if t["contact"]}
    # Namen nach Ebene: intern links, „ohne Zone“ beim Edge, Transfer und extern rechts davon
    assert contacts == {"Server-Knoten ohne Zone", "LAN-Knoten ohne Zone",
                        "Knoten ohne Zone-Internet", "Knoten ohne Zone-Transfer"}  # fmt: skip
    assert plan["summary"]["transitions"] == 1
    # Zone ohne Knoten: das Netz zur ACI bleibt ihres (kein Router dahinter), die L3Out der VRF
    # ist der Uebergang
    tr = client.get(f"/zones/{z['transfer']['id']}/nets").json()
    assert [n["network"] for n in tr["items"]] == ["10.70.0.0/30", "10.255.0.0/29"]
    assert tr["transitions"][0]["to"] == "ACI Web" and tr["nodes"] == []
    # Internet: die Netze der Provider-Beine an fw-01 (ohne Zone) liegen in der Zone
    inet = client.get(f"/zones/{z['inet']['id']}/nets").json()["items"]
    assert {n["via"] for n in inet if n["network"].startswith("198.51.100.")} == {
        "Interface wan1 · fw-01 · VDOM root", "Interface wan2 · fw-01 · VDOM root"}  # fmt: skip
    assert [u["network"] for u in plan["unzoned"]["items"]] == ["10.255.1.0/30"]
    lay = plan["layout"]
    assert lay["format"] == 2 and {e["label"] for e in lay["edges"]} == {"ACI Web-Transfer", ""}
    assert all(e["dashed"] for e in lay["edges"] if e["contact"])


def _model(client, tid, ids) -> dict[str, dict]:
    """Zonenmodell nach Ebenen: DC = ACI-Tenant, Edge Internet = beide Firewalls (root),
    Office und Server ueber ihr Netz bzw. Interface, Public hinter der Anbindung."""
    fw = [_sel("fw_vdom", "root", source_system_id=ids[n]) for n in ("fw-01", "fw-02")]
    return {
        "dc": _zone(client, tid, "DC", [_sel("aci_tenant", "TE")], kind="dc", code="DC"),
        "einet": _zone(client, tid, "Edge Internet", fw, kind="edge", code="EINET"),
        "office": _zone(client, tid, "Office", [_sel("prefix", "10.60.0.0/24")], kind="access",
                        code="OACC"),
        "server": _zone(client, tid, "Server", [_sel("fw_interface", "srv",
                                                     source_system_id=ids["fw-01"])],
                        code="SRV"),
        "public": _zone(client, tid, "Public", [_sel("exit", "Internet")], kind="external",
                        code="PUB"),
    }  # fmt: skip


def test_zonenmodell_knoten_und_uebergaenge(client):
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    plan = client.get("/zones/plan", params={"tenant_id": tid}).json()
    byid = {x["id"]: x for x in plan["zones"]}
    labels = {n["id"]: n["label"] for n in plan["nodes"]}
    einet = byid[z["einet"]["id"]]
    assert [labels[n] for n in einet["nodes"]] == ["fw-01 · VDOM root", "fw-02"]
    assert [labels[n] for n in byid[z["dc"]["id"]]["nodes"]] == ["apic-01 · VRF TE:VRF1"]
    assert byid["none"]["nodes"] == ["1|kunde-a"]
    trans = {t["name"]: t for t in plan["transitions"]}
    assert set(trans) == {"DC-EINET", "EINET-OACC", "EINET-PUB", "SRV-EINET"}
    assert trans["DC-EINET"]["l3out"] and not trans["EINET-PUB"]["l3out"]
    pub = trans["EINET-PUB"]
    assert [n["network"] for n in pub["networks"]] == ["198.51.100.0/30", "198.51.100.4/30"]
    routers = {r["ip"]: r["names"] for n in pub["networks"] for r in n["routers"]}
    assert set(routers) == {"198.51.100.1", "198.51.100.5"}
    assert "isp-a-gw.example.ch" in routers["198.51.100.1"]
    # Firewalls derselben Zone: Transfernetz in der Zone, kein Uebergang
    nets = client.get(f"/zones/{z['einet']['id']}/nets").json()
    assert nets["items"] == [{"network": "10.70.0.0/30",
                              "via": "Transfer fw-01 · VDOM root ↔ fw-02"}]  # fmt: skip
    assert nets["nodes"] == ["fw-01 · VDOM root", "fw-02"]
    assert {t["name"] for t in nets["transitions"]} == {
        "DC-EINET", "EINET-OACC", "EINET-PUB", "SRV-EINET"}  # fmt: skip
    # Transfernetze (Router bzw. Knoten auf beiden Seiten) gehoeren keiner Zone, ein LAN am
    # Interface bleibt Netz seiner Zone; Public behaelt 0.0.0.0/0
    office = client.get(f"/zones/{z['office']['id']}/nets").json()
    assert office["items"] == [{"network": "10.60.0.0/24", "via": "Netz 10.60.0.0/24"}]
    assert office["transitions"][0]["name"] == "EINET-OACC"  # nach Ebene: Edge vor Access
    pub_nets = client.get(f"/zones/{z['public']['id']}/nets").json()["items"]
    assert [n["network"] for n in pub_nets] == ["0.0.0.0/0"]
    dc = client.get(f"/zones/{z['dc']['id']}/nets").json()["items"]
    assert [n["network"] for n in dc] == ["10.50.1.0/24"]
    # Liste: Knoten und Uebergaenge je Zone, Farbe aus der Vorlage (DC passt auf „dc*“)
    listed = {x["name"]: x for x in client.get("/zones", params={"tenant_id": tid}).json()}
    assert listed["Edge Internet"]["nodes"] == ["fw-01 · VDOM root", "fw-02"]
    assert listed["DC"]["transitions"] == [{"to": "Edge Internet", "to_id": z["einet"]["id"],
                                            "name": "DC-EINET", "count": 1}]  # fmt: skip
    assert (listed["DC"]["color"], listed["DC"]["color_source"]) == ("#dae8fc", "template")
    # Spalten nach Ebene: DC links, Public rechts
    lay = {b["title"]: b for b in plan["layout"]["zones"]}
    assert lay["DC"]["x"] < lay["Edge Internet"]["x"] < lay["Office"]["x"] < lay["Public"]["x"]
    assert lay["Edge Internet"]["lines"] == ["fw-01 · VDOM root", "fw-02"]
    assert [u["network"] for u in plan["unzoned"]["items"]] == ["10.255.1.0/30"]


def test_knoten_einer_zone_zuordnen(client):
    tid, ids = _seed(client)
    z = _model(client, tid, ids)
    url = f"/zones/{z['einet']['id']}/nodes"
    body = {"nodes": ["1|kunde-a"]}
    assert client.post(url, json=body, headers=VIEW).status_code == 403
    r = client.post(url, json=body, headers=OP)
    assert r.status_code == 200, r.text
    assert r.json()["added"] == ["fw-01 · VDOM kunde-a"]
    assert client.post(url, json=body, headers=OP).json()["added"] == []  # schon da
    listed = {x["name"]: x for x in client.get("/zones", params={"tenant_id": tid}).json()}
    sel = {(s["kind"], s["value"], s["source_system_id"])
           for s in listed["Edge Internet"]["selectors"]}  # fmt: skip
    assert ("fw_vdom", "kunde-a", ids["fw-01"]) in sel
    plan = client.get("/zones/plan", params={"tenant_id": tid}).json()
    assert "none" not in {x["id"] for x in plan["zones"]}
    # das Netz am VDOM kunde-a liegt jetzt in der Zone (Router dahinter: ausserhalb)
    assert plan["unzoned"]["total"] == 0
    assert "EINET-ausserhalb (nicht gelesen)" in {t["name"] for t in plan["transitions"]}
    assert client.post(url, json={"nodes": ["99|x"]}, headers=OP).status_code == 404


def test_rechte_kunde_und_loeschen(client):
    tid, ids = _seed(client)
    assert client.post("/zones", json={"tenant_id": tid, "name": "X", "selectors": []},
                       headers=VIEW).status_code == 403  # fmt: skip
    cust = client.post("/customers", json={"tenant_id": tid, "name": "Kunde A", "selectors": []},
                       headers=OP).json()  # fmt: skip
    zone = _zone(client, tid, "Kunde A Server", [_sel("prefix", "10.20.0.0/24")],
                 customer_id=cust["id"])  # fmt: skip
    assert zone["customer"] == "Kunde A"
    # Kunden-Benutzer (seit 0.53.0): lesen ihre Zonen, aendern nichts
    who = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": str(cust["id"])}
    (mine,) = client.get("/zones", params={"tenant_id": tid}, headers=who).json()
    assert mine["name"] == "Kunde A Server" and mine["own"] and mine["selectors"] == []
    body = {"tenant_id": tid, "name": "X", "selectors": []}
    assert client.post("/zones", json=body, headers=who).status_code == 403
    # Kunde geloescht: die Zone bleibt, gehoert keinem mehr
    assert client.delete(f"/customers/{cust['id']}", headers=OP).status_code == 204
    (left,) = client.get("/zones", params={"tenant_id": tid}).json()
    assert left["customer_id"] is None
    # Absicht wird mit der Zone geloescht; Mandant loeschen raeumt alles
    other = _zone(client, tid, "Andere", [_sel("prefix", "10.60.0.0/24")])
    body = {"tenant_id": tid, "src_zone_id": other["id"], "dst_zone_id": left["id"],
            "intent": "deny"}  # fmt: skip
    assert client.put("/zones/policy", json=body, headers=OP).status_code == 200
    assert client.delete(f"/zones/{other['id']}", headers=OP).status_code == 204
    assert client.get("/zones/matrix", params={"tenant_id": tid}).json()["intents"] == []
    r = client.delete(f"/tenants/{tid}?confirm=Kunde A", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert client.get(f"/zones/{left['id']}/nets").status_code == 404
