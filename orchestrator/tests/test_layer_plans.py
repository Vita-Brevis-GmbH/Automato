# ruff: noqa: S104 - 0.0.0.0/0 sind Routen bzw. Selektoren, keine Bind-Adressen
"""Plaene je Netzwerkschicht (1.23.0, ADR 0067 Nachtrag 1.23.0): L1 Physik, L2 Switching, L3
Routing fuer den ganzen Mandanten aus den gelesenen Daten, Filter nach Standort, Grenzen gezaehlt,
Export draw.io mit Schicht und Standort im Dateinamen, Rechte (lesen, ADR 0068).

Daten: L1 wie `test_cable_labels` (Kabelnummer K-0815 aus NetBox, K-0816 nur in NetBox), L2 wie
`test_stp` (sw-core-01 Root, blockierte Ports), L3 wie `test_vpn` (fw-a und fw-b mit IPsec, ein
Tunnel zu einer fremden Gegenstelle, Dial-up) mit einer statischen Route und BGP.
"""

import copy
import xml.etree.ElementTree as ET

import pytest
import test_cable_labels as tcl
import test_paths as tp
import test_stp as tstp
import test_vpn as tv
from app.store import layer_plans

ADMIN = {"X-Dev-Roles": "admin"}


def _plan(client, tid, layer, headers=None, **params):
    r = client.get(f"/network-plans/{layer}", params={"tenant_id": tid, **params},
                   headers=headers or {})  # fmt: skip
    assert r.status_code == 200, r.text
    return r.json()


def _node(plan, label):
    return next(n for n in plan["nodes"] if n["label"] == label)


def _edges(plan, a, b):
    ids = {_node(plan, a)["id"], _node(plan, b)["id"]}
    return [e for e in plan["edges"] if {e["a"], e["b"]} == ids]


def test_vlan_kuerzung():
    assert layer_plans.vlan_short({10, 20, 30, 31, 32, 50}) == "10,20,30-32,50"
    assert layer_plans.vlan_short({1, 3, 5, 7, 9, 10, 11}) == "1,3,5,7 +3"


def test_l1_geraete_kabel_ports_und_kabelnummern(client):
    ids = tcl._setup(client)
    plan = _plan(client, ids["tid"], "l1")
    names = {n["label"]: n for n in plan["nodes"]}
    assert {"sw-acc-01", "sw-core-01", "fw-08", "leaf-101", "patch-01"} <= set(names)
    assert names["sw-acc-01"]["read"] and names["sw-acc-01"]["kind"] == "switch"
    assert names["sw-acc-01"]["link"] == {"view": "verkabelung", "params": {"device": "sw-acc-01"}}
    # fw-08 liest Automato nicht (nur CDP/LLDP) - Ebene Endgeraete; Kern oben
    assert not names["fw-08"]["read"] and names["fw-08"]["layer"] == "ends"
    assert names["sw-core-01"]["y"] < names["fw-08"]["y"]
    # Port-Channel Po1 <-> Po10 gebuendelt mit Mitgliedern, Kabelnummer aus NetBox
    edges = _edges(plan, "sw-acc-01", "sw-core-01")
    lag = next(e for e in edges if e["kind"] == "lag" and "Po10" in (e["end_b"] or ""))
    assert lag["end_a"] == "Po1: Gi1/0/47" and lag["end_b"] == "Po10: Eth1/1"
    assert {"text": "Nr. K-0815", "layer": "labels"} in lag["texts"]
    assert lag["link"] == {"view": "verkabelung", "params": {"tab": "labels", "q": "K-0815"}}
    # Gi1/0/48 <-> Eth1/2: Port-Channel nur auf einer Seite - eigene Linie mit Abweichung
    single = next(e for e in edges if e["end_b"] == "Eth1/2")
    assert single["tone"] == "warn" and any("Port-Channel nur auf" in i for i in single["info"])
    # K-0816 steht nur in NetBox (CDP meldet Eth1/2): gestrichelt in der Ebene „netbox“
    nb = [e for e in plan["edges"] if e["kind"] == "netbox"]
    assert {(e["label"], e["end_b"]) for e in nb} >= {("K-0816", "Ethernet1/9")}
    assert all(e["dash"] and e["layer"] == "netbox" for e in nb)
    assert names["patch-01"]["kind"] == "netbox" and names["patch-01"]["layer"] == "netbox"
    assert plan["limits"]["netbox_only"] == 2
    keys = {x["key"] for x in plan["layers"] if x["used"]}
    assert {"ports", "labels", "ends", "netbox"} <= keys
    # Linien mit Punkten am Rand der Elemente und Ports nahe den Enden
    assert len(lag["points"]) == 2 and lag["pa"]["text"] == "Po1: Gi1/0/47"


def test_l2_trunks_vlans_root_und_blockierte_ports(client):
    tid, _sw = tstp._setup(client)
    plan = _plan(client, tid, "l2")
    names = {n["label"]: n for n in plan["nodes"]}
    # weiterleitende Geraete: Switches und fw-08 (meldet sich per LLDP als FortiGate - seit
    # 1.23.0 als Firewall erkannt, nicht gelesen)
    assert {"sw-acc-01", "sw-core-01", "sw-cbs-01"} <= set(names)
    assert names["fw-08"]["sub"] == "Firewall (nicht gelesen)" and not names["fw-08"]["read"]
    # Root je VLAN: sw-core-01 fuer 10, 20, 30, sw-acc-01 fuer 40
    core = [b for b in names["sw-core-01"]["badges"] if b["layer"] == "stp"]
    assert core == [{"text": "Root VLAN 10,20,30", "tone": "root", "layer": "stp"}]
    assert any(b["text"] == "Root VLAN 40" for b in names["sw-acc-01"]["badges"])
    assert names["sw-core-01"]["link"] == {"view": "spanningtree", "params": {"q": "sw-core-01"}}
    # Switch ohne Spanning Tree gezaehlt
    assert any("nicht unterstützt" in b["text"] for b in names["sw-cbs-01"]["badges"])
    assert plan["limits"]["stp_unread"] == 1 and plan["notes"]
    # Trunk mit erlaubten VLANs (beide Enden), Port-Channel gebuendelt
    edges = _edges(plan, "sw-acc-01", "sw-core-01")
    po = next(e for e in edges if e["end_b"] == "Po10: Eth1/1")
    assert {"text": "Trunk 10,20,30", "layer": "vlans"} in po["texts"]
    # Eth1/2: VLANs bzw. Native VLAN abweichend
    eth2 = next(e for e in edges if e["end_b"] == "Eth1/2")
    assert eth2["tone"] in ("warn", "stp") and any("abweichend" in i for i in eth2["info"])
    # Te1/1/1 zur Firewall blockiert VLAN 30 (Spanning Tree der Switches)
    (fw,) = _edges(plan, "sw-acc-01", "fw-08")
    assert fw["tone"] == "stp" and fw["dash"]
    assert {"text": "✕ STP blockiert sw-acc-01 Te1/1/1: VLAN 30", "layer": "stp"} in fw["texts"]


def _l3_seed(client):
    fwa = copy.deepcopy(tv.FWA)
    fwa["routing"]["routes"].append(
        tp._r(tv.FW_A, "root", "10.40.0.0/16", [{"ip": "203.0.113.2", "interface": "wan1"}],
              table="static"))  # fmt: skip
    fwa["routing"]["peers"] = [{"node": tv.FW_A, "context": "root", "peer_ip": "203.0.113.2",
                                "remote_as": 65001, "state": "Established"}]  # fmt: skip
    return tv._seed(client, fwa=fwa)


def test_l3_firewalls_netze_routen_bgp_und_ipsec(client):
    tid, sids = _l3_seed(client)
    plan = _plan(client, tid, "l3")
    names = {n["label"]: n for n in plan["nodes"]}
    a, b = names[tv.FW_A], names[tv.FW_B]
    assert a["kind"] == b["kind"] == "firewall"
    ctx = f"{sids[tv.FW_A]}|root"
    assert a["link"] == {"view": "routing", "params": {"tab": "routes", "context": ctx}}
    # IPsec zwischen den beiden gelesenen FortiGates: eine Linie (beide Enden gesehen)
    (tunnel,) = (e for e in _edges(plan, tv.FW_A, tv.FW_B) if e["kind"] == "ipsec")
    assert tunnel["layer"] == "ipsec" and tunnel["tone"] == "vpn"
    assert tunnel["label"] in ("IPsec vpn-b / vpn-a", "IPsec vpn-a / vpn-b")
    assert tunnel["link"]["view"] == "vpn"
    # Gegenstelle nicht gelesen: Adresse als eigenes Element, Tunnel down rot
    partner = names["192.0.2.77"]
    assert partner["kind"] == "vpn_peer" and partner["layer"] == "ipsec"
    (down,) = _edges(plan, tv.FW_A, "192.0.2.77")
    assert down["tone"] == "bad" and down["dash"]
    assert any(n["kind"] == "vpn_peer" and n["label"].startswith("Dial-up") for n in plan["nodes"])
    # Netze an einem Knoten (LAN) als Elemente der Ebene „Netze“
    lan = names["10.10.0.0/24"]
    assert lan["kind"] == "net" and lan["layer"] == "nets"
    (att,) = _edges(plan, tv.FW_A, "10.10.0.0/24")
    assert att["end_a"] == "lan 10.10.0.1"
    # Router hinter wan1 mit BGP und statischer Route
    router = next(n for n in plan["nodes"] if n["kind"] == "router")
    assert router["sub"] == "203.0.113.2 · AS 65001"
    (up,) = _edges(plan, tv.FW_A, router["label"])
    assert {"text": "BGP AS 65001 (Established)", "layer": "bgp"} in up["texts"]
    assert {"text": f"1 statisch {tv.FW_A} → {router['label']}", "layer": "static"} in up["texts"]
    assert {x["key"] for x in plan["layers"] if x["used"]} >= {"nets", "ipsec", "bgp", "static"}


def _sites(client, tid) -> dict:
    """Zwei physische Standorte (mit nur einem gilt er fuer alle Quellsysteme ohne Angabe)."""
    zrh = client.post(f"/tenants/{tid}/sites", json={"code": "chzrh1", "name": "Zürich"},
                      headers=ADMIN).json()  # fmt: skip
    client.post(f"/tenants/{tid}/sites", json={"code": "chbsl1", "name": "Basel"}, headers=ADMIN)
    return zrh


def test_standort_filter_nachbarn_blass_und_grenze(client, monkeypatch):
    tid, sids = _l3_seed(client)
    site = _sites(client, tid)
    r = client.patch(f"/systems/{sids[tv.FW_A]}", json={"site_id": site["id"]}, headers=ADMIN)
    assert r.status_code == 200, r.text
    whole = _plan(client, tid, "l3")
    assert [g["title"] for g in whole["groups"]] == ["chzrh1 · Zürich", "ohne Standort"]
    assert [s["code"] for s in whole["sites"]] == ["chbsl1", "chzrh1"]
    # Zuerich: fw-a mit seinen Netzen und Gegenstellen; fw-b nur blass als Nachbar (Tunnel)
    zrh = _plan(client, tid, "l3", site=site["id"])
    names = {n["label"]: n for n in zrh["nodes"]}
    assert not names[tv.FW_A]["foreign"] and names[tv.FW_B]["foreign"]
    assert "10.20.0.0/24" not in names  # Netz von fw-b gehoert nicht dazu
    assert names["10.10.0.0/24"]["sites"] == [site["id"]]
    assert zrh["summary"]["foreign"] >= 1
    # ohne Standort: fw-b und sein LAN, fw-a als Nachbar
    none = _plan(client, tid, "l3", site=0)
    assert {n["label"] for n in none["nodes"] if not n["foreign"]} >= {tv.FW_B, "10.20.0.0/24"}
    assert _node(none, tv.FW_A)["foreign"]
    # fremder bzw. unbekannter Standort
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    alien = client.post(f"/tenants/{other}/sites", json={"code": "chbrn1", "name": "Bern"},
                        headers=ADMIN).json()  # fmt: skip
    r = client.get("/network-plans/l3", params={"tenant_id": tid, "site": alien["id"]})
    assert r.status_code == 404
    assert client.get("/network-plans/l4", params={"tenant_id": tid}).status_code == 422
    # keine stille Grenze: darueber gezaehlt
    monkeypatch.setattr(layer_plans, "MAX_NODES", 3)
    small = _plan(client, tid, "l3")
    lim = small["limits"]
    assert lim["nodes_shown"] == len(small["nodes"]) == 3
    assert lim["nodes_total"] > 3 and sum(lim["dropped"].values()) == lim["nodes_total"] - 3
    assert lim["edges_total"] > lim["edges_shown"]


def test_export_drawio_mit_schicht_standort_und_ebenen(client):
    tid, sids = _l3_seed(client)
    site = _sites(client, tid)
    client.patch(f"/systems/{sids[tv.FW_A]}", json={"site_id": site["id"]}, headers=ADMIN)
    query = {"tenant_id": tid, "site": site["id"], "hide": "nets,static"}
    r = client.get("/network-plans/l3/export", params=query)
    assert r.status_code == 200, r.text
    name = r.headers["content-disposition"]
    assert "plan-l3-routing-chzrh1-" in name and name.endswith('.drawio"')
    root = ET.fromstring(r.text)  # noqa: S314 - eigener Export im Test
    cells = root.findall(".//mxCell")
    layers = {c.get("value"): c for c in cells if c.get("parent") == "0"}
    assert {"Netze", "IPsec-Tunnel", "Statische Routen"} <= set(layers)
    assert layers["Netze"].get("visible") == "0" and layers["IPsec-Tunnel"].get("visible") is None
    objs = {o.get("automato_name") for o in root.iter("UserObject") if o.get("automato_name")}
    assert {tv.FW_A, tv.FW_B, "10.10.0.0/24"} <= objs
    assert any(c.get("value") == "chzrh1 · Zürich" for c in cells)
    # L1 alle Standorte, englisch; L2 ohne Standort
    r = client.get("/network-plans/l1/export", params={"tenant_id": tid},
                   headers={"Accept-Language": "en"})  # fmt: skip
    assert "plan-l1-physical-all-" in r.headers["content-disposition"]
    assert "L1 plan · Physical" in r.text
    r = client.get("/network-plans/l2/export", params={"tenant_id": tid, "site": 0})
    assert "plan-l2-switching-ohne-standort-" in r.headers["content-disposition"]


@pytest.mark.parametrize("lang", ["de", "en"])
def test_texte_zweisprachig(client, lang):
    tid, _sids = _l3_seed(client)
    plan = _plan(client, tid, "l3", headers={"Accept-Language": lang})
    assert plan["title"] == ("L3-Plan · Routing" if lang == "de" else "L3 plan · Routing")
    labels = {x["key"]: x["label"] for x in plan["layers"]}
    assert labels["nets"] == ("Netze" if lang == "de" else "Networks")


def test_rechte_lesen_kunden_benutzer_nicht(client):
    tid, _sids = _l3_seed(client)
    viewer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid)}
    assert (
        client.get("/network-plans/l1", params={"tenant_id": tid}, headers=viewer).status_code
        == 200
    )
    other = client.post("/tenants", json={"name": "Kunde B"}, headers=ADMIN).json()["id"]
    foreign = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(other)}
    assert client.get("/network-plans/l1", params={"tenant_id": tid},
                      headers=foreign).status_code == 403  # fmt: skip
    customer = {"X-Dev-Roles": "viewer", "X-Dev-Tenants": str(tid), "X-Dev-Customers": "1"}
    assert client.get("/network-plans/l3", params={"tenant_id": tid},
                      headers=customer).status_code == 403  # fmt: skip
    # Modul Analyse aus: auch die Plaene
    r = client.put("/modules/analyse", params={"tenant_id": tid}, json={"enabled": False},
                   headers=ADMIN)  # fmt: skip
    assert r.status_code == 200, r.text
    assert client.get("/network-plans/l2", params={"tenant_id": tid}).status_code == 403
