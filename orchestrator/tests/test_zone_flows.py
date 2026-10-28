"""Zonentyp, Geraete, Ausgaenge aus dem Routing und Befund (0.105.0, seit 0.106.0 einfach, ADR
0035, Anwender 2026-10-28):

- Zonentyp = Zone des Grundschemas (hier die eingebaute Vorlage): erkannt nach dem Namen
  („DC Web“ → DC) bzw. von Hand (Netzwerkdesign → Kategorien);
- zuerst die Sonderfaelle „innerhalb der Applikation“ (7) und „innerhalb der eigenen Zone“ (8),
  dann die Richtung Zonentyp → Zonentyp;
- Ausgaenge ohne Pflege: eine Gegenstelle any bzw. ein grosser Bereich gehoert zu den Zonen
  hinter dem Interface der Regel (Anbindung, geroutete Netze) - nicht eindeutig: unspezifisch;
- Geraete der Zonen (Firewall/VDOM, ACI-VRF, Anbindung) im Netzwerkdesign;
- Befund, wenn eine Firewall ein Netz einer Zone nicht ueber den Uebergang Richtung Zone lernt.
"""

import test_paths as tp
from test_appdoc_names import ADMIN, OP
from test_appdoc_zones import _row, _setup, _types, _where
from test_vmware import _deliver, _system


def _fw(pid, name, src, dst, src_zone, dst_zone, **raw):
    return {"source_platform": "fortigate", "external_id": str(pid), "name": name,
            "action": "allow", "sources": src, "destinations": dst, "services": ["tcp/443"],
            "family": "v4", "src_zone": src_zone, "dst_zone": dst_zone, "vdom": "root",
            "raw": {"policyid": pid, "status": "enable", **raw}}  # fmt: skip


FW5 = {
    "status": "completed",
    "rules": [_fw(61, "dc-intern", ["10.20.9.9"], ["10.20.0.5"], "srv", "srv")],
}


def test_zonentyp_und_sonderfaelle(client):
    tid, app, ids = _setup(client)
    fw5 = _system(client, tid, "fw-05", "fortigate", base_url="https://fw5")
    _deliver(client, tid, fw5, "fortigate", FW5)
    body = {"tenant_id": tid, "name": "DC Web", "kind": "dc",
            "selectors": [{"kind": "prefix", "value": "10.20.0.0/16"}]}  # fmt: skip
    dc = client.post("/zones", json=body, headers=OP).json()["id"]

    # Zonentypen: DC erkannt nach dem Namen, die anderen von Hand (Vorlage ohne Grundschema)
    zc = client.get("/zones/appdoc/catalog", params={"tenant_id": tid}).json()
    assert "Edge Office" in zc["zone_types"] and "has_flows" not in zc
    assert zc["types"][str(dc)] == "DC" and zc["types"][str(ids["Clients"])] is None
    assert zc["auto_types"][str(dc)] == "DC"
    r = _types(client, tid, {ids["Clients"]: "Edge Office", ids["Partner"]: "Public"})
    assert r.status_code == 200, r.text
    listed = {z["name"]: z for z in client.get(f"/zones?tenant_id={tid}").json()}
    assert listed["Clients"]["schema"] == "Edge Office" and listed["DC Web"]["schema"] == "DC"

    doc = client.get(f"/applications/{app['id']}/doc").json()
    where = _where(doc)
    assert where["clients-to-web"] == {4}  # Edge Office → DC (das System liegt in DC Web)
    assert where["web-to-clients"] == {3}
    assert where["dc-intern"] == {8}  # innerhalb der eigenen Zone
    assert where["to-db"] == {7}  # beide Seiten in der Applikation (Server intern)
    assert _row(doc, "dc-intern")["Zone"] == "DC Web"
    t4 = next(t for t in doc["tables"] if t["index"] == 4)
    assert [(z["name"], z["type"]) for z in t4["zones"]] == [("Clients", "Edge Office")]
    system = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}).json()
    assert _where(system)["clients-to-web"] == {4} and _where(system)["dc-intern"] == {8}


FW1 = {**tp.FW1, "rules": [
    _fw(71, "inet-to-app", ["all"], ["10.20.0.5"], "internet", "server",
        srcintf=[{"name": "wan1"}], dstintf=[{"name": "srv"}]),
    _fw(72, "lan-to-app", ["all"], ["10.20.0.5"], "transit", "server",
        srcintf=[{"name": "to-fw02"}], dstintf=[{"name": "srv"}]),
    _fw(73, "wide-to-app", ["10.0.0.0/8"], ["10.20.0.5"], "transit", "server",
        srcintf=[{"name": "to-fw02"}], dstintf=[{"name": "srv"}]),
    _fw(74, "aci-to-app", ["all"], ["10.20.0.5"], "aci", "server",
        srcintf=[{"name": "to-aci"}], dstintf=[{"name": "srv"}]),
]}  # fmt: skip


def _zone(client, tid, name, selectors, kind="internal"):
    body = {"tenant_id": tid, "name": name, "kind": kind, "selectors": selectors}
    r = client.post("/zones", json=body, headers=OP)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ids(client, tid) -> dict[str, int]:
    systems = client.get(f"/systems?tenant_id={tid}", headers=ADMIN).json()
    return {s["name"]: s["id"] for s in systems}


def test_ausgaenge_aus_geraeten_und_routing(client):
    tid = tp._seed(client)
    sys = _ids(client, tid)
    tp._deliver(client, tid, sys["fw-01"], "fortigate", FW1)
    exit_ = {"tenant_id": tid, "name": "Internet Provider", "kind": "internet",
             "source_system_ids": [sys["fw-01"]], "interfaces": ["wan1", "wan2"]}  # fmt: skip
    assert client.post("/routing/exits", json=exit_, headers=OP).status_code == 201

    # Geraete zur Auswahl (Netzwerkdesign → Kategorien)
    devices = client.get("/zones/appdoc/catalog", params={"tenant_id": tid}).json()["devices"]
    labels = {(d["kind"], d["label"]) for d in devices}
    assert {("fw_vdom", "fw-01 · root"), ("fw_vdom", "fw-01 · kunde-a"), ("fw_vdom", "fw-02"),
            ("aci_vrf", "TE:VRF1"), ("exit", "Internet Provider")} <= labels  # fmt: skip

    server = _zone(client, tid, "Server", [{"kind": "prefix", "value": "10.20.0.0/24"}])
    lan = _zone(client, tid, "Lan", [])
    inet = _zone(client, tid, "Internet", [], kind="external")
    # Geraete je Zone: fw-02 in Lan, die Anbindung in Internet - die uebrigen Selektoren bleiben
    zones = [{"id": lan, "zone_type": "Office Access",
              "devices": [{"kind": "fw_vdom", "value": "root", "source_system_id": sys["fw-02"]}]},
             {"id": inet, "devices": [{"kind": "exit", "value": "Internet Provider"}]},
             {"id": server, "zone_type": "DC"}]  # fmt: skip
    r = client.put("/zones/appdoc", json={"tenant_id": tid, "zones": zones}, headers=OP)
    assert r.status_code == 200, r.text
    out = {z["name"]: z for z in r.json()}
    assert out["Lan"]["devices"] == [{"kind": "fw_vdom", "value": "root",
                                      "source_system_id": sys["fw-02"]}]  # fmt: skip
    assert out["Server"]["nets"] == ["10.20.0.0/24"] and out["Server"]["devices"] == []
    bad = [{"id": lan, "devices": [{"kind": "prefix", "value": "10.0.0.0/8"}]}]
    assert client.put("/zones/appdoc", json={"tenant_id": tid, "zones": bad},
                      headers=OP).status_code == 422  # fmt: skip
    other = [{"id": lan, "devices": [{"kind": "fw_vdom", "value": "root",
                                      "source_system_id": 999999}]}]  # fmt: skip
    assert client.put("/zones/appdoc", json={"tenant_id": tid, "zones": other},
                      headers=OP).status_code == 404  # fmt: skip

    doc = client.get("/appdoc/systems/10.20.0.5", params={"tenant_id": tid}).json()
    where = _where(doc)
    # any ueber wan1: die Anbindung der Zone Internet (Public) → Zugriff aus dem Internet
    assert where["inet-to-app"] == {2} and _row(doc, "inet-to-app")["Zone"] == "Internet"
    # any ueber to-fw02: die Netze dahinter (10.60.0.0/24 an fw-02) liegen in Lan
    assert where["lan-to-app"] == {4} and _row(doc, "lan-to-app")["Zone"] == "Lan"
    # 10.0.0.0/8 ueber to-fw02: nur die Zonen darin, die dort liegen
    assert where["wide-to-app"] == {4}
    # any ueber to-aci: dahinter keine Zone - unspezifisch
    assert where["aci-to-app"] == {10}


def test_befund_netz_nicht_ueber_den_uebergang(client):
    tid = tp._seed(client)
    sys = _ids(client, tid)

    def dev(sid, vdom="root"):
        return {"kind": "fw_vdom", "value": vdom, "source_system_id": sid}

    _zone(client, tid, "DC FW", [dev(sys["fw-01"])])
    _zone(client, tid, "ACI", [{"kind": "aci_vrf", "value": "TE:VRF1"}])
    _zone(client, tid, "Server", [{"kind": "prefix", "value": "10.20.0.0/24"}])
    # Lan hinter fw-02: 10.60.0.0/24 lernt fw-01 ueber to-fw02 (in Ordnung), 10.50.0.0/16 aber
    # ueber to-aci (Richtung ACI) und 10.99.0.0/16 nur ueber die Default-Route
    _zone(client, tid, "Lan", [dev(sys["fw-02"]), {"kind": "prefix", "value": "10.99.0.0/16"},
                               {"kind": "prefix", "value": "10.50.0.0/16"}])  # fmt: skip
    found = client.get("/findings", params={"tenant_id": tid, "type": "zone_net_exit"}).json()
    by = {f["subject_id"]: f for f in found}
    assert set(by) == {"Lan · 10.50.0.0/16", "Lan · 10.99.0.0/16"}, set(by)
    aci = by["Lan · 10.50.0.0/16"]
    assert aci["detail"]["expected"] == ["to-fw02"] and aci["detail"]["toward"] == ["Lan"]
    assert "über to-aci" in aci["message"] and "to-aci führt zur Zone ACI" in aci["message"]
    assert "erwartet über to-fw02 Richtung Lan" in aci["message"]
    assert "nur über die Default-Route (wan1, wan2)" in by["Lan · 10.99.0.0/16"]["message"]
    assert aci["severity"] == "warning"
