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
    assert "10.0.0.0/8" not in {n["network"] for n in inet} and len(inet) == 8
    assert all(n["via"] == "Anbindung Internet" for n in inet)

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


def test_zonenplan(client):
    tid, ids = _seed(client)
    z = _zones(client, tid, ids)
    remote = _zone(client, tid, "Remote", [_sel("prefix", "10.99.0.0/16")])
    plan = client.get("/zones/plan", params={"tenant_id": tid}).json()
    nodes = {n["id"]: n["label"] for n in plan["nodes"]}
    att = {(a["zone"], nodes[a["node"]]): a for a in plan["attachments"]}

    srv = att[(z["server"]["id"], "fw-01 · VDOM root")]
    assert srv["via"] == ["interface"] and srv["interfaces"] == ["srv"]
    assert srv["networks"] == ["10.20.0.0/24"]
    assert att[(z["lan"]["id"], "fw-02")]["interfaces"] == ["lan"]
    web = att[(z["web"]["id"], "apic-01 · VRF TE:VRF1")]
    assert web["via"] == ["fabric"] and web["networks"] == ["10.50.1.0/24"]
    # Transfernetze an allen drei Knoten (auch die L3Out-SVI)
    assert {k[1] for k in att if k[0] == z["transfer"]["id"]} == {
        "fw-01 · VDOM root", "fw-02", "apic-01 · VRF TE:VRF1"}  # fmt: skip
    inet = att[(z["inet"]["id"], "fw-01 · VDOM root")]
    assert inet["interfaces"] == ["wan1", "wan2"]
    # Zone ohne eigenes Netz an einem Knoten: ueber Routen zu Routern ohne gelesenes Geraet
    rem = att[(remote["id"], "fw-01 · VDOM root")]
    assert rem["via"] == ["route"] and rem["default"]
    assert {r["next_hop"] for r in rem["routes"]} == {"198.51.100.1", "198.51.100.5"}
    assert "isp-a-gw.example.ch" in {n for r in rem["routes"] for n in r["names"]}
    assert att[(remote["id"], "fw-01 · VDOM kunde-a")]["interfaces"] == ["vl-kunde"]
    assert (remote["id"], "fw-02") not in att  # fw-02 kennt 10.99/16 nicht

    links = {frozenset((x["a"], x["b"])) for x in plan["links"]}
    assert frozenset((z["server"]["id"], z["inet"]["id"])) in links
    assert frozenset((z["lan"]["id"], z["transfer"]["id"])) in links
    assert frozenset((z["web"]["id"], z["transfer"]["id"])) in links
    # Netze ohne Zone: Transfer im VDOM kunde-a
    assert [u["network"] for u in plan["unzoned"]["items"]] == ["10.255.1.0/30"]
    assert plan["summary"]["without_node"] == 0


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
