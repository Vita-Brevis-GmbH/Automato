# ruff: noqa: S104 - 0.0.0.0/0 sind Subnetze und Routen, keine Bind-Adressen
"""ACI ist zustandslos (0.69.1, Anwender): Contracts mit Filtern ohne Ports gelten in beide
Richtungen - der Provider kann Verbindungen zum Consumer aufbauen. Mit Zielport (tcp/1433)
laesst der Rueckweg nur Antworten zu.

Topologie aus `test_aci_check` (VRF TE:VRF1 mit EPG_web 10.50.1.0/24, EPG_db 10.50.2.0/24,
Taboo TB_db, CT_db web -> db tcp/1433+3306, CT_mgmt vzAny -> db tcp/22), dazu:

- CT_open web -> db mit tcp/1433 und ICMP; der Collector meldet zurueck nur ICMP (`reverse`);
- CT_legacy web -> db mit udp/any, ohne `reverse` (aelterer Lauf: aus den Filtern abgeleitet);
- CT_world: Consumer EN_inet (L3O_inet, 0.0.0.0/0), Provider EPG_web, ip/any.
"""

import copy

import test_aci_check as ac
import test_aci_relevance as ar
import test_paths as tp
import test_zones as tz

OP = {"X-Dev-Roles": "operator"}
TE = "uni/tn-TE"
INET = f"{TE}/out-L3O_inet/instP-EN_inet"


def _contract(name, src, dst, services, reverse=None):
    raw = {"dn": f"{TE}/brc-{name}", "name": name, "kind": "contract", "scope": "context"}
    if reverse is not None:
        raw |= {"subject": "SJ_1", "reverse": reverse}
    return {"source_platform": "cisco_aci", "external_id": f"{TE}/brc-{name}/SJ_1",
            "name": name, "action": "allow", "sources": src, "destinations": dst,
            "services": services, "aci_scope": "context", "raw": raw}  # fmt: skip


APIC = copy.deepcopy(ac.APIC)
next(e for e in APIC["epgs"] if e["dn"] == ar.WEB)["endpoints"] = [{"ip": "10.50.1.10"}]
APIC["rules"] += [
    _contract("CT_open", [f"epg:{ar.WEB}"], [f"epg:{ac.DB}"], ["tcp/1433", "icmp/any"],
              reverse=["icmp/any"]),
    _contract("CT_legacy", [f"epg:{ar.WEB}"], [f"epg:{ac.DB}"], ["udp/any"]),
    _contract("CT_world", [f"epg:{INET}"], [f"epg:{ar.WEB}"], ["ip/any"], reverse=["ip/any"]),
]  # fmt: skip


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=tp.ADMIN).json()["id"]
    for name, platform, result in (("fw-01", "fortigate", ac.FW1), ("apic-01", "cisco_aci", APIC)):
        tp._deliver(client, tid, tp._system(client, tid, name, platform), platform, result)
    return tid


def _aci(client, tid, **params) -> dict:
    out = tp._trace(client, tid, **params)
    (path,) = out["paths"]
    (hop,) = (h for h in path["hops"] if h["kind"] == "vrf")
    return hop["rules"]


def _rule(client, tid, name) -> dict:
    (row,) = client.get("/rules", params={"tenant_id": tid, "q": name}).json()
    return client.get(f"/rules/{row['id']}").json()


def test_weg_rueckrichtung_nur_ohne_ports(client):
    tid = _seed(client)
    back = {"src": "10.50.2.20", "dst": "10.50.1.10"}  # db -> web: Provider -> Consumer

    # ICMP: CT_open laesst den Provider zurueck aufbauen (laut Collector nur ICMP)
    rules = _aci(client, tid, **back, service="icmp")
    assert rules["verdict"] == "allow"
    assert rules["text"].startswith("erlaubt durch Contract CT_open in der Rückrichtung")
    (ct,) = (e for e in rules["rules"] if e["decides"])
    assert (ct["name"], ct["reverse"], ct["services"]) == ("CT_open", True, ["icmp/any"])
    assert (ct["src"], ct["dst"]) == ("direkt", "direkt")

    # tcp/1433: Filter mit Zielport - zurueck nur Antworten, keine neue Verbindung
    rules = _aci(client, tid, **back, service="tcp/1433")
    assert rules["verdict"] == "implicit_deny" and rules["rules"] == []

    # aelterer Lauf ohne Richtung je Subject: udp/any (ohne Ports) gilt auch zurueck
    rules = _aci(client, tid, **back, service="udp/53")
    assert rules["verdict"] == "allow" and rules["rules"][0]["name"] == "CT_legacy"

    # vorwaerts unveraendert: web -> db tcp/1433 ueber CT_db, nicht als Rueckrichtung
    rules = _aci(client, tid, src="10.50.1.10", dst="10.50.2.20", service="tcp/1433")
    assert rules["text"] == "erlaubt durch Contract CT_db"
    assert rules["rules"][0]["reverse"] is False

    # Regel-Detail: Richtung mit Erklaerung
    d = _rule(client, tid, "CT_open")["aci_direction"]
    assert (d["both"], d["reverse"], d["derived"]) == (True, ["icmp/any"], False)
    assert "beide Richtungen" in d["text"]
    assert _rule(client, tid, "CT_legacy")["aci_direction"]["derived"] is True
    db = _rule(client, tid, "CT_db")["aci_direction"]
    assert db["both"] is False and db["text"].startswith("nur Consumer → Provider")
    assert _rule(client, tid, "TB_db")["aci_direction"] is None  # Taboo: Richtung fest


def test_doku_und_verbindungen_in_beide_richtungen(client):
    tid = _seed(client)
    body = {"name": "Web", "members": [{"kind": "epg", "ref": ar.WEB}]}
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert app.status_code == 201, app.text
    aid = app.json()["id"]
    doc = client.get(f"/applications/{aid}/doc").json()
    rules = {r["id"]: r["name"] for r in doc["rules"]}
    hits = [(rules[h["rule_id"]], h["direction"], h["reverse"]) for h in doc["hits"]
            if h["rule_id"] in rules and rules[h["rule_id"]] == "CT_world"]  # fmt: skip
    # aus dem Internet zum Provider - und zurueck: EPG_web baut ins Internet auf (ip/any)
    assert sorted(hits) == [("CT_world", "in", False), ("CT_world", "out", True)]
    tables = {t["index"]: t["rows"] for t in doc["tables"]}
    inbound = [r for r in tables[2] if r["Description"].startswith("CT_world")]
    outbound = [r for r in tables[1] if r["Description"].startswith("CT_world")]
    assert inbound and inbound[0]["Source"]  # Gegenstelle auch in der Spalte Source
    assert outbound and outbound[0]["TCP/UDP"] == "ip/any (Rückrichtung)"
    # innerhalb des Tenants: eine Zeile, beide Richtungen vermerkt
    (open_row,) = (r for t in doc["tables"] for r in t["rows"]
                   if r["Description"].startswith("CT_open"))  # fmt: skip
    assert open_row["TCP/UDP"].endswith("(↔ beide Richtungen)")
    (db_row,) = (r for t in doc["tables"] for r in t["rows"]
                 if r["Description"].startswith("CT_db"))  # fmt: skip
    assert "beide Richtungen" not in db_row["TCP/UDP"]

    # Verbindungen Ende zu Ende: die Rueckrichtung als eigene Verbindung, vom Weg bestaetigt
    out = client.get(f"/applications/{aid}/connections").json()
    rev = [c for c in out["connections"] if c["reverse"]]
    icmp = next(c for c in rev if c["src"]["ip"] == "10.50.2.20" and c["service"] == "icmp")
    assert icmp["direction"] == "in" and icmp["verdict"] == "allow"
    states = {s["label"]: s["state"] for s in icmp["rules"]}
    assert states["Contract CT_open"] == "decides"


def test_zonenmatrix_rueckrichtung(client, monkeypatch):
    """Zonen: der Contract oeffnet auch Provider-Zone -> Consumer-Zone (Absicht pruefen)."""
    apic = copy.deepcopy(tz.APIC)
    apic["epgs"] = []  # _seed setzt EPG_web
    monkeypatch.setattr(tz, "APIC", apic)
    tid, ids = tz._seed(client)
    lan_epg = {"name": "EN_lan", "kind": "external", "dn": f"{TE}/out-L3O_fw/instP-EN_lan",
               "tenant": "TE", "l3out": "L3O_fw", "vrf": "VRF1", "subnets": ["10.60.0.0/24"],
               "subnet_scopes": {}}  # fmt: skip
    web = {"name": "EPG_web", "dn": ar.WEB, "tenant": "TE", "ap": "AP1", "bd": "BD_web",
           "vrf": "VRF1", "bd_subnets": ["10.50.1.1/24"]}  # fmt: skip
    result = copy.deepcopy(apic)
    result["epgs"] = [web, lan_epg]
    result["rules"] = [
        _contract("CT_lan_any", [f"epg:{ar.WEB}"], [f"epg:{lan_epg['dn']}"], ["ip/any"],
                  reverse=["ip/any"]),
        _contract("CT_lan_https", [f"epg:{ar.WEB}"], [f"epg:{lan_epg['dn']}"], ["tcp/443"],
                  reverse=[]),
    ]  # fmt: skip
    tp._deliver(client, tid, ids["apic-01"], "cisco_aci", result)
    z = tz._zones(client, tid, ids)
    web_z, lan = z["web"]["id"], z["lan"]["id"]
    cells = tz._cells(client.get("/zones/matrix", params={"tenant_id": tid}).json())
    assert cells[(web_z, lan)]["allow"] == 2
    assert cells[(lan, web_z)]["allow"] == 1  # nur CT_lan_any (ohne Ports) zurueck
    rules = client.get("/zones/matrix/rules", params={"tenant_id": tid, "src": lan,
                                                      "dst": web_z}).json()  # fmt: skip
    names = [x["name"] for x in rules["items"]]
    assert "CT_lan_any" in names and "CT_lan_https" not in names  # dazu die Deny-Policy der FW

    # Absicht LAN -> ACI Web gesperrt: der Contract verstoesst in der Rueckrichtung
    body = {"tenant_id": tid, "src_zone_id": lan, "dst_zone_id": web_z, "intent": "deny"}
    assert client.put("/zones/policy", json=body, headers=OP).status_code == 200
    m = client.get("/zones/matrix", params={"tenant_id": tid}).json()
    assert tz._cells(m)[(lan, web_z)]["violations"] == 1
    # eingeschraenkt auf tcp/443: ip/any zurueck geht darueber hinaus
    body |= {"intent": "limited", "services": ["tcp/443"]}
    assert client.put("/zones/policy", json=body, headers=OP).status_code == 200
    rules = client.get("/zones/matrix/rules", params={"tenant_id": tid, "src": lan,
                                                      "dst": web_z}).json()  # fmt: skip
    assert rules["violations"] == 1 and rules["items"][0]["outside"] == ["ALL"]
