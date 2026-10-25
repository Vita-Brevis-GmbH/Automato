# ruff: noqa: S104 - 0.0.0.0/0 sind Netze, keine Bind-Adressen
"""Zonen erkennen und vorschlagen (0.54.0, ADR 0024 Nr. 10).

Umgebung wie in test_zones (fw-01 mit Zonen server/aci/transit/internet, fw-02 mit lan,
ACI-VRF TE:VRF1, Anbindung Internet) und dazu fw-03: Management-Interface, Interface mit
FortiOS-Rolle dmz, Interface „DMZ-Mail“, Interface ohne Hinweis, weiteres Interface in der
Zone lan. Im DDI ein Netz mit dem Attribut Zone=Backup.
"""

from app.models import AuditLog
from app.store.zone_detect import classify, key_of
from test_paths import TOKEN, _deliver, _run, _system
from test_zones import OP, VIEW, _seed, _sel, _zone


def _if(name, ip, zone=None, role=None, description=None):
    return {"device": "fw-03", "name": name, "ips": [ip], "zone": zone, "vdom": "root",
            "role": role, "description": description}  # fmt: skip


FW3 = {
    "status": "completed",
    "inventory": {
        "devices": [{"name": "fw-03", "role": "firewall"}],
        "interfaces": [
            _if("mgmt", "10.99.0.2/24", role="management"),
            _if("port3", "10.30.0.1/24", role="dmz"),
            _if("port4", "10.31.0.1/24", description="DMZ-Mail"),
            _if("port5", "10.32.0.1/24"),
            _if("lan2", "10.61.0.1/24", zone="lan"),
        ],
    },
    "routing": {
        "contexts": [{"node": "fw-03", "context": "root"}],
        "routes": [
            {"node": "fw-03", "context": "root", "prefix": p, "protocol": "connected",
             "next_hops": [{"interface": i}]}
            for p, i in (("10.30.0.0/24", "port3"), ("10.31.0.0/24", "port4"),
                         ("10.32.0.0/24", "port5"), ("10.61.0.0/24", "lan2"))
        ],
    },
}  # fmt: skip


def _setup(client) -> tuple[int, dict[str, int]]:
    tid, ids = _seed(client)
    ids["fw-03"] = _system(client, tid, "fw-03", "fortigate")
    _deliver(client, tid, ids["fw-03"], "fortigate", FW3)
    ddi = _system(client, tid, "ddi-2", "infoblox", username="u", password="p")
    run_id = _run(client, tid, ddi, "infoblox")
    rec = [{"kind": "prefix", "value": "10.40.0.0/24", "attrs": {"Zone": "Backup"}}]
    client.post(f"/internal/collector-runs/{run_id}/references", headers=TOKEN,
                json={"records": rec})  # fmt: skip
    client.post(f"/internal/collector-runs/{run_id}/result", headers=TOKEN,
                json={"status": "completed", "reference_count": 1})  # fmt: skip
    body = {"tenant_id": tid, "name": "Kunde TE", "code": "te",
            "selectors": [_sel("aci_tenant", "TE")]}  # fmt: skip
    r = client.post("/customers", json=body, headers=OP)
    assert r.status_code == 201, r.text
    ids["customer"] = r.json()["id"]
    return tid, ids


def _get(client, tid, **params) -> dict:
    r = client.get("/zones/suggestions", params={"tenant_id": tid, **params}, headers=VIEW)
    assert r.status_code == 200, r.text
    return r.json()


def _by_key(out) -> dict[str, dict]:
    return {s["key"]: s for s in out["items"]}


def _sels(s) -> set[tuple]:
    return {(x["kind"], x["value"], x["exclude"]) for x in s["selectors"]}


def test_namen_und_art():
    assert key_of("Z_DMZ") == "dmz" and key_of("wan1", strip_digits=True) == "wan"
    assert key_of("VL100_SRV", strip_digits=True) == "srv" and key_of("DMZ1") == "dmz1"
    assert classify("port3", "DMZ-Mail") == ("dmz", "dmz")
    assert classify("wan2") == ("external", "wan") and classify("OOB-Netz")[0] == "management"
    assert classify("server") is None


def test_vorschlaege_aus_dem_gelesenen(client):
    tid, ids = _setup(client)
    out = _get(client, tid)
    s = _by_key(out)
    assert set(s) == {"transfer", "server", "internet", "lan", "management", "dmz", "dmz-mail",
                      "backup", "aci:te:vrf1"}  # fmt: skip
    assert all(x["action"] == "create" for x in out["items"])

    # Transfernetze zwischen gelesenen Knoten plus die Firewall-Zone transit; die Zone aci auf
    # fw-01 hat nur das Transfernetz zur ACI und faellt weg
    t = s["transfer"]
    assert (t["name"], t["kind"]) == ("Transfer", "transfer")
    assert _sels(t) == {("prefix", "10.70.0.0/30", False), ("prefix", "10.255.0.0/29", False),
                        ("fw_zone", "transit", False)}  # fmt: skip
    assert t["sources"] == ["transfer", "fw_zone"] and t["counts"]["open"] == 2
    # Firewall-Zone und Anbindung mit gleichem Namen = eine Zone, ohne die privaten Netze
    inet = s["internet"]
    assert (inet["name"], inet["kind"], inet["color"]) == ("Internet", "external", "#cc3333")
    assert {("fw_zone", "internet", False), ("exit", "Internet", False),
            ("prefix", "10.0.0.0/8", True)} <= _sels(inet)  # fmt: skip
    # die Stuecke von 0.0.0.0/0 ohne die privaten Netze zaehlen als ein Eintrag
    assert inet["samples"] == ["198.51.100.0/30", "198.51.100.4/30", "Anbindung Internet"]
    assert inet["counts"] == {"nets": 3, "open": 3, "epgs": 0}
    # gleiche Firewall-Zone auf zwei Firewalls
    lan = s["lan"]
    assert lan["name"] == "LAN" and _sels(lan) == {("fw_zone", "lan", False)}
    assert lan["samples"] == ["10.60.0.0/24", "10.61.0.0/24"]
    assert {e.split(" · ")[1] for e in lan["evidence"]} == {"fw-02", "fw-03"}
    # Management-Interface, Rolle dmz, Name mit dmz - Interface je Firewall
    mg = s["management"]
    assert mg["kind"] == "management" and mg["selectors"] == [
        {"kind": "fw_interface", "value": "mgmt", "source_system_id": ids["fw-03"],
         "exclude": False}]  # fmt: skip
    assert (s["dmz"]["name"], s["dmz"]["kind_reason"]) == ("DMZ", "FortiOS-Rolle dmz")
    assert (s["dmz-mail"]["name"], s["dmz-mail"]["kind"]) == ("DMZ-Mail", "dmz")
    # IPAM-Merkmal und ACI-VRF (mit Kunde)
    assert _sels(s["backup"]) == {("ipam_tag", "Zone=Backup", False)}
    assert s["backup"]["samples"] == ["10.40.0.0/24"]
    vrf = s["aci:te:vrf1"]
    assert (vrf["name"], vrf["customer_id"], vrf["customer"]) == (
        "TE:VRF1",
        ids["customer"],
        "Kunde TE",
    )
    assert vrf["counts"]["epgs"] == 1 and vrf["samples"] == ["10.50.1.0/24"]
    # nicht erkannt: Interface ohne Hinweis, Interface im VDOM kunde-a
    assert [r["network"] for r in out["rest"]["items"]] == ["10.32.0.0/24", "10.255.1.0/30"]
    assert out["summary"]["create"] == 9 and out["summary"]["rest"] == 2


def test_uebernehmen_ergaenzen_ausblenden(client, session_factory):
    tid, ids = _setup(client)
    # bestehende Zonen: Transfer (ein Netz) wird ergaenzt; das Netz der ACI-VRF liegt schon in
    # einer Zone - der Vorschlag schliesst es aus und faellt weg
    transfer = _zone(client, tid, "Transfer", [_sel("prefix", "10.70.0.0/30")], kind="transfer")
    _zone(client, tid, "Web", [_sel("prefix", "10.50.1.0/24")])
    s = _by_key(_get(client, tid))
    assert "aci:te:vrf1" not in s
    t = s["transfer"]
    assert (t["action"], t["zone_id"], t["zone"]) == ("extend", transfer["id"], "Transfer")
    assert _sels(t) == {("prefix", "10.255.0.0/29", False), ("fw_zone", "transit", False)}
    assert t["samples"] == ["10.255.0.0/29"]
    assert t["refines"] == []  # die ergaenzte Zone selbst ist kein „genauer als“

    # nur Operator
    body = {"tenant_id": tid, "keys": ["transfer", "dmz", "server", "gibt-es-nicht"]}
    assert client.post("/zones/suggestions/accept", json=body, headers=VIEW).status_code == 403
    r = client.post("/zones/suggestions/accept", json=body, headers=OP)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["updated"] == [transfer["id"]] and len(res["created"]) == 2
    assert res["missing"] == ["gibt-es-nicht"]
    zones = {z["name"]: z for z in client.get("/zones", params={"tenant_id": tid}).json()}
    assert zones["Transfer"]["nets"] == 2 and len(zones["Transfer"]["selectors"]) == 3
    assert (
        zones["DMZ"]["kind"] == "dmz" and zones["DMZ"]["description"] == "Vorschlag aus Interface"
    )
    assert zones["Server"]["nets"] == 1
    assert not {"transfer", "dmz", "server"} & set(_by_key(_get(client, tid)))
    # kein gleiches Netz in zwei Zonen nach dem Uebernehmen
    f = client.get("/findings", params={"tenant_id": tid, "type": "zone_overlap"}).json()
    assert (f["items"] if isinstance(f, dict) else f) == []
    s = session_factory()
    vias = [a.detail.get("via") for a in s.query(AuditLog).filter_by(action="zone.created")]
    s.close()
    assert vias.count("suggestion") == 2

    # ausblenden und wieder vorschlagen
    body = {"tenant_id": tid, "keys": ["backup"]}
    assert client.post("/zones/suggestions/dismiss", json=body, headers=OP).json() == {
        "keys": ["backup"]}  # fmt: skip
    out = _get(client, tid)
    assert "backup" not in _by_key(out) and out["summary"]["dismissed"] == 1
    assert _by_key(_get(client, tid, dismissed=True))["backup"]["dismissed"] is True
    assert client.post("/zones/suggestions/dismiss/undo", json=body, headers=OP).json() == {
        "keys": ["backup"]}  # fmt: skip
    assert _by_key(_get(client, tid))["backup"]["dismissed"] is False


def test_kunden_benutzer_ohne_vorschlaege(client):
    tid, ids = _setup(client)
    headers = {"X-Dev-Roles": "customer", "X-Dev-Customers": str(ids["customer"])}
    r = client.get("/zones/suggestions", params={"tenant_id": tid}, headers=headers)
    assert r.status_code == 403
