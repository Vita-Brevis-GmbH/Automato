# ruff: noqa: S104 - 0.0.0.0/0 sind Subnetze und Routen, keine Bind-Adressen
"""L3Outs: die Firewall entscheidet, nicht das ACI (ADR 0034, Befunde des Anwenders 2026-10-27).

Beispiel des Anwenders: im ACI bindet L3O_bit alle internen Netze an (externe EPG EN_bit
192.168.0.0/16), L3O_inet das Internet (EN_inet 0.0.0.0/0). Hinter jedem L3Out eine Firewall mit
eigenem Routing:

- fw-bit (Transfernetz 10.255.1.0/29 zu L3O_bit): 192.168.0.0/16 nach innen (internal), das
  Internet zurueck ins ACI;
- fw-inet (Transfernetz 10.255.2.0/29 zu L3O_inet): Internet ueber wan1, die DMZ
  192.168.50.0/24 angeschlossen, die uebrigen internen Netze zurueck ins ACI.

Die DMZ liegt im ACI im Subnetz von EN_bit - sie kommt aber ueber L3O_inet (die Firewall routet
sie dort), nicht ueber L3O_bit. Dazu L3O_vpn ohne Firewall dahinter (Peer ohne gelesenes
Geraet). Applikation: EPG_app (10.50.1.10) mit den Contracts CT_bit (EN_bit -> App, tcp/443,
161, 22), CT_inet (App -> EN_inet, tcp/443) und CT_vpn (EN_vpn -> App, tcp/22).
"""

import io
import zipfile

import test_paths as tp

ADMIN, OP, VIEW = tp.ADMIN, {"X-Dev-Roles": "operator"}, {"X-Dev-Roles": "viewer"}
EN = {"Accept-Language": "en"}
TE = "uni/tn-TE"
APP_DN = f"{TE}/ap-AP1/epg-EPG_app"
BIT, INET, VPN = (f"{TE}/out-{n}" for n in ("L3O_bit", "L3O_inet", "L3O_vpn"))
APP = "10.50.1.10"


def _l3out(name, svi, peer, ext):
    return {"tenant": "TE", "name": name, "dn": f"{TE}/out-{name}", "vrf": "TE:VRF1",
            "nodes": [{"pod": "1", "node": "101"}],
            "paths": [{"path": f"topology/pod-1/paths-101/pathep-[{name}]", "nodes": ["101"],
                       "interface": f"eth1/{len(name)}", "kind": "ext-svi",
                       "ips": [{"ip": svi}]}],
            "peers": [{"ip": peer, "asn": 65000}], "ext_epgs": [ext]}  # fmt: skip


def _ext(name, l3out, subnets):
    return {"name": name, "kind": "external", "dn": f"{TE}/out-{l3out}/instP-{name}",
            "tenant": "TE", "l3out": l3out, "vrf": "VRF1", "subnets": subnets}  # fmt: skip


def _contract(name, src, dst, *ports):
    return {"source_platform": "cisco_aci", "external_id": f"{TE}/brc-{name}/SJ", "name": name,
            "action": "allow", "sources": [f"epg:{src}"], "destinations": [f"epg:{dst}"],
            "services": [f"tcp/{p}" for p in ports], "aci_scope": "context",
            "raw": {"name": name, "kind": "contract", "scope": "context",
                    "tenant": "TE"}}  # fmt: skip


def _hop(ip):
    return [{"ip": ip, "interface": "vlan-1"}]


APIC = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "leaf-101", "role": "leaf", "node_id": "101"}],
        "subnets": [
            {"prefix": "10.50.1.0/24", "gateway": "10.50.1.1/24", "vrf": "TE:VRF1",
             "context": "epg", "context_name": "EPG_app", "aci_tenant": "TE", "ap": "AP1",
             "epg": "EPG_app"},
        ],
    },
    "routing": {
        "contexts": [{"node": "leaf-101", "context": "TE:VRF1", "kind": "vrf"}],
        # das ACI routet weit offen: alle internen Netze ueber L3O_bit, alles andere ueber inet
        "routes": [
            tp._r("leaf-101", "TE:VRF1", "192.168.0.0/16", _hop("10.255.1.1"), "bgp", distance=20),
            tp._r("leaf-101", "TE:VRF1", "0.0.0.0/0", _hop("10.255.2.1"), "bgp", distance=20),
            tp._r("leaf-101", "TE:VRF1", "10.99.0.0/16", _hop("10.255.3.1"), "bgp", distance=20),
        ],
        "l3outs": [
            _l3out("L3O_bit", "10.255.1.2/29", "10.255.1.1", "EN_bit"),
            _l3out("L3O_inet", "10.255.2.2/29", "10.255.2.1", "EN_inet"),
            _l3out("L3O_vpn", "10.255.3.2/29", "10.255.3.1", "EN_vpn"),
        ],
    },
    "epgs": [
        {"name": "EPG_app", "dn": APP_DN, "tenant": "TE", "ap": "AP1", "bd": "BD_app",
         "vrf": "VRF1", "bd_subnets": ["10.50.1.1/24"], "endpoints": [{"ip": APP}]},
        _ext("EN_bit", "L3O_bit", ["192.168.0.0/16"]),
        _ext("EN_inet", "L3O_inet", ["0.0.0.0/0"]),
        _ext("EN_vpn", "L3O_vpn", ["10.99.0.0/16"]),
    ],
    "rules": [
        _contract("CT_bit", f"{BIT}/instP-EN_bit", APP_DN, 443, 161, 22),
        _contract("CT_inet", APP_DN, f"{INET}/instP-EN_inet", 443),
        _contract("CT_vpn", f"{VPN}/instP-EN_vpn", APP_DN, 22),
    ],
}  # fmt: skip


def _rule(ext, name, src, dst, services, srcintf, dstintf, zones, refs=((), ())):
    return {"source_platform": "fortigate", "external_id": ext, "name": name, "action": "allow",
            "sources": [src], "destinations": [dst], "services": services,
            "source_refs": list(refs[0]), "destination_refs": list(refs[1]),
            "src_zone": zones[0], "dst_zone": zones[1], "vdom": "root", "family": "v4",
            "raw": {"policyid": int(ext), "srcintf": [{"name": srcintf}],
                    "dstintf": [{"name": dstintf}], "status": "enable"}}  # fmt: skip


OBJECTS = [
    {"name": "H_app", "category": "address", "family": "v4", "values": [APP]},
    {"name": "N_dc", "category": "address", "family": "v4", "values": ["10.50.0.0/16"]},
    {"name": "N_bit", "category": "address", "family": "v4", "values": ["192.168.0.0/16"]},
    {"name": "H_mon", "category": "address", "family": "v4", "values": ["192.168.9.9"]},
]


def _fw_inet(services=("tcp/443",)) -> dict:
    return {
        "status": "completed",
        "inventory": {
            "devices": [{"name": "fw-inet", "role": "firewall"}],
            "interfaces": [
                tp._if("fw-inet", "to-aci", "10.255.2.1/29", "aci"),
                tp._if("fw-inet", "wan1", "198.51.100.2/30", "inet"),
                tp._if("fw-inet", "dmz", "192.168.50.1/24", "dmz"),
            ],
        },
        "routing": {
            "contexts": [{"node": "fw-inet", "context": "root"}],
            "routes": [
                tp._r("fw-inet", "root", "10.255.2.0/29", [{"interface": "to-aci"}], "connected"),
                tp._r("fw-inet", "root", "198.51.100.0/30", [{"interface": "wan1"}], "connected"),
                tp._r("fw-inet", "root", "192.168.50.0/24", [{"interface": "dmz"}], "connected"),
                tp._r("fw-inet", "root", "0.0.0.0/0",
                      [{"ip": "198.51.100.1", "interface": "wan1"}]),
                tp._r("fw-inet", "root", "10.50.0.0/16",
                      [{"ip": "10.255.2.2", "interface": "to-aci"}]),
                tp._r("fw-inet", "root", "192.168.0.0/16",
                      [{"ip": "10.255.2.2", "interface": "to-aci"}]),
            ],
        },
        "objects": OBJECTS,
        "rules": [
            _rule("31", "app-inet", APP, "all", list(services), "to-aci", "wan1", ("aci", "inet"),
                  (["H_app"], ["all"])),
            # unspezifisch (Netz des Rechenzentrums) aus dem Internet - ueber L3O_inet
            _rule("32", "ext-dc", "all", "10.50.0.0/16", ["tcp/80"], "wan1", "to-aci",
                  ("ext", "aci"), (["all"], ["N_dc"])),
        ],
    }  # fmt: skip


FW_BIT = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "fw-bit", "role": "firewall"}],
        "interfaces": [
            tp._if("fw-bit", "to-aci", "10.255.1.1/29", "aci"),
            tp._if("fw-bit", "internal", "192.168.1.1/24", "bit"),
            tp._if("fw-bit", "vpn-aci", "10.255.4.1/29", "vpn"),
        ],
    },
    "routing": {
        "contexts": [{"node": "fw-bit", "context": "root"}],
        "routes": [
            tp._r("fw-bit", "root", "10.255.1.0/29", [{"interface": "to-aci"}], "connected"),
            tp._r("fw-bit", "root", "192.168.1.0/24", [{"interface": "internal"}], "connected"),
            tp._r("fw-bit", "root", "10.255.4.0/29", [{"interface": "vpn-aci"}], "connected"),
            tp._r("fw-bit", "root", "192.168.0.0/16",
                  [{"ip": "192.168.1.254", "interface": "internal"}]),
            tp._r("fw-bit", "root", "10.50.0.0/16", [{"ip": "10.255.1.2", "interface": "to-aci"}]),
            tp._r("fw-bit", "root", "0.0.0.0/0", [{"ip": "10.255.1.2", "interface": "to-aci"}]),
        ],
    },
    "objects": OBJECTS,
    "rules": [
        _rule("11", "bit-app", "192.168.0.0/16", APP, ["tcp/443"], "internal", "to-aci",
              ("bit", "aci"), (["N_bit"], ["H_app"])),
        # Kategorie 10: spezifisch (Monitoring) und unspezifisch (Netz des RZ) - ueber L3O_bit
        _rule("12", "mon-app", "192.168.9.9", APP, ["tcp/161"], "internal", "to-aci",
              ("lan", "aci"), (["H_mon"], ["H_app"])),
        _rule("13", "lan-dc", "all", "10.50.0.0/16", ["tcp/22"], "internal", "to-aci",
              ("lan", "aci"), (["all"], ["N_dc"])),
    ],
}  # fmt: skip


def _seed(client) -> dict:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=ADMIN).json()["id"]
    ids = {"tid": tid}
    # fw-inet zuerst: seine Regeln stehen vor denen von fw-bit (Sortierung nach L3Out pruefen)
    for name, platform, result in (("fw-inet", "fortigate", _fw_inet()),
                                   ("fw-bit", "fortigate", FW_BIT),
                                   ("apic-01", "cisco_aci", APIC)):  # fmt: skip
        ids[name] = tp._system(client, tid, name, platform)
        tp._deliver(client, tid, ids[name], platform, result)
    body = {"name": "App", "members": [{"kind": "epg", "ref": APP_DN}]}
    r = client.post(f"/applications?tenant_id={tid}", json=body, headers=OP)
    assert r.status_code == 201, r.text
    ids["app"] = r.json()["id"]
    return ids


def _l3outs(client, tid, headers=None) -> dict[str, dict]:
    r = client.get("/routing/l3outs", params={"tenant_id": tid}, headers=headers or {})
    assert r.status_code == 200, r.text
    return {o["name"]: o for o in r.json()}


def _doc(client, aid, headers=None) -> dict:
    r = client.get(f"/applications/{aid}/doc", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def _rows(doc: dict, index: int) -> list[dict]:
    return next(t["rows"] for t in doc["tables"] if t["index"] == index)


def _by_name(doc: dict, index: int) -> dict[str, dict]:
    return {r["Description"]: r for r in _rows(doc, index)}


def _put(client, ids, dn, headers=OP, **body):
    return client.put("/routing/l3outs/firewall", headers=headers,
                      json={"tenant_id": ids["tid"], "source_system_id": ids["apic-01"],
                            "dn": dn, **body})  # fmt: skip


def test_firewall_hinter_dem_l3out_abgeleitet(client):
    ids = _seed(client)
    outs = _l3outs(client, ids["tid"])
    bit, inet, vpn = (outs[n]["behind"] for n in ("L3O_bit", "L3O_inet", "L3O_vpn"))
    # Transfernetz bzw. BGP-Peer = Interface der Firewall
    assert bit["origin"] == "derived" and [
        (f["label"], f["interface"], f["zone"]) for f in bit["firewalls"]
    ] == [("fw-bit", "to-aci", "aci")]
    assert [(f["label"], f["interface"]) for f in inet["firewalls"]] == [("fw-inet", "to-aci")]
    # Peer ohne gelesenes Geraet: keine Firewall
    assert vpn["firewalls"] == [] and vpn["derived"] == []
    # Auswahl: Firewalls mit Interfaces
    choices = client.get("/routing/l3outs/firewall-choices", params={"tenant_id": ids["tid"]})
    assert choices.status_code == 200
    by = {c["label"]: c for c in choices.json()}
    assert set(by) == {"fw-bit", "fw-inet"}
    assert {i["name"] for i in by["fw-bit"]["interfaces"]} == {"to-aci", "internal", "vpn-aci"}


def test_gegenstelle_folgt_der_firewall_nicht_dem_aci(client):
    ids = _seed(client)
    tid = ids["tid"]

    def relevance(ip):
        params = {"tenant_id": tid, "q": ip, "domain": "aci_contract"}
        return {x["name"]: x["relevance"] for x in client.get("/rules", params=params).json()}

    # internes Netz: fw-bit routet es nach innen, fw-inet zurueck ins ACI -> L3O_bit
    rel = relevance("192.168.10.5")
    assert rel["CT_bit"]["state"] == "possible"
    assert rel["CT_bit"]["text"] == "über L3O_bit: fw-bit routet 192.168.0.0/16 über internal"
    # DMZ: im ACI im Subnetz von EN_bit (192.168.0.0/16), die Firewall fw-inet hat sie aber
    # angeschlossen - genauer als fw-bit -> sie kommt ueber L3O_inet, nicht ueber L3O_bit
    rel = relevance("192.168.50.10")
    assert rel["CT_bit"]["state"] == "impossible"
    assert rel["CT_bit"]["text"] == (
        "192.168.50.10 kommt über L3O_inet: fw-inet routet 192.168.50.0/24 über dmz – nicht über "
        "L3O_bit"
    )
    # Weg aus der DMZ zur Applikation: im ACI die externe EPG von L3O_inet - dort kein Contract
    r = client.get("/routing/trace", params={"tenant_id": tid, "src": "192.168.50.10", "dst": APP,
                                             "service": "tcp/443"})  # fmt: skip
    assert r.status_code == 200, r.text
    (path,) = r.json()["paths"]
    assert [h["label"] for h in path["hops"]] == ["fw-inet", "apic-01 · VRF TE:VRF1"]
    aci = path["hops"][1]["rules"]
    assert aci["src_epg"] == "externe EPG L3O_inet|EN_inet (0.0.0.0/0)"
    assert aci["verdict"] == "implicit_deny"
    # aus dem internen Netz: EN_bit mit CT_bit
    r = client.get("/routing/trace", params={"tenant_id": tid, "src": "192.168.1.20", "dst": APP,
                                             "service": "tcp/443"})  # fmt: skip
    (path,) = r.json()["paths"]
    aci = path["hops"][-1]["rules"]
    assert aci["src_epg"] == "externe EPG L3O_bit|EN_bit (192.168.0.0/16)"
    assert aci["verdict"] == "allow"

    # Englisch
    params = {"tenant_id": tid, "q": "192.168.50.10", "domain": "aci_contract"}
    en = {x["name"]: x["relevance"] for x in client.get("/rules", params=params, headers=EN).json()}
    assert en["CT_bit"]["text"] == (
        "192.168.50.10 comes via L3O_inet: fw-inet routes 192.168.50.0/24 via dmz – not via "
        "L3O_bit"
    )


def test_hilfs_contracts_an_der_firewall_regel(client):
    ids = _seed(client)
    doc = _doc(client, ids["app"])
    names = {r["id"]: r["name"] for r in doc["rules"]}
    # Contracts zu externen EPGs mit Firewall dahinter: keine eigene Regel der Doku
    assert "CT_bit" not in names.values() and "CT_inet" not in names.values()
    assert {"bit-app", "app-inet", "ext-dc", "lan-dc", "mon-app"} <= set(names.values())
    helpers = {h["name"]: h for h in doc["helpers"]}
    assert set(helpers) == {"CT_bit", "CT_inet"} and doc["summary"]["helpers"] == 2
    assert helpers["CT_bit"]["l3outs"] == ["L3O_bit"]
    # an allen erlaubenden Regeln von fw-bit Richtung Applikation
    assert {names[r] for r in helpers["CT_bit"]["firewall_rules"]} == {"bit-app", "mon-app",
                                                                        "lan-dc"}  # fmt: skip
    # Treffer bleiben, als Hilfs-Contract markiert
    assert all(h["helper"] for h in doc["hits"] if h["rule_id"] in
               {helpers["CT_bit"]["rule_id"], helpers["CT_inet"]["rule_id"]})  # fmt: skip
    # an der Firewall-Regel vermerkt: Spalte L3Out mit dem Hilfs-Contract
    internet = _by_name(doc, 1)
    assert set(internet) == {"app-inet"}
    assert internet["app-inet"]["L3Out"] == "L3O_inet · Contract CT_inet"
    cat = next(t for t in doc["tables"] if t["index"] == 1)
    assert "L3Out" in cat["columns"]
    # „bit“ ist seit 0.106.0 kein Merkmal mehr (kein Zonentyp im Grundschema): unspezifisch
    assert _by_name(doc, 10)["bit-app"]["L3Out"] == "L3O_bit · Contract CT_bit"
    assert _by_name(doc, 10)["lan-dc"]["L3Out"] == "L3O_bit · Contract CT_bit"
    assert _by_name(doc, 10)["ext-dc"]["L3Out"] == "L3O_inet"  # kein Contract von dort
    hit = next(h for h in doc["hits"] if names.get(h["rule_id"]) == "bit-app")
    assert hit["l3out"] == "L3O_bit" and hit["helpers"][0]["names"] == ["CT_bit"]

    # Contract ohne Firewall hinter dem L3Out (L3O_vpn): bleibt sichtbar, mit Hinweis
    assert "CT_vpn" in names.values()
    vpn = _by_name(doc, 5)["CT_vpn"]
    assert vpn["L3Out"] == "L3O_vpn (ohne Firewall)"
    (note,) = {h["l3out_note"] for h in doc["hits"] if names.get(h["rule_id"]) == "CT_vpn"}
    assert note == "keine Firewall hinter L3O_vpn – der Contract regelt allein"

    # Verbindungen Ende zu Ende: der Hilfs-Contract ist keine eigene Verbindung, prueft aber im
    # ACI-Hop der Verbindung der Firewall-Regel mit
    conns = client.get(f"/applications/{ids['app']}/connections").json()["connections"]
    by = {(c["src"]["ip"], c["dst"]["ip"], c["service"]): c for c in conns}
    assert ("10.50.1.10", "0.0.0.0/0", "tcp/443") not in by
    https = by[("192.168.0.0/16", APP, "tcp/443")]
    states = {s["label"]: s["state"] for s in https["rules"]}
    # Start auf fw-bit (das Netz haengt an keinem gelesenen Interface): die Policy ist moeglich
    assert set(states) == {"Policy 11 „bit-app“", "Contract CT_bit"}
    assert states["Contract CT_bit"] == "decides"
    (aci,) = (h for h in https["hops"] if h["kind"] == "vrf")
    assert aci["checks"][0]["src_epg"] == "externe EPG L3O_bit|EN_bit (192.168.0.0/16)"

    # Export (Word): Spalte L3Out mit dem Hilfs-Contract, keine eigene Zeile des Contracts
    r = client.get(f"/applications/{ids['app']}/export",
                   params={"format": "docx", "sections": "categories,rules"})  # fmt: skip
    assert r.status_code == 200, r.text
    body = zipfile.ZipFile(io.BytesIO(r.content)).read("word/document.xml").decode()
    assert "L3O_inet · Contract CT_inet" in body and "CT_vpn" in body

    # Englisch: Spalte und Hinweis
    en = _doc(client, ids["app"], EN)
    assert _by_name(en, 5)["CT_vpn"]["L3Out"] == "L3O_vpn (no firewall)"
    assert {h["l3out_note"] for h in en["hits"] if h["l3out_note"]} == {
        "no firewall behind L3O_vpn – the contract alone controls access"
    }


def test_hilfs_contract_enger_als_die_firewall_bleibt_befund(client):
    ids = _seed(client)
    tid = ids["tid"]

    def narrower() -> list[dict]:
        r = client.get("/findings", params={"tenant_id": tid, "type": "app_contract_narrower"})
        assert r.status_code == 200, r.text
        return r.json()

    assert narrower() == []
    # die Firewall oeffnet zusaetzlich tcp/8443 - der Contract nicht: er sperrt real
    tp._deliver(client, tid, ids["fw-inet"], "fortigate", _fw_inet(("tcp/443", "tcp/8443")))
    doc = _doc(client, ids["app"])
    row = _by_name(doc, 1)["app-inet"]
    assert row["L3Out"] == "L3O_inet · Contract CT_inet – es fehlt tcp/8443"
    assert not any(k.startswith("CT_inet") for k in _by_name(doc, 1))  # keine eigene Zeile
    (entry,) = doc["narrower"]
    assert entry["missing"] == ["tcp/8443"]
    (f,) = narrower()
    assert f["subject_id"] == "TE/CT_inet · Applikation App"
    assert [x["name"] for x in f["detail"]["firewall_rules"]] == ["app-inet"]
    en = _doc(client, ids["app"], EN)
    assert _by_name(en, 1)["app-inet"]["L3Out"] == "L3O_inet · Contract CT_inet – missing tcp/8443"
    # wieder abgedeckt: Befund weg
    tp._deliver(client, tid, ids["fw-inet"], "fortigate", _fw_inet())
    assert narrower() == []


def test_unspezifische_zugriffe_nach_l3out(client):
    ids = _seed(client)
    doc = _doc(client, ids["app"])
    rows = _rows(doc, 10)
    # spezifische zuerst, dann die unspezifischen je L3Out (fw-inet steht im Regelwerk vorn)
    # seit 0.106.0 auch bit-app (L3O_bit ist kein Zonentyp im Grundschema)
    assert [r["Description"] for r in rows] == ["bit-app", "mon-app", "lan-dc", "ext-dc"]
    assert [r["_l3out_in"] for r in rows] == ["L3O_bit", "L3O_bit", "L3O_bit", "L3O_inet"]
    assert [r["_scope"] for r in rows] == ["specific", "specific", "subnet", "subnet"]
    # gleich in der Doku je System (EPG)
    systems = client.get("/appdoc/systems", params={"tenant_id": ids["tid"]}).json()
    name = next(s["name"] for s in systems if s["kind"] == "epg")
    system = client.get(f"/appdoc/systems/{name}", params={"tenant_id": ids["tid"]}).json()
    cat = next(t for t in system["tables"] if t["index"] == 10)
    assert [r["Description"] for r in cat["rows"]] == ["bit-app", "mon-app", "lan-dc", "ext-dc"]
    assert "L3Out" in cat["columns"]


def test_zuordnung_von_hand(client, session_factory):
    ids = _seed(client)
    tid = ids["tid"]
    # bestaetigen: Herkunft confirmed, gleiche Firewall
    r = _put(client, ids, BIT, confirm=True)
    assert r.status_code == 200, r.text
    assert r.json()["behind"]["origin"] == "confirmed"
    # L3O_vpn von Hand: fw-bit ueber vpn-aci - CT_vpn wird Hilfs-Contract (ohne Firewall-Regel
    # dort: nirgends vermerkt, nur Treffer)
    r = _put(client, ids, VPN, firewalls=[{"source_system_id": ids["fw-bit"], "vdom": "root",
                                           "interface": "vpn-aci"}])  # fmt: skip
    assert r.status_code == 200, r.text
    behind = r.json()["behind"]
    assert behind["origin"] == "manual"
    assert [(f["label"], f["interface"], f["zone"]) for f in behind["firewalls"]] == [
        ("fw-bit", "vpn-aci", "vpn")
    ]
    doc = _doc(client, ids["app"])
    assert "CT_vpn" not in {r["name"] for r in doc["rules"]}
    assert "CT_vpn" not in _by_name(doc, 5)
    # bit-app bleibt bei L3O_bit (Interface to-aci), nicht bei L3O_vpn
    assert _by_name(doc, 10)["bit-app"]["L3Out"] == "L3O_bit · Contract CT_bit"
    # L3O_inet: ausdruecklich keine Firewall - CT_inet regelt allein und steht da
    r = _put(client, ids, INET, none=True)
    assert r.status_code == 200 and r.json()["behind"]["origin"] == "none"
    assert r.json()["behind"]["firewalls"] == []
    doc = _doc(client, ids["app"])
    internet = _by_name(doc, 1)
    assert internet["CT_inet"]["L3Out"] == "L3O_inet (ohne Firewall)"
    assert "L3Out" not in internet["app-inet"] or not internet["app-inet"]["L3Out"]
    assert "CT_inet" in {r["name"] for r in doc["rules"]}
    # ohne Firewall dahinter: Rueckfall auf das ACI (0.0.0.0/0, nur EN_inet erfasst die IP)
    params = {"tenant_id": tid, "q": "192.168.50.10", "domain": "aci_contract"}
    rel = {x["name"]: x["relevance"] for x in client.get("/rules", params=params).json()}
    assert rel["CT_bit"]["state"] == "possible"  # das ACI ordnet die DMZ EN_bit zu
    # entfernen: wieder abgeleitet (fw-inet)
    r = client.delete(
        "/routing/l3outs/firewall",
        headers=OP,
        params={"tenant_id": tid, "source_system_id": ids["apic-01"], "dn": INET},
    )
    assert r.status_code == 200 and r.json()["removed"] == 1
    assert r.json()["behind"]["origin"] == "derived"
    doc = _doc(client, ids["app"])
    assert set(_by_name(doc, 1)) == {"app-inet"}

    # Audit
    from app.models import AuditLog

    s = session_factory()
    actions = [a.action for a in s.query(AuditLog).filter(
        AuditLog.action.like("routing.l3out_firewall.%")).order_by(AuditLog.id)]  # fmt: skip
    assert actions == ["routing.l3out_firewall.set"] * 3 + ["routing.l3out_firewall.removed"]
    entry = s.query(AuditLog).filter(AuditLog.action == "routing.l3out_firewall.set").first()
    assert entry.detail["l3out"] == "L3O_bit" and entry.detail["origin"] == "confirmed"
    assert "_dropped" not in entry.detail

    # Pruefungen: Firewall bzw. Interface unbekannt, L3Out unbekannt, Rechte
    bad = _put(client, ids, VPN, firewalls=[{"source_system_id": ids["apic-01"], "vdom": "x"}])
    assert bad.status_code == 422
    bad = _put(client, ids, VPN, firewalls=[{"source_system_id": ids["fw-bit"], "vdom": "root",
                                             "interface": "nope"}])  # fmt: skip
    assert bad.status_code == 422 and "nope" in bad.json()["detail"]
    bad = _put(
        client,
        ids,
        VPN,
        headers={**OP, **EN},
        firewalls=[{"source_system_id": ids["fw-bit"], "vdom": "root", "interface": "nope"}],
    )
    assert bad.json()["detail"] == "interface nope does not exist on fw-bit"
    assert _put(client, ids, VPN).status_code == 422  # nichts gewaehlt
    assert _put(client, ids, f"{TE}/out-L3O_x", none=True).status_code == 404
    assert _put(client, ids, VPN, headers=VIEW, none=True).status_code == 403
    # Kunden-Benutzer: Routing nicht freigegeben (fail-closed)
    customer = {"X-Dev-Roles": "customer", "X-Dev-Customers": "1"}
    assert _put(client, ids, VPN, headers=customer, none=True).status_code == 403
    r = client.get("/routing/l3outs", params={"tenant_id": tid}, headers=customer)
    assert r.status_code == 403


def test_l3out_firewall_beim_loeschen_der_firewall(client, session_factory):
    ids = _seed(client)
    _put(client, ids, VPN, firewalls=[{"source_system_id": ids["fw-bit"], "vdom": "root",
                                       "interface": "vpn-aci"}])  # fmt: skip
    r = client.delete(f"/systems/{ids['fw-bit']}", headers=ADMIN)
    assert r.status_code in (200, 204), r.text
    from app.models import L3OutFirewall

    assert session_factory().query(L3OutFirewall).count() == 0
    assert _l3outs(client, ids["tid"])["L3O_vpn"]["behind"]["origin"] == "derived"
