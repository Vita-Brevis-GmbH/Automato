# ruff: noqa: S104 - 0.0.0.0/0 sind Netze, keine Bind-Adressen
"""Zonen erkennen und vorschlagen (0.54.0, ADR 0024 Nr. 10; seit 0.56.0 nach dem Zonenmodell).

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
    """Vorschlaege nach dem Zonenmodell (0.56.0) mit der eingebauten Vorlage: DC aus den
    ACI-Tenants (je Kunde), Edge Internet fuer die Firewall mit einem Bein an der Anbindung,
    Public aus der Anbindung, Muster der Vorlage auf Firewall-Zonen und Interfaces, IPAM."""
    tid, ids = _setup(client)
    out = _get(client, tid)
    s = _by_key(out)
    cid = ids["customer"]
    assert set(s) == {f"dc:{cid}", "edge-internet", "edge-management", "edge-transfer",
                      "public", "backup"}  # fmt: skip
    assert all(x["action"] == "create" for x in out["items"])
    assert out["summary"]["template"] and out["schema"] is None

    # ACI: alle Tenants sind Datacenter, der Tenant des Kunden in seiner DC-Zone
    dc = s[f"dc:{cid}"]
    assert (dc["name"], dc["code"], dc["kind"], dc["customer"]) == (
        "DC Kunde TE", "DC-TE", "dc", "Kunde TE")  # fmt: skip
    assert _sels(dc) == {("aci_tenant", "TE", False)} and dc["color"] == "#dae8fc"
    assert dc["counts"] == {"nets": 1, "epgs": 1, "nodes": 0}
    # Internet-Firewall: Bein an der Anbindung -> Edge Internet (Farbe der Vorlage)
    edge = s["edge-internet"]
    assert (edge["name"], edge["code"], edge["kind"], edge["color"]) == (
        "Edge Internet", "EINET", "edge", "#ffcc99")  # fmt: skip
    assert edge["selectors"] == [{"kind": "fw_vdom", "value": "root",
                                  "source_system_id": ids["fw-01"], "exclude": False}]  # fmt: skip
    assert edge["nodes"] == ["fw-01 · VDOM root"] and edge["sources"] == ["node"]
    assert edge["evidence"] == ["Firewall fw-01 · VDOM root: Anbindung Internet über wan1"]
    # Public: Anbindung ohne private Netze und die Firewall-Zone internet (Muster internet*)
    pub = s["public"]
    assert (pub["name"], pub["code"], pub["kind"]) == ("Public", "PUB", "external")
    assert {("exit", "Internet", False), ("prefix", "10.0.0.0/8", True),
            ("fw_zone", "internet", False)} <= _sels(pub)  # fmt: skip
    assert pub["samples"] == ["198.51.100.0/30", "198.51.100.4/30", "Anbindung Internet"]
    # Muster der Vorlage: Zone transit -> Edge Transfer, Interface mgmt -> Edge Management
    assert _sels(s["edge-transfer"]) == {("fw_zone", "transit", False)}
    assert s["edge-transfer"]["samples"] == ["10.70.0.0/30"]
    mg = s["edge-management"]
    assert mg["selectors"] == [{"kind": "fw_interface", "value": "mgmt",
                                "source_system_id": ids["fw-03"], "exclude": False}]  # fmt: skip
    assert _sels(s["backup"]) == {("ipam_tag", "Zone=Backup", False)}
    # Knoten ohne Treffer zum Zuordnen; Netze ohne Zone und Vorschlag ohne die Netze der
    # vorgeschlagenen Knoten (fw-01 root)
    assert [n["label"] for n in out["nodes"]] == ["fw-01 · VDOM kunde-a", "fw-02", "fw-03"]
    assert out["nodes"][1]["id"] == f"{ids['fw-02']}|root"
    assert [r["network"] for r in out["rest"]["items"]] == [
        "10.255.1.0/30", "10.60.0.0/24", "10.30.0.0/24", "10.31.0.0/24", "10.32.0.0/24",
        "10.61.0.0/24"]  # fmt: skip
    assert out["summary"]["create"] == 6 and out["summary"]["nodes"] == 3


def test_uebernehmen_ergaenzen_ausblenden(client, session_factory):
    tid, ids = _setup(client)
    # bestehende Zonen: Edge Transfer (ein Netz) wird ergaenzt; das Netz der EPG liegt schon in
    # einer Zone - der DC-Vorschlag schliesst es aus
    transfer = _zone(client, tid, "Edge Transfer", [_sel("prefix", "10.70.0.0/30")],
                     kind="transfer", code="ETRF")  # fmt: skip
    _zone(client, tid, "Web", [_sel("prefix", "10.50.1.0/24")])
    s = _by_key(_get(client, tid))
    t = s["edge-transfer"]
    assert (t["action"], t["zone_id"], t["zone"]) == ("extend", transfer["id"], "Edge Transfer")
    assert _sels(t) == {("fw_zone", "transit", False)} and t["samples"] == []
    dc = s[f"dc:{ids['customer']}"]
    assert dc["conflicts"] == [{"network": "10.50.1.0/24", "zone": "Web"}]
    assert ("prefix", "10.50.1.0/24", True) in _sels(dc)

    # nur Operator
    keys = ["edge-transfer", "edge-internet", "public", f"dc:{ids['customer']}", "gibt-es-nicht"]
    body = {"tenant_id": tid, "keys": keys}
    assert client.post("/zones/suggestions/accept", json=body, headers=VIEW).status_code == 403
    r = client.post("/zones/suggestions/accept", json=body, headers=OP)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["updated"] == [transfer["id"]] and len(res["created"]) == 3
    assert res["missing"] == ["gibt-es-nicht"]
    zones = {z["name"]: z for z in client.get("/zones", params={"tenant_id": tid}).json()}
    assert len(zones["Edge Transfer"]["selectors"]) == 2
    einet = zones["Edge Internet"]
    assert (einet["kind"], einet["code"], einet["description"]) == (
        "edge", "EINET", "Vorschlag aus Firewall")  # fmt: skip
    assert einet["nodes"] == ["fw-01 · VDOM root"]
    # Farbe aus der Vorlage, ueberall gleich
    assert (einet["color"], einet["color_source"]) == ("#ffcc99", "template")
    assert zones["DC Kunde TE"]["customer_id"] == ids["customer"]
    assert zones["DC Kunde TE"]["code"] == "DC-TE"
    names = {t["name"] for t in zones["Edge Internet"]["transitions"]}
    assert {"EINET-PUB", "DC-TE-EINET"} <= names
    assert not {"edge-transfer", "edge-internet", "public"} & set(_by_key(_get(client, tid)))
    # kein gleiches Netz in zwei Zonen nach dem Uebernehmen
    f = client.get("/findings", params={"tenant_id": tid, "type": "zone_overlap"}).json()
    assert (f["items"] if isinstance(f, dict) else f) == []
    s = session_factory()
    vias = [a.detail.get("via") for a in s.query(AuditLog).filter_by(action="zone.created")]
    s.close()
    assert vias.count("suggestion") == 3

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
