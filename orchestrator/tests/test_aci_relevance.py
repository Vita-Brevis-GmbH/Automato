# ruff: noqa: S104 - 0.0.0.0/0 sind Subnetze und Routen, keine Bind-Adressen
"""ACI-Contracts ueber externe EPGs (0.28.0, ADR 0019 Punkt 6): welche externe EPG ordnet das
ACI einer IP ausserhalb des Fabrics zu, und welche Contracts gelten darum fuer sie?

Topologie aus `test_paths` (fw-01 mit Servernetz 10.20.0.0/24 hinter L3O_fw, ACI-VRF TE:VRF1
mit EPG_web 10.50.1.0/24), dazu L3O_inet mit Default-Route. Externe EPGs der VRF:
EN_any 0.0.0.0/0 und EN_srv 10.20.0.0/24 (L3O_fw), EN_rt 10.20.0.0/16 nur fuer das Routing
(export-rtctrl), EN_inet 0.0.0.0/0 (L3O_inet). Je eine Contract-Beziehung zu EPG_web.
"""

import copy

import test_paths as tp

OP = {"X-Dev-Roles": "operator"}
TE = "uni/tn-TE"
WEB = f"{TE}/ap-AP1/epg-EPG_web"


def _ext(name, l3out, subnets, scopes=None):
    return {"name": name, "kind": "external", "dn": f"{TE}/out-{l3out}/instP-{name}",
            "tenant": "TE", "l3out": l3out, "vrf": "VRF1", "subnets": subnets,
            "subnet_scopes": scopes or {}}  # fmt: skip


def _contract(name, consumer, port):
    ct = f"{TE}/brc-{name}"
    return {"source_platform": "cisco_aci", "external_id": f"{ct}/SJ_1", "name": name,
            "action": "allow", "sources": [f"epg:{TE}/out-{consumer}"],
            "destinations": [f"epg:{WEB}"], "services": [f"tcp/{port}"], "aci_scope": "context",
            "raw": {"dn": ct, "name": name, "scope": "context"}}  # fmt: skip


INET_HOP = [{"ip": "10.255.9.1", "interface": "eth1/10"}]
APIC = copy.deepcopy(tp.APIC)
APIC["routing"]["routes"] += [
    tp._r("leaf-101", "TE:VRF1", "0.0.0.0/0", INET_HOP, "bgp", distance=20),
    tp._r("leaf-102", "TE:VRF1", "0.0.0.0/0", INET_HOP, "bgp", distance=20),
]
APIC["routing"]["l3outs"].append(
    {"tenant": "TE", "name": "L3O_inet", "dn": f"{TE}/out-L3O_inet", "vrf": "TE:VRF1",
     "nodes": [{"pod": "1", "node": "102"}],
     "paths": [{"path": "topology/pod-1/paths-102/pathep-[eth1/10]", "nodes": ["102"],
                "interface": "eth1/10", "kind": "l3-port", "ips": [{"ip": "10.255.9.2/30"}]}],
     "peers": [{"ip": "10.255.9.1", "asn": 64600}], "ext_epgs": ["EN_inet"]}
)  # fmt: skip
APIC["epgs"] = [
    {"name": "EPG_web", "dn": WEB, "tenant": "TE", "ap": "AP1", "bd": "BD_web", "vrf": "VRF1",
     "bd_subnets": ["10.50.1.1/24"]},
    _ext("EN_any", "L3O_fw", ["0.0.0.0/0"]),
    _ext("EN_srv", "L3O_fw", ["10.20.0.0/24"], {"10.20.0.0/24": "import-security,shared-rtctrl"}),
    _ext("EN_rt", "L3O_fw", ["10.20.0.0/16"], {"10.20.0.0/16": "export-rtctrl"}),
    _ext("EN_inet", "L3O_inet", ["0.0.0.0/0"]),
    _ext("EN_wide", "L3O_fw", ["10.50.0.0/16"]),  # erfasst auch das Fabric-Netz
]  # fmt: skip
APIC["rules"] = [
    _contract("CT_srv", "L3O_fw/instP-EN_srv", 443),
    _contract("CT_any", "L3O_fw/instP-EN_any", 80),
    _contract("CT_rt", "L3O_fw/instP-EN_rt", 22),
    _contract("CT_inet", "L3O_inet/instP-EN_inet", 8443),
    _contract("CT_wide", "L3O_fw/instP-EN_wide", 3306),
]

# Firewall-Regel, damit die Server als Systeme bekannt sind
FW1 = {**tp.FW1, "rules": [
    {"source_platform": "fortigate", "external_id": "1", "name": "srv-to-web", "action": "allow",
     "sources": ["10.20.0.5", "10.20.1.7"], "destinations": ["10.50.1.10"],
     "services": ["tcp/443"], "src_zone": "server", "dst_zone": "aci", "vdom": "root",
     "family": "v4", "raw": {"policyid": 1, "srcintf": [{"name": "srv"}],
                             "dstintf": [{"name": "to-aci"}], "status": "enable"}},
]}  # fmt: skip


def _seed(client) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=tp.ADMIN).json()["id"]
    for name, platform, result in (("fw-01", "fortigate", FW1), ("apic-01", "cisco_aci", APIC)):
        tp._deliver(client, tid, tp._system(client, tid, name, platform), platform, result)
    return tid


def _doc(client, tid, ip) -> tuple[dict[str, dict], dict[str, dict]]:
    systems = client.get("/appdoc/systems", params={"tenant_id": tid}).json()
    name = next(s["name"] for s in systems if s["ip"] == ip)
    doc = client.get(f"/appdoc/systems/{name}", params={"tenant_id": tid}).json()
    shown = {r["Description"]: r for t in doc["tables"] for r in t["rows"]}
    return shown, {x["description"]: x for x in doc["impossible"]}


def test_contracts_ueber_die_externe_epg_mit_dem_laengsten_subnetz(client):
    tid = _seed(client)

    # hinter der Firewall im Servernetz: EN_srv (/24) ist genauer als 0.0.0.0/0
    shown, gone = _doc(client, tid, "10.20.0.5")
    srv = shown["CT_srv"]
    assert (srv["_side"], srv["_dir"], srv["Target Systems"]) == ("src", "out", "TE/AP1/EPG_web")
    assert srv["Bezug"] == "externe EPG L3O_fw|EN_srv (10.20.0.0/24)"
    assert (srv["_obj"], srv["_val"], srv["_scope"]) == (
        "externe EPG L3O_fw|EN_srv",
        "10.20.0.0/24",
        "subnet",
    )
    assert "CT_any" not in shown and "CT_inet" not in shown and "CT_rt" not in shown
    assert gone["CT_any"]["text"] == (
        "10.20.0.5 gehört in VRF TE:VRF1 zur externen EPG L3O_fw|EN_srv (10.20.0.0/24) – genauer "
        "als 0.0.0.0/0 von L3O_fw|EN_any"
    )
    assert "genauer als 0.0.0.0/0 von L3O_inet|EN_inet" in gone["CT_inet"]["text"]
    assert gone["CT_rt"]["text"] == (
        "Subnetz 10.20.0.0/16 der externen EPG L3O_fw|EN_rt steuert nur das Routing (Scope "
        "export-rtctrl) – es ordnet keinen Verkehr zu"
    )

    # ausserhalb des /24: 0.0.0.0/0 in zwei L3Outs - die VRF leitet 10.20.1.7 ueber L3O_fw
    shown, gone = _doc(client, tid, "10.20.1.7")
    assert shown["CT_any"]["Bezug"] == "externe EPG L3O_fw|EN_any (0.0.0.0/0)"
    assert shown["CT_any"]["_scope"] == "any" and shown["CT_any"]["_rel"] == "possible"
    inet = shown["CT_inet"]  # nicht ausgeschlossen, nur unbekannt
    assert (inet["_rel"], inet["_rel_text"]) == (
        "unknown",
        "0.0.0.0/0 auch bei L3O_fw|EN_any; die VRF TE:VRF1 leitet 10.20.1.7 über L3O_fw",
    )
    assert {k for k in gone if k.startswith("CT_")} == {"CT_rt"} and "CT_srv" not in shown

    # im Fabric: die IP gehoert zu ihrer EPG - externe EPGs fehlen in der Doku ganz
    shown, gone = _doc(client, tid, "10.50.1.10")
    assert not {"CT_any", "CT_inet", "CT_wide"} & (set(shown) | set(gone))

    # Applikations-Doku: Treffer mit Relevanz, Kennzahlen ohne die unmoeglichen
    body = {"name": "App", "members": [{"kind": "host", "ref": "10.20.0.5"}]}
    systems = client.get("/appdoc/systems", params={"tenant_id": tid}).json()
    body["members"][0]["ref"] = next(s["name"] for s in systems if s["ip"] == "10.20.0.5")
    app = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert app.status_code == 201, app.text
    out = client.get(f"/applications/{app.json()['id']}/doc", params={"tenant_id": tid}).json()
    rules = {r["id"]: r["name"] for r in out["rules"]}
    rel = {rules[h["rule_id"]]: h["relevance"] for h in out["hits"] if h["rule_id"] in rules}
    assert {k: rel[k] for k in ("CT_srv", "CT_any", "CT_inet", "CT_rt")} == {
        "CT_srv": "possible",
        "CT_any": "impossible",
        "CT_inet": "impossible",
        "CT_rt": "impossible",
    }


def test_regel_liste_contracts_fuer_eine_ip(client):
    """Suche nach einer IP: Contracts ueber externe EPGs tragen die Zuordnung (0.26.0 fuer
    Contracts); 0.0.0.0/0 zaehlt in der IP-Suche nicht."""
    tid = _seed(client)
    params = {"tenant_id": tid, "q": "10.20.0.5", "domain": "aci_contract"}
    r = client.get("/rules", params=params)
    rel = {x["name"]: x["relevance"] for x in r.json()}
    assert set(rel) == {"CT_srv", "CT_rt"}
    assert rel["CT_srv"] == {"state": "possible", "text": "", "sides": {"src": "possible"}}
    assert rel["CT_rt"]["state"] == "impossible" and "nur das Routing" in rel["CT_rt"]["text"]
    assert r.headers["X-Impossible-Count"] == "1"
    hidden = client.get("/rules", params={**params, "routable": True}).json()
    assert [x["name"] for x in hidden] == ["CT_srv"]

    # IP im Fabric: die externe EPG ordnet sie nicht zu, als Provider (EPG_web) gilt der
    # Contract aber - darum nicht ausgeschlossen
    fabric = client.get("/rules", params={**params, "q": "10.50.1.10"})
    wide = next(x["relevance"] for x in fabric.json() if x["name"] == "CT_wide")
    assert wide["sides"] == {"src": "impossible", "dst": "unknown"}
    assert wide["state"] == "unknown" and fabric.headers["X-Impossible-Count"] == "0"

    # Lage der externen EPG: Scope je Subnetz
    rid = next(x["id"] for x in r.json() if x["name"] == "CT_rt")
    detail = client.get(f"/rules/{rid}").json()
    (ext,) = (e["epg"] for e in detail["endpoints"] if e["role"] == "src")
    assert [(s["ip"], s["scope"]) for s in ext["subnets"]] == [("10.20.0.0/16", "export-rtctrl")]
