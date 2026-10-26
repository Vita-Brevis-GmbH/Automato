"""Vorschlaege beim Erfassen einer Zone (0.66.0, ADR 0024 Nr. 15).

Umgebung wie in test_zone_detect: fw-01 (Zonen server/aci/transit/internet, VDOM kunde-a),
fw-02 (Zone lan), fw-03 (Interface mit Rolle dmz, „DMZ-Mail“, Zone lan), ACI-VRF TE:VRF1 mit
EPG_web, DDI mit Zone=Backup und Zone=EPG_web.
"""

from test_zone_detect import _setup
from test_zones import OP, VIEW, _sel, _zone


def _assist(client, tid, **body) -> dict:
    r = client.post("/zones/assist", json={"tenant_id": tid, **body}, headers=VIEW)
    assert r.status_code == 200, r.text
    return r.json()


def _sels(out) -> dict[tuple, dict]:
    return {(i["selector"]["kind"], i["selector"]["value"], i["selector"]["source_system_id"]): i
            for i in out["items"]}  # fmt: skip


def test_vorschlaege_beim_erfassen(client):
    tid, ids = _setup(client)
    # DMZ: Interface mit „DMZ“ in der Beschreibung (Name), Interface mit Rolle dmz (zur Art)
    dmz = _sels(_assist(client, tid, name="DMZ", kind="dmz"))
    mail = dmz[("fw_interface", "port4", ids["fw-03"])]
    assert mail["source_label"] == "Firewall-Interface" and mail["samples"] == ["10.31.0.0/24"]
    assert "DMZ-Mail" in mail["label"] and mail["reason"] == "Name in „DMZ-Mail“"
    role = dmz[("fw_interface", "port3", ids["fw-03"])]
    assert role["reason"] == "Rolle dmz" and role["score"] < mail["score"]
    # ohne Art „DMZ“ kein Treffer ueber die Rolle
    assert ("fw_interface", "port3", ids["fw-03"]) not in _sels(_assist(client, tid, name="DMZ"))

    # LAN: die FortiGate-Zone lan je Firewall (die Interfaces darin nicht noch einmal)
    lan = _assist(client, tid, name="LAN", code="LAN1")
    s = _sels(lan)
    assert {k for k in s if k[0] == "fw_zone"} == {("fw_zone", "lan", ids["fw-02"]),
                                                  ("fw_zone", "lan", ids["fw-03"])}  # fmt: skip
    assert not any(k[0] == "fw_interface" for k in s)
    assert s[("fw_zone", "lan", ids["fw-02"])]["reason"] == "gleicher Name „lan“"
    # schon gewaehlte Selektoren fallen weg; was sie erfassen, ist „schon erfasst“
    again = _sels(_assist(client, tid, name="LAN", selectors=[
        {"kind": "fw_zone", "value": "lan", "source_system_id": ids["fw-02"]},
        {"kind": "prefix", "value": "10.61.0.0/24"},
    ]))  # fmt: skip
    assert ("fw_zone", "lan", ids["fw-02"]) not in again
    assert again[("fw_zone", "lan", ids["fw-03"])]["covered"]

    # IPAM-Merkmal; ein Netz in einer anderen Zone wird genannt
    backup = _sels(_assist(client, tid, name="Backup"))
    assert backup[("ipam_tag", "Zone=Backup", None)]["samples"] == ["10.40.0.0/24"]
    _zone(client, tid, "Office", [_sel("prefix", "10.61.0.0/24")], kind="access")
    office = _sels(_assist(client, tid, name="LAN"))[("fw_zone", "lan", ids["fw-03"])]
    assert office["conflicts"] == [{"network": "10.61.0.0/24", "zone": "Office"}]

    # ACI und VDOM nach Namen; zu kurze Namen ohne Vorschlag
    web = _sels(_assist(client, tid, name="Web"))
    assert ("aci_epg", "TE/AP1/EPG_web", None) in web and ("aci_bd", "TE/BD_web", None) in web
    assert ("fw_vdom", "kunde-a", ids["fw-01"]) in _sels(_assist(client, tid, name="Kunde A"))
    assert _assist(client, tid, name="x")["items"] == []


def test_assist_rechte_und_pruefung(client):
    tid, _ids = _setup(client)
    body = {"tenant_id": tid, "name": "LAN"}
    assert client.post("/zones/assist", json=body, headers=OP).status_code == 200
    bad = {**body, "selectors": [{"kind": "prefix", "value": "kein-netz"}]}
    assert client.post("/zones/assist", json=bad, headers=OP).status_code == 422
    cust = {"X-Dev-Roles": "viewer", "X-Dev-Customers": "1"}
    assert client.post("/zones/assist", json=body, headers=cust).status_code == 403
