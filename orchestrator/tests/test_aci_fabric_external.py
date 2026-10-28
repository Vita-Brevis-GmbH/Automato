"""Externe EPG im Fabric, wenn die Firewall hinter dem L3Out die IP zurueck ins ACI leitet
(0.103.0, Befund des Anwenders 2026-10-28 in den Verbindungen Ende zu Ende: „keine EPG bzw.
externe EPG erfasst 10.67.0.13 in VRF … – das Fabric verwirft den Verkehr; jedoch ist die IP das
Ziel und bei 10.0.0.0/8 im L3O / external EPG vorhanden und mit Contract erlaubt“).

Umgebung wie `test_l3out_firewall` (ADR 0034, Firewall fuehrend), dazu die externe EPG EN_dc
(10.0.0.0/8) am L3O_bit mit dem Contract CT_dc (App -> EN_dc, tcp/443). Beide Firewalls leiten
10.67.0.0/16 zurueck ins ACI (fw-bit ueber 0.0.0.0/0, fw-inet ueber 10.0.0.0/8) - nach ihrem
Routing kommt die IP ueber keinen L3Out. Das Fabric ordnet sie trotzdem zu: VRF-weit das
laengste zuordnende Subnetz, also EN_dc - mit Contract erlaubt. Die Doku bleibt Firewall
fuehrend; geaendert ist nur die Pruefung im Fabric (Weg-Suche, Verbindungen).
"""

import copy

import test_l3out_firewall as lf
import test_paths as tp

TARGET = "10.67.0.13"


def _seed(client, contract: bool = True) -> int:
    tid = client.post("/tenants", json={"name": "Kunde A"}, headers=lf.ADMIN).json()["id"]
    fw_inet = lf._fw_inet()
    fw_inet["routing"]["routes"].append(
        tp._r("fw-inet", "root", "10.0.0.0/8", [{"ip": "10.255.2.2", "interface": "to-aci"}])
    )
    apic = copy.deepcopy(lf.APIC)
    apic["epgs"].append(lf._ext("EN_dc", "L3O_bit", ["10.0.0.0/8"]))
    bit = next(o for o in apic["routing"]["l3outs"] if o["name"] == "L3O_bit")
    bit["ext_epgs"].append("EN_dc")
    if contract:
        apic["rules"].append(lf._contract("CT_dc", lf.APP_DN, f"{lf.BIT}/instP-EN_dc", 443))
    for name, platform, result in (("fw-inet", "fortigate", fw_inet),
                                   ("fw-bit", "fortigate", lf.FW_BIT),
                                   ("apic-01", "cisco_aci", apic)):  # fmt: skip
        sid = tp._system(client, tid, name, platform)
        tp._deliver(client, tid, sid, platform, result)
    return tid


def _aci_hop(client, tid, headers=None) -> dict:
    params = {"tenant_id": tid, "src": lf.APP, "dst": TARGET, "service": "tcp/443"}
    r = client.get("/routing/trace", params=params, headers=headers or {})
    assert r.status_code == 200, r.text
    hops = [h for p in r.json()["paths"] for h in p["hops"] if "VRF" in h["label"]]
    assert hops, r.json()
    return hops[0]["rules"]


def test_ziel_ueber_die_externe_epg_im_fabric(client):
    tid = _seed(client)
    aci = _aci_hop(client, tid)
    assert aci["dst_epg"] == "externe EPG L3O_bit|EN_dc (10.0.0.0/8) – Zuordnung im ACI"
    assert aci["verdict"] == "allow", aci
    assert any(r["name"] == "CT_dc" for r in aci["rules"])
    en = _aci_hop(client, tid, headers=lf.EN)
    assert en["dst_epg"] == "external EPG L3O_bit|EN_dc (10.0.0.0/8) – assignment in the ACI"


def test_ohne_contract_gesperrt_mit_der_externen_epg(client):
    tid = _seed(client, contract=False)
    aci = _aci_hop(client, tid)
    assert aci["dst_epg"].startswith("externe EPG L3O_bit|EN_dc (10.0.0.0/8)")
    assert aci["verdict"] == "implicit_deny"


def test_firewall_bleibt_fuehrend_wenn_sie_einen_l3out_nennt(client):
    """Die DMZ (fw-inet angeschlossen) kommt weiter ueber L3O_inet (ADR 0034), auch wenn EN_bit
    sie im ACI genauer erfasst."""
    tid = _seed(client)
    params = {"tenant_id": tid, "src": "192.168.50.10", "dst": lf.APP, "service": "tcp/443"}
    (path,) = client.get("/routing/trace", params=params).json()["paths"]
    aci = path["hops"][1]["rules"]
    assert aci["src_epg"] == "externe EPG L3O_inet|EN_inet (0.0.0.0/0)"
